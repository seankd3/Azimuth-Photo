"""Persistent, frozen client-gallery snapshots.

This deliberately has no dependency on the website publisher. A gallery is a
local share token with its own options and frozen collection membership.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Iterable
from typing import Any

from data import connection


VALID_LAYOUTS = {"grid", "masonry", "slideshow"}
VALID_THEMES = {"light", "dark", "warm"}
VALID_DOWNLOAD_SIZES = {"sm", "md", "lg", "original"}

DDL = """
CREATE TABLE IF NOT EXISTS client_galleries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    layout TEXT NOT NULL DEFAULT 'grid',
    theme TEXT NOT NULL DEFAULT 'light',
    cover_image_id INTEGER DEFAULT NULL REFERENCES images(id) ON DELETE SET NULL,
    password_hash TEXT DEFAULT NULL,
    allow_download_all INTEGER NOT NULL DEFAULT 1,
    download_size TEXT NOT NULL DEFAULT 'lg',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    view_count INTEGER NOT NULL DEFAULT 0,
    first_viewed_at REAL DEFAULT NULL,
    last_viewed_at REAL DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS client_gallery_images (
    gallery_id INTEGER NOT NULL REFERENCES client_galleries(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (gallery_id, image_id)
);
CREATE INDEX IF NOT EXISTS idx_client_galleries_collection ON client_galleries(collection_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_client_gallery_images_position ON client_gallery_images(gallery_id, position, image_id);
"""


def _unique_ids(image_ids: Iterable[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in image_ids:
        try:
            image_id = int(value)
        except (TypeError, ValueError):
            continue
        if image_id > 0 and image_id not in seen:
            result.append(image_id)
            seen.add(image_id)
    return result


def normalize_options(options: dict[str, Any] | None) -> dict[str, Any]:
    options = options if isinstance(options, dict) else {}
    layout = str(options.get("layout") or "grid").lower()
    theme = str(options.get("theme") or "light").lower()
    download_size = str(options.get("download_size") or "lg").lower()
    cover_raw = options.get("cover_image_id")
    try:
        cover_image_id = int(cover_raw) if cover_raw is not None else None
    except (TypeError, ValueError):
        cover_image_id = None
    return {
        "layout": layout if layout in VALID_LAYOUTS else "grid",
        "theme": theme if theme in VALID_THEMES else "light",
        "cover_image_id": cover_image_id if cover_image_id and cover_image_id > 0 else None,
        "allow_download_all": bool(options.get("allow_download_all", True)),
        "download_size": download_size if download_size in VALID_DOWNLOAD_SIZES else "lg",
    }


async def ensure_tables(conn) -> None:
    await conn.executescript(DDL)


async def create_gallery(
    db_path: str,
    *,
    collection_id: int,
    title: str,
    image_ids: Iterable[int],
    options: dict[str, Any] | None = None,
    password_hash: str | None = None,
) -> dict[str, Any]:
    """Create a new gallery token and freeze the supplied active membership."""
    clean_ids = _unique_ids(image_ids)
    clean = normalize_options(options)
    now = time.time()
    conn = await connection.open_async(db_path)
    try:
        await ensure_tables(conn)
        # Snapshot only active catalog rows; no client can later access a stale ID.
        active_ids: set[int] = set()
        for image_id in clean_ids:
            cursor = await conn.execute(
                "SELECT id FROM images WHERE id = ? AND status IN ('kept', 'maybe') AND missing_at IS NULL",
                (image_id,),
            )
            if await cursor.fetchone():
                active_ids.add(image_id)
        snapshot = [image_id for image_id in clean_ids if image_id in active_ids]
        cover_image_id = clean["cover_image_id"] if clean["cover_image_id"] in active_ids else (snapshot[0] if snapshot else None)
        for _ in range(5):
            token = secrets.token_urlsafe(24)
            try:
                cursor = await conn.execute(
                    """INSERT INTO client_galleries
                       (collection_id, token, title, layout, theme, cover_image_id, password_hash,
                        allow_download_all, download_size, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        int(collection_id), token, (title or "Client gallery").strip() or "Client gallery",
                        clean["layout"], clean["theme"], cover_image_id, password_hash,
                        int(clean["allow_download_all"]), clean["download_size"], now, now,
                    ),
                )
                gallery_id = int(cursor.lastrowid)
                await conn.executemany(
                    "INSERT INTO client_gallery_images (gallery_id, image_id, position) VALUES (?, ?, ?)",
                    [(gallery_id, image_id, position) for position, image_id in enumerate(snapshot)],
                )
                await conn.commit()
                return await get_gallery(db_path, gallery_id) or {"id": gallery_id, "token": token}
            except Exception as exc:
                if "UNIQUE constraint failed: client_galleries.token" not in str(exc):
                    raise
        raise RuntimeError("Could not allocate a unique gallery token")
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_gallery(db_path: str, gallery_id: int) -> dict[str, Any] | None:
    conn = await connection.open_async(db_path)
    try:
        await ensure_tables(conn)
        cursor = await conn.execute("SELECT * FROM client_galleries WHERE id = ?", (int(gallery_id),))
        row = await cursor.fetchone()
        return await _gallery_payload(conn, row) if row else None
    finally:
        await connection.close_async(conn, db_path=db_path)


async def resolve_token(db_path: str, token: str) -> dict[str, Any] | None:
    conn = await connection.open_async(db_path)
    try:
        await ensure_tables(conn)
        cursor = await conn.execute("SELECT * FROM client_galleries WHERE token = ?", (str(token),))
        row = await cursor.fetchone()
        return await _gallery_payload(conn, row) if row else None
    finally:
        await connection.close_async(conn, db_path=db_path)


async def list_galleries(db_path: str, collection_id: int) -> list[dict[str, Any]]:
    conn = await connection.open_async(db_path)
    try:
        await ensure_tables(conn)
        cursor = await conn.execute(
            "SELECT * FROM client_galleries WHERE collection_id = ? ORDER BY updated_at DESC, id DESC", (int(collection_id),)
        )
        return [await _gallery_payload(conn, row) for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)


async def update_gallery(
    db_path: str,
    gallery_id: int,
    *,
    title: str | None = None,
    options: dict[str, Any],
    password_hash: str | None | object = ...,
):
    clean = normalize_options(options)
    conn = await connection.open_async(db_path)
    try:
        await ensure_tables(conn)
        current = await _gallery_row(conn, gallery_id)
        if current is None:
            return None
        member_ids = {image["id"] for image in await _gallery_images(conn, int(gallery_id))}
        cover_image_id = clean["cover_image_id"] if clean["cover_image_id"] in member_ids else current["cover_image_id"]
        clean_title = (title or current["title"] or "Client gallery").strip() or current["title"]
        fields = ["title = ?", "layout = ?", "theme = ?", "cover_image_id = ?", "allow_download_all = ?", "download_size = ?", "updated_at = ?"]
        values: list[Any] = [clean_title, clean["layout"], clean["theme"], cover_image_id, int(clean["allow_download_all"]), clean["download_size"], time.time()]
        if password_hash is not ...:
            fields.append("password_hash = ?")
            values.append(password_hash)
        values.append(int(gallery_id))
        await conn.execute(f"UPDATE client_galleries SET {', '.join(fields)} WHERE id = ?", values)
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)
    return await get_gallery(db_path, gallery_id)


