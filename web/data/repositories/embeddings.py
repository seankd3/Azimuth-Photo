"""Embedding SQL helpers."""

import time as _time

from data import connection


EMBEDDING_COUNT_CACHE_TTL_SECONDS = 10.0
_embedding_count_cache = {"key": None, "value": None, "expires": 0}


def invalidate_embedding_count_cache() -> None:
    _embedding_count_cache["key"] = None
    _embedding_count_cache["value"] = None
    _embedding_count_cache["expires"] = 0


def normalize_search_query(query: str) -> str:
    return " ".join(str(query or "").split())


async def _online_embedding_count_on_conn(conn, model_key: str, legacy_model_key: str = "") -> int:
    del legacy_model_key
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM embeddings_by_model e "
        "JOIN images i ON e.image_id = i.id "
        "JOIN catalog_sources s ON s.id = i.source_id "
        "WHERE e.model_key = ? AND s.included = 1 "
        "AND i.status IN ('kept', 'maybe') "
        "AND i.missing_at IS NULL",
        (model_key,),
    )
    return int((await cursor.fetchone())["c"] or 0)


async def ensure_embedding_model_tables(
    conn,
    *,
    active_config: dict,
    default_settings: dict,
    legacy_model_key: str,
    ensured_keys: set[str],
) -> None:
    del default_settings, legacy_model_key
    active_model_key = active_config["model_key"]
    if active_model_key in ensured_keys:
        return
    await conn.execute(
        "INSERT OR IGNORE INTO embedding_models "
        "(model_key, model_id, revision, dimension) VALUES (?, ?, ?, ?)",
        (
            active_model_key,
            active_config["model_id"],
            active_config["revision"],
            active_config["dimension"],
        ),
    )
    await conn.commit()
    ensured_keys.add(active_model_key)


async def ensure_embedding_model_row(conn, config: dict) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO embedding_models "
        "(model_key, model_id, revision, dimension) VALUES (?, ?, ?, ?)",
        (
            config["model_key"],
            config["model_id"],
            config["revision"],
            int(config["dimension"]),
        ),
    )


async def purge_retired_embedding_data(conn, *, active_config: dict) -> dict:
    await ensure_embedding_model_row(conn, active_config)
    active_key = active_config["model_key"]
    counts = {}
    for name, sql, params in (
        (
            "inactive_embeddings",
            "DELETE FROM embeddings_by_model WHERE model_key != ?",
            (active_key,),
        ),
        ("legacy_embeddings", "DELETE FROM embeddings", ()),
        (
            "inactive_search_query_embeddings",
            "DELETE FROM search_query_embeddings WHERE model_key != ?",
            (active_key,),
        ),
        (
            "inactive_models",
            "DELETE FROM embedding_models WHERE model_key != ?",
            (active_key,),
        ),
    ):
        cursor = await conn.execute(sql, params)
        counts[name] = max(0, int(cursor.rowcount or 0))
    await conn.commit()
    return counts


async def get_search_query_embedding(db_path: str, *, config: dict, query: str) -> bytes | None:
    normalized = normalize_search_query(query)
    if not normalized:
        return None
    key = normalized.casefold()
    now = _time.time()
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT embedding, dimension FROM search_query_embeddings "
            "WHERE model_key = ? AND query_key = ?",
            (config["model_key"], key),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if int(row["dimension"] or 0) != int(config["dimension"]):
            return None
        await conn.execute(
            "UPDATE search_query_embeddings SET last_used_at = ? "
            "WHERE model_key = ? AND query_key = ?",
            (now, config["model_key"], key),
        )
        await conn.commit()
        return row["embedding"]
    finally:
        await connection.close_async(conn, db_path=db_path)


