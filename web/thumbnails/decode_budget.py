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


def estimate_decode_bytes(
    source_bytes: int | None = None,
    *,
    raw: bool = False,
    width: int | None = None,
    height: int | None = None,
) -> int:
    """Estimate peak decode working set.

    Prefer catalog/EXIF (or rawpy) dimensions: peak is W×H×3×safety, not file
    size. A 60MP demosaic is ~180MB of RGB alone; rawpy peaks near 3× that.
    File-size fallback remains for rows missing dimensions.
    """
    pixels = max(0, int(width or 0)) * max(0, int(height or 0))
    if pixels > 0:
        output_bytes = pixels * _RGB_BYTES_PER_PIXEL
        factor = _RAW_PEAK_FACTOR if raw else _NON_RAW_PEAK_FACTOR
        return max(MIN_DECODE_ESTIMATE_BYTES, output_bytes * factor)

    size = max(0, int(source_bytes or 0))
    if size <= 0:
        return MIN_DECODE_ESTIMATE_BYTES
    # Bayer → RGB (and LibRaw temps) roughly 3–4× compressed RAW size.
    # Non-RAW JPEG/PNG decode is closer to 1× file + decoded raster.
    factor = 4 if raw else 2
    return max(MIN_DECODE_ESTIMATE_BYTES, size * factor)


class DecodeByteBudget:
    """Async weighted semaphore over estimated decoded-frame bytes."""

    def __init__(self, max_bytes: int | None = None):
        self.max_bytes = max(MIN_DECODE_ESTIMATE_BYTES, int(max_bytes or MAX_INFLIGHT_DECODE_BYTES))
        self._used = 0
        self._cond = asyncio.Condition()

    @property
    def used_bytes(self) -> int:
        return self._used

    async def acquire(self, estimate: int) -> int:
        weight = max(MIN_DECODE_ESTIMATE_BYTES, int(estimate or 0))
        # Never block forever on a single frame larger than the budget.
        weight = min(weight, self.max_bytes)
        async with self._cond:
            while self._used > 0 and self._used + weight > self.max_bytes:
                await self._cond.wait()
            self._used += weight
        return weight

    async def release(self, weight: int) -> None:
        async with self._cond:
            self._used = max(0, self._used - max(0, int(weight or 0)))
            self._cond.notify_all()

    @asynccontextmanager
    async def hold(self, estimate: int):
        weight = await self.acquire(estimate)
        try:
            yield weight
        finally:
            await self.release(weight)


# Process-wide bulk decode budget (one archive process).
bulk_decode_budget = DecodeByteBudget()
