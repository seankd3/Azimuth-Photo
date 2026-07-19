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
from features.sync import client_update, contract, oplog, preview_mirror, satellite
from features.sync.executor import run_sync_work
from features.trash import remote as trash_remote
from features.trash import service as trash_service


log = logging.getLogger(__name__)
CHUNK_BYTES = 32 * 1024 * 1024
RequestFn = Callable[..., Awaitable[tuple[int, dict, bytes]]]
_BASE_IDLE_SECONDS = 15.0
_MAX_BACKOFF_SECONDS = 300.0


async def _urllib_request(method: str, url: str, *, body: bytes | None = None, headers: dict | None = None) -> tuple[int, dict, bytes]:
    def request() -> tuple[int, dict, bytes]:
        request_headers = dict(headers or {})
        request_headers.update(satellite.hub_request_headers())
        req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers or {}), error.read()
    return await run_sync_work(request)


class SyncWorker:
    def __init__(
        self,
        *,
        db_path: str,
        hub: str | None = None,
        request: RequestFn | None = None,
        updater: client_update.ClientUpdater | None = None,
    ):
        self.db_path = db_path
        self.hub = (hub or satellite.hub_url()).rstrip("/")
        self._request = request or _urllib_request
        self.mirror = MirrorPuller(db_path=db_path, hub=self.hub, request=self._hub_request)
        self.prefetch = ThumbPrefetcher(db_path=db_path, hub=self.hub, request=self._hub_request)
        self.preview_mirror = preview_mirror.PreviewMirrorFiller(
            db_path=db_path, hub=self.hub, request=self._hub_request
        )
        self.updater = updater
        self._force_mirror_refresh = False
        self._force_contract_refresh = False
        self._paused = False
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._failure_streak = 0
        self._pending_trash_failure_streak = 0
        self._pending_trash_next_attempt = 0.0
        self._next_idle_seconds = _BASE_IDLE_SECONDS
        self._status: dict[str, Any] = {
            "mode": "satellite",
            "paused": False,
            "state": "idle",  # idle | syncing | recovering | paused — recovering means work is owed and the last cycle failed
            "queue_depth": 0,
            "bytes_remaining": 0,
            "throughput_bps": 0,
            "current_file": None,
            "recent_errors": [],
            "last_sync_at": None,
            "backoff_seconds": 0,
            "pending_hub_trash": 0,
        }

    def status(self) -> dict:
        payload = {
            **self._status,
            "paused": self._paused,
            "mirror": self.mirror.status(),
            "prefetch": self.prefetch.status(),
            "preview_mirror": self.preview_mirror.status(),
            **contract.hub_status(self.hub),
        }
        if self.updater is not None:
            payload.update(self.updater.status.as_dict())
        return payload

    def pause(self) -> None:
        self._paused = True
        self._status["state"] = "paused"
        self._wake.set()

    def resume(self) -> None:
        self._paused = False
        self._status["state"] = "syncing" if self._status["queue_depth"] else "idle"
        self._wake.set()

    def sync_now(self) -> None:
        self._force_mirror_refresh = True
        self._force_contract_refresh = True
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            if not self._paused:
                try:
                    await self.sync_once()
                    self._clear_backoff()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._error(error)
                    self._note_failure(error)
                    await self._reconcile_status_after_failure()
            # Safe point between cycles: in-flight upload chunk loops have drained.
            self._maybe_restart_for_update()
            self._wake.clear()
            idle = self._next_idle_seconds if not self._paused else 3600.0
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=idle)
            except asyncio.TimeoutError:
                pass

    async def _reconcile_status_after_failure(self) -> None:
        # A failed cycle skips sync_once's end-of-cycle bookkeeping. Without
        # this, the status freezes at whatever the cycle start showed (or worse,
        # a stale zero) and "queue 0 + old error" reads as done while files are
        # still owed — the 2026-07-16 Holland ingest incident. Re-read the
        # durable pending snapshot; if even that fails, we don't know — and
        # unknown must read as recovering, never as done.
        self._status["current_file"] = None  # the failed upload is not active
        try:
            self._refresh_queue(await satellite.pending_upload_snapshot(self.db_path))
        except Exception:
            self._status["state"] = "paused" if self._paused else "recovering"
            return
        owed = bool(self._status["queue_depth"] or self._status["bytes_remaining"])
        self._status["state"] = "paused" if self._paused else ("recovering" if owed else "idle")

    def _clear_backoff(self) -> None:
        self._failure_streak = 0
        self._next_idle_seconds = _BASE_IDLE_SECONDS
        self._status["backoff_seconds"] = 0

    def _note_failure(self, error: Exception) -> None:
        message = str(error).lower()
        # Timing-out / unreachable hubs must not hot-loop every 15s.
        transient = any(
            token in message
            for token in ("timed out", "timeout", "temporarily unavailable", "connection refused", "unreachable", "name or service not known")
        )
        if not transient:
            self._next_idle_seconds = _BASE_IDLE_SECONDS
            self._status["backoff_seconds"] = 0
            return
        self._failure_streak += 1
        backoff = min(_MAX_BACKOFF_SECONDS, _BASE_IDLE_SECONDS * (2 ** min(self._failure_streak, 5)))
        self._next_idle_seconds = backoff
        self._status["backoff_seconds"] = backoff

    async def sync_once(self) -> None:
        if not self.hub:
            raise RuntimeError("PHOTOARCHIVE_HUB_URL is required for satellite sync")
        await self.refresh_hub_contract(
            force=self._force_contract_refresh or self.updater is not None
        )
        self._force_contract_refresh = False
        await self._consider_client_update()
        await self._retry_pending_hub_trash()
        items = await satellite.record_local_images(self.db_path)
        # A transient empty scan pass must not zero an owed queue mid-cycle;
        # zero is only authoritative from the end-of-cycle durable snapshot.
        if items:
            self._refresh_queue(items)
        if not self._paused and self._status["queue_depth"]:
            self._status["state"] = "syncing"
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
                    # Safe point between files: chunk loop for this upload has drained.
                    if self._maybe_restart_for_update():
                        return
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
        self._refresh_queue(await satellite.pending_upload_snapshot(self.db_path))
        self._status["state"] = "paused" if self._paused else ("syncing" if self._status["queue_depth"] else "idle")
        self._maybe_restart_for_update()

    async def _retry_pending_hub_trash(self) -> None:
        """Drain durable satellite Trash work without turning it into a hot loop."""

        refs = await trash_service.pending_hub_trash_refs(self.db_path)
        self._status["pending_hub_trash"] = int(refs["count"])
        if not refs["count"] or time.time() < self._pending_trash_next_attempt:
            return
        if not await contract.hub_supports(
            "trash.scoped_empty", hub=self.hub, request=self._hub_request
        ):
            return
        if len(refs["hub_image_ids"]) != refs["count"]:
            self._schedule_pending_trash_retry("Some synced photos are missing their hub identity.")
            return
        try:
            result = await trash_remote.empty_hub_trash(self.hub, refs["hub_image_ids"])
            if result.get("errors") or int(result.get("skipped_offline") or 0):
                raise trash_remote.HubTrashRequestError("The hub could not permanently remove every synced photo.")
            await trash_service.empty_trash(self.db_path, image_ids=refs["image_ids"])
        except trash_remote.HubTrashRequestError as error:
            self._schedule_pending_trash_retry(str(error))
            return
        self._pending_trash_failure_streak = 0
        self._pending_trash_next_attempt = 0.0
        self._status["pending_hub_trash"] = int((await trash_service.pending_hub_trash_refs(self.db_path))["count"])

    def _schedule_pending_trash_retry(self, message: str) -> None:
        self._pending_trash_failure_streak += 1
        delay = min(_MAX_BACKOFF_SECONDS, _BASE_IDLE_SECONDS * (2 ** min(self._pending_trash_failure_streak, 5)))
        self._pending_trash_next_attempt = time.time() + delay
        self._status["pending_hub_retry_at"] = self._pending_trash_next_attempt
        self._error(RuntimeError(message))

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
                status_code, _headers, body = await self._hub_request(
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
        status_code, _headers, body = await self._hub_request(
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
        # Live count-down: the queue must shrink as photos land, not only at
        # cycle boundaries (bytes_remaining already decrements per chunk).
        self._status["queue_depth"] = max(0, int(self._status["queue_depth"]) - 1)

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
            # Thermal doctrine: only sprint while the user isn't browsing.
            if not preview_mirror.is_idle():
                return
            await self.preview_mirror.burst_once()
            if not preview_mirror.is_idle():
                return
            # Browse (`sm`) exclusively until complete; only then fill loupe (`md`).
            await self.prefetch.prefetch_browse_first()
            await self.prefetch.seed_predictive()
            await self.prefetch.run_predictive_once(uploads_active=bool(self._status.get("current_file")))
        except Exception as error:
            self.prefetch._status["last_error"] = str(error)
            self.preview_mirror._status["last_error"] = str(error)

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
        status_code, _headers, response = await self._hub_request(method, self.hub + path, body=body, headers=headers)
        if not 200 <= status_code < 300:
            raise RuntimeError(f"sync {method} {path} failed ({status_code}): {response.decode(errors='replace')[:300]}")
        return json.loads(response or b"{}")

    async def _hub_request(self, method: str, url: str, *, body: bytes | None = None, headers: dict | None = None):
        """Attach the revision header to every request made by this satellite."""

        outbound_headers = {**dict(headers or {}), **contract.request_headers()}
        return await self._request(method, url, body=body, headers=outbound_headers)

    async def refresh_hub_contract(self, *, force: bool = False) -> None:
        """Refresh once at startup, then use the ten-minute shared cache."""

        await contract.refresh_hub_contract(self.hub, request=self._hub_request, force=force)

    async def _consider_client_update(self) -> None:
        if self.updater is None:
            return
        hub_contract = contract._contracts.get(self.hub.rstrip("/"))
        if hub_contract is None or not hub_contract.reachable:
            return
        payload = {
            "sha": hub_contract.sha,
            "bundle_sha256": hub_contract.bundle_sha256,
            "schema_version": hub_contract.schema_version,
        }
        # Breaking schema: hub is ahead of this binary — pause until update lands.
        if hub_contract.schema_version is not None:
            try:
                from data.schema import SCHEMA_VERSION

                if int(hub_contract.schema_version) > int(SCHEMA_VERSION):
                    # Still attempt the update; only pause sync if we cannot converge.
                    pass
            except Exception:
                pass
        await self.updater.consider_hub_version(payload)
        if (
            hub_contract.schema_version is not None
            and self.updater.status.state == client_update.STATUS_RETRY
        ):
            try:
                from data.schema import SCHEMA_VERSION

                if int(hub_contract.schema_version) > int(SCHEMA_VERSION):
                    self.pause()
                    self._status["recent_errors"] = [
                        "Hub schema is newer than this satellite — sync paused until update succeeds.",
                        *self._status["recent_errors"],
                    ][:5]
            except Exception:
                pass

    def _maybe_restart_for_update(self) -> bool:
        if self.updater is None:
            return False
        uploading = self._status.get("current_file") is not None
        return self.updater.request_restart_if_safe(uploading=uploading)

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
        errors = [message, *self._status["recent_errors"]]
        self._status["recent_errors"] = errors[:5]


_worker: SyncWorker | None = None


def configure_worker(worker: SyncWorker | None) -> None:
    global _worker
    _worker = worker


def get_worker() -> SyncWorker | None:
    return _worker
