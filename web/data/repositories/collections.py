"""Persistent user collection repository."""

from __future__ import annotations

import time

from data import connection as data_connection
from data.repositories.common import chunked

VALID_VISIBILITIES = {"private", "shared", "public", "exported"}
VALID_STATUSES = {"draft", "active", "archived"}


def _clean_text(value: str | None, *, fallback: str = "") -> str:
    text = (value or "").strip()
    return text or fallback


def _normalize_visibility(value: str | None) -> str:
    visibility = _clean_text(value, fallback="private").lower()
    return visibility if visibility in VALID_VISIBILITIES else "private"


def _normalize_status(value: str | None) -> str:
    status = _clean_text(value, fallback="draft").lower()
    return status if status in VALID_STATUSES else "draft"


async def create_collection(
    db_path: str,
    *,
    name: str,
    description: str = "",
    image_ids: list[int] | None = None,
    visibility: str = "private",
    status: str = "draft",
) -> dict:
    now = time.time()
    clean_name = _clean_text(name, fallback="Untitled collection")
    clean_description = _clean_text(description)
    image_ids = _unique_ids(image_ids or [])
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "INSERT INTO collections "
            "(name, description, visibility, status, cover_image_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                clean_name,
                clean_description,
                _normalize_visibility(visibility),
                _normalize_status(status),
                None,
                now,
                now,
            ),
        )
        collection_id = int(cursor.lastrowid)
        if image_ids:
            await _insert_members(conn, collection_id, image_ids, now=now)
            # Cover comes from the members that actually exist, not the raw
            # request ids, so an invalid first id can't leave a dangling cover.
            await _ensure_cover(conn, collection_id)
        await conn.commit()
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return await get_collection(db_path, collection_id) or {"id": collection_id}


async def rename_collection(db_path: str, collection_id: int, *, name: str) -> dict | None:
    now = time.time()
    clean_name = _clean_text(name)
    conn = await data_connection.open_async(db_path)
    try:
        if not await _collection_exists(conn, collection_id):
            return None
        await conn.execute(
            "UPDATE collections SET name = ?, updated_at = ? WHERE id = ?",
            (clean_name, now, int(collection_id)),
        )
        await conn.commit()
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return await get_collection(db_path, collection_id)


