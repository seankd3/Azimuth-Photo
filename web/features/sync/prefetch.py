"""Satellite thumbnail and predictive prefetch for mirrored hub photos."""

from __future__ import annotations

import asyncio
import pathlib
import hashlib
import io
import json
import os
import tarfile
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from data import connection
from features.sync import satellite
from features.sync.executor import run_foreground_sync_work, run_sync_work


RequestFn = Callable[..., Awaitable[tuple[int, dict[str, str], bytes]]]
StoreFn = Callable[[str, int, str, bytes], Any]
# 0 = auto. Satellite: clamp(full sm+md need + 20% headroom, floor 8GB,
# cap 25% of TOTAL cache-drive capacity). Hub/legacy free-disk clamp retained
# only when not in satellite mode.
DEFAULT_THUMB_BUDGET_GB = 0
AUTO_BUDGET_FLOOR_GB = 8
AUTO_BUDGET_CEILING_GB = 64
AUTO_BUDGET_FREE_FRACTION = 0.25
AUTO_BUDGET_CAPACITY_FRACTION = 0.25
AUTO_BUDGET_HEADROOM = 1.20
# Fallback per-image JPEG sizes when the local cache has no samples yet.
# Tuned so ~150k images ≈ 32GB for a full sm+md set.
_ESTIMATE_SM_BYTES = 64 * 1024
_ESTIMATE_MD_BYTES = 160 * 1024


def _disk_usage(cache_root: str | None):
    import shutil as _shutil

    probe = cache_root or str(pathlib.Path.home())
    return _shutil.disk_usage(probe)


def estimate_sm_md_set_bytes(
    *,
    image_count: int,
    avg_sm_bytes: int | None = None,
    avg_md_bytes: int | None = None,
) -> int:
    """Bytes for a full local sm+md mirror of ``image_count`` photos."""

    count = max(0, int(image_count))
    sm = int(avg_sm_bytes) if avg_sm_bytes and avg_sm_bytes > 0 else _ESTIMATE_SM_BYTES
    md = int(avg_md_bytes) if avg_md_bytes and avg_md_bytes > 0 else _ESTIMATE_MD_BYTES
    return count * (sm + md)


def auto_thumb_budget_bytes(
    cache_root: str | None,
    *,
    needed_bytes: int | None = None,
    image_count: int | None = None,
) -> int:
    """Derive the satellite thumb mirror budget from disk + library size.

    Formula (satellite): clamp(needed * 1.20, floor 8GB, cap 25% of TOTAL disk).
    Non-satellite keeps the legacy free-disk fraction clamp for safety.
    """

    floor = AUTO_BUDGET_FLOOR_GB * 1024 ** 3
    try:
        usage = _disk_usage(cache_root)
    except OSError:
        return floor

    if not satellite.is_satellite_mode():
        auto = int(usage.free * AUTO_BUDGET_FREE_FRACTION)
        return min(AUTO_BUDGET_CEILING_GB * 1024 ** 3, max(floor, auto))

    if needed_bytes is None:
        needed_bytes = estimate_sm_md_set_bytes(image_count=int(image_count or 0))
    target = int(max(0, int(needed_bytes)) * AUTO_BUDGET_HEADROOM)
    # Cap = 25% of TOTAL capacity of the cache drive (not free space).
    capacity_cap = int(usage.total * AUTO_BUDGET_CAPACITY_FRACTION)
    if target <= 0:
        target = floor
    # clamp(target, floor, cap) — if cap < floor (tiny disk), floor still wins.
    return max(floor, min(target, capacity_cap if capacity_cap > 0 else target))
