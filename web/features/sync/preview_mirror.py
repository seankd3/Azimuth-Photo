"""Satellite local preview mirror — sm/md from SSD, hub only on miss.

Extends the existing thumb-cache layout under cache_root (same paths and
``cache_entries`` rows). ``source_signature`` holds the preview version so a
catalog change is a lazy miss, not a push invalidation.
"""

from __future__ import annotations


import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from data import connection
from features.sync import satellite
from archive import transport


log = logging.getLogger(__name__)

MIRROR_SIZES = ("sm", "md")
# Match the satellite thumb budget: full sm+md with headroom (~40GB).
DEFAULT_MIRROR_MAX_BYTES = 40 * 1024**3
DEFAULT_IDLE_GATE_SECONDS = 10.0
DEFAULT_BURST_LIMIT = 48
DEFAULT_BURST_SLEEP_SECONDS = 1.0
DEFAULT_STARRED_ELO_FLOOR = 1400.0
# Sorts above every real date: "older than here" starts at the newest photo.
NEWEST = ("9999", 0)
# Photos looked at per burst to find `limit` still needing a preview. Most are
# already done, so the scan reads further than it fills.
_SCAN_PAGE = 40

RequestFn = Callable[..., Awaitable[tuple[int, dict[str, str], bytes]]]

_lock = threading.Lock()
_last_request_at = 0.0  # monotonic; 0 means "never" → idle