async def delete_gallery(db_path: str, gallery_id: int) -> bool:
    """Delete a gallery and its frozen membership, permanently invalidating its token."""
    conn = await connection.open_async(db_path)
    try:
        await ensure_tables(conn)
        cursor = await conn.execute("DELETE FROM client_galleries WHERE id = ?", (int(gallery_id),))
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await connection.close_async(conn, db_path=db_path)


async def gallery_allows_image(db_path: str, token: str, image_id: int) -> bool:
    conn = await connection.open_async(db_path)
    try:
        await ensure_tables(conn)
        cursor = await conn.execute(
            """SELECT 1 FROM client_galleries g JOIN client_gallery_images gi ON gi.gallery_id = g.id
               JOIN images i ON i.id = gi.image_id
               WHERE g.token = ? AND gi.image_id = ? AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL""",
            (str(token), int(image_id)),
        )
        return await cursor.fetchone() is not None
    finally:
        await connection.close_async(conn, db_path=db_path)


async def record_view(db_path: str, token: str) -> None:
    now = time.time()
    conn = await connection.open_async(db_path)
    try:
        await ensure_tables(conn)
        await conn.execute(
            """UPDATE client_galleries SET view_count = view_count + 1,
               first_viewed_at = COALESCE(first_viewed_at, ?), last_viewed_at = ? WHERE token = ?""",
            (now, now, str(token)),
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def image_file(db_path: str, token: str, image_id: int) -> dict[str, Any] | None:
    conn = await connection.open_async(db_path)
    try:
        await ensure_tables(conn)
        cursor = await conn.execute(
            """SELECT i.id, i.filename, i.filepath, i.hub_remote, i.hub_image_id,
                      source.path AS source_path FROM client_galleries g
               JOIN client_gallery_images gi ON gi.gallery_id = g.id
               JOIN images i ON i.id = gi.image_id
               LEFT JOIN catalog_sources source ON source.id = i.source_id
               WHERE g.token = ? AND i.id = ? AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL""",
            (str(token), int(image_id)),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await connection.close_async(conn, db_path=db_path)


async def _gallery_row(conn, gallery_id: int):
    cursor = await conn.execute("SELECT * FROM client_galleries WHERE id = ?", (int(gallery_id),))
    return await cursor.fetchone()


async def _gallery_images(conn, gallery_id: int) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """SELECT i.id, i.filename, i.filepath, i.width, i.height, i.date_taken,
                  i.hub_remote, i.hub_image_id,
                  source.path AS source_path, gi.position
           FROM client_gallery_images gi JOIN images i ON i.id = gi.image_id
           LEFT JOIN catalog_sources source ON source.id = i.source_id
           WHERE gi.gallery_id = ? AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL
           ORDER BY gi.position, gi.image_id""",
        (int(gallery_id),),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def _gallery_payload(conn, row) -> dict[str, Any]:
    gallery = dict(row)
    images = await _gallery_images(conn, int(gallery["id"]))
    return {
        "id": int(gallery["id"]), "collection_id": int(gallery["collection_id"]), "token": gallery["token"],
        "title": gallery["title"], "layout": gallery["layout"], "theme": gallery["theme"],
        "cover_image_id": int(gallery["cover_image_id"]) if gallery["cover_image_id"] is not None else None,
        "protected": bool(gallery["password_hash"]), "password_hash": gallery["password_hash"],
        "allow_download_all": bool(gallery["allow_download_all"]), "download_size": gallery["download_size"],
        "created_at": float(gallery["created_at"]), "updated_at": float(gallery["updated_at"]),
        "view_count": int(gallery["view_count"] or 0), "first_viewed_at": gallery["first_viewed_at"], "last_viewed_at": gallery["last_viewed_at"],
        "image_count": len(images), "images": images,
    }
