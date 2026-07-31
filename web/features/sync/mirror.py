"""Satellite catalog mirroring for the FIELD_SPEC_V2 hub export contract."""

from __future__ import annotations

import functools
import gzip
import inspect
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
from features.sync import elo_stars, family_clock, satellite
from features.sync.develop_merge import preserve_local_rating
from features.sync.executor import run_sync_work
from features.trash import service as trash_service


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


async def _urllib_request(method: str, url: str, *, body: bytes | None = None, headers: dict | None = None, timeout: float = 20) -> tuple[int, dict[str, str], bytes]:
    def request() -> tuple[int, dict[str, str], bytes]:
        request_headers = dict(headers or {})
        request_headers.update(satellite.hub_request_headers())
        req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - configured tailnet hub.
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers or {}), error.read()

    return await run_sync_work(request)


def _accepts_timeout(request) -> bool:
    try:
        parameters = inspect.signature(request).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "timeout" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


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
        await family_clock.migrate_legacy_states(conn)
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


class MirrorPuller:
    """Incrementally apply the hub's gzip NDJSON catalog stream to one satellite."""

    def __init__(self, *, db_path: str, hub: str | None = None, request: RequestFn | None = None):
        self.db_path = db_path
        self.hub = (hub or satellite.hub_url()).rstrip("/")
        self._request = request or _urllib_request
        # An export page still takes minutes on a busy hub over the tailnet;
        # the interactive 20s default abandons it. Only widen the timeout when
        # the transport actually accepts one — injected test fakes may not.
        if _accepts_timeout(self._request):
            self._bulk_request = functools.partial(self._request, timeout=600)
        else:
            self._bulk_request = self._request
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
        """Pull export pages until the mirror has caught up to the hub."""
        result = await self._refresh_page()
        total_applied = int(result.get("rows_applied") or 0)
        total_skipped = int(result.get("skipped_unhashed") or 0)
        for _round in range(400):
            cursor_before = int(result.get("cursor") or 0)
            if int(result.get("rows_applied") or 0) <= 0:
                break
            result = await self._refresh_page()
            total_applied += int(result.get("rows_applied") or 0)
            total_skipped += int(result.get("skipped_unhashed") or 0)
            if int(result.get("cursor") or 0) <= cursor_before:
                break
        self._status.update(rows_applied=total_applied, skipped_unhashed=total_skipped)
        return self.status()

    async def _refresh_page(self) -> dict[str, Any]:
        if not self.hub:
            raise RuntimeError("AZIMUTH_HUB_URL is required for catalog mirror refresh")
        await ensure_mirror_schema(self.db_path)
        cursor = await self._state_int("cursor")
        # Paged: a whole-catalog export over a slow tailnet cannot finish
        # inside any sane timeout; five-thousand-row pages can, and the
        # cursor makes every page resumable. Old hubs ignore the limit and
        # stream everything — the long timeout covers that case.
        query = urlencode({"cursor": cursor, "limit": 5000})
        status, _headers, body = await self._bulk_request("GET", f"{self.hub}/api/sync/catalog/export?{query}")
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
            trash_service.invalidate_pending_hub_trash_refs(self.db_path)
            # Mirrored rows carry hub Elo; re-persist the local star projection.
            elo_stars.schedule_stored_stars_refresh(self.db_path)

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
        if existing is not None:
            # The same photo can be two rows: a local import matched by hash
            # and an older mirror row already holding this hub identity.
            # Stamping the identity onto the local row while the mirror row
            # keeps it violates the unique index and used to kill the whole
            # refresh. The local row wins; the redundant mirror row folds in,
            # its earned ranking carried over rather than lost.
            other = await (await conn.execute(
                "SELECT id, elo, comparisons FROM images WHERE hub_image_id = ? AND id != ?",
                (hub_image_id, int(existing["id"])),
            )).fetchone()
            if other is not None:
                await conn.execute(
                    "UPDATE images SET elo = ?, comparisons = ? "
                    "WHERE id = ? AND COALESCE(comparisons, 0) = 0 AND COALESCE(?, 0) > 0",
                    (other["elo"], other["comparisons"], int(existing["id"]), other["comparisons"]),
                )
                await conn.execute("DELETE FROM images WHERE id = ?", (int(other["id"]),))
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
            else:
                # Hub paths can move (mount migrations, re-filed folders) — the
                # mirror must follow, or the folder tree shows the old layout forever.
                values["filepath"] = str(remote.get("filepath") or "")
            values["hub_image_id"] = hub_image_id
            assignments = ", ".join(f"{column} = ?" for column in values)
            await conn.execute(f"UPDATE images SET {assignments} WHERE id = ?", (*values.values(), image_id))
        await self._apply_develop(conn, image_id, remote)
        await self._apply_rating(conn, image_id, remote, available_columns)
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
        current = await (await conn.execute(
            "SELECT develop.settings, develop.updated_at, images.content_hash "
            "FROM images LEFT JOIN develop_settings develop ON develop.image_id = images.id "
            "WHERE images.id = ?",
            (image_id,),
        )).fetchone()
        if current is None:
            return
        content_hash = str(current["content_hash"] or "")
        row_key = family_clock.legacy_key(current["updated_at"]) if current["updated_at"] is not None else None
        state_key = await family_clock.state_key(conn, content_hash, "develop") if content_hash else None
        existing = family_clock.newest_key(row_key, state_key)
        incoming = family_clock.legacy_key(updated_at)
        if existing is not None and incoming[0] <= existing[0]:
            return
        settings = dict(settings)
        settings.pop("_lr_rating", None)
        settings = preserve_local_rating(settings, current["settings"])
        await conn.execute(
            """INSERT INTO develop_settings(image_id, settings, origin, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(image_id) DO UPDATE SET settings = excluded.settings, origin = excluded.origin, updated_at = excluded.updated_at""",
            (image_id, json.dumps(settings, separators=(",", ":")), remote.get("develop_origin") or "hub", updated_at),
        )
        if content_hash:
            await family_clock.record_state(conn, content_hash, "develop", incoming)

    @staticmethod
    async def _apply_rating(
        conn,
        image_id: int,
        remote: dict[str, Any],
        available_columns: set[str],
    ) -> None:
        winner = remote.get("rating_winner_key")
        if "rating" not in remote or not isinstance(winner, dict):
            return
        try:
            incoming = family_clock.winner_key(winner)
        except (KeyError, TypeError, ValueError):
            return
        row = await (await conn.execute(
            "SELECT content_hash FROM images WHERE id = ?",
            (image_id,),
        )).fetchone()
        if row is None or not row["content_hash"]:
            return
        content_hash = str(row["content_hash"])
        existing = await family_clock.state_key(conn, content_hash, "rating")
        if existing is not None and incoming <= existing:
            return
        rating = remote["rating"]
        if "rating" in available_columns:
            await conn.execute("UPDATE images SET rating = ? WHERE id = ?", (rating, image_id))
        else:
            current = await (await conn.execute(
                "SELECT origin FROM develop_settings WHERE image_id = ?",
                (image_id,),
            )).fetchone()
            settings = json.dumps({"_lr_rating": rating}, separators=(",", ":"))
            await conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) "
                "VALUES (?, ?, ?, '') ON CONFLICT(image_id) DO UPDATE SET settings=json_set("
                "CASE WHEN json_valid(develop_settings.settings) THEN "
                "  CASE WHEN json_type(develop_settings.settings) = 'object' "
                "    THEN develop_settings.settings ELSE '{}' END "
                "ELSE '{}' END, '$._lr_rating', ?)",
                (image_id, settings, current["origin"] if current else "sync", rating),
            )
        await family_clock.record_state(conn, content_hash, "rating", incoming)

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
