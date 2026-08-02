"""Frozen destination trees materialized from the workspace collection graph."""

from __future__ import annotations

from core.stored_json import stored_object as _parse_query

import re
import sqlite3
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from data import connection as data_connection


ResolveSmartImageIds = Callable[[dict], Awaitable[list[int]]]
VALID_AREAS = {"website", "private"}


class PublishedNodeConflict(ValueError):
    pass


class PublishedNodeNotFound(LookupError):
    pass


class PublishedNodeSourceDeleted(PublishedNodeConflict):
    pass


async def published_tree(db_path: str, area: str) -> dict:
    area = _valid_area(area)
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            """
            SELECT
                node.*,
                COUNT(DISTINCT image.image_id) AS image_count,
                share.token AS share_token,
                share.password_hash AS share_password_hash
            FROM published_nodes node
            LEFT JOIN published_node_images image ON image.node_id = node.id
            LEFT JOIN collection_shares share
                ON share.published_node_id = node.id AND share.revoked_at IS NULL
            WHERE node.area = ?
            GROUP BY node.id
            ORDER BY node.parent_id ASC, node.position ASC, node.created_at ASC, node.id ASC
            """,
            (area,),
        )
        nodes = [_node_payload(dict(row)) for row in await cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return {
        "area": area,
        "nodes": nodes,
        "links": [
            {
                "parent_id": int(node["parent_id"]),
                "child_id": int(node["id"]),
                "position": int(node["position"]),
            }
            for node in nodes
            if node["parent_id"] is not None
        ],
        "root_ids": [int(node["id"]) for node in nodes if node["parent_id"] is None],
    }


async def get_node(db_path: str, node_id: int) -> dict | None:
    conn = await data_connection.open_async(db_path)
    try:
        return await _get_node_on_conn(conn, int(node_id))
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def create_snapshot_tree(
    db_path: str,
    *,
    area: str,
    parent_id: int | None,
    source_collection_id: int,
    slug: str | None,
    title: str | None,
    resolve_smart_image_ids: ResolveSmartImageIds,
) -> dict | None:
    area = _valid_area(area)
    spec = await _source_tree_spec(
        db_path,
        int(source_collection_id),
        resolve_smart_image_ids=resolve_smart_image_ids,
    )
    if spec is None:
        return None

    conn = await data_connection.open_async(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        clean_parent = int(parent_id) if parent_id is not None else None
        if clean_parent is not None:
            parent = await _get_node_on_conn(conn, clean_parent)
            if parent is None:
                raise PublishedNodeNotFound("Published parent not found")
            if parent["area"] != area:
                raise PublishedNodeConflict("Published parent belongs to a different area")
        position = await _next_position(conn, area, clean_parent)
        node_id = await _materialize_spec(
            conn,
            spec,
            area=area,
            parent_id=clean_parent,
            position=position,
            slug_override=slug,
            title_override=title,
        )
        await conn.commit()
    except sqlite3.IntegrityError as exc:
        if conn.in_transaction:
            await conn.rollback()
        raise PublishedNodeConflict("Slug is already used under this destination") from exc
    except Exception:
        if conn.in_transaction:
            await conn.rollback()
        raise
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return await get_node(db_path, node_id)


async def patch_node(db_path: str, node_id: int, fields: dict) -> dict | None:
    conn = await data_connection.open_async(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        node = await _get_node_on_conn(conn, int(node_id))
        if node is None:
            await conn.rollback()
            return None

        updates: list[str] = []
        values: list[object] = []
        target_parent = node["parent_id"]
        if "parent_id" in fields:
            target_parent = int(fields["parent_id"]) if fields["parent_id"] is not None else None
            await _validate_destination_parent(conn, node, target_parent)
            updates.append("parent_id = ?")
            values.append(target_parent)
        if "slug" in fields:
            clean_slug = _slugify(str(fields["slug"] or ""))
            if not clean_slug:
                raise PublishedNodeConflict("slug is required")
            updates.append("slug = ?")
            values.append(clean_slug)
        if "title" in fields:
            clean_title = str(fields["title"] or "").strip()
            if not clean_title:
                raise PublishedNodeConflict("title is required")
            updates.append("title = ?")
            values.append(clean_title[:160])
        if "position" in fields:
            updates.append("position = ?")
            values.append(int(fields["position"]))
        if updates:
            updates.append("updated_at = ?")
            values.extend([time.time(), int(node_id)])
            await conn.execute(
                f"UPDATE published_nodes SET {', '.join(updates)} WHERE id = ?",
                values,
            )
        await conn.commit()
    except sqlite3.IntegrityError as exc:
        if conn.in_transaction:
            await conn.rollback()
        raise PublishedNodeConflict("Slug is already used under this destination") from exc
    except Exception:
        if conn.in_transaction:
            await conn.rollback()
        raise
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return await get_node(db_path, node_id)


async def delete_node(db_path: str, node_id: int) -> bool:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute("DELETE FROM published_nodes WHERE id = ?", (int(node_id),))
        await conn.commit()
        return bool(cursor.rowcount)
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def node_diff(
    db_path: str,
    node_id: int,
    *,
    resolve_smart_image_ids: ResolveSmartImageIds,
) -> dict | None:
    conn = await data_connection.open_async(db_path)
    try:
        return await _node_diff_on_conn(
            conn,
            int(node_id),
            resolve_smart_image_ids=resolve_smart_image_ids,
        )
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def _node_diff_on_conn(
    conn,
    node_id: int,
    *,
    resolve_smart_image_ids: ResolveSmartImageIds,
) -> dict | None:
    node = await _get_node_on_conn(conn, int(node_id))
    if node is None:
        return None
    source_id = node.get("source_collection_id")
    if source_id is None:
        return {
            "node_id": int(node_id),
            "source_deleted": True,
            "added": [],
            "removed": [],
            "attachable_children": [],
            "attachable_child_nodes": [],
        }

    cursor = await conn.execute(
        "SELECT query FROM collections WHERE id = ?",
        (int(source_id),),
    )
    source = await cursor.fetchone()
    if source is None:
        return {
            "node_id": int(node_id),
            "source_deleted": True,
            "added": [],
            "removed": [],
            "attachable_children": [],
            "attachable_child_nodes": [],
        }

    query = _parse_query(source["query"])
    if query is None:
        cursor = await conn.execute(
            """
            SELECT membership.image_id FROM collection_images membership
            JOIN images i ON i.id = membership.image_id
            JOIN catalog_sources source ON source.id = i.source_id AND source.included = 1
            WHERE membership.collection_id = ?
              AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL
            ORDER BY position ASC, added_at ASC, image_id ASC
            """,
            (int(source_id),),
        )
        current_ids = [int(row["image_id"]) for row in await cursor.fetchall()]
    else:
        current_ids = _unique_ids(await resolve_smart_image_ids(query))

    snapshot_ids = await _node_image_ids_on_conn(conn, int(node_id))
    cursor = await conn.execute(
        """
        SELECT links.child_id, links.position, collections.name
        FROM collection_links links
        JOIN collections ON collections.id = links.child_id
        WHERE links.parent_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM published_nodes child
              WHERE child.parent_id = ?
                AND child.source_collection_id = links.child_id
          )
        ORDER BY links.position ASC, links.added_at ASC, links.child_id ASC
        """,
        (int(source_id), int(node_id)),
    )
    attachable = [
        {
            "collection_id": int(row["child_id"]),
            "title": row["name"] or "Untitled collection",
            "position": int(row["position"]),
        }
        for row in await cursor.fetchall()
    ]

    current_set = set(current_ids)
    snapshot_set = set(snapshot_ids)
    return {
        "node_id": int(node_id),
        "source_collection_id": int(source_id),
        "source_deleted": False,
        "added": [image_id for image_id in current_ids if image_id not in snapshot_set],
        "removed": [image_id for image_id in snapshot_ids if image_id not in current_set],
        "attachable_children": [item["collection_id"] for item in attachable],
        "attachable_child_nodes": attachable,
    }


async def update_node(
    db_path: str,
    node_id: int,
    *,
    add_image_ids: list[int],
    remove_image_ids: list[int],
    attach_child_collection_ids: list[int],
    resolve_smart_image_ids: ResolveSmartImageIds,
) -> dict | None:
    add_ids = _unique_ids(add_image_ids)
    remove_ids = _unique_ids(remove_image_ids)
    attach_ids = _unique_ids(attach_child_collection_ids)

    conn = await data_connection.open_async(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        diff = await _node_diff_on_conn(
            conn,
            int(node_id),
            resolve_smart_image_ids=resolve_smart_image_ids,
        )
        if diff is None:
            await conn.rollback()
            return None
        if diff["source_deleted"]:
            raise PublishedNodeSourceDeleted("Source collection was deleted")
        if not set(add_ids).issubset(diff["added"]):
            raise PublishedNodeConflict("Accepted additions must come from the current diff")
        if not set(remove_ids).issubset(diff["removed"]):
            raise PublishedNodeConflict("Accepted removals must come from the current diff")
        if not set(attach_ids).issubset(diff["attachable_children"]):
            raise PublishedNodeConflict("Accepted child collections must come from the current diff")

        attach_positions = {
            int(item["collection_id"]): int(item["position"])
            for item in diff["attachable_child_nodes"]
        }
        specs = []
        for collection_id in attach_ids:
            spec = await _source_tree_spec_on_conn(
                conn,
                collection_id,
                resolve_smart_image_ids=resolve_smart_image_ids,
            )
            if spec is None:
                raise PublishedNodeConflict("An accepted child collection no longer exists")
            specs.append(spec)

        node = await _get_node_on_conn(conn, int(node_id))
        if remove_ids:
            await conn.executemany(
                "DELETE FROM published_node_images WHERE node_id = ? AND image_id = ?",
                [(int(node_id), image_id) for image_id in remove_ids],
            )
        if add_ids:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(position), -1) AS position FROM published_node_images WHERE node_id = ?",
                (int(node_id),),
            )
            next_position = int((await cursor.fetchone())["position"]) + 1
            now = time.time()
            await conn.executemany(
                """
                INSERT OR IGNORE INTO published_node_images (node_id, image_id, position, added_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (int(node_id), image_id, next_position + index, now)
                    for index, image_id in enumerate(add_ids)
                ],
            )
        for spec in specs:
            await _materialize_spec(
                conn,
                spec,
                area=node["area"],
                parent_id=int(node_id),
                position=attach_positions[int(spec["collection_id"])],
            )
        await conn.execute(
            "UPDATE published_nodes SET updated_at = ? WHERE id = ?",
            (time.time(), int(node_id)),
        )
        await conn.commit()
    except sqlite3.IntegrityError as exc:
        if conn.in_transaction:
            await conn.rollback()
        error_name = str(getattr(exc, "sqlite_errorname", "") or "")
        if "FOREIGNKEY" in error_name:
            message = "Destination update references an image or collection that no longer exists"
        elif "UNIQUE" in error_name:
            message = "Slug is already used under this destination"
        else:
            message = "Destination update conflicted with a newer change"
        raise PublishedNodeConflict(message) from exc
    except Exception:
        if conn.in_transaction:
            await conn.rollback()
        raise
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return await get_node(db_path, node_id)


async def node_images(db_path: str, node_id: int) -> list[dict] | None:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT 1 FROM published_nodes WHERE id = ?", (int(node_id),))
        if await cursor.fetchone() is None:
            return None
        cursor = await conn.execute(
            """
            SELECT
                i.*,
                COALESCE(
                    (
                        SELECT caption.caption
                        FROM image_captions caption
                        WHERE caption.image_id = i.id
                        ORDER BY caption.user_edited DESC, caption.created_at DESC, caption.model_key ASC
                        LIMIT 1
                    ),
                    ''
                ) AS caption
            FROM published_node_images membership
            JOIN images i ON i.id = membership.image_id
            JOIN catalog_sources source ON source.id = i.source_id AND source.included = 1
            WHERE membership.node_id = ?
              AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL
            ORDER BY membership.position ASC, membership.added_at ASC, membership.image_id ASC
            """,
            (int(node_id),),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def _source_tree_spec(
    db_path: str,
    source_collection_id: int,
    *,
    resolve_smart_image_ids: ResolveSmartImageIds,
) -> dict | None:
    conn = await data_connection.open_async(db_path)
    try:
        return await _source_tree_spec_on_conn(
            conn,
            int(source_collection_id),
            resolve_smart_image_ids=resolve_smart_image_ids,
        )
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def _source_tree_spec_on_conn(
    conn,
    source_collection_id: int,
    *,
    resolve_smart_image_ids: ResolveSmartImageIds,
) -> dict | None:
    cursor = await conn.execute("SELECT id, name, query FROM collections")
    collections = {
        int(row["id"]): {
            "collection_id": int(row["id"]),
            "title": row["name"] or "Untitled collection",
            "query": _parse_query(row["query"]),
        }
        for row in await cursor.fetchall()
    }
    if int(source_collection_id) not in collections:
        return None
    cursor = await conn.execute(
        """
        SELECT parent_id, child_id, position
        FROM collection_links
        ORDER BY parent_id ASC, position ASC, added_at ASC, child_id ASC
        """
    )
    child_links: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for row in await cursor.fetchall():
        child_links[int(row["parent_id"])].append((int(row["child_id"]), int(row["position"])))
    cursor = await conn.execute(
        """
        SELECT membership.collection_id, membership.image_id
        FROM collection_images membership
        JOIN images i ON i.id = membership.image_id
        JOIN catalog_sources source ON source.id = i.source_id AND source.included = 1
        WHERE i.status IN ('kept', 'maybe') AND i.missing_at IS NULL
        ORDER BY collection_id ASC, position ASC, added_at ASC, image_id ASC
        """
    )
    manual_ids: dict[int, list[int]] = defaultdict(list)
    for row in await cursor.fetchall():
        manual_ids[int(row["collection_id"])].append(int(row["image_id"]))

    own_ids_cache: dict[int, list[int]] = {}

    async def own_ids(collection_id: int) -> list[int]:
        if collection_id not in own_ids_cache:
            query = collections[collection_id]["query"]
            own_ids_cache[collection_id] = (
                _unique_ids(await resolve_smart_image_ids(query or {}))
                if query is not None
                else list(manual_ids.get(collection_id, []))
            )
        return own_ids_cache[collection_id]

    async def build(collection_id: int, path: frozenset[int]) -> dict:
        if collection_id in path:
            raise PublishedNodeConflict("Workspace collection graph contains a cycle")
        source = collections[collection_id]
        children = []
        for child_id, position in child_links.get(collection_id, []):
            child = await build(child_id, path | {collection_id})
            child["position"] = position
            children.append(child)
        return {
            "collection_id": collection_id,
            "title": source["title"],
            "image_ids": await own_ids(collection_id),
            "children": children,
            "position": 0,
        }

    return await build(int(source_collection_id), frozenset())


async def _materialize_spec(
    conn,
    spec: dict,
    *,
    area: str,
    parent_id: int | None,
    position: int,
    slug_override: str | None = None,
    title_override: str | None = None,
) -> int:
    base_slug = _slugify(slug_override if slug_override is not None else spec["title"]) or "collection"
    slug = await _unique_slug(conn, area, parent_id, base_slug)
    title = str(title_override if title_override is not None else spec["title"]).strip() or spec["title"]
    now = time.time()
    cursor = await conn.execute(
        """
        INSERT INTO published_nodes
            (area, parent_id, slug, title, source_collection_id, position, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (area, parent_id, slug, title[:160], int(spec["collection_id"]), int(position), now, now),
    )
    node_id = int(cursor.lastrowid)
    image_ids = _unique_ids(spec.get("image_ids"))
    if image_ids:
        await conn.executemany(
            """
            INSERT INTO published_node_images (node_id, image_id, position, added_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                (node_id, image_id, image_position, now)
                for image_position, image_id in enumerate(image_ids)
            ],
        )
    for child in spec.get("children") or []:
        await _materialize_spec(
            conn,
            child,
            area=area,
            parent_id=node_id,
            position=int(child.get("position") or 0),
        )
    return node_id


async def _get_node_on_conn(conn, node_id: int) -> dict | None:
    cursor = await conn.execute(
        """
        SELECT
            node.*,
            COUNT(DISTINCT active_image.id) AS image_count,
            share.token AS share_token,
            share.password_hash AS share_password_hash
        FROM published_nodes node
        LEFT JOIN published_node_images image ON image.node_id = node.id
        LEFT JOIN images active_image ON active_image.id = image.image_id
            AND active_image.status IN ('kept', 'maybe') AND active_image.missing_at IS NULL
            AND EXISTS (
                SELECT 1 FROM catalog_sources source
                WHERE source.id = active_image.source_id AND source.included = 1
            )
        LEFT JOIN collection_shares share
            ON share.published_node_id = node.id AND share.revoked_at IS NULL
        WHERE node.id = ?
        GROUP BY node.id
        """,
        (int(node_id),),
    )
    row = await cursor.fetchone()
    return _node_payload(dict(row)) if row is not None else None


async def _node_image_ids_on_conn(conn, node_id: int) -> list[int]:
    cursor = await conn.execute(
        """
        SELECT image_id FROM published_node_images
        WHERE node_id = ?
        ORDER BY position ASC, added_at ASC, image_id ASC
        """,
        (int(node_id),),
    )
    return [int(row["image_id"]) for row in await cursor.fetchall()]


async def _validate_destination_parent(conn, node: dict, parent_id: int | None) -> None:
    if parent_id is None:
        return
    if parent_id == int(node["id"]):
        raise PublishedNodeConflict("A published node cannot contain itself")
    parent = await _get_node_on_conn(conn, parent_id)
    if parent is None:
        raise PublishedNodeNotFound("Published parent not found")
    if parent["area"] != node["area"]:
        raise PublishedNodeConflict("Published parent belongs to a different area")
    current = parent
    while current is not None:
        if int(current["id"]) == int(node["id"]):
            raise PublishedNodeConflict("Published node move would create a cycle")
        ancestor_id = current.get("parent_id")
        current = await _get_node_on_conn(conn, int(ancestor_id)) if ancestor_id is not None else None


async def _next_position(conn, area: str, parent_id: int | None) -> int:
    if parent_id is None:
        cursor = await conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS position FROM published_nodes WHERE area = ? AND parent_id IS NULL",
            (area,),
        )
    else:
        cursor = await conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS position FROM published_nodes WHERE area = ? AND parent_id = ?",
            (area, int(parent_id)),
        )
    return int((await cursor.fetchone())["position"]) + 1


async def _unique_slug(conn, area: str, parent_id: int | None, base: str) -> str:
    candidate = base[:96]
    index = 1
    while await _slug_exists(conn, area, parent_id, candidate):
        index += 1
        suffix = f"-{index}"
        candidate = f"{base[:96 - len(suffix)]}{suffix}"
    return candidate


async def _slug_exists(conn, area: str, parent_id: int | None, slug: str) -> bool:
    if parent_id is None:
        cursor = await conn.execute(
            "SELECT 1 FROM published_nodes WHERE area = ? AND parent_id IS NULL AND slug = ? LIMIT 1",
            (area, slug),
        )
    else:
        cursor = await conn.execute(
            "SELECT 1 FROM published_nodes WHERE area = ? AND parent_id = ? AND slug = ? LIMIT 1",
            (area, int(parent_id), slug),
        )
    return await cursor.fetchone() is not None


def _node_payload(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "area": row["area"],
        "parent_id": int(row["parent_id"]) if row.get("parent_id") is not None else None,
        "slug": row["slug"],
        "title": row["title"],
        "source_collection_id": (
            int(row["source_collection_id"])
            if row.get("source_collection_id") is not None
            else None
        ),
        "position": int(row.get("position") or 0),
        "image_count": int(row.get("image_count") or 0),
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
        "share_token": row.get("share_token"),
        "share_protected": bool(row.get("share_password_hash")),
    }


def _valid_area(area: str) -> str:
    clean = str(area or "").strip().lower()
    if clean not in VALID_AREAS:
        raise PublishedNodeConflict("area must be website or private")
    return clean


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")[:96]


def _unique_ids(values) -> list[int]:
    unique = []
    seen = set()
    for value in values or []:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique
