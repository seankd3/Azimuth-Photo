"""Satellite catalog mirroring for the FIELD_SPEC_V2 hub export contract."""

from __future__ import annotations

import asyncio
import gzip
import json
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

from core import cache_events
from data import connection
from data.repositories import catalog as catalog_repository
from features.sync import satellite
from features.sync.executor import run_sync_work


RequestFn = Callable[..., Awaitable[tuple[int, dict[str, str], bytes]]]
_MIRROR_DDL = """
CREATE TABLE IF NOT EXISTS sync_mirror_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
_HUB_SOURCE_PATH = "hub://"
_HUB_SOURCE_NAME = "Hub library"
_IMAGE_COLUMNS = {
    "filename", "filepath", "content_hash", "elo", "comparisons", "status", "flag",
    "orientation", "date_taken", "camera_make", "camera_model", "lens", "file_ext",
    "file_size", "width", "height", "latitude", "longitude", "location_source", "missing_at", "trashed_at",
    "hub_image_id", "hub_remote",
}
# Commit mirror batches so a long hub export never holds a write lock for seconds
# while interactive reads (stats/grid) wait on busy_timeout.
_MIRROR_COMMIT_EVERY = 250


async def _urllib_request(method: str, url: str, *, body: bytes | None = None, headers: dict | None = None) -> tuple[int, dict[str, str], bytes]:
    def request() -> tuple[int, dict[str, str], bytes]:
        request_headers = dict(headers or {})
        request_headers.update(satellite.device_auth_headers())
        req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:  # noqa: S310 - configured tailnet hub.
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers or {}), error.read()

    return await run_sync_work(request)


async def ensure_mirror_schema(db_path: str) -> None:
    """Keep old satellite catalogs usable until the additive schema migration lands."""

    conn = await connection.open_async(db_path)
    try:
        columns = {row["name"] for row in await (await conn.execute("PRAGMA table_info(images)")).fetchall()}
        if "hub_image_id" not in columns:
            await conn.execute("ALTER TABLE images ADD COLUMN hub_image_id INTEGER")
        if "hub_remote" not in columns:
            await conn.execute("ALTER TABLE images ADD COLUMN hub_remote INTEGER NOT NULL DEFAULT 0")
        await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_images_hub_image_id ON images(hub_image_id) WHERE hub_image_id IS NOT NULL")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_images_hub_remote ON images(hub_remote, hub_image_id)")
        await conn.executescript(_MIRROR_DDL)
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


class MirrorPuller:
    """Incrementally apply the hub's gzip NDJSON catalog stream to one satellite."""

    def __init__(self, *, db_path: str, hub: str | None = None, request: RequestFn | None = None):
        self.db_path = db_path
        self.hub = (hub or satellite.hub_url()).rstrip("/")
        self._request = request or _urllib_request
        self._status: dict[str, Any] = {
            "cursor": 0,
            "rows_applied": 0,
            "skipped_unhashed": 0,
            "last_refresh_at": None,
            "last_error": "",
        }

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    async def refresh(self) -> dict[str, Any]:
        if not self.hub:
            raise RuntimeError("PHOTOARCHIVE_HUB_URL is required for catalog mirror refresh")
        await ensure_mirror_schema(self.db_path)
        cursor = await self._state_int("cursor")
        query = urlencode({"cursor": cursor})
        status, _headers, body = await self._request("GET", f"{self.hub}/api/sync/catalog/export?{query}")
        if not 200 <= status < 300:
            raise RuntimeError(f"mirror export failed ({status}): {body.decode(errors='replace')[:300]}")
        try:
            lines = gzip.decompress(body).decode("utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            raise RuntimeError("hub returned an invalid catalog export") from error

        applied = 0
        skipped_unhashed = 0
        new_cursor = cursor
        conn = await connection.open_async(self.db_path)
        try:
            source_id = await self._hub_source_id(conn)
            available_columns = {row["name"] for row in await (await conn.execute("PRAGMA table_info(images)")).fetchall()}
            for line in lines:
                if not line.strip():
                    continue
                row = json.loads(line)
                if "cursor" in row and len(row) == 1:
                    new_cursor = max(new_cursor, int(row["cursor"] or 0))
                    continue
                if row.get("hub_image_id") is None:
                    continue
                if not row.get("content_hash"):
                    skipped_unhashed += 1
                    continue
                await self._apply_row(conn, source_id, row, available_columns)
                applied += 1
                if applied % _MIRROR_COMMIT_EVERY == 0:
                    await conn.commit()
            await self._set_state(conn, "cursor", str(new_cursor))
            # The library service short-circuits on these denormalized counts;
            # a mirror that fills rows without them makes All Photos look like
            # only local/recent imports. Always resync hub:// after refresh.
            await catalog_repository.update_source_counts_on_conn(conn, source_id)
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=self.db_path)

        if applied > 0:
            cache_events.invalidate_stats_cache()

        self._status.update(
            cursor=new_cursor,
            rows_applied=applied,
            skipped_unhashed=skipped_unhashed,
            last_refresh_at=time.time(),
            last_error="",
        )
        return self.status()

    async def _state_int(self, key: str) -> int:
        conn = await connection.open_async(self.db_path)
        try:
            await conn.executescript(_MIRROR_DDL)
            row = await (await conn.execute("SELECT value FROM sync_mirror_state WHERE key = ?", (key,))).fetchone()
            return int(row["value"]) if row else 0
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    @staticmethod
    async def _set_state(conn, key: str, value: str) -> None:
        await conn.execute(
            "INSERT INTO sync_mirror_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    @staticmethod
    async def _hub_source_id(conn) -> int:
        await conn.execute(
            """INSERT INTO catalog_sources(path, display_name, included, online)
               VALUES (?, ?, 1, 1)
               ON CONFLICT(path) DO UPDATE SET display_name = excluded.display_name, online = 1""",
            (_HUB_SOURCE_PATH, _HUB_SOURCE_NAME),
        )
        row = await (await conn.execute("SELECT id FROM catalog_sources WHERE path = ?", (_HUB_SOURCE_PATH,))).fetchone()
        return int(row["id"])

    async def _apply_row(self, conn, source_id: int, remote: dict[str, Any], available_columns: set[str]) -> None:
        content_hash = str(remote["content_hash"])
        hub_image_id = int(remote["hub_image_id"])
        existing = await (await conn.execute(
            "SELECT id, hub_remote FROM images WHERE content_hash = ? OR hub_image_id = ? ORDER BY hub_remote ASC, id ASC LIMIT 1",
            (content_hash, hub_image_id),
        )).fetchone()
        values = self._image_values(remote, available_columns)
        if existing is None:
            values.update(source_id=source_id, hub_image_id=hub_image_id, hub_remote=1, filepath=str(remote.get("filepath") or ""))
            columns = list(values)
            await conn.execute(
                f"INSERT INTO images ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(values[column] for column in columns),
            )
            image_id = int((await (await conn.execute("SELECT last_insert_rowid() AS id")).fetchone())["id"])
        else:
            image_id = int(existing["id"])
            # A matching field import stays local; it merely gains the hub identity.
            if not int(existing["hub_remote"] or 0):
                values.pop("filepath", None)
                values.pop("source_id", None)
                values["hub_remote"] = 0
            values["hub_image_id"] = hub_image_id
            assignments = ", ".join(f"{column} = ?" for column in values)
            await conn.execute(f"UPDATE images SET {assignments} WHERE id = ?", (*values.values(), image_id))
        await self._apply_develop(conn, image_id, remote)
        await self._apply_keywords(conn, image_id, remote.get("keywords"))

    @staticmethod
    def _image_values(remote: dict[str, Any], available_columns: set[str]) -> dict[str, Any]:
        aliases = {"file_ext": "file_ext", "file_size": "file_size", "date_taken": "date_taken"}
        values: dict[str, Any] = {}
        for column in _IMAGE_COLUMNS & available_columns:
            if column in {"hub_image_id", "hub_remote", "filepath"}:
                continue
            source = aliases.get(column, column)
            if source in remote:
                values[column] = remote[source]
        if "status" in remote and str(remote["status"]).lower() in {"trashed", "deleted"}:
            values["status"] = "trashed"
            if "trashed_at" in available_columns:
                values["trashed_at"] = float(remote.get("trashed_at") or time.time())
        elif "trash_pending_hub" in available_columns:
            values["trash_pending_hub"] = 0
        return values

    @staticmethod
    async def _apply_develop(conn, image_id: int, remote: dict[str, Any]) -> None:
        settings = remote.get("develop_settings")
        updated_at = remote.get("develop_updated_at")
        if not isinstance(settings, dict) or not updated_at:
            return
        current = await (await conn.execute("SELECT updated_at, origin FROM develop_settings WHERE image_id = ?", (image_id,))).fetchone()
        if current and str(current["updated_at"] or "") > str(updated_at) and current["origin"] == "user":
            return
        await conn.execute(
            """INSERT INTO develop_settings(image_id, settings, origin, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(image_id) DO UPDATE SET settings = excluded.settings, origin = excluded.origin, updated_at = excluded.updated_at""",
            (image_id, json.dumps(settings, separators=(",", ":")), remote.get("develop_origin") or "hub", updated_at),
        )

    @staticmethod
    async def _apply_keywords(conn, image_id: int, paths: Any) -> None:
        if not isinstance(paths, list):
            return
        from features.library.keywords import KEYWORD_DDL

        await conn.executescript(KEYWORD_DDL)
        for path in paths:
            parent_id = None
            for name in [part.strip() for part in str(path).split(">") if part.strip()]:
                row = await (await conn.execute(
                    "SELECT id FROM keywords WHERE name = ? AND parent_id IS ?", (name, parent_id)
                )).fetchone()
                if row is None:
                    await conn.execute(
                        "INSERT INTO keywords(name, parent_id, created_at) VALUES (?, ?, datetime('now'))",
                        (name, parent_id),
                    )
                    row = await (await conn.execute("SELECT last_insert_rowid() AS id")).fetchone()
                parent_id = int(row["id"])
            if parent_id is not None:
                await conn.execute("INSERT OR IGNORE INTO image_keywords(image_id, keyword_id) VALUES (?, ?)", (image_id, parent_id))