async def delete_collection(db_path: str, collection_id: int) -> bool:
    conn = await data_connection.open_async(db_path)
    try:
        if not await _collection_exists(conn, collection_id):
            return False
        await conn.execute("DELETE FROM collection_images WHERE collection_id = ?", (int(collection_id),))
        await conn.execute("DELETE FROM collections WHERE id = ?", (int(collection_id),))
        await conn.commit()
        return True
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def list_collections(db_path: str) -> list[dict]:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            """
            SELECT
                c.*,
                COUNT(ci.image_id) AS image_count,
                cover.filename AS cover_filename
            FROM collections c
            LEFT JOIN collection_images ci ON ci.collection_id = c.id
            LEFT JOIN images cover ON cover.id = c.cover_image_id
            GROUP BY c.id
            ORDER BY c.updated_at DESC, c.id DESC
            """
        )
        rows = [dict(row) for row in await cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return [_collection_summary(row) for row in rows]


async def get_collection(db_path: str, collection_id: int, *, limit: int = 200, offset: int = 0) -> dict | None:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            """
            SELECT
                c.*,
                COUNT(ci.image_id) AS image_count,
                cover.filename AS cover_filename
            FROM collections c
            LEFT JOIN collection_images ci ON ci.collection_id = c.id
            LEFT JOIN images cover ON cover.id = c.cover_image_id
            WHERE c.id = ?
            GROUP BY c.id
            """,
            (int(collection_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        collection = _collection_summary(dict(row))
        image_cursor = await conn.execute(
            """
            SELECT i.*
            FROM collection_images ci
            JOIN images i ON i.id = ci.image_id
            WHERE ci.collection_id = ?
            ORDER BY ci.position ASC, ci.added_at ASC, ci.image_id ASC
            LIMIT ? OFFSET ?
            """,
            (int(collection_id), max(1, min(int(limit), 1000)), max(0, int(offset))),
        )
        collection["images"] = [dict(row) for row in await image_cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return collection


async def collection_image_ids(db_path: str, collection_id: int, *, limit: int = 2000) -> list[int] | None:
    conn = await data_connection.open_async(db_path)
    try:
        if not await _collection_exists(conn, collection_id):
            return None
        cursor = await conn.execute(
            """
            SELECT image_id
            FROM collection_images
            WHERE collection_id = ?
            ORDER BY position ASC, added_at ASC, image_id ASC
            LIMIT ?
            """,
            (int(collection_id), max(1, int(limit))),
        )
        return [int(row["image_id"]) for row in await cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def add_images(db_path: str, collection_id: int, image_ids: list[int]) -> dict | None:
    image_ids = _unique_ids(image_ids)
    if not image_ids:
        return await get_collection(db_path, collection_id)
    now = time.time()
    conn = await data_connection.open_async(db_path)
    try:
        if not await _collection_exists(conn, collection_id):
            return None
        await _insert_members(conn, collection_id, image_ids, now=now)
        await _ensure_cover(conn, collection_id)
        await conn.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (now, int(collection_id)))
        await conn.commit()
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return await get_collection(db_path, collection_id)


async def remove_images(db_path: str, collection_id: int, image_ids: list[int]) -> dict | None:
    image_ids = _unique_ids(image_ids)
    now = time.time()
    conn = await data_connection.open_async(db_path)
    try:
        if not await _collection_exists(conn, collection_id):
            return None
        for ids in chunked(image_ids):
            placeholders = ",".join("?" for _ in ids)
            await conn.execute(
                f"DELETE FROM collection_images WHERE collection_id = ? AND image_id IN ({placeholders})",
                (int(collection_id), *ids),
            )
        await _ensure_cover(conn, collection_id)
        await conn.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (now, int(collection_id)))
        await conn.commit()
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return await get_collection(db_path, collection_id)


def _unique_ids(image_ids: list[int]) -> list[int]:
    seen = set()
    unique = []
    for raw_id in image_ids:
        try:
            image_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if image_id > 0 and image_id not in seen:
            seen.add(image_id)
            unique.append(image_id)
    return unique


async def _collection_exists(conn, collection_id: int) -> bool:
    cursor = await conn.execute("SELECT 1 FROM collections WHERE id = ?", (int(collection_id),))
    return await cursor.fetchone() is not None


async def _insert_members(conn, collection_id: int, image_ids: list[int], *, now: float) -> None:
    cursor = await conn.execute(
        "SELECT COALESCE(MAX(position), -1) FROM collection_images WHERE collection_id = ?",
        (int(collection_id),),
    )
    next_position = int((await cursor.fetchone())[0]) + 1
    existing_ids: set[int] = set()
    for ids in chunked(image_ids):
        existing_cursor = await conn.execute(
            "SELECT id FROM images WHERE id IN ({})".format(",".join("?" for _ in ids)),
            ids,
        )
        existing_ids.update(int(row["id"]) for row in await existing_cursor.fetchall())
    rows = []
    for image_id in image_ids:
        if image_id not in existing_ids:
            continue
        rows.append((int(collection_id), image_id, next_position, now))
        next_position += 1
    for row_chunk in chunked(rows):
        await conn.executemany(
            "INSERT OR IGNORE INTO collection_images (collection_id, image_id, position, added_at) "
            "VALUES (?, ?, ?, ?)",
            row_chunk,
        )


async def _ensure_cover(conn, collection_id: int) -> None:
    cursor = await conn.execute(
        """
        SELECT image_id
        FROM collection_images
        WHERE collection_id = ?
        ORDER BY position ASC, added_at ASC, image_id ASC
        LIMIT 1
        """,
        (int(collection_id),),
    )
    row = await cursor.fetchone()
    await conn.execute(
        "UPDATE collections SET cover_image_id = ? WHERE id = ?",
        (int(row["image_id"]) if row else None, int(collection_id)),
    )


def _collection_summary(row: dict) -> dict:
    cover_image_id = row.get("cover_image_id")
    return {
        "id": int(row["id"]),
        "name": row.get("name") or "Untitled collection",
        "description": row.get("description") or "",
        "visibility": row.get("visibility") or "private",
        "status": row.get("status") or "draft",
        "image_count": int(row.get("image_count") or 0),
        "cover_image_id": int(cover_image_id) if cover_image_id is not None else None,
        "cover_filename": row.get("cover_filename") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
