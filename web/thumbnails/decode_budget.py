"""Bound in-flight decoded source bytes during bulk preview warming."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager


_GIB = 1024**3
_RGB_BYTES_PER_PIXEL = 3
# rawpy peak ≈ raw buffer + demosaic intermediates + RGB output (~3× output).
_RAW_PEAK_FACTOR = 3
# Non-RAW decode holds source raster + working copy.
_NON_RAW_PEAK_FACTOR = 2
# Embedded JPEG/bitmap thumbs are ~10× cheaper than full demosaic of the same
# sensor dimensions (CTO 2026-07-19: load_raw_preview ~0.2s, not the bottleneck).
_EMBEDDED_RAW_COST_DIVISOR = 10


def _env_bytes(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        lowered = raw.lower()
        if lowered.endswith("g"):
            return int(float(lowered[:-1]) * _GIB)
        if lowered.endswith("m"):
            return int(float(lowered[:-1]) * 1024 * 1024)
        return int(raw)
    except ValueError:
        return default


# Soft cap on concurrent demosaic/decode working sets in the bulk path.
# Full-res RAW postprocess alone can peak near this for one 40–60MP frame.
MAX_INFLIGHT_DECODE_BYTES = _env_bytes(
    "PHOTOARCHIVE_BULK_DECODE_BYTES",
    768 * 1024 * 1024,
)
MIN_DECODE_ESTIMATE_BYTES = 16 * 1024 * 1024
# Acquire waits forever only if holders never release — bound the wait so a
# leaked weight cannot freeze the pregen loop indefinitely.
DECODE_BUDGET_WAIT_SECONDS = float(os.environ.get("PHOTOARCHIVE_DECODE_BUDGET_WAIT_SECONDS", "30"))


def estimate_decode_bytes(
    source_bytes: int | None = None,
    *,
    raw: bool = False,
    embedded_preview: bool = False,
    width: int | None = None,
    height: int | None = None,
) -> int:
    """Estimate peak decode working set.

    Prefer catalog/EXIF (or rawpy) dimensions: peak is W×H×3×safety, not file
    size. A 60MP demosaic is ~180MB of RGB alone; rawpy peaks near 3× that.

    When ``embedded_preview`` is True (bulk thumb path for RAW that prefers
    ``load_raw_preview``), charge ~1/10 of the demosaic estimate — embedded
    JPEG decode is that much cheaper than a full postprocess.
    """
    pixels = max(0, int(width or 0)) * max(0, int(height or 0))
    if pixels > 0:
        output_bytes = pixels * _RGB_BYTES_PER_PIXEL
        if raw and embedded_preview:
            demosaic = output_bytes * _RAW_PEAK_FACTOR
            return max(MIN_DECODE_ESTIMATE_BYTES, demosaic // _EMBEDDED_RAW_COST_DIVISOR)
        factor = _RAW_PEAK_FACTOR if raw else _NON_RAW_PEAK_FACTOR
        return max(MIN_DECODE_ESTIMATE_BYTES, output_bytes * factor)

    size = max(0, int(source_bytes or 0))
    if size <= 0:
        return MIN_DECODE_ESTIMATE_BYTES
    # Bayer → RGB (and LibRaw temps) roughly 3–4× compressed RAW size.
    # Embedded JPEG extract is closer to non-RAW decode cost.
    # Non-RAW JPEG/PNG decode is closer to 1× file + decoded raster.
    if raw and embedded_preview:
        return max(MIN_DECODE_ESTIMATE_BYTES, size // 2)
    factor = 4 if raw else 2
    return max(MIN_DECODE_ESTIMATE_BYTES, size * factor)


class DecodeByteBudget:
    """Async weighted semaphore over estimated decoded-frame bytes.

    Holds are epoch-stamped. ``reset()`` bumps the epoch so a cancelled batch's
    late ``release`` cannot steal weight from a newer acquisition (watchdog
    reset → cancel → finally race). ``release_nowait`` is safe inside a
    cancelled task's ``finally`` (no await).
    """

    def __init__(self, max_bytes: int | None = None):
        self.max_bytes = max(MIN_DECODE_ESTIMATE_BYTES, int(max_bytes or MAX_INFLIGHT_DECODE_BYTES))
        self._used = 0
        self._epoch = 0
        self._cond = asyncio.Condition()

    @property
    def used_bytes(self) -> int:
        return self._used

    @property
    def epoch(self) -> int:
        return self._epoch

    def reset(self) -> int:
        """Drop held weight — recovery path for a stalled/leaked budget.

        Returns the bytes that were marked in-use before the reset.
        Bumps epoch so in-flight releases from the cancelled generation are no-ops.
        """
        leaked = self._used
        self._used = 0
        self._epoch += 1
        return leaked

    async def acquire(self, estimate: int) -> int:
        weight = max(MIN_DECODE_ESTIMATE_BYTES, int(estimate or 0))
        # Never block forever on a single frame larger than the budget.
        weight = min(weight, self.max_bytes)
        charged = False
        try:
            async with self._cond:
                while self._used > 0 and self._used + weight > self.max_bytes:
                    # Timed wait so cancellation / watchdog can interrupt; the
                    # pregen self-watchdog resets leaked weight on stall.
                    try:
                        await asyncio.wait_for(
                            self._cond.wait(),
                            timeout=DECODE_BUDGET_WAIT_SECONDS,
                        )
                    except TimeoutError:
                        continue
                self._used += weight
                charged = True
            return weight
        except asyncio.CancelledError:
            # If we charged then got cancelled before the caller stored the
            # weight (or during lock exit), drop it synchronously.
            if charged:
                self.release_nowait(weight, epoch=self._epoch)
                self.wake_waiters_soon()
            raise

    def release_nowait(self, weight: int, *, epoch: int | None = None) -> bool:
        """Release without awaiting — safe from cancel-interrupted finally blocks.

        Returns False when ``epoch`` is stale (hold was already cleared by reset).
        """
        if epoch is not None and epoch != self._epoch:
            return False
        self._used = max(0, self._used - max(0, int(weight or 0)))
        return True

    def wake_waiters_soon(self) -> None:
        """Schedule a condition notify without awaiting (cancel-safe)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _notify() -> None:
            async with self._cond:
                self._cond.notify_all()

        loop.create_task(_notify())

    async def release(self, weight: int, *, epoch: int | None = None) -> None:
        async with self._cond:
            if self.release_nowait(weight, epoch=epoch):
                self._cond.notify_all()

    async def reset_and_notify(self) -> int:
        async with self._cond:
            leaked = self.reset()
            self._cond.notify_all()
            return leaked

    @asynccontextmanager
    async def hold(self, estimate: int):
        weight = await self.acquire(estimate)
        epoch = self._epoch
        try:
            yield weight
        finally:
            # Never await here — CancelledError must not skip the release.
            if self.release_nowait(weight, epoch=epoch):
                self.wake_waiters_soon()


# Process-wide bulk decode budget (one archive process).
bulk_decode_budget = DecodeByteBudget()
