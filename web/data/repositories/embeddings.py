"""Embedding and deep-search query SQL helpers."""

import time as _time

from data import connection


EMBEDDING_COUNT_CACHE_TTL_SECONDS = 10.0
_embedding_count_cache = {"key": None, "value": None, "expires": 0}


def invalidate_embedding_count_cache() -> None:
    _embedding_count_cache["key"] = None
    _embedding_count_cache["value"] = None
    _embedding_count_cache["expires"] = 0


def normalize_deep_search_query(query: str) -> str:
    return " ".join(str(query or "").split())


def deep_search_query_key(query: str) -> str:
    return normalize_deep_search_query(query).casefold()


async def ensure_embedding_model_tables(
    conn,
    *,
    active_config: dict,
    default_settings: dict,
    legacy_model_key: str,
    ensured_keys: set[str],
) -> None:
    active_model_key = active_config["model_key"]
    legacy_model_id = default_settings["embed_model_id"]
    legacy_revision = default_settings["embed_model_revision"]
    legacy_dimension = int(default_settings["embed_model_dim"])
    if active_model_key in ensured_keys:
        cursor = await conn.execute(
            "SELECT 1 FROM embeddings e "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM embeddings_by_model bm "
            "  WHERE bm.model_key = ? AND bm.image_id = e.image_id"
            ") LIMIT 1",
            (legacy_model_key,),
        )
        if await cursor.fetchone() is None:
            return
    cursor = await conn.execute(
        "SELECT model_key FROM embedding_models WHERE model_key IN (?, ?)",
        (active_model_key, legacy_model_key),
    )
    existing_keys = {row["model_key"] for row in await cursor.fetchall()}
    if active_model_key in existing_keys and legacy_model_key in existing_keys:
        cursor = await conn.execute(
            "SELECT 1 FROM embeddings e "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM embeddings_by_model bm "
            "  WHERE bm.model_key = ? AND bm.image_id = e.image_id"
            ") LIMIT 1",
            (legacy_model_key,),
        )
        if await cursor.fetchone() is None:
            ensured_keys.add(active_model_key)
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
    await conn.execute(
        "INSERT OR IGNORE INTO embedding_models "
        "(model_key, model_id, revision, dimension) VALUES (?, ?, ?, ?)",
        (legacy_model_key, legacy_model_id, legacy_revision, legacy_dimension),
    )
    await conn.execute(
        "INSERT OR IGNORE INTO embeddings_by_model "
        "(model_key, image_id, embedding, dimension, created_at) "
        "SELECT ?, image_id, embedding, ?, created_at FROM embeddings",
        (legacy_model_key, legacy_dimension),
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


async def record_deep_search_query(
    db_path: str,
    query: str,
    *,
    source: str = "search",
    pinned: bool = False,
) -> dict | None:
    normalized = normalize_deep_search_query(query)
    if not normalized:
        return None
    key = normalized.casefold()
    now = _time.time()
    source = source if source in {"search", "settings"} else "search"
    pinned_value = 1 if pinned else 0
    conn = await connection.open_async(db_path)
    try:
        await conn.execute(
            "INSERT INTO deep_search_queries "
            "(query_key, query, source, use_count, pinned, created_at, last_used_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(query_key) DO UPDATE SET "
            "query = excluded.query, "
            "source = CASE WHEN excluded.pinned = 1 THEN excluded.source ELSE deep_search_queries.source END, "
            "use_count = deep_search_queries.use_count + excluded.use_count, "
            "pinned = CASE WHEN excluded.pinned = 1 THEN 1 ELSE deep_search_queries.pinned END, "
            "last_used_at = COALESCE(excluded.last_used_at, deep_search_queries.last_used_at), "
            "updated_at = excluded.updated_at",
            (
                key,
                normalized,
                source,
                0 if pinned else 1,
                pinned_value,
                now,
                None if pinned else now,
                now,
            ),
        )
        await conn.commit()
        return {"query_key": key, "query": normalized}
    finally:
        await connection.close_async(conn, db_path=db_path)


async def sync_deep_search_terms(db_path: str, normalized_terms: list[str]) -> None:
    desired = {term.casefold(): term for term in normalized_terms}
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT query_key, query FROM deep_search_queries WHERE pinned = 1"
        )
        current = {
            str(row["query_key"]): str(row["query"])
            for row in await cursor.fetchall()
        }
        if current == desired:
            return

        await conn.execute("UPDATE deep_search_queries SET pinned = 0 WHERE pinned = 1")
        now = _time.time()
        for term in normalized_terms:
            key = term.casefold()
            await conn.execute(
                "INSERT INTO deep_search_queries "
                "(query_key, query, source, use_count, pinned, created_at, last_used_at, updated_at) "
                "VALUES (?, ?, 'settings', 0, 1, ?, NULL, ?) "
                "ON CONFLICT(query_key) DO UPDATE SET "
                "query = excluded.query, "
                "source = 'settings', "
                "pinned = 1, "
                "updated_at = excluded.updated_at",
                (key, term, now, now),
            )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_deep_search_query_embedding(db_path: str, query: str, model_key: str) -> bytes | None:
    key = deep_search_query_key(query)
    if not key:
        return None
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT embedding FROM deep_search_query_embeddings "
            "WHERE model_key = ? AND query_key = ?",
            (model_key, key),
        )
        row = await cursor.fetchone()
        return row["embedding"] if row else None
    finally:
        await connection.close_async(conn, db_path=db_path)