PACK_ORDER = "newest"
_PREFETCH_DDL = """
CREATE TABLE IF NOT EXISTS sync_prefetch_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


async def _request_with_runner(
    runner,
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict | None = None,
    timeout: float,
) -> tuple[int, dict[str, str], bytes]:
    def request() -> tuple[int, dict[str, str], bytes]:
        request_headers = dict(headers or {})
        request_headers.update(satellite.hub_request_headers())
        req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - configured tailnet hub.
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers or {}), error.read()

    return await runner(request)


async def _urllib_request(method: str, url: str, *, body: bytes | None = None, headers: dict | None = None) -> tuple[int, dict[str, str], bytes]:
    return await _request_with_runner(
        run_sync_work,
        method,
        url,
        body=body,
        headers=headers,
        timeout=10,
    )


async def _foreground_urllib_request(method: str, url: str, *, body: bytes | None = None, headers: dict | None = None) -> tuple[int, dict[str, str], bytes]:
    return await _request_with_runner(
        run_foreground_sync_work,
        method,
        url,
        body=body,
        headers=headers,
        timeout=2,
    )


def _store_with_thumbnail_cache(size: str, image_id: int, signature: str, data: bytes) -> None:
    if size in {"sm", "md"} and str(signature).startswith("pv:"):
        from features.sync import preview_mirror

        preview_mirror.put(image_id, size, signature, data, hot=False)
        return
    import thumbnails

    thumbnails._write_thumbnail_to_disk(size, image_id, signature, data, hot=False)


@dataclass(order=True)
class _QueuedItem:
    priority: int
    image_id: int = field(compare=False)
    kind: str = field(compare=False, default="thumb")


class ThumbPrefetcher:
    """Resumable newest-first thumb pack fetcher plus a tiny predictive queue.

    Browse tier (`sm`) always fills before loupe tier (`md`). The All Photos
    grid renders `sm`; burning the 8GB budget on `md` first leaves the grid
    cold and hammering the hub for every cell.
    """

    BROWSE_SIZE = "sm"
    LOUPE_SIZE = "md"

    def __init__(self, *, db_path: str, hub: str | None = None, request: RequestFn | None = None, store: StoreFn | None = None, cache_root: str | None = None, budget_bytes: int | None = None):
        self.db_path = db_path
        self.hub = (hub or satellite.hub_url()).rstrip("/")
        self._request = request or _urllib_request
        self._store = store or _store_with_thumbnail_cache
        self.cache_root = cache_root
        self._budget_override = budget_bytes is not None
        self.budget_bytes = (
            budget_bytes if budget_bytes is not None else self._settings_budget(cache_root)
        )
        self._queue: asyncio.PriorityQueue[_QueuedItem] = asyncio.PriorityQueue()
        self._last_predictive_seed_at = 0.0
        self._sm_gap_retry_at = 0.0
        self._status: dict[str, Any] = {
            "state": "idle",
            "size": "sm",
            "after_id": 0,
            "cached": 0,
            "total": 0,
            "library_cached": 0,
            "library_total": 0,
            "mirror_cached": 0,
            "mirror_total": 0,
            "budget_bytes": self.budget_bytes,
            "needed_bytes": 0,
            "disk_total_bytes": 0,
            "last_error": "",
            "tier": "browse",
        }

    @staticmethod
    def _settings_budget(cache_root: str | None = None) -> int:
        try:
            import settings
            gigabytes = int(settings.get_settings().get("sync_thumb_budget_gb", DEFAULT_THUMB_BUDGET_GB) or DEFAULT_THUMB_BUDGET_GB)
        except Exception:
            gigabytes = DEFAULT_THUMB_BUDGET_GB
        if gigabytes > 0:
            return gigabytes * 1024 ** 3
        return auto_thumb_budget_bytes(cache_root)

    def status(self) -> dict[str, Any]:
        return {**self._status, "queued": self._queue.qsize()}

    async def refresh_budget_status(self) -> dict[str, Any]:
        """Recompute budget + mirror completeness for the sync status payload."""

        needed, avg_sm, avg_md, image_count = await self._estimate_needed_bytes()
        if not self._budget_override:
            budget = auto_thumb_budget_bytes(
                self.cache_root,
                needed_bytes=needed,
                image_count=image_count,
            )
            self.budget_bytes = budget
        else:
            budget = self.budget_bytes
        cached, total = await self._mirror_progress()
        disk_total = 0
        try:
            disk_total = int(_disk_usage(self.cache_root).total)
        except OSError:
            disk_total = 0
        self._status.update(
            budget_bytes=budget,
            needed_bytes=needed,
            disk_total_bytes=disk_total,
            mirror_cached=cached,
            mirror_total=total,
            avg_sm_bytes=avg_sm,
            avg_md_bytes=avg_md,
        )
        return self.status()

    async def prefetch_once(self, *, size: str = "sm", limit: int = 500) -> dict[str, Any]:
        if not self.hub:
            raise RuntimeError("AZIMUTH_HUB_URL is required for thumbnail prefetch")
        if size not in {"sm", "md"}:
            raise ValueError("thumbnail prefetch supports sm or md")
        await self._ensure_state()
        await self.refresh_budget_status()
        if await self._at_budget():
            if size == self.BROWSE_SIZE:
                # Prefer reclaiming loupe thumbs over leaving the grid cold.
                reclaimed = await self._reclaim_loupe_bytes(target_bytes=64 * 1024 * 1024)
                if reclaimed <= 0 or await self._at_budget():
                    cached, total = await self._library_progress(size)
                    self._status.update(library_cached=cached, library_total=total)
                    self._status.update(state="budget", size=size, tier="browse")
                    return self.status()
            else:
                cached, total = await self._library_progress(size)
                self._status.update(library_cached=cached, library_total=total)
                self._status.update(state="budget", size=size, tier="loupe")
                return self.status()
        after_id = await self._state_int(f"after:{size}")
        tier = "browse" if size == self.BROWSE_SIZE else "loupe"
        self._status.update(state="fetching", size=size, after_id=after_id, tier=tier)
        query = urlencode(
            {
                "size": size,
                "after_id": after_id,
                "limit": min(500, max(1, limit)),
                "order": PACK_ORDER,
            }
        )
        code, _headers, body = await self._request("GET", f"{self.hub}/api/sync/thumbs/pack?{query}")
        if not 200 <= code < 300:
            raise RuntimeError(f"thumb pack failed ({code}): {body.decode(errors='replace')[:300]}")
        stored, last_hub_id, skipped = await self._store_pack(size, body)
        # newest: cursor descends (trailer after_id = lowest id scanned).
        # asc fallback hubs ignore order= and ascend; accept either direction.
        if last_hub_id > 0 and last_hub_id != after_id:
            await self._set_state(f"after:{size}", str(last_hub_id))
        elif size == self.BROWSE_SIZE and stored == 0 and (last_hub_id == after_id or last_hub_id == 0):
            # Cursor reached the hub end while some sm cells are still missing
            # (hub skipped them). Periodically rewind so later hub generation
            # can fill browse gaps without waiting for a manual reset.
            cached, total = await self._library_progress(size)
            if total > 0 and cached < total and time.monotonic() - self._sm_gap_retry_at > 3600:
                self._sm_gap_retry_at = time.monotonic()
                await self._set_state(f"after:{size}", "0")
                after_id = 0
        cached, total = await self._library_progress(size)
        next_cursor = last_hub_id if last_hub_id > 0 else after_id
        self._status.update(
            state="idle",
            after_id=next_cursor,
            cached=int(self._status["cached"]) + stored,
            total=int(self._status["total"]) + stored + skipped,
            library_cached=cached,
            library_total=total,
            last_error="",
            tier=tier,
            order=PACK_ORDER,
        )
        return self.status()

    async def browse_tier_complete(self) -> bool:
        """True when every hub-remote row has a local `sm` thumb (or none exist)."""

        cached, total = await self._library_progress(self.BROWSE_SIZE)
        self._status.update(library_cached=cached, library_total=total, size=self.BROWSE_SIZE, tier="browse")
        return total <= 0 or cached >= total

    async def prefetch_browse_first(self, *, limit: int = 500) -> dict[str, Any]:
        """Fill `sm` exclusively until the browse tier is complete, then `md`."""

        if not await self.browse_tier_complete():
            return await self.prefetch_once(size=self.BROWSE_SIZE, limit=limit)
        return await self.prefetch_once(size=self.LOUPE_SIZE, limit=limit)

    async def enqueue_loupe_neighbors(self, image_id: int) -> None:
        await self._enqueue_neighbors(image_id, span=5, priority=0, kind="thumb")

    async def enqueue_picks_last_30_days(self) -> None:
        conn = await connection.open_async(self.db_path)
        try:
            rows = await (await conn.execute(
                """SELECT id FROM images WHERE hub_remote = 1 AND flag = 'picked'
                   AND date_taken >= date('now', '-30 days') ORDER BY date_taken DESC LIMIT 100"""
            )).fetchall()
            for row in rows:
                await self._queue.put(_QueuedItem(2, int(row["id"]), "thumb"))
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def seed_predictive(self) -> None:
        """Periodically seed picked photos without continually duplicating the queue."""

        if time.monotonic() - self._last_predictive_seed_at < 600:
            return
        self._last_predictive_seed_at = time.monotonic()
        await self.enqueue_picks_last_30_days()

    async def enqueue_develop_siblings(self, image_id: int) -> None:
        conn = await connection.open_async(self.db_path)
        try:
            row = await (await conn.execute("SELECT date_taken FROM images WHERE id = ?", (image_id,))).fetchone()
            if not row or not row["date_taken"]:
                return
            rows = await (await conn.execute(
                "SELECT id FROM images WHERE hub_remote = 1 AND substr(date_taken, 1, 10) = substr(?, 1, 10) AND id != ? LIMIT 100",
                (row["date_taken"], image_id),
            )).fetchall()
            for sibling in rows:
                await self._queue.put(_QueuedItem(1, int(sibling["id"]), "base"))
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def run_predictive_once(self, *, uploads_active: bool = False) -> int:
        """Fetch one queued item only when uploads leave the network idle."""

        if uploads_active or self._queue.empty():
            return 0
        item = await self._queue.get()
        try:
            if item.kind == "thumb":
                # Grid first, then loupe. Never spend predictive bandwidth on
                # md/lg while the browse tier is still incomplete.
                await self.fetch_single(self.BROWSE_SIZE, item.image_id)
                if await self.browse_tier_complete():
                    await self.fetch_single(self.LOUPE_SIZE, item.image_id)
                    await self.fetch_single("lg", item.image_id)
            else:
                await self.fetch_base(item.image_id)
            return 1
        finally:
            self._queue.task_done()

    async def fetch_single(self, size: str, image_id: int) -> bytes:
        conn = await connection.open_async(self.db_path)
        try:
            row = await (await conn.execute(
                "SELECT hub_image_id, content_hash FROM images WHERE id = ? AND hub_remote = 1",
                (image_id,),
            )).fetchone()
        finally:
            await connection.close_async(conn, db_path=self.db_path)
        if row is None:
            raise LookupError("image is not a hub-remote catalog row")
        code, _headers, body = await self._request("GET", f"{self.hub}/api/thumb/{size}/{int(row['hub_image_id'])}")
        if code == 503:
            raise ConnectionError("Hub is unreachable")
        if not 200 <= code < 300:
            raise RuntimeError(f"hub thumb failed ({code})")
        from features.sync import preview_mirror

        signature = preview_mirror.preview_version_for_image(row)
        self._store(size, image_id, signature, body)
        return body

    async def fetch_base(self, image_id: int) -> None:
        """Materialize a sibling's cached hub base without ever decoding its original locally."""

        conn = await connection.open_async(self.db_path)
        try:
            row = await (await conn.execute(
                "SELECT filepath FROM images WHERE id = ? AND hub_remote = 1", (image_id,)
            )).fetchone()
        finally:
            await connection.close_async(conn, db_path=self.db_path)
        if row is None:
            return
        from features.develop import rawproc

        await run_sync_work(rawproc.ensure_base_cache, image_id, str(row["filepath"] or ""))

    async def _enqueue_neighbors(self, image_id: int, *, span: int, priority: int, kind: str) -> None:
        conn = await connection.open_async(self.db_path)
        try:
            row = await (await conn.execute("SELECT date_taken, id FROM images WHERE id = ?", (image_id,))).fetchone()
            if row is None:
                return
            rows = await (await conn.execute(
                """SELECT id FROM images WHERE hub_remote = 1
                   ORDER BY ABS(id - ?) ASC LIMIT ?""", (int(row["id"]), span * 2 + 1)
            )).fetchall()
            for neighbor in rows:
                if int(neighbor["id"]) != image_id:
                    await self._queue.put(_QueuedItem(priority, int(neighbor["id"]), kind))
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def _store_pack(self, size: str, body: bytes) -> tuple[int, int, int]:
        stored = skipped = last_hub_id = 0
        try:
            archive = tarfile.open(fileobj=io.BytesIO(body), mode="r:")
        except tarfile.TarError as error:
            raise RuntimeError("hub returned an invalid thumbnail pack") from error

        with archive:
            for member in archive:
                if not member.isfile():
                    continue
                payload = archive.extractfile(member)
                data = payload.read() if payload else b""
                if member.name.endswith(".json"):
                    try:
                        trailer = json.loads(data)
                        skipped += len(trailer.get("skipped", []))
                        last_hub_id = max(last_hub_id, int(trailer.get("after_id") or 0))
                    except (TypeError, ValueError):
                        pass
                    continue
                stem = member.name.rsplit("/", 1)[-1].split(".", 1)[0]
                try:
                    hub_id = int(stem)
                except ValueError:
                    continue
                local = await self._local_row_for_hub(hub_id)
                last_hub_id = max(last_hub_id, hub_id)
                if local is None or not data:
                    continue
                local_id, signature = local
                self._store(size, local_id, signature, data)
                stored += 1
        return stored, last_hub_id, skipped

    async def _local_id_for_hub(self, hub_image_id: int) -> int | None:
        row = await self._local_row_for_hub(hub_image_id)
        return None if row is None else row[0]

    async def _local_row_for_hub(self, hub_image_id: int) -> tuple[int, str] | None:
        from features.sync import preview_mirror

        conn = await connection.open_async(self.db_path)
        try:
            row = await (await conn.execute(
                "SELECT id, hub_image_id, content_hash FROM images WHERE hub_image_id = ?",
                (hub_image_id,),
            )).fetchone()
            if row is None:
                return None
            return int(row["id"]), preview_mirror.preview_version_for_image(row)
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def _ensure_state(self) -> None:
        conn = await connection.open_async(self.db_path)
        try:
            await conn.executescript(_PREFETCH_DDL)
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def _state_int(self, key: str) -> int:
        conn = await connection.open_async(self.db_path)
        try:
            row = await (await conn.execute("SELECT value FROM sync_prefetch_state WHERE key = ?", (key,))).fetchone()
            return int(row["value"]) if row else 0
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def _set_state(self, key: str, value: str) -> None:
        conn = await connection.open_async(self.db_path)
        try:
            await conn.execute("INSERT INTO sync_prefetch_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def _cache_bytes(self) -> int:
        if self.cache_root is None:
            import thumbnails
            self.cache_root = thumbnails.SSD_CACHE_DIR
        conn = await connection.open_async(self.db_path)
        try:
            row = await (await conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) AS bytes FROM cache_entries WHERE cache_root = ?",
                (self.cache_root,),
            )).fetchone()
            return int(row["bytes"] or 0)
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def _at_budget(self) -> bool:
        if self.budget_bytes <= 0:
            return True
        return await self._cache_bytes() >= self.budget_bytes

    async def _reclaim_loupe_bytes(self, *, target_bytes: int) -> int:
        """Delete oldest `md` cache rows so `sm` can keep filling within budget."""

        if target_bytes <= 0:
            return 0
        if self.cache_root is None:
            import thumbnails
            self.cache_root = thumbnails.SSD_CACHE_DIR
        conn = await connection.open_async(self.db_path)
        reclaimed = 0
        try:
            rows = await (await conn.execute(
                """SELECT image_id, path, size_bytes FROM cache_entries
                   WHERE cache_root = ? AND size = ?
                   ORDER BY last_accessed ASC, image_id ASC LIMIT 200""",
                (self.cache_root, self.LOUPE_SIZE),
            )).fetchall()
            for row in rows:
                if reclaimed >= target_bytes:
                    break
                path = str(row["path"] or "")
                await conn.execute(
                    "DELETE FROM cache_entries WHERE cache_root = ? AND size = ? AND image_id = ?",
                    (self.cache_root, self.LOUPE_SIZE, int(row["image_id"])),
                )
                reclaimed += int(row["size_bytes"] or 0)
                if path:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            if reclaimed:
                await conn.commit()
        finally:
            await connection.close_async(conn, db_path=self.db_path)
        return reclaimed

    async def _library_progress(self, size: str) -> tuple[int, int]:
        if self.cache_root is None:
            import thumbnails
            self.cache_root = thumbnails.SSD_CACHE_DIR
        conn = await connection.open_async(self.db_path)
        try:
            total = await (await conn.execute("SELECT COUNT(*) AS count FROM images WHERE hub_remote = 1")).fetchone()
            cached = await (await conn.execute(
                """SELECT COUNT(*) AS count FROM cache_entries entry
                   JOIN images image ON image.id = entry.image_id
                   WHERE entry.cache_root = ? AND entry.size = ? AND image.hub_remote = 1""",
                (self.cache_root, size),
            )).fetchone()
            return int(cached["count"] or 0), int(total["count"] or 0)
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def _mirror_progress(self) -> tuple[int, int]:
        """sm+md local thumb rows vs 2× hub-remote catalog count."""

        sm_cached, total = await self._library_progress(self.BROWSE_SIZE)
        md_cached, _ = await self._library_progress(self.LOUPE_SIZE)
        return sm_cached + md_cached, total * 2

    async def _estimate_needed_bytes(self) -> tuple[int, int, int, int]:
        if self.cache_root is None:
            import thumbnails
            self.cache_root = thumbnails.SSD_CACHE_DIR
        conn = await connection.open_async(self.db_path)
        try:
            total_row = await (
                await conn.execute("SELECT COUNT(*) AS count FROM images WHERE hub_remote = 1")
            ).fetchone()
            image_count = int(total_row["count"] or 0)
            averages = {}
            for size in (self.BROWSE_SIZE, self.LOUPE_SIZE):
                row = await (
                    await conn.execute(
                        """SELECT AVG(size_bytes) AS avg_bytes FROM cache_entries
                           WHERE cache_root = ? AND size = ? AND size_bytes > 0""",
                        (self.cache_root, size),
                    )
                ).fetchone()
                averages[size] = int(row["avg_bytes"] or 0) if row and row["avg_bytes"] else 0
        finally:
            await connection.close_async(conn, db_path=self.db_path)
        avg_sm = averages.get(self.BROWSE_SIZE) or _ESTIMATE_SM_BYTES
        avg_md = averages.get(self.LOUPE_SIZE) or _ESTIMATE_MD_BYTES
        needed = estimate_sm_md_set_bytes(
            image_count=image_count, avg_sm_bytes=avg_sm, avg_md_bytes=avg_md
        )
        return needed, avg_sm, avg_md, image_count

    @staticmethod
    def _signature(hub_image_id: Any, data: bytes) -> str:
        return f"hub:{int(hub_image_id)}:{hashlib.blake2b(data, digest_size=8).hexdigest()}"
