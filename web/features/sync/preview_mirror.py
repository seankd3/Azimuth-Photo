"""Satellite local preview mirror — sm/md from SSD, hub only on miss.

Extends the existing thumb-cache layout under cache_root (same paths and
``cache_entries`` rows). ``source_signature`` holds the preview version so a
catalog change is a lazy miss, not a push invalidation.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from data import connection
from features.sync import satellite


log = logging.getLogger(__name__)

MIRROR_SIZES = ("sm", "md")
# Match the satellite thumb budget: full sm+md with headroom (~40GB).
DEFAULT_MIRROR_MAX_BYTES = 40 * 1024**3
DEFAULT_IDLE_GATE_SECONDS = 10.0
DEFAULT_BURST_LIMIT = 48
DEFAULT_BURST_SLEEP_SECONDS = 1.0
DEFAULT_RECENT_LIMIT = 8000
DEFAULT_STARRED_ELO_FLOOR = 1400.0

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


def _db_path() -> str:
    import db

    return str(db.DB_PATH)


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
        try:
            thumbnails.touch_cached_signature(size, int(image_id), preview_version)
        except Exception:
            pass
    return signature, path


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


def mirror_bytes_used() -> int:
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
        from features.sync.prefetch import _urllib_request as request  # type: ignore[assignment]

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


def candidate_query_sql(*, recent_limit: int, elo_floor: float) -> str:
    """SQL selecting hub-remote ids for background fill (recent ∪ starred/high-Elo)."""

    return f"""
    SELECT id FROM (
        SELECT id FROM images
        WHERE hub_remote = 1 AND status IN ('kept', 'maybe')
        ORDER BY date_taken DESC NULLS LAST, id DESC
        LIMIT {int(recent_limit)}
    )
    UNION
    SELECT id FROM images
    WHERE hub_remote = 1 AND status IN ('kept', 'maybe')
      AND (
        flag = 'picked'
        OR COALESCE(elo, 0) >= {float(elo_floor)}
      )
    """


async def list_fill_candidates(
    db_path: str,
    *,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
    elo_floor: float = DEFAULT_STARRED_ELO_FLOOR,
) -> list[int]:
    conn = await connection.open_async(db_path)
    try:
        try:
            rows = await (
                await conn.execute(
                    candidate_query_sql(recent_limit=recent_limit, elo_floor=elo_floor)
                )
            ).fetchall()
        except sqlite3.OperationalError:
            # Older SQLite without NULLS LAST — fall back.
            rows = await (
                await conn.execute(
                    """
                    SELECT id FROM (
                        SELECT id FROM images
                        WHERE hub_remote = 1 AND status IN ('kept', 'maybe')
                        ORDER BY date_taken DESC, id DESC
                        LIMIT ?
                    )
                    UNION
                    SELECT id FROM images
                    WHERE hub_remote = 1 AND status IN ('kept', 'maybe')
                      AND (
                        flag = 'picked'
                        OR COALESCE(elo, 0) >= ?
                      )
                    """,
                    (int(recent_limit), float(elo_floor)),
                )
            ).fetchall()
        return [int(row["id"]) for row in rows]
    finally:
        await connection.close_async(conn, db_path=db_path)


async def missing_mirror_targets(
    db_path: str,
    image_ids: list[int],
    *,
    sizes: tuple[str, ...] = MIRROR_SIZES,
) -> list[tuple[int, str, str, int]]:
    """Return ``(local_id, size, preview_version, hub_image_id)`` still needed."""

    if not image_ids:
        return []
    import thumbnails

    conn = await connection.open_async(db_path)
    needed: list[tuple[int, str, str, int]] = []
    try:
        placeholders = ",".join("?" for _ in image_ids)
        rows = await (
            await conn.execute(
                f"SELECT id, hub_image_id, content_hash, flag, elo FROM images "
                f"WHERE id IN ({placeholders}) AND hub_remote = 1",
                tuple(image_ids),
            )
        ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        for image_id in image_ids:
            row = by_id.get(int(image_id))
            if row is None:
                continue
            version = preview_version_for_image(row)
            hub_id = int(row["hub_image_id"] or 0)
            if hub_id <= 0:
                continue
            for size in sizes:
                if thumbnails.fast_disk_has(size, int(image_id), version):
                    continue
                # Stale different version counts as missing (lazy delete on serve).
                needed.append((int(image_id), size, version, hub_id))
    finally:
        await connection.close_async(conn, db_path=db_path)
    return needed


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
        recent_limit: int = DEFAULT_RECENT_LIMIT,
        elo_floor: float = DEFAULT_STARRED_ELO_FLOOR,
    ):
        self.db_path = db_path
        self.hub = (hub or satellite.hub_url() or "").rstrip("/")
        self._request = request
        self.idle_seconds = float(idle_seconds)
        self.burst_limit = max(1, int(burst_limit))
        self.burst_sleep_seconds = max(0.0, float(burst_sleep_seconds))
        self.recent_limit = max(1, int(recent_limit))
        self.elo_floor = float(elo_floor)
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
            "mirror_bytes": mirror_bytes_used(),
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
            candidates = await list_fill_candidates(
                self.db_path,
                recent_limit=self.recent_limit,
                elo_floor=self.elo_floor,
            )
            targets = await missing_mirror_targets(self.db_path, candidates)
            filled = 0
            for image_id, size, version, hub_id in targets[: self.burst_limit]:
                if self.refuse_if_busy():
                    break
                if self._request is None:
                    from features.sync.prefetch import _urllib_request

                    request = _urllib_request
                else:
                    request = self._request
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
