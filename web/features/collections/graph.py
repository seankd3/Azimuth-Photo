"""Workspace collection graph links and ordered recursive membership."""

from __future__ import annotations

from core.stored_json import stored_object as _parse_query

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from data import connection as data_connection


ResolveSmartImageIds = Callable[[dict], Awaitable[list[int]]]
GetImagesByIds = Callable[[list[int]], Awaitable[dict[int, dict]]]


class CollectionGraphConflict(ValueError):
    pass


async def add_link(db_path: str, parent_id: int, child_id: int, position: int = 0) -> dict | None:
    parent_id = int(parent_id)
    child_id = int(child_id)
    if parent_id == child_id:
        raise CollectionGraphConflict("A collection cannot contain itself")

    conn = await data_connection.open_async(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        cursor = await conn.execute(
            "SELECT id FROM collections WHERE id IN (?, ?)",
            (parent_id, child_id),
        )
        found = {int(row["id"]) for row in await cursor.fetchall()}
        if parent_id not in found or child_id not in found:
            await conn.rollback()
            return None

        cursor = await conn.execute(
            """
            WITH RECURSIVE ancestors(id) AS (
                SELECT parent_id FROM collection_links WHERE child_id = ?
                UNION
                SELECT links.parent_id
                FROM collection_links links
                JOIN ancestors ON links.child_id = ancestors.id
            )
            SELECT 1 FROM ancestors WHERE id = ? LIMIT 1
            """,
            (parent_id, child_id),
        )
        if await cursor.fetchone() is not None:
            await conn.rollback()
            raise CollectionGraphConflict("Collection link would create a cycle")

        now = time.time()
        await conn.execute(
            """
            INSERT INTO collection_links (parent_id, child_id, position, added_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(parent_id, child_id) DO UPDATE SET position = excluded.position
            """,
            (parent_id, child_id, int(position), now),
        )
        await conn.commit()
        return {
            "parent_id": parent_id,
            "child_id": child_id,
            "position": int(position),
            "added_at": now,
        }
    except Exception:
        if conn.in_transaction:
            await conn.rollback()
        raise
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def delete_link(db_path: str, parent_id: int, child_id: int) -> bool | None:
    conn = await data_connection.open_async(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        cursor = await conn.execute("SELECT 1 FROM collections WHERE id = ?", (int(parent_id),))
        if await cursor.fetchone() is None:
            await conn.rollback()
            return None
        cursor = await conn.execute(
            "DELETE FROM collection_links WHERE parent_id = ? AND child_id = ?",
            (int(parent_id), int(child_id)),
        )
        await conn.commit()
        return bool(cursor.rowcount)
    except Exception:
        if conn.in_transaction:
            await conn.rollback()
        raise
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def workspace_tree(db_path: str) -> dict:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            """
            SELECT c.*, COUNT(ci.image_id) AS own_image_count
            FROM collections c
            LEFT JOIN collection_images ci ON ci.collection_id = c.id
            GROUP BY c.id
            ORDER BY c.created_at ASC, c.id ASC
            """
        )
        nodes = [_collection_node(dict(row)) for row in await cursor.fetchall()]
        cursor = await conn.execute(
            """
            SELECT parent_id, child_id, position, added_at
            FROM collection_links
            ORDER BY parent_id ASC, position ASC, added_at ASC, child_id ASC
            """
        )
        links = [dict(row) for row in await cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)

    child_ids = {int(link["child_id"]) for link in links}
    return {
        "nodes": nodes,
        "links": links,
        "root_ids": [int(node["id"]) for node in nodes if int(node["id"]) not in child_ids],
    }


async def collection_own_image_ids(
    db_path: str,
    collection_id: int,
    *,
    resolve_smart_image_ids: ResolveSmartImageIds,
) -> list[int] | None:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT query FROM collections WHERE id = ?", (int(collection_id),))
        row = await cursor.fetchone()
        if row is None:
            return None
        query = _parse_query(row["query"])
        if query is None:
            cursor = await conn.execute(
                """
                SELECT image_id FROM collection_images
                WHERE collection_id = ?
                ORDER BY position ASC, added_at ASC, image_id ASC
                """,
                (int(collection_id),),
            )
            return [int(image["image_id"]) for image in await cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return _unique_ids(await resolve_smart_image_ids(query or {}))


async def recursive_image_ids(
    db_path: str,
    collection_id: int,
    *,
    recursive: bool,
    resolve_smart_image_ids: ResolveSmartImageIds,
) -> list[int] | None:
    collection_id = int(collection_id)
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT id, query FROM collections")
        collections = {
            int(row["id"]): _parse_query(row["query"])
            for row in await cursor.fetchall()
        }
        if collection_id not in collections:
            return None
        cursor = await conn.execute(
            """
            SELECT parent_id, child_id
            FROM collection_links
            ORDER BY parent_id ASC, position ASC, added_at ASC, child_id ASC
            """
        )
        children: dict[int, list[int]] = defaultdict(list)
        for row in await cursor.fetchall():
            children[int(row["parent_id"])].append(int(row["child_id"]))
        cursor = await conn.execute(
            """
            SELECT collection_id, image_id
            FROM collection_images
            ORDER BY collection_id ASC, position ASC, added_at ASC, image_id ASC
            """
        )
        manual: dict[int, list[int]] = defaultdict(list)
        for row in await cursor.fetchall():
            manual[int(row["collection_id"])].append(int(row["image_id"]))
    finally:
        await data_connection.close_async(conn, db_path=db_path)

    ordered_collections: list[int] = []
    visited: set[int] = set()

    def visit(current_id: int, path: frozenset[int]) -> None:
        if current_id in path:
            raise CollectionGraphConflict("Collection link would create a cycle")
        if current_id in visited:
            return
        visited.add(current_id)
        ordered_collections.append(current_id)
        if recursive:
            for child_id in children.get(current_id, []):
                visit(child_id, path | {current_id})

    visit(collection_id, frozenset())
    image_ids: list[int] = []
    seen_images: set[int] = set()
    for current_id in ordered_collections:
        query = collections[current_id]
        own_ids = (
            _unique_ids(await resolve_smart_image_ids(query or {}))
            if query is not None
            else manual.get(current_id, [])
        )
        for image_id in own_ids:
            if image_id not in seen_images:
                seen_images.add(image_id)
                image_ids.append(image_id)
    return image_ids


async def recursive_images(
    db_path: str,
    collection_id: int,
    *,
    recursive: bool,
    resolve_smart_image_ids: ResolveSmartImageIds,
    get_images_by_ids: GetImagesByIds,
) -> tuple[list[int], list[dict]] | None:
    image_ids = await recursive_image_ids(
        db_path,
        collection_id,
        recursive=recursive,
        resolve_smart_image_ids=resolve_smart_image_ids,
    )
    if image_ids is None:
        return None
    rows = await get_images_by_ids(image_ids) if image_ids else {}
    return image_ids, [rows[image_id] for image_id in image_ids if image_id in rows]


def _collection_node(row: dict) -> dict:
    query = _parse_query(row.get("query"))
    return {
        "id": int(row["id"]),
        "name": row.get("name") or "Untitled collection",
        "description": row.get("description") or "",
        "visibility": row.get("visibility") or "private",
        "status": row.get("status") or "draft",
        "smart": query is not None,
        "query": query,
        "own_image_count": int(row.get("own_image_count") or 0),
        "cover_image_id": int(row["cover_image_id"]) if row.get("cover_image_id") is not None else None,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _unique_ids(values) -> list[int]:
    unique = []
    seen = set()
    for value in values or []:
        try:
            image_id = int(value)
        except (TypeError, ValueError):
            continue
        if image_id > 0 and image_id not in seen:
            seen.add(image_id)
            unique.append(image_id)
    return unique
