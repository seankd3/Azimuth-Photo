"""Carry semantic embeddings from the hub to a satellite.

Search is the one capability a satellite could not do for itself. A hub-backed
satellite skips the AI workers, so it never computes a vector, and until now
nothing sent it any — a laptop with a hundred and fifty thousand photos had zero
of them and semantic search away from home had nothing to search.

Embeddings are the best possible thing to sync: GPU-hours to compute and about a
gigabyte to move for a whole library. Unlike thumbnails there is no budget to
reason about — a satellite takes all of them.

The wire format is the catalog export's: gzip NDJSON, one row per line, a
trailing ``{"cursor": n}`` line, and an ``image_id`` cursor so a transfer
resumes where it stopped. Vectors travel base64-encoded because they are
opaque bytes, and are refused on arrival unless the model and dimension match
what this machine searches with — a vector from another model is not a worse
answer, it is a meaningless one.
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
from typing import Any

from data import connection

log = logging.getLogger(__name__)

PAGE_LIMIT = 500
MAX_PAGE_LIMIT = 2000


async def export_page(db_path: str, *, cursor: int = 0, limit: int = PAGE_LIMIT) -> bytes:
    """One gzip NDJSON page of embeddings with ``image_id`` greater than cursor."""

    limit = max(1, min(int(limit or PAGE_LIMIT), MAX_PAGE_LIMIT))
    conn = await connection.open_async(db_path)
    try:
        rows = await (await conn.execute(
            "SELECT model_key, image_id, dimension, embedding FROM embeddings_by_model "
            "WHERE image_id > ? ORDER BY image_id ASC LIMIT ?",
            (int(cursor), limit),
        )).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)

    lines = []
    page_cursor = int(cursor)
    for row in rows:
        page_cursor = int(row["image_id"])
        lines.append(json.dumps({
            "hub_image_id": page_cursor,
            "model_key": str(row["model_key"]),
            "dimension": int(row["dimension"]),
            "embedding": base64.b64encode(bytes(row["embedding"])).decode("ascii"),
        }, separators=(",", ":")))
    lines.append(json.dumps({"cursor": page_cursor}, separators=(",", ":")))
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


class EmbeddingPuller:
    """Pull the hub's embeddings into this satellite's catalog."""

    def __init__(self, *, db_path: str, hub: str, request, model_key: str, dimension: int):
        self.db_path = db_path
        self.hub = (hub or "").rstrip("/")
        self._request = request
        self.model_key = model_key
        self.dimension = int(dimension)
        self._status: dict[str, Any] = {
            "cursor": 0,
            "rows_applied": 0,
            "skipped_unknown_image": 0,
            "skipped_wrong_model": 0,
            "last_error": "",
        }

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    async def _cursor(self) -> int:
        conn = await connection.open_async(self.db_path)
        try:
            await conn.executescript(
                "CREATE TABLE IF NOT EXISTS sync_embedding_state ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            )
            row = await (await conn.execute(
                "SELECT value FROM sync_embedding_state WHERE key = ?", (self._cursor_key(),)
            )).fetchone()
        finally:
            await connection.close_async(conn, db_path=self.db_path)
        return int(row["value"]) if row else 0

    def _cursor_key(self) -> str:
        # Per model: a new model means a different vector space, so its fill
        # starts from the beginning rather than inheriting a stale position.
        return f"cursor:{self.model_key}"

    async def _set_cursor(self, conn, value: int) -> None:
        await conn.execute(
            "INSERT INTO sync_embedding_state(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self._cursor_key(), str(int(value))),
        )

    async def refresh(self, *, max_pages: int = 400) -> dict[str, Any]:
        """Pull pages until the satellite has caught up with the hub."""

        applied = unknown = wrong_model = 0
        cursor = await self._cursor()
        for _page in range(max_pages):
            status, _headers, body = await self._request(
                "GET", f"{self.hub}/api/sync/embeddings/pack?cursor={cursor}&limit={PAGE_LIMIT}"
            )
            if not 200 <= status < 300:
                self._status["last_error"] = f"embedding pack failed ({status})"
                break
            rows, next_cursor = _decode_page(body)
            if not rows:
                cursor = next_cursor or cursor
                break
            page_applied, page_unknown, page_wrong = await self._apply(rows, next_cursor)
            applied += page_applied
            unknown += page_unknown
            wrong_model += page_wrong
            if next_cursor <= cursor:
                break
            cursor = next_cursor

        self._status.update(
            cursor=cursor,
            rows_applied=applied,
            skipped_unknown_image=unknown,
            skipped_wrong_model=wrong_model,
        )
        return self.status()

    async def _apply(self, rows: list[dict], next_cursor: int) -> tuple[int, int, int]:
        applied = unknown = wrong_model = 0
        conn = await connection.open_async(self.db_path)
        try:
            hub_ids = [int(row["hub_image_id"]) for row in rows]
            placeholders = ",".join("?" for _ in hub_ids)
            mapping = {
                int(found["hub_image_id"]): int(found["id"])
                for found in await (await conn.execute(
                    f"SELECT id, hub_image_id FROM images WHERE hub_image_id IN ({placeholders})",
                    hub_ids,
                )).fetchall()
            }
            payload = []
            for row in rows:
                if row.get("model_key") != self.model_key or int(row.get("dimension") or 0) != self.dimension:
                    wrong_model += 1
                    continue
                image_id = mapping.get(int(row["hub_image_id"]))
                if image_id is None:
                    # The catalog mirror has not reached this photo yet; the
                    # next pass picks it up once the row exists.
                    unknown += 1
                    continue
                payload.append((self.model_key, image_id, base64.b64decode(row["embedding"]), self.dimension))
            if payload:
                await conn.executemany(
                    "INSERT OR REPLACE INTO embeddings_by_model "
                    "(model_key, image_id, embedding, dimension) VALUES (?, ?, ?, ?)",
                    payload,
                )
                applied = len(payload)
            await self._set_cursor(conn, next_cursor)
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=self.db_path)
        return applied, unknown, wrong_model


def _decode_page(body: bytes) -> tuple[list[dict], int]:
    try:
        text = gzip.decompress(body).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise RuntimeError("hub returned an invalid embedding pack") from None
    rows: list[dict] = []
    cursor = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "cursor" in entry and "hub_image_id" not in entry:
            cursor = int(entry["cursor"])
            continue
        rows.append(entry)
    return rows, cursor
