"""Schema-driven cleanup for permanent image catalog deletion."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass

from data.repositories.common import chunked
from photo.visibility import visible_image_condition


@dataclass(frozen=True)
class ForeignKey:
    child_table: str
    parent_table: str
    columns: tuple[tuple[str, str], ...]
    on_delete: str


@dataclass(frozen=True)
class DependencyGraph:
    foreign_keys: tuple[ForeignKey, ...]
    owned_paths: tuple[tuple[ForeignKey, ...], ...]
    image_self_references: tuple[ForeignKey, ...]
    image_set_null_references: tuple[ForeignKey, ...]


_GRAPH_CACHE_MAX_ENTRIES = 32
_dependency_graph_cache: OrderedDict[tuple[str, int], DependencyGraph] = OrderedDict()
_PRESERVED_IMAGE_REFERENCES = {
    ("collections", "cover_image_id"),
    ("stacks", "representative_image_id"),
}


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


async def _schema_cache_key(conn) -> tuple[str, int]:
    row = await (await conn.execute("PRAGMA schema_version")).fetchone()
    schema_version = int(row[0] if row is not None else 0)
    rows = await (await conn.execute("PRAGMA database_list")).fetchall()
    main_path = next((str(row[2] or "") for row in rows if str(row[1]) == "main"), "")
    identity = main_path or f"connection:{id(conn)}"
    return identity, schema_version


async def _introspect_foreign_keys(conn) -> tuple[ForeignKey, ...]:
    cursor = await conn.execute(
        "SELECT name FROM sqlite_schema "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    tables = [str(row["name"]) for row in await cursor.fetchall()]
    foreign_keys: list[ForeignKey] = []
    for table in tables:
        cursor = await conn.execute(f"PRAGMA foreign_key_list({_quote_identifier(table)})")
        grouped: dict[int, list] = defaultdict(list)
        for row in await cursor.fetchall():
            grouped[int(row["id"])].append(row)
        for rows in grouped.values():
            ordered = sorted(rows, key=lambda row: int(row["seq"]))
            foreign_keys.append(
                ForeignKey(
                    child_table=table,
                    parent_table=str(ordered[0]["table"]),
                    columns=tuple((str(row["from"]), str(row["to"])) for row in ordered),
                    on_delete=str(ordered[0]["on_delete"] or "NO ACTION").upper(),
                )
            )
    return tuple(foreign_keys)


def _is_preserved_image_reference(foreign_key: ForeignKey) -> bool:
    return foreign_key.parent_table == "images" and any(
        (foreign_key.child_table, child_column) in _PRESERVED_IMAGE_REFERENCES
        for child_column, _parent_column in foreign_key.columns
    )


def _build_dependency_graph(foreign_keys: tuple[ForeignKey, ...]) -> DependencyGraph:
    by_parent: dict[str, list[ForeignKey]] = defaultdict(list)
    for foreign_key in foreign_keys:
        by_parent[foreign_key.parent_table].append(foreign_key)

    owned_paths: list[tuple[ForeignKey, ...]] = []

    def visit(parent_table: str, path_to_images: tuple[ForeignKey, ...], ancestors: frozenset[str]) -> None:
        for foreign_key in by_parent.get(parent_table, []):
            if foreign_key.child_table == "images":
                continue
            if foreign_key.on_delete == "SET NULL" or _is_preserved_image_reference(foreign_key):
                continue
            path = (foreign_key, *path_to_images)
            owned_paths.append(path)
            if foreign_key.child_table not in ancestors:
                visit(
                    foreign_key.child_table,
                    path,
                    ancestors | {foreign_key.child_table},
                )

    visit("images", (), frozenset({"images"}))
    owned_paths.sort(
        key=lambda path: (
            -len(path),
            path[0].child_table,
            tuple(column for column, _target in path[0].columns),
        )
    )
    image_references = tuple(
        foreign_key
        for foreign_key in foreign_keys
        if foreign_key.parent_table == "images"
        and any(parent_column == "id" for _child_column, parent_column in foreign_key.columns)
    )
    return DependencyGraph(
        foreign_keys=foreign_keys,
        owned_paths=tuple(owned_paths),
        image_self_references=tuple(
            foreign_key for foreign_key in image_references if foreign_key.child_table == "images"
        ),
        image_set_null_references=tuple(
            foreign_key
            for foreign_key in image_references
            if foreign_key.on_delete == "SET NULL" and foreign_key.child_table != "images"
        ),
    )


async def dependency_graph(conn) -> DependencyGraph:
    cache_key = await _schema_cache_key(conn)
    cached = _dependency_graph_cache.get(cache_key)
    if cached is not None:
        _dependency_graph_cache.move_to_end(cache_key)
        return cached
    graph = _build_dependency_graph(await _introspect_foreign_keys(conn))
    _dependency_graph_cache[cache_key] = graph
    _dependency_graph_cache.move_to_end(cache_key)
    while len(_dependency_graph_cache) > _GRAPH_CACHE_MAX_ENTRIES:
        _dependency_graph_cache.popitem(last=False)
    return graph


def _target_image_ids(ids: list[int]) -> tuple[str, tuple[int, ...]]:
    placeholders = ",".join("?" for _ in ids)
    return placeholders, tuple(ids)


async def _expand_owned_image_ids(
    conn,
    image_ids: list[int],
    self_references: tuple[ForeignKey, ...],
) -> list[int]:
    owned_references = [reference for reference in self_references if reference.on_delete != "SET NULL"]
    if not owned_references:
        return image_ids
    if any(len(reference.columns) != 1 or reference.columns[0][1] != "id" for reference in owned_references):
        raise RuntimeError("unsupported composite images self-reference")
    placeholders, params = _target_image_ids(image_ids)
    child_match = " OR ".join(
        f"child.{_quote_identifier(reference.columns[0][0])} = parent.id"
        for reference in owned_references
    )
    cursor = await conn.execute(
        "WITH RECURSIVE image_tree(id) AS ("
        f"SELECT id FROM images WHERE id IN ({placeholders}) "
        "UNION "
        f"SELECT child.id FROM images child JOIN image_tree parent ON ({child_match})"
        ") SELECT id FROM image_tree ORDER BY id",
        params,
    )
    return [int(row["id"]) for row in await cursor.fetchall()]


def _path_match_sql(path: tuple[ForeignKey, ...], image_ids: list[int]) -> tuple[str, tuple[int, ...]]:
    child = path[0]
    parent_aliases = [f"dependency_{index}" for index in range(1, len(path) + 1)]
    from_sql = f"{_quote_identifier(child.parent_table)} AS {parent_aliases[0]}"
    joins: list[str] = []
    for index, foreign_key in enumerate(path[1:], start=1):
        child_alias = parent_aliases[index - 1]
        parent_alias = parent_aliases[index]
        conditions = " AND ".join(
            f"{child_alias}.{_quote_identifier(child_column)} = "
            f"{parent_alias}.{_quote_identifier(parent_column)}"
            for child_column, parent_column in foreign_key.columns
        )
        joins.append(
            f"JOIN {_quote_identifier(foreign_key.parent_table)} AS {parent_alias} ON {conditions}"
        )
    direct_conditions = " AND ".join(
        f"{_quote_identifier(child.child_table)}.{_quote_identifier(child_column)} = "
        f"{parent_aliases[0]}.{_quote_identifier(parent_column)}"
        for child_column, parent_column in child.columns
    )
    placeholders, params = _target_image_ids(image_ids)
    root_alias = parent_aliases[-1]
    return (
        f"EXISTS (SELECT 1 FROM {from_sql} {' '.join(joins)} "
        f"WHERE {direct_conditions} AND {root_alias}.id IN ({placeholders}))",
        params,
    )


async def _repair_collection_covers(conn, image_ids: list[int]) -> None:
    for ids in chunked(image_ids):
        placeholders, params = _target_image_ids(ids)
        await conn.execute(
            "UPDATE collections SET cover_image_id = ("
            "  SELECT ci.image_id FROM collection_images ci "
            "  JOIN images i ON i.id = ci.image_id "
            "  WHERE ci.collection_id = collections.id "
            f"    AND ci.image_id NOT IN ({placeholders}) "
            f"    AND {visible_image_condition()} "
            "  ORDER BY ci.position ASC, ci.added_at ASC, ci.image_id ASC LIMIT 1"
            f") WHERE cover_image_id IN ({placeholders})",
            (*params, *params),
        )


async def _repair_stacks(conn, image_ids: list[int]) -> None:
    affected: set[int] = set()
    for ids in chunked(image_ids):
        placeholders, params = _target_image_ids(ids)
        cursor = await conn.execute(
            "SELECT id FROM stacks "
            f"WHERE representative_image_id IN ({placeholders}) "
            "UNION SELECT stack_id FROM stack_members "
            f"WHERE image_id IN ({placeholders})",
            (*params, *params),
        )
        affected.update(int(row["id"]) for row in await cursor.fetchall())
    target_ids = set(image_ids)
    for stack_id in sorted(affected):
        cursor = await conn.execute(
            "SELECT sm.image_id FROM stack_members sm "
            "JOIN images i ON i.id = sm.image_id "
            "WHERE sm.stack_id = ? "
            f"AND {visible_image_condition()} "
            "ORDER BY sm.score DESC, sm.image_id ASC",
            (stack_id,),
        )
        remaining_ids = [
            int(row["image_id"])
            for row in await cursor.fetchall()
            if int(row["image_id"]) not in target_ids
        ]
        if len(remaining_ids) <= 1:
            await conn.execute("DELETE FROM stack_members WHERE stack_id = ?", (stack_id,))
            await conn.execute("DELETE FROM stacks WHERE id = ?", (stack_id,))
            continue
        row = await (
            await conn.execute(
                "SELECT representative_image_id FROM stacks WHERE id = ?",
                (stack_id,),
            )
        ).fetchone()
        if row is not None and int(row["representative_image_id"]) in target_ids:
            await conn.execute(
                "UPDATE stacks SET representative_image_id = ?, "
                "updated_at = strftime('%s', 'now') WHERE id = ?",
                (remaining_ids[0], stack_id),
            )


async def _null_set_null_references(
    conn,
    image_ids: list[int],
    references: tuple[ForeignKey, ...],
) -> None:
    for reference in references:
        if any(parent_column != "id" for _child_column, parent_column in reference.columns):
            continue
        assignments = ", ".join(
            f"{_quote_identifier(child_column)} = NULL"
            for child_column, _parent_column in reference.columns
        )
        for ids in chunked(image_ids):
            placeholders, params = _target_image_ids(ids)
            match = " OR ".join(
                f"{_quote_identifier(child_column)} IN ({placeholders})"
                for child_column, _parent_column in reference.columns
            )
            await conn.execute(
                f"UPDATE {_quote_identifier(reference.child_table)} SET {assignments} WHERE {match}",
                params * len(reference.columns),
            )


async def _delete_owned_dependents(
    conn,
    image_ids: list[int],
    paths: tuple[tuple[ForeignKey, ...], ...],
) -> None:
    for path in paths:
        for ids in chunked(image_ids):
            match_sql, params = _path_match_sql(path, ids)
            await conn.execute(
                f"DELETE FROM {_quote_identifier(path[0].child_table)} WHERE {match_sql}",
                params,
            )


async def expand_image_deletion_ids(conn, image_ids: list[int]) -> list[int]:
    graph = await dependency_graph(conn)
    return await _expand_owned_image_ids(conn, image_ids, graph.image_self_references)


async def prepare_image_deletion(conn, expanded_image_ids: list[int]) -> None:
    """Remove or repair every schema-declared dependency of ``images(id)``."""

    graph = await dependency_graph(conn)
    await _repair_collection_covers(conn, expanded_image_ids)
    await _repair_stacks(conn, expanded_image_ids)
    await _null_set_null_references(conn, expanded_image_ids, graph.image_set_null_references)
    await _delete_owned_dependents(conn, expanded_image_ids, graph.owned_paths)