async def store_search_query_embedding(
    db_path: str,
    *,
    config: dict,
    query: str,
    blob: bytes,
) -> str | None:
    normalized = normalize_search_query(query)
    if not normalized or not blob:
        return None
    key = normalized.casefold()
    now = _time.time()
    conn = await connection.open_async(db_path)
    try:
        await ensure_embedding_model_row(conn, config)
        await conn.execute(
            "INSERT INTO search_query_embeddings "
            "(model_key, query_key, query, embedding, dimension, created_at, last_used_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(model_key, query_key) DO UPDATE SET "
            "query = excluded.query, "
            "embedding = excluded.embedding, "
            "dimension = excluded.dimension, "
            "last_used_at = excluded.last_used_at, "
            "updated_at = excluded.updated_at",
            (
                config["model_key"],
                key,
                normalized,
                blob,
                int(config["dimension"]),
                now,
                now,
                now,
            ),
        )
        await conn.commit()
        return normalized
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_unembedded_images(
    db_path: str,
    *,
    embedding_config: dict,
    active_config: dict,
    default_settings: dict,
    legacy_model_key: str,
    ensured_keys: set[str],
    limit: int,
    md_cache_root: str = "",
    cache_size: str = "md",
    after_id: int = 0,
):
    model_key = embedding_config["model_key"]
    after_id = max(0, int(after_id or 0))
    after_id_filter = "AND i.id > ? " if after_id else ""
    conn = await connection.open_async(db_path)
    try:
        await ensure_embedding_model_tables(
            conn,
            active_config=active_config,
            default_settings=default_settings,
            legacy_model_key=legacy_model_key,
            ensured_keys=ensured_keys,
        )
        await ensure_embedding_model_row(conn, embedding_config)
        cache_size = cache_size if cache_size in {"sm", "md"} else "md"
        if md_cache_root:
            cursor = await conn.execute(
                "SELECT i.id, c.path AS filepath FROM images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "JOIN cache_entries c "
                "  ON c.cache_root = ? AND c.size = ? AND c.image_id = i.id "
                "WHERE s.included = 1 "
                "AND i.status IN ('kept', 'maybe') "
                "AND i.missing_at IS NULL "
                f"{after_id_filter}"
                "AND NOT EXISTS ("
                "  SELECT 1 FROM embeddings_by_model e "
                "  WHERE e.model_key = ? AND e.image_id = i.id"
                ") "
                "ORDER BY i.id ASC "
                "LIMIT ?",
                (
                    md_cache_root,
                    cache_size,
                    *((after_id,) if after_id else ()),
                    model_key,
                    limit,
                ),
            )
        else:
            cursor = await conn.execute(
                "SELECT i.id, i.filepath FROM images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 "
                "AND i.status IN ('kept', 'maybe') "
                "AND i.missing_at IS NULL "
                f"{after_id_filter}"
                "AND NOT EXISTS ("
                "  SELECT 1 FROM embeddings_by_model e "
                "  WHERE e.model_key = ? AND e.image_id = i.id"
                ") "
                "ORDER BY i.id ASC "
                "LIMIT ?",
                (
                    *((after_id,) if after_id else ()),
                    model_key,
                    limit,
                ),
            )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def store_embeddings_batch(
    db_path: str,
    *,
    rows: list[tuple[int, bytes]],
    embedding_config: dict,
    active_config: dict,
    default_settings: dict,
    legacy_model_key: str,
    ensured_keys: set[str],
    default_model_key: str,
) -> list[int]:
    del default_model_key
    if not rows:
        return []
    model_key = embedding_config["model_key"]
    dimension = int(embedding_config["dimension"])
    # Keep write transactions short so interactive saves aren't lock-stormed.
    write_chunk = 4
    conn = await connection.open_async(db_path)
    try:
        await ensure_embedding_model_tables(
            conn,
            active_config=active_config,
            default_settings=default_settings,
            legacy_model_key=legacy_model_key,
            ensured_keys=ensured_keys,
        )
        await ensure_embedding_model_row(conn, embedding_config)
        await conn.commit()
        stored: list[int] = []
        for start in range(0, len(rows), write_chunk):
            chunk = rows[start:start + write_chunk]
            await conn.executemany(
                "INSERT OR REPLACE INTO embeddings_by_model "
                "(model_key, image_id, embedding, dimension) VALUES (?, ?, ?, ?)",
                [(model_key, image_id, blob, dimension) for image_id, blob in chunk],
            )
            await conn.commit()
            stored.extend(int(image_id) for image_id, _blob in chunk)
        return stored
    finally:
        await connection.close_async(conn, db_path=db_path)