async def store_deep_search_query_embedding(
    db_path: str,
    *,
    config: dict,
    query: str,
    blob: bytes,
) -> str | None:
    normalized = normalize_deep_search_query(query)
    if not normalized:
        return None
    key = normalized.casefold()
    now = _time.time()
    conn = await connection.open_async(db_path)
    try:
        await ensure_embedding_model_row(conn, config)
        await conn.execute(
            "INSERT INTO deep_search_queries "
            "(query_key, query, source, use_count, pinned, created_at, updated_at) "
            "VALUES (?, ?, 'search', 0, 0, ?, ?) "
            "ON CONFLICT(query_key) DO UPDATE SET "
            "query = excluded.query, "
            "updated_at = excluded.updated_at",
            (key, normalized, now, now),
        )
        await conn.execute(
            "INSERT OR REPLACE INTO deep_search_query_embeddings "
            "(model_key, query_key, embedding, dimension, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM deep_search_query_embeddings "
            "WHERE model_key = ? AND query_key = ?), ?), ?)",
            (
                config["model_key"],
                key,
                blob,
                int(config["dimension"]),
                config["model_key"],
                key,
                now,
                now,
            ),
        )
        await conn.commit()
        return normalized
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_pending_deep_search_queries(
    db_path: str,
    *,
    config: dict,
    limit: int,
) -> list[dict]:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT q.query_key, q.query, q.source, q.use_count, q.pinned, q.last_used_at, q.updated_at "
            "FROM deep_search_queries q "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM deep_search_query_embeddings e "
            "  WHERE e.model_key = ? AND e.query_key = q.query_key"
            ") "
            "ORDER BY q.pinned DESC, q.last_used_at IS NULL ASC, q.last_used_at DESC, q.updated_at DESC "
            "LIMIT ?",
            (config["model_key"], limit),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_deep_search_cache_status(db_path: str, *, config: dict) -> dict:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN pinned = 1 THEN 1 ELSE 0 END) AS pinned, "
            "SUM(CASE WHEN use_count > 0 THEN 1 ELSE 0 END) AS used "
            "FROM deep_search_queries"
        )
        query_counts = await cursor.fetchone()
        cursor = await conn.execute(
            "SELECT COUNT(*) AS embedded FROM deep_search_query_embeddings WHERE model_key = ?",
            (config["model_key"],),
        )
        embedded = int((await cursor.fetchone())["embedded"] or 0)
        total = int(query_counts["total"] or 0)
        return {
            "model_key": config["model_key"],
            "model_id": config["model_id"],
            "dimension": int(config["dimension"]),
            "total_queries": total,
            "pinned_queries": int(query_counts["pinned"] or 0),
            "used_queries": int(query_counts["used"] or 0),
            "embedded_queries": embedded,
            "pending_queries": max(0, total - embedded),
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def list_deep_search_queries(db_path: str, *, config: dict, limit: int) -> list[dict]:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT q.query, q.source, q.use_count, q.pinned, q.last_used_at, q.updated_at, "
            "e.updated_at AS embedded_at "
            "FROM deep_search_queries q "
            "LEFT JOIN deep_search_query_embeddings e "
            "ON e.model_key = ? AND e.query_key = q.query_key "
            "ORDER BY q.pinned DESC, e.updated_at IS NULL DESC, q.last_used_at DESC, q.updated_at DESC "
            "LIMIT ?",
            (config["model_key"], limit),
        )
        rows = []
        for row in await cursor.fetchall():
            item = dict(row)
            item["cached"] = item.get("embedded_at") is not None
            rows.append(item)
        return rows
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
):
    model_key = embedding_config["model_key"]
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
                "AND i.missing_at IS NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM embeddings_by_model e "
                "  WHERE e.model_key = ? AND e.image_id = i.id"
                ") "
                "ORDER BY i.id ASC "
                "LIMIT ?",
                (md_cache_root, cache_size, model_key, limit),
            )
        else:
            cursor = await conn.execute(
                "SELECT i.id, i.filepath FROM images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 "
                "AND i.missing_at IS NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM embeddings_by_model e "
                "  WHERE e.model_key = ? AND e.image_id = i.id"
                ") "
                "ORDER BY i.id ASC "
                "LIMIT ?",
                (model_key, limit),
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
    if not rows:
        return []
    model_key = embedding_config["model_key"]
    dimension = int(embedding_config["dimension"])
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
        await conn.executemany(
            "INSERT OR REPLACE INTO embeddings_by_model "
            "(model_key, image_id, embedding, dimension) VALUES (?, ?, ?, ?)",
            [(model_key, image_id, blob, dimension) for image_id, blob in rows],
        )
        if model_key == default_model_key:
            await conn.executemany(
                "INSERT OR REPLACE INTO embeddings (image_id, embedding) VALUES (?, ?)",
                rows,
            )
        await conn.commit()
        return [int(image_id) for image_id, _blob in rows]
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
        await ensure_embedding_model_tables(
            conn,
            active_config=active_config,
            default_settings=default_settings,
            legacy_model_key=legacy_model_key,
            ensured_keys=ensured_keys,
        )
        await ensure_embedding_model_row(conn, embedding_config)
        if online_only:
            cursor = await conn.execute(
                "SELECT COUNT(*) AS c FROM embeddings_by_model e "
                "JOIN images i ON e.image_id = i.id "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE e.model_key = ? AND s.included = 1 "
                "AND i.missing_at IS NULL",
                (model_key,),
            )
            return int((await cursor.fetchone())["c"] or 0)

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
                f"WHERE e.model_key = ? AND i.source_id IN ({placeholders}) AND i.missing_at IS NULL",
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
            "AND s.included = 1 AND i.missing_at IS NULL",
            (model_key,),
        )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)
