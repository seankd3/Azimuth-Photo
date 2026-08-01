"""Keep the Develop base cache inside a size the machine can afford.

Decoding a RAW is expensive, so the result is cached — but nothing bounded that
cache, and on the hub it had grown to 77 GB, which is also why it had been
pushed onto the archive disk it was never supposed to touch. A cache without a
ceiling is not a cache, it is a slow leak.

The policy is the one the owner already settled for the laptop cache: **evict by
age, nothing else**. Not by whether a photo was exported, not by sync state —
the oldest decodes go first, so "what is in the cache" is always answerable as
"the most recent N gigabytes".

Only ``base/`` is ever evicted. The same directory tree holds downloaded models,
AI masks and pending exports, none of which are a decode result and none of
which are cheap to recreate.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# A decode result is one .bin.gz plus its .json sidecar; both go together.
_EVICTABLE_SUFFIXES = (".bin.gz", ".json", ".bin")


@dataclass
class Eviction:
    budget_bytes: int
    used_bytes: int
    removed_files: int
    reclaimed_bytes: int

    def summary(self) -> dict:
        return {
            "budget_gb": round(self.budget_bytes / 1024 ** 3, 2),
            "used_gb": round(self.used_bytes / 1024 ** 3, 2),
            "removed_files": self.removed_files,
            "reclaimed_gb": round(self.reclaimed_bytes / 1024 ** 3, 2),
        }


def auto_budget_bytes(cache_root: str | os.PathLike[str], *, configured_gb: float = 0) -> int:
    """A configured size, or a quarter of the drive if the owner has not chosen.

    A quarter matches the thumbnail mirror's ceiling, so the two caches cannot
    between them decide to own the whole disk.
    """

    if configured_gb and configured_gb > 0:
        return int(configured_gb * 1024 ** 3)
    import shutil

    try:
        usage = shutil.disk_usage(str(cache_root))
    except OSError:
        return 32 * 1024 ** 3
    return max(8 * 1024 ** 3, int(usage.total * 0.25))


def _entries(base_dir: Path) -> list[tuple[float, int, Path]]:
    found: list[tuple[float, int, Path]] = []
    for dirpath, _dirs, filenames in os.walk(base_dir):
        for name in filenames:
            if not name.endswith(_EVICTABLE_SUFFIXES):
                continue
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append((stat.st_mtime, int(stat.st_size), path))
    return found


def evict_to_budget(base_dir: str | os.PathLike[str], budget_bytes: int, *, apply: bool = True) -> Eviction:
    """Delete the oldest decodes until the cache fits. Reads only when apply=False."""

    base = Path(base_dir)
    if not base.is_dir():
        return Eviction(budget_bytes, 0, 0, 0)

    entries = _entries(base)
    used = sum(size for _mtime, size, _path in entries)
    if used <= budget_bytes:
        return Eviction(budget_bytes, used, 0, 0)

    entries.sort(key=lambda item: item[0])  # oldest first, and only that
    removed = reclaimed = 0
    for _mtime, size, path in entries:
        if used - reclaimed <= budget_bytes:
            break
        if apply:
            try:
                path.unlink()
            except OSError:
                continue
        removed += 1
        reclaimed += size
    return Eviction(budget_bytes, used, removed, reclaimed)


async def run_base_cache_budget_worker(*, interval_seconds: float = 3600.0) -> None:
    """Hold the decode cache at its ceiling, quietly."""

    import asyncio

    from core.background import wait_for_user_gap
    from core.runtime_paths import resolve_runtime_paths

    while True:
        try:
            await wait_for_user_gap()
            root = Path(resolve_runtime_paths().develop_cache_dir)
            configured = 0.0
            try:
                import settings as app_settings

                configured = float(app_settings.get_settings().get("develop_cache_gb", 0) or 0)
            except Exception:
                configured = 0.0
            budget = auto_budget_bytes(root, configured_gb=configured)
            result = await asyncio.to_thread(evict_to_budget, root / "base", budget)
            if result.removed_files:
                log.info("develop base cache evicted %s", result.summary())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("develop base cache budget pass failed")
        await asyncio.sleep(interval_seconds)