async def count_embeddings_for_model(
    db_path: str,
    *,
    embedding_config: dict,
    active_config: dict,
    default_settings: dict,
    legacy_model_key: str,
    ensured_keys: set[str],
    online_only: bool,
    catalog_counts: dict | None = None,
    active_source_ids: list[int] | None = None,
) -> int:
    model_key = embedding_config["model_key"]
    conn = await connection.open_async(db_path)
    try:
        if online_only:
            return await _online_embedding_count_on_conn(conn, model_key, legacy_model_key)
        await ensure_embedding_model_tables(
            conn,
            active_config=active_config,
            default_settings=default_settings,
            legacy_model_key=legacy_model_key,
            ensured_keys=ensured_keys,
        )
        await ensure_embedding_model_row(conn, embedding_config)

        active = int((catalog_counts or {}).get("active_images") or 0)
        if active <= 0:
            return 0
        all_catalog_images_active = (
            active == int((catalog_counts or {}).get("total_catalog_images") or 0)
            and int((catalog_counts or {}).get("removed_images") or 0) == 0
        )
        if all_catalog_images_active:
            cursor = await conn.execute(
                "SELECT COUNT(*) AS c FROM embeddings_by_model WHERE model_key = ?",
                (model_key,),
            )
        else:
            if not active_source_ids:
                return 0
            placeholders = ",".join("?" for _ in active_source_ids)
            cursor = await conn.execute(
                "SELECT COUNT(*) AS c FROM embeddings_by_model e "
                "JOIN images i ON e.image_id = i.id "
                f"WHERE e.model_key = ? AND i.source_id IN ({placeholders}) "
                "AND i.status IN ('kept', 'maybe') "
                "AND i.missing_at IS NULL",
                [model_key] + list(active_source_ids),
            )
        return int((await cursor.fetchone())["c"] or 0)
    finally:
        await connection.close_async(conn, db_path=db_path)


async def embedding_count_cached(
    *,
    active_embedding_config,
    count_embeddings_for_model,
    ttl_seconds: float = EMBEDDING_COUNT_CACHE_TTL_SECONDS,
) -> int:
    now = _time.time()
    embedding_config = active_embedding_config()
    model_key = embedding_config["model_key"]
    if (
        _embedding_count_cache["key"] in (model_key, None)
        and _embedding_count_cache["value"] is not None
        and now < _embedding_count_cache["expires"]
    ):
        return int(_embedding_count_cache["value"])
    count = await count_embeddings_for_model(embedding_config, online_only=True)
    _embedding_count_cache["key"] = model_key
    _embedding_count_cache["value"] = count
    _embedding_count_cache["expires"] = _time.time() + ttl_seconds
    return count


async def get_all_embeddings(
    db_path: str,
    *,
    model_key: str,
    active_config: dict,
    default_settings: dict,
    legacy_model_key: str,
    ensured_keys: set[str],
):
    conn = await connection.open_async(db_path)
    try:
        await ensure_embedding_model_tables(
            conn,
            active_config=active_config,
            default_settings=default_settings,
            legacy_model_key=legacy_model_key,
            ensured_keys=ensured_keys,
        )
        cursor = await conn.execute(
            "SELECT e.image_id, e.embedding FROM embeddings_by_model e "
            "JOIN images i ON e.image_id = i.id "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE e.model_key = ? "
            "AND s.included = 1 "
            "AND i.status IN ('kept', 'maybe') "
            "AND i.missing_at IS NULL",
            (model_key,),
        )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)
