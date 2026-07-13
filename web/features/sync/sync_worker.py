"""A small, restart-safe satellite sync worker for the frozen hub contract."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import settings
from data import connection
from features.sync.mirror import MirrorPuller
from features.sync.prefetch import ThumbPrefetcher
from features.sync import oplog, satellite


log = logging.getLogger(__name__)
CHUNK_BYTES = 32 * 1024 * 1024
RequestFn = Callable[..., Awaitable[tuple[int, dict, bytes]]]


async def _urllib_request(method: str, url: str, *, body: bytes | None = None, headers: dict | None = None) -> tuple[int, dict, bytes]:
    def request() -> tuple[int, dict, bytes]:
        request_headers = dict(headers or {})
        request_headers.update(satellite.device_auth_headers())
        req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers or {}), error.read()
    return await asyncio.to_thread(request)


class SyncWorker:
    def __init__(self, *, db_path: str, hub: str | None = None, request: RequestFn | None = None):
        self.db_path = db_path
        self.hub = (hub or satellite.hub_url()).rstrip("/")
        self._request = request or _urllib_request
        self.mirror = MirrorPuller(db_path=db_path, hub=self.hub, request=self._request)
        self.prefetch = ThumbPrefetcher(db_path=db_path, hub=self.hub, request=self._request)
        self._force_mirror_refresh = False
        self._paused = False
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._status: dict[str, Any] = {
            "mode": "satellite",
            "paused": False,
            "queue_depth": 0,
            "bytes_remaining": 0,
            "throughput_bps": 0,
            "current_file": None,
            "recent_errors": [],
            "last_sync_at": None,
        }

    def status(self) -> dict:
        return {
            **self._status,
            "paused": self._paused,
            "mirror": self.mirror.status(),
            "prefetch": self.prefetch.status(),
        }

    def pause(self) -> None:
        self._paused = True
        self._wake.set()

    def resume(self) -> None:
        self._paused = False
        self._wake.set()

    def sync_now(self) -> None:
        self._force_mirror_refresh = True
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            if not self._paused:
                try:
                    await self.sync_once()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._error(error)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=15.0 if not self._paused else 3600.0)
            except asyncio.TimeoutError:
                pass

    async def sync_once(self) -> None:
        if not self.hub:
            raise RuntimeError("PHOTOARCHIVE_HUB_URL is required for satellite sync")
        items = await satellite.record_local_images(self.db_path)
        self._refresh_queue(items)
        pushed = False
        if items and not self._paused:
            manifest = {
                "items": [
                    {key: item[key] for key in ("content_hash", "full_hash", "bytes", "filename", "date_taken") if item.get(key) is not None}
                    for item in items
                ]
            }
            response = await self._json("POST", "/api/sync/manifest", manifest)
            missing = set(response.get("missing") or [])
            known = {item.get("content_hash") for item in (response.get("known") or [])}
            by_hash = {item["content_hash"]: item for item in items}
            for content_hash in missing:
                if self._paused:
                    return
                item = by_hash.get(content_hash)
                if item is not None:
                    await self._upload(item)
                    pushed = True
            if known:
                await self._set_uploaded(known)
            pushed = await self._push_dirty_metadata() or pushed
        oplog_result = await self._exchange_oplog()
        pushed = bool(oplog_result["pushed"]) or pushed
        await self._refresh_mirror(force=pushed or self._force_mirror_refresh)
        self._force_mirror_refresh = False
        if not self._paused:
            await self._run_prefetch()
        self._status["last_sync_at"] = time.time()
        self._status["current_file"] = None
        self._refresh_queue(await satellite.record_local_images(self.db_path))

    async def _upload(self, item: dict) -> None:
        content_hash = item["content_hash"]
        self._status["current_file"] = item["filename"]
        status = await self._json("GET", f"/api/sync/upload/{content_hash}/status")
        offset = max(0, min(int(status.get("offset") or 0), item["bytes"]))
        with open(item["filepath"], "rb") as source:
            source.seek(offset)
            while offset < item["bytes"]:
                if self._paused:
                    return
                chunk = source.read(min(CHUNK_BYTES, item["bytes"] - offset))
                if not chunk:
                    raise RuntimeError(f"Original changed during sync: {item['filename']}")
                started = time.monotonic()
                status_code, _headers, body = await self._request(
                    "POST",
                    self.hub + f"/api/sync/upload/{content_hash}",
                    body=chunk,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "X-Offset": str(offset),
                        "X-Total-Bytes": str(item["bytes"]),
                        "X-Filename": item["filename"],
                    },
                )
                if not 200 <= status_code < 300:
                    raise RuntimeError(f"upload {item['filename']} failed ({status_code}): {body.decode(errors='replace')[:300]}")
                elapsed = max(time.monotonic() - started, 0.001)
                self._status["throughput_bps"] = round(len(chunk) / elapsed)
                offset += len(chunk)
                self._status["bytes_remaining"] = max(0, int(self._status["bytes_remaining"]) - len(chunk))
                await self._cap_bandwidth(len(chunk), elapsed)
        status_code, _headers, body = await self._request(
            "POST",
            self.hub + f"/api/sync/upload/{content_hash}",
            body=b"",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Offset": str(offset),
                "X-Total-Bytes": str(item["bytes"]),
            },
        )
        if not 200 <= status_code < 300:
            raise RuntimeError(
                f"upload finalize {item['filename']} failed ({status_code}): "
                f"{body.decode(errors='replace')[:300]}"
            )
        await self._set_uploaded({content_hash})

    async def _cap_bandwidth(self, sent_bytes: int, elapsed: float) -> None:
        mbps = float(settings.get_settings().get("sync_bandwidth_mbps", 0) or 0)
        if mbps <= 0:
            return
        target_seconds = sent_bytes / (mbps * 1024 * 1024 / 8)
        if target_seconds > elapsed:
            await asyncio.sleep(target_seconds - elapsed)

    async def _push_dirty_metadata(self) -> bool:
        rows = await self._dirty_metadata_rows()
        if not rows:
            return False
        snapshot = time.time()
        response = await self._json("POST", "/api/sync/metadata", {"items": [row["item"] for row in rows]})
        if response is None:
            return False
        await self._mark_pushed([row["content_hash"] for row in rows], snapshot)
        return True

    async def _refresh_mirror(self, *, force: bool) -> None:
        last_refresh = self.mirror.status().get("last_refresh_at") or 0
        if not force and time.time() - float(last_refresh) < 600:
            return
        try:
            await self.mirror.refresh()
        except Exception as error:
            # A v1 hub remains usable for field uploads while its v2 catalog route rolls out.
            self.mirror._status["last_error"] = str(error)

    async def _exchange_oplog(self) -> dict[str, int]:
        try:
            return await oplog.exchange_with_hub(self.db_path, self._json)
        except RuntimeError as error:
            if "failed (404)" not in str(error):
                raise
            # A v1 hub remains usable while its additive oplog routes roll out.
            return {"pushed": 0, "pulled": 0}

    async def _run_prefetch(self) -> None:
        try:
            await self.prefetch.prefetch_once(size="sm")
            await self.prefetch.prefetch_once(size="md")
            await self.prefetch.seed_predictive()
            await self.prefetch.run_predictive_once(uploads_active=bool(self._status.get("current_file")))
        except Exception as error:
            self.prefetch._status["last_error"] = str(error)

    async def _dirty_metadata_rows(self) -> list[dict]:
        conn = await connection.open_async(self.db_path)
        try:
            cursor = await conn.execute(
                """
                SELECT s.content_hash, s.image_id, s.last_local_change_at, i.flag, d.settings AS develop_settings,
                       d.updated_at AS develop_updated_at
                FROM sync_state s
                JOIN images i ON i.id = s.image_id
                LEFT JOIN develop_settings d ON d.image_id = i.id
                WHERE s.last_local_change_at > COALESCE(s.last_pushed_at, 0)
                """
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            await self._ensure_keyword_schema(conn)
            for row in rows:
                item = {"content_hash": row["content_hash"], "flag": row.get("flag")}
                item["flag_updated_at"] = self._iso_timestamp(row.get("last_local_change_at"))
                if row.get("develop_settings"):
                    item["develop_settings"] = json.loads(row["develop_settings"])
                    item["develop_updated_at"] = row.get("develop_updated_at")
                keywords = await self._keyword_paths(conn, row["image_id"])
                if keywords:
                    item["keywords"] = keywords
                iptc = await self._iptc(conn, row["image_id"])
                if iptc:
                    item["iptc"] = iptc
                row["item"] = item
            return rows
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def _ensure_keyword_schema(self, conn) -> None:
        from features.library.keywords import KEYWORD_DDL
        await conn.executescript(KEYWORD_DDL)

    async def _keyword_paths(self, conn, image_id: int) -> list[str]:
        cursor = await conn.execute(
            """
            WITH RECURSIVE tree(id, parent_id, path) AS (
                SELECT id, parent_id, name FROM keywords WHERE parent_id IS NULL
                UNION ALL
                SELECT child.id, child.parent_id, tree.path || ' > ' || child.name
                FROM keywords child JOIN tree ON child.parent_id = tree.id
            )
            SELECT tree.path FROM image_keywords JOIN tree ON tree.id = image_keywords.keyword_id
            WHERE image_keywords.image_id = ? ORDER BY tree.path COLLATE NOCASE
            """,
            (image_id,),
        )
        return [str(row["path"]) for row in await cursor.fetchall()]

    async def _iptc(self, conn, image_id: int) -> dict:
        cursor = await conn.execute(
            "SELECT title, caption, copyright, creator, updated_at FROM iptc_fields WHERE image_id = ?", (image_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else {}

    @staticmethod
    def _iso_timestamp(value: Any) -> str:
        return datetime.fromtimestamp(float(value or time.time()), timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    async def _json(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        status_code, _headers, response = await self._request(method, self.hub + path, body=body, headers=headers)
        if not 200 <= status_code < 300:
            raise RuntimeError(f"sync {method} {path} failed ({status_code}): {response.decode(errors='replace')[:300]}")
        return json.loads(response or b"{}")

    async def _set_uploaded(self, content_hashes: set[str]) -> None:
        if not content_hashes:
            return
        conn = await connection.open_async(self.db_path)
        try:
            placeholders = ",".join("?" for _ in content_hashes)
            await conn.execute(f"UPDATE sync_state SET uploaded = 1 WHERE content_hash IN ({placeholders})", tuple(content_hashes))
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def _mark_pushed(self, content_hashes: list[str], snapshot: float) -> None:
        if not content_hashes:
            return
        conn = await connection.open_async(self.db_path)
        try:
            placeholders = ",".join("?" for _ in content_hashes)
            await conn.execute(
                f"UPDATE sync_state SET last_pushed_at = ? WHERE content_hash IN ({placeholders}) "
                "AND last_local_change_at <= ?",
                (snapshot, *content_hashes, snapshot),
            )
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    def _refresh_queue(self, items: list[dict]) -> None:
        pending = [item for item in items if not int(item.get("uploaded") or 0)]
        self._status["queue_depth"] = len(pending)
        self._status["bytes_remaining"] = sum(int(item.get("bytes") or 0) for item in pending)

    def _error(self, error: Exception) -> None:
        message = str(error)
        log.warning("satellite sync failed: %s", message)
        friendly = "Can't reach Azimuth Photo. Retrying…" if isinstance(error, (ConnectionError, OSError)) else message
        errors = [friendly, *self._status["recent_errors"]]
        self._status["recent_errors"] = errors[:5]


_worker: SyncWorker | None = None


def configure_worker(worker: SyncWorker | None) -> None:
    global _worker
    _worker = worker


def get_worker() -> SyncWorker | None:
    return _worker
