"""Repository for public portfolio gallery publish records."""

from __future__ import annotations

import time

import sqlite3

from data import connection as data_connection


def _summary(row) -> dict:
    return {
        "id": int(row["id"]),
        "collection_id": int(row["collection_id"]),
        "slug": row["slug"],
        "title": row["title"],
        "published_at": float(row["published_at"]),
        "updated_at": float(row["updated_at"]),
        "image_count": int(row["image_count"] or 0),
        "bundle_bytes": int(row["bundle_bytes"] or 0),
        "last_commit": row["last_commit"],
        "hook_exit_code": int(row["hook_exit_code"]) if row["hook_exit_code"] is not None else None,
        "hook_output": row["hook_output"] or "",
        "hook_ran_at": float(row["hook_ran_at"]) if row["hook_ran_at"] is not None else None,
        "collection_name": row["collection_name"] if "collection_name" in row.keys() else None,
        "cover_image_id": int(row["cover_image_id"]) if "cover_image_id" in row.keys() and row["cover_image_id"] is not None else None,
    }


async def get_publish(db_path: str, collection_id: int) -> dict | None:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT * FROM collection_publishes WHERE collection_id = ?",
            (int(collection_id),),
        )
        row = await cursor.fetchone()
        return _summary(row) if row is not None else None
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def get_publish_by_slug(db_path: str, slug: str) -> dict | None:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT * FROM collection_publishes WHERE slug = ?",
            ((slug or "").strip(),),
        )
        row = await cursor.fetchone()
        return _summary(row) if row is not None else None
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def list_publishes(db_path: str) -> list[dict]:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            """
            SELECT p.*, c.name AS collection_name, c.cover_image_id
            FROM collection_publishes p
            JOIN collections c ON c.id = p.collection_id
            ORDER BY p.updated_at DESC, p.id DESC
            """
        )
        return [_summary(row) for row in await cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def upsert_publish(
    db_path: str,
    *,
    collection_id: int,
    slug: str,
    title: str,
    image_count: int,
    bundle_bytes: int,
    last_commit: str | None,
    hook_exit_code: int | None = None,
    hook_output: str = "",
    hook_ran_at: float | None = None,
    now: float | None = None,
) -> dict:
    clean_slug = (slug or "").strip()
    clean_title = (title or "").strip() or clean_slug
    if not clean_slug:
        raise ValueError("slug is required")
    now = time.time() if now is None else float(now)
    conn = await data_connection.open_async(db_path)
    try:
        existing = await _get_publish_on_conn(conn, int(collection_id))
        published_at = float(existing["published_at"]) if existing is not None else now
        try:
            await conn.execute(
                """
                INSERT INTO collection_publishes
                    (collection_id, slug, title, published_at, updated_at, image_count, bundle_bytes,
                     last_commit, hook_exit_code, hook_output, hook_ran_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_id) DO UPDATE SET
                    slug = excluded.slug,
                    title = excluded.title,
                    updated_at = excluded.updated_at,
                    image_count = excluded.image_count,
                    bundle_bytes = excluded.bundle_bytes,
                    last_commit = excluded.last_commit,
                    hook_exit_code = excluded.hook_exit_code,
                    hook_output = excluded.hook_output,
                    hook_ran_at = excluded.hook_ran_at
                """,
                (
                    int(collection_id),
                    clean_slug,
                    clean_title,
                    published_at,
                    now,
                    max(0, int(image_count)),
                    max(0, int(bundle_bytes)),
                    last_commit,
                    hook_exit_code,
                    str(hook_output or ""),
                    hook_ran_at,
                ),
            )
            await conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("slug is already published") from exc
        row = await _get_publish_on_conn(conn, int(collection_id))
        if row is None:
            raise RuntimeError("publish upsert failed")
        return _summary(row)
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def delete_publish(db_path: str, collection_id: int) -> bool:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "DELETE FROM collection_publishes WHERE collection_id = ?",
            (int(collection_id),),
        )
        await conn.commit()
        return bool(cursor.rowcount)
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def slug_available(db_path: str, slug: str, *, collection_id: int | None = None) -> bool:
    existing = await get_publish_by_slug(db_path, slug)
    if existing is None:
        return True
    return collection_id is not None and int(existing["collection_id"]) == int(collection_id)


async def _get_publish_on_conn(conn, collection_id: int):
    cursor = await conn.execute(
        "SELECT * FROM collection_publishes WHERE collection_id = ?",
        (int(collection_id),),
    )
    return await cursor.fetchone()