def mirror_max_bytes() -> int:
    raw = os.environ.get("AZIMUTH_MIRROR_MAX_BYTES", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return DEFAULT_MIRROR_MAX_BYTES


def preview_version_for_image(image: Any) -> str:
    """Stable version key for a mirrored hub (or local) preview row."""

    if image is None:
        return "pv:missing"
    try:
        content_hash = str(image["content_hash"] or "").strip().lower()
    except (KeyError, TypeError, IndexError):
        content_hash = str(getattr(image, "content_hash", "") or "").strip().lower()
    if len(content_hash) >= 8:
        return f"pv:{content_hash}"
    try:
        hub_id = int(image["hub_image_id"] or 0)
    except (KeyError, TypeError, IndexError, ValueError):
        hub_id = int(getattr(image, "hub_image_id", 0) or 0)
    if hub_id > 0:
        return f"pv:hub:{hub_id}"
    try:
        image_id = int(image["id"] or 0)
    except (KeyError, TypeError, IndexError, ValueError):
        image_id = int(getattr(image, "id", 0) or 0)
    return f"pv:id:{image_id}"


def note_request() -> None:
    """Mark a user-facing preview request for the idle gate."""

    global _last_request_at
    with _lock:
        _last_request_at = time.monotonic()


def last_request_at() -> float:
    with _lock:
        return _last_request_at


def seconds_since_request() -> float:
    stamp = last_request_at()
    if stamp <= 0:
        return float("inf")
    return max(0.0, time.monotonic() - stamp)


def is_idle(*, idle_seconds: float = DEFAULT_IDLE_GATE_SECONDS) -> bool:
    return seconds_since_request() >= float(idle_seconds)


def _cache_root() -> str:
    import thumbnails

    return str(thumbnails.SSD_CACHE_DIR or "")


def _ensure_write_budget(size: str) -> None:
    """sm/md must accept writes; mirror LRU is the real byte gate."""

    import thumbnails

    thumbnails._ensure_disk_allocations()
    cap = mirror_max_bytes()
    current = int(thumbnails._disk_allocations.get(size, 0) or 0)
    if current < cap:
        thumbnails._disk_allocations[size] = cap


def get_local(
    image_id: int,
    size: str,
    preview_version: str,
    *,
    touch: bool = True,
) -> tuple[str, str] | None:
    """Return ``(signature, path)`` on hit; delete stale version on mismatch."""

    if size not in MIRROR_SIZES or not preview_version:
        return None
    import thumbnails

    entry = thumbnails.fast_disk_path_entry(size, int(image_id), None)
    if entry is None:
        return None
    signature, path = entry
    if signature != preview_version:
        delete_entry(int(image_id), size)
        return None
    if touch:
        # LRU bookkeeping, never a reason to make someone wait for a photo:
        # this takes the cache metadata lock, which background writers hold, and
        # tiles were measured waiting 33s to record a timestamp nobody reads
        # back. Hand it to a thread and return the pixels now.
        threading.Thread(
            target=_touch_quietly,
            args=(size, int(image_id), preview_version),
            name="mirror-touch",
            daemon=True,
        ).start()
    return signature, path


def _touch_quietly(size: str, image_id: int, preview_version: str) -> None:
    try:
        import thumbnails

        thumbnails.touch_cached_signature(size, image_id, preview_version)
    except Exception:
        pass


def read_local(
    image_id: int,
    size: str,
    preview_version: str,
    *,
    touch: bool = True,
) -> tuple[str, bytes] | None:
    hit = get_local(image_id, size, preview_version, touch=touch)
    if hit is None:
        return None
    signature, path = hit
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        delete_entry(int(image_id), size)
        return None
    if not data:
        delete_entry(int(image_id), size)
        return None
    return signature, data


def delete_entry(image_id: int, size: str) -> None:
    """Remove one mirrored preview from disk + cache_entries (lazy invalidation)."""

    import thumbnails
    from thumbnails import cache_entries

    cache_root = _cache_root()
    if not cache_root:
        return
    path = thumbnails._thumbnail_disk_path(size, int(image_id))
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass
    try:
        cache_entries._unindex_disk_entry(size, int(image_id))
    except Exception:
        pass
    try:
        with cache_entries._p().meta_lock:
            conn = cache_entries._db_connect()
            try:
                conn.execute(
                    "DELETE FROM cache_entries WHERE cache_root = ? AND size = ? AND image_id = ?",
                    (cache_root, size, int(image_id)),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:
        log.debug("preview_mirror delete failed image_id=%s size=%s: %s", image_id, size, exc)


def store(image_id: int, tier: str, signature: str, data: bytes, *, hot: bool) -> bool:
    """Put a preview the hub sent into the one cache, by the one rule.

    A versioned preview at a mirrored size belongs to the mirror, which owns the
    byte cap; anything else is an ordinary tile. Both callers already decided
    this, one by asking the tier and one by asking the signature, and the two
    tests disagreed for a versioned preview at a size the mirror does not carry.

    `hot` means a person is waiting: warm RAM as well. A bulk prefetch does not,
    because warming five hundred tiles evicts the working set the user is on.
    """

    import thumbnails

    if tier in MIRROR_SIZES and str(signature).startswith("pv:"):
        return put(image_id, tier, signature, data, hot=hot)
    thumbnails._write_thumbnail_to_disk(tier, int(image_id), signature, data, hot=False)
    if hot and tier != thumbnails.FULL_TIER:
        thumbnails._memory_put(tier, int(image_id), signature, data)
    return True


def put(image_id: int, size: str, preview_version: str, data: bytes, *, hot: bool = True) -> bool:
    """Write one preview into the shared thumb cache and enforce the mirror cap."""

    if size not in MIRROR_SIZES or not data or not preview_version:
        return False
    import thumbnails

    _ensure_write_budget(size)
    ok = thumbnails._write_thumbnail_to_disk(
        size, int(image_id), preview_version, data, hot=hot
    )
    if not ok:
        # Fallback: force a direct store if the write-behind path refused (budget race).
        _ensure_write_budget(size)
        ok = thumbnails._write_thumbnail_to_disk(
            size, int(image_id), preview_version, data, hot=hot
        )
    if ok:
        try:
            thumbnails._memory_put(size, int(image_id), preview_version, data)
        except Exception:
            pass
        enforce_byte_cap()
    return bool(ok)


# How much of the mirror is on disk is a number to show, never a number to
# wait for: computing it flushes the write queue and sums every cache row under
# the metadata lock, which on a 121k-row satellite froze the whole app each time
# the shell polled sync status. Requests read this cache; a refresh runs behind.
_MIRROR_BYTES_TTL_SECONDS = 30.0
_mirror_bytes_cache: dict[str, float | int | bool] = {"value": 0, "at": 0.0, "refreshing": False}
_mirror_bytes_lock = threading.Lock()


def mirror_bytes_used_cached() -> int:
    """Last known mirror size, refreshed off the request path."""
    now = time.monotonic()
    with _mirror_bytes_lock:
        value = int(_mirror_bytes_cache["value"] or 0)
        fresh = (now - float(_mirror_bytes_cache["at"] or 0.0)) < _MIRROR_BYTES_TTL_SECONDS
        already = bool(_mirror_bytes_cache["refreshing"])
        if fresh or already:
            return value
        _mirror_bytes_cache["refreshing"] = True

    def _refresh() -> None:
        try:
            measured = mirror_bytes_used()
        except Exception:
            measured = None
        with _mirror_bytes_lock:
            if measured is not None:
                _mirror_bytes_cache["value"] = measured
                _mirror_bytes_cache["at"] = time.monotonic()
            _mirror_bytes_cache["refreshing"] = False

    threading.Thread(target=_refresh, name="mirror-bytes-refresh", daemon=True).start()
    return value


def mirror_bytes_used() -> int:
    """Exact mirror size. Blocking — background callers only."""
    cache_root = _cache_root()
    if not cache_root:
        return 0
    import thumbnails
    from thumbnails import cache_entries

    try:
        thumbnails._flush_write_queue()
    except Exception:
        pass
    try:
        with cache_entries._p().meta_lock:
            conn = cache_entries._db_connect()
            try:
                placeholders = ",".join("?" for _ in MIRROR_SIZES)
                row = conn.execute(
                    f"SELECT COALESCE(SUM(size_bytes), 0) AS bytes FROM cache_entries "
                    f"WHERE cache_root = ? AND size IN ({placeholders})",
                    (cache_root, *MIRROR_SIZES),
                ).fetchone()
                return int(row["bytes"] or 0)
            finally:
                conn.close()
    except Exception:
        return 0


def enforce_byte_cap(*, max_bytes: int | None = None) -> int:
    """Evict oldest-accessed sm/md entries until under the mirror byte cap.

    Returns bytes reclaimed.
    """

    cap = mirror_max_bytes() if max_bytes is None else max(0, int(max_bytes))
    cache_root = _cache_root()
    if not cache_root or cap <= 0:
        return 0
    import thumbnails
    from thumbnails import cache_entries

    try:
        thumbnails._flush_write_queue()
    except Exception:
        pass

    reclaimed = 0
    try:
        with cache_entries._p().meta_lock:
            conn = cache_entries._db_connect()
            try:
                placeholders = ",".join("?" for _ in MIRROR_SIZES)
                total_row = conn.execute(
                    f"SELECT COALESCE(SUM(size_bytes), 0) AS bytes FROM cache_entries "
                    f"WHERE cache_root = ? AND size IN ({placeholders})",
                    (cache_root, *MIRROR_SIZES),
                ).fetchone()
                total = int(total_row["bytes"] or 0)
                if total <= cap:
                    return 0
                rows = conn.execute(
                    f"SELECT cache_root, size, image_id, path, size_bytes FROM cache_entries "
                    f"WHERE cache_root = ? AND size IN ({placeholders}) "
                    f"ORDER BY last_accessed ASC, image_id ASC",
                    (cache_root, *MIRROR_SIZES),
                ).fetchall()
                for row in rows:
                    if total <= cap:
                        break
                    size_bytes = int(row["size_bytes"] or 0)
                    cache_entries._remove_cache_entry_locked(conn, row)
                    total -= size_bytes
                    reclaimed += size_bytes
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:
        log.debug("preview_mirror enforce_byte_cap failed: %s", exc)
        return reclaimed
    return reclaimed


async def fetch_and_store(
    image: Any,
    size: str,
    *,
    hub: str | None = None,
    request: RequestFn | None = None,
    timeout: float = 10.0,
) -> tuple[str, bytes] | None:
    """Single hub fetch: return bytes and write them into the mirror (tee)."""

    if size not in MIRROR_SIZES:
        return None
    remote_id = int(image["hub_image_id"] or 0)
    if remote_id <= 0:
        return None
    base = (hub or satellite.hub_url() or "").rstrip("/")
    if not base:
        return None
    version = preview_version_for_image(image)
    if request is None:
        request = transport.request_async

    status_code, _headers, data = await request(
        "GET",
        f"{base}/api/thumb/{size}/{remote_id}",
        headers=satellite.hub_request_headers(),
    )
    if not 200 <= int(status_code) < 300 or not data:
        return None
    image_id = int(image["id"])
    put(image_id, size, version, data, hot=True)
    return version, data




async def next_mirror_targets(
    db_path: str,
    *,
    limit: int,
    after: tuple[str, int] = NEWEST,
    sizes: tuple[str, ...] = MIRROR_SIZES,
) -> tuple[list[tuple[int, str, str, int]], tuple[str, int]]:
    """The next photos still missing a mirrored preview, newest first.

    This used to be two passes: list the newest 8,000 ids, then filter them for
    ones still needing a preview. Once those 8,000 were done the filter returned
    nothing, every time, while the rest of the library stayed blank — the
    owner's laptop sat at 87,139 of 142,121 and did not move. A window that only
    ever looks where the work is already finished is not a window.

    Asking for "the next ones that need a preview" needs no window at all. The
    disk budget is the real limit and it is checked when writing.
    """

    import thumbnails

    conn = await connection.open_async(db_path)
    needed: list[tuple[int, str, str, int]] = []
    cursor = after
    try:
        # Ordered to match idx_images_missing_date_taken_id so this reads the
        # index rather than sorting the library, and cursored so each burst
        # carries on from the last instead of re-reading the newest page.
        rows = await (
            await conn.execute(
                "SELECT id, hub_image_id, content_hash, flag, elo, "
                "COALESCE(date_taken, '') AS taken FROM images "
                "WHERE missing_at IS NULL AND hub_remote = 1 "
                "AND status IN ('kept', 'maybe') AND hub_image_id IS NOT NULL "
                "AND (COALESCE(date_taken, '') < ? "
                "     OR (COALESCE(date_taken, '') = ? AND id < ?)) "
                "ORDER BY COALESCE(date_taken, '') DESC, id DESC "
                "LIMIT ?",
                (after[0], after[0], after[1], max(1, int(limit)) * _SCAN_PAGE),
            )
        ).fetchall()
        for row in rows:
            cursor = (str(row["taken"]), int(row["id"]))
            hub_id = int(row["hub_image_id"] or 0)
            if hub_id <= 0:
                continue
            version = preview_version_for_image(row)
            for size in sizes:
                if thumbnails.fast_disk_has(size, int(row["id"]), version):
                    continue
                needed.append((int(row["id"]), size, version, hub_id))
            if len(needed) >= limit:
                break
    finally:
        await connection.close_async(conn, db_path=db_path)
    return needed, cursor


class PreviewMirrorFiller:
    """Burst-only sm+md mirror fill while the satellite is idle."""

    def __init__(
        self,
        *,
        db_path: str,
        hub: str | None = None,
        request: RequestFn | None = None,
        idle_seconds: float = DEFAULT_IDLE_GATE_SECONDS,
        burst_limit: int = DEFAULT_BURST_LIMIT,
        burst_sleep_seconds: float = DEFAULT_BURST_SLEEP_SECONDS,
        elo_floor: float = DEFAULT_STARRED_ELO_FLOOR,
    ):
        self.db_path = db_path
        self.hub = (hub or satellite.hub_url() or "").rstrip("/")
        self._request = request
        self.idle_seconds = float(idle_seconds)
        self.burst_limit = max(1, int(burst_limit))
        self.burst_sleep_seconds = max(0.0, float(burst_sleep_seconds))
        self.elo_floor = float(elo_floor)
        self._cursor: tuple[str, int] = NEWEST
        self._status: dict[str, Any] = {
            "state": "idle",
            "filled": 0,
            "refused_busy": 0,
            "last_error": "",
        }

    def status(self) -> dict[str, Any]:
        return {
            **self._status,
            "idle": is_idle(idle_seconds=self.idle_seconds),
            "seconds_since_request": (
                None if last_request_at() <= 0 else round(seconds_since_request(), 2)
            ),
            "mirror_bytes": mirror_bytes_used_cached(),
            "mirror_max_bytes": mirror_max_bytes(),
        }

    def refuse_if_busy(self) -> bool:
        """True when a burst must not run (recent user request)."""

        if is_idle(idle_seconds=self.idle_seconds):
            return False
        self._status["state"] = "refused_busy"
        self._status["refused_busy"] = int(self._status.get("refused_busy") or 0) + 1
        return True

    async def burst_once(self) -> dict[str, Any]:
        """Fill up to ``burst_limit`` missing sm/md cells, or refuse while busy."""

        if self.refuse_if_busy():
            return self.status()
        if not self.hub:
            self._status.update(state="idle", last_error="no hub")
            return self.status()

        self._status["state"] = "burst"
        try:
            targets, cursor = await next_mirror_targets(
                self.db_path, limit=self.burst_limit, after=self._cursor
            )
            self._cursor = NEWEST if not targets else cursor
            filled = 0
            for image_id, size, version, hub_id in targets[: self.burst_limit]:
                if self.refuse_if_busy():
                    break
                request = self._request or transport.request_async
                code, _headers, body = await request(
                    "GET",
                    f"{self.hub}/api/thumb/{size}/{hub_id}",
                    headers=satellite.hub_request_headers(),
                )
                if 200 <= int(code) < 300 and body:
                    if put(image_id, size, version, body, hot=False):
                        filled += 1
            self._status["filled"] = int(self._status.get("filled") or 0) + filled
            self._status["state"] = "idle"
            self._status["last_error"] = ""
            if filled and self.burst_sleep_seconds > 0:
                import asyncio

                await asyncio.sleep(self.burst_sleep_seconds)
        except Exception as exc:
            self._status["state"] = "idle"
            self._status["last_error"] = str(exc)
        return self.status()
