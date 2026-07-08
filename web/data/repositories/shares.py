"""Private collection share link repository."""

from __future__ import annotations

import secrets
import time

import sqlite3

from data import connection as data_connection


def _share_summary(row) -> dict:
    return {
        "id": int(row["id"]),
        "collection_id": int(row["collection_id"]),
        "token": row["token"],
        "created_at": float(row["created_at"]),
        "expires_at": float(row["expires_at"]) if row["expires_at"] is not None else None,
        "revoked_at": float(row["revoked_at"]) if row["revoked_at"] is not None else None,
        "password_hash": row["password_hash"],
        "view_count": int(row["view_count"] or 0),
        "first_viewed_at": (
            float(row["first_viewed_at"]) if row["first_viewed_at"] is not None else None
        ),
        "last_viewed_at": (
            float(row["last_viewed_at"]) if row["last_viewed_at"] is not None else None
        ),
    }


def _favorite_summary(row) -> dict:
    return {
        "image_id": int(row["image_id"]),
        "client_name": row["client_name"],
        "created_at": float(row["created_at"]),
    }


def _active_unexpired_clause(alias: str = "s") -> str:
    return f"{alias}.revoked_at IS NULL AND ({alias}.expires_at IS NULL OR {alias}.expires_at > ?)"


async def create_or_rotate_share(
    db_path: str,
    collection_id: int,
    *,
    expires_at: float | None = None,
    rotate: bool = False,
    password_hash: str | None = None,
) -> dict | None:
    collection_id = int(collection_id)
    now = time.time()
    conn = await data_connection.open_async(db_path)
    try:
        if not await _collection_exists(conn, collection_id):
            return None
        previous_share_id = None
        if not rotate:
            active = await _active_share_on_conn(conn, collection_id)
            if active is not None:
                return active
        else:
            active = await _active_share_on_conn(conn, collection_id)
            previous_share_id = int(active["id"]) if active is not None else None
        await conn.execute(
            "UPDATE collection_shares SET revoked_at = ? "
            "WHERE collection_id = ? AND revoked_at IS NULL",
            (now, collection_id),
        )
        for _attempt in range(4):
            token = secrets.token_urlsafe(24)
            try:
                cursor = await conn.execute(
                    "INSERT INTO collection_shares "
                    "(collection_id, token, created_at, expires_at, revoked_at, password_hash) "
                    "VALUES (?, ?, ?, ?, NULL, ?)",
                    (collection_id, token, now, expires_at, password_hash),
                )
                share_id = int(cursor.lastrowid)
                if previous_share_id is not None:
                    await conn.execute(
                        """
                        INSERT OR IGNORE INTO share_favorites
                            (share_id, image_id, client_name, created_at)
                        SELECT ?, image_id, client_name, created_at
                        FROM share_favorites
                        WHERE share_id = ?
                        """,
                        (share_id, previous_share_id),
                    )
                await conn.commit()
                return {
                    "id": share_id,
                    "collection_id": collection_id,
                    "token": token,
                    "created_at": now,
                    "expires_at": expires_at,
                    "revoked_at": None,
                    "password_hash": password_hash,
                    "view_count": 0,
                    "first_viewed_at": None,
                    "last_viewed_at": None,
                }
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Could not create unique share token")
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def get_share(db_path: str, collection_id: int) -> dict | None:
    conn = await data_connection.open_async(db_path)
    try:
        return await _active_share_on_conn(conn, int(collection_id))
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def revoke_share(db_path: str, collection_id: int) -> bool:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "UPDATE collection_shares SET revoked_at = ? "
            "WHERE collection_id = ? AND revoked_at IS NULL",
            (time.time(), int(collection_id)),
        )
        await conn.commit()
        return bool(cursor.rowcount)
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def set_share_password(
    db_path: str,
    collection_id: int,
    password_hash: str | None,
) -> dict | None:
    conn = await data_connection.open_async(db_path)
    try:
        active = await _active_share_on_conn(conn, int(collection_id))
        if active is None:
            return None
        await conn.execute(
            """
            UPDATE collection_shares
            SET password_hash = ?
            WHERE id = ?
            """,
            (password_hash, int(active["id"])),
        )
        await conn.commit()
        return await _active_share_on_conn(conn, int(collection_id))
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def record_share_view(db_path: str, token: str) -> dict | None:
    token = (token or "").strip()
    if not token:
        return None
    now = time.time()
    conn = await data_connection.open_async(db_path)
    try:
        await conn.execute(
            f"""
            UPDATE collection_shares
            SET view_count = COALESCE(view_count, 0) + 1,
                first_viewed_at = COALESCE(first_viewed_at, ?),
                last_viewed_at = ?
            WHERE token = ? AND {_active_unexpired_clause("collection_shares")}
            """,
            (now, now, token, now),
        )
        await conn.commit()
        cursor = await conn.execute(
            """
            SELECT *
            FROM collection_shares
            WHERE token = ?
            LIMIT 1
            """,
            (token,),
        )
        row = await cursor.fetchone()
        return _share_summary(row) if row is not None else None
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def resolve_token(db_path: str, token: str) -> dict | None:
    token = (token or "").strip()
    if not token:
        return None
    now = time.time()
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            f"""
            SELECT
                c.*,
                s.token,
                s.id AS share_id,
                s.password_hash,
                s.created_at AS share_created_at,
                s.expires_at AS share_expires_at,
                s.view_count,
                s.first_viewed_at,
                s.last_viewed_at,
                COUNT(ci.image_id) AS image_count,
                MIN(i.date_taken) AS date_min,
                MAX(i.date_taken) AS date_max
            FROM collection_shares s
            JOIN collections c ON c.id = s.collection_id
            LEFT JOIN collection_images ci ON ci.collection_id = c.id
            LEFT JOIN images i ON i.id = ci.image_id
            WHERE s.token = ? AND {_active_unexpired_clause("s")}
            GROUP BY c.id, s.id
            """,
            (token, now),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        collection = dict(row)
        collection["created_at"] = row["share_created_at"]
        collection["expires_at"] = row["share_expires_at"]
        collection["revoked_at"] = None
        images_cursor = await conn.execute(
            """
            SELECT
                i.id,
                i.filename,
                COALESCE(i.aspect_ratio, 1.5) AS aspect_ratio,
                i.date_taken
            FROM collection_images ci
            JOIN images i ON i.id = ci.image_id
            WHERE ci.collection_id = ?
            ORDER BY ci.position ASC, ci.added_at ASC, ci.image_id ASC
            """,
            (int(collection["id"]),),
        )
        collection["images"] = [dict(image) for image in await images_cursor.fetchall()]
        return collection
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def token_allows_image(db_path: str, token: str, image_id: int) -> bool:
    now = time.time()
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            f"""
            SELECT EXISTS(
                SELECT 1
                FROM collection_shares s
                JOIN collection_images ci ON ci.collection_id = s.collection_id
                WHERE s.token = ?
                AND ci.image_id = ?
                AND {_active_unexpired_clause("s")}
            ) AS allowed
            """,
            ((token or "").strip(), int(image_id), now),
        )
        row = await cursor.fetchone()
        return bool(row["allowed"] if row else 0)
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def set_favorite(
    db_path: str,
    share_id: int,
    image_id: int,
    on: bool,
    client_name: str | None = None,
) -> bool:
    now = time.time()
    clean_name = (client_name or "").strip()[:120] or None
    conn = await data_connection.open_async(db_path)
    try:
        if not await _share_contains_image(conn, int(share_id), int(image_id)):
            return False
        if on:
            await conn.execute(
                """
                INSERT INTO share_favorites (share_id, image_id, client_name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(share_id, image_id) DO UPDATE SET
                    client_name = COALESCE(excluded.client_name, share_favorites.client_name)
                """,
                (int(share_id), int(image_id), clean_name, now),
            )
        else:
            await conn.execute(
                "DELETE FROM share_favorites WHERE share_id = ? AND image_id = ?",
                (int(share_id), int(image_id)),
            )
        await conn.commit()
        return True
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def list_favorites(db_path: str, share_id: int) -> list[dict]:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            """
            SELECT image_id, client_name, created_at
            FROM share_favorites
            WHERE share_id = ?
            ORDER BY created_at ASC, image_id ASC
            """,
            (int(share_id),),
        )
        return [_favorite_summary(row) for row in await cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def favorites_for_collection(db_path: str, collection_id: int) -> list[dict]:
    conn = await data_connection.open_async(db_path)
    try:
        active = await _active_share_on_conn(conn, int(collection_id))
        if active is None:
            return []
        cursor = await conn.execute(
            """
            SELECT sf.image_id, sf.client_name, sf.created_at
            FROM share_favorites sf
            JOIN collection_images ci
                ON ci.collection_id = ?
                AND ci.image_id = sf.image_id
            WHERE sf.share_id = ?
            ORDER BY sf.created_at ASC, sf.image_id ASC
            """,
            (int(collection_id), int(active["id"])),
        )
        return [_favorite_summary(row) for row in await cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def _collection_exists(conn, collection_id: int) -> bool:
    cursor = await conn.execute("SELECT 1 FROM collections WHERE id = ?", (int(collection_id),))
    return await cursor.fetchone() is not None


async def _share_contains_image(conn, share_id: int, image_id: int) -> bool:
    cursor = await conn.execute(
        """
        SELECT EXISTS(
            SELECT 1
            FROM collection_shares s
            JOIN collection_images ci ON ci.collection_id = s.collection_id
            WHERE s.id = ?
            AND ci.image_id = ?
        ) AS allowed
        """,
        (int(share_id), int(image_id)),
    )
    row = await cursor.fetchone()
    return bool(row["allowed"] if row else 0)


async def _active_share_on_conn(conn, collection_id: int) -> dict | None:
    cursor = await conn.execute(
        """
        SELECT *
        FROM collection_shares s
        WHERE s.collection_id = ? AND s.revoked_at IS NULL
        ORDER BY s.created_at DESC, s.id DESC
        LIMIT 1
        """,
        (int(collection_id),),
    )
    row = await cursor.fetchone()
    return _share_summary(row) if row is not None else None
