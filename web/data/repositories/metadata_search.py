"""Metadata text-search helpers backed by the metadata FTS index."""

from data import connection
from data.repositories.common import chunked as _chunked


def metadata_fts_query(text_query: str) -> str:
    """Build an FTS query that ANDs per-word substring terms.

    Each token becomes its own quoted term so multi-word queries match across
    different fields/positions instead of requiring one exact phrase. Tokens
    under 3 characters are dropped (the trigram tokenizer cannot match them);
    the LIKE refinement applied downstream still enforces them.
    """
    tokens = [t for t in (text_query or "").split() if len(t) >= 3]
    if not tokens:
        return '"' + (text_query or "").replace('"', '""') + '"'
    return " ".join('"' + token.replace('"', '""') + '"' for token in tokens)


async def metadata_search_image_ids(
    db_path: str,
    text_query: str,
    *,
    active_source_ids,
    max_results: int = 5000,
) -> set[int] | None:
    """Return bounded active image IDs for metadata search.

    ``None`` means callers should fall back to slower LIKE filtering. An empty
    set is a real no-match result.
    """

    query = (text_query or "").strip()
    if len(query) < 3:
        return None
    active_source_ids = sorted(int(source_id) for source_id in active_source_ids)
    if not active_source_ids:
        return set()

    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT rowid AS id FROM images_metadata_fts "
            "WHERE images_metadata_fts MATCH ? LIMIT ?",
            [metadata_fts_query(query), int(max_results) + 1],
        )
        candidate_ids = [int(row["id"]) for row in await cursor.fetchall()]
        if len(candidate_ids) > int(max_results):
            return None
        if not candidate_ids:
            return set()

        active_ids: set[int] = set()
        source_placeholders = ",".join("?" for _ in active_source_ids)
        for chunk in _chunked(candidate_ids, 900):
            id_placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                "SELECT i.id FROM images i "
                f"WHERE i.id IN ({id_placeholders}) "
                f"AND i.source_id IN ({source_placeholders}) "
                "AND i.status IN ('kept', 'maybe') "
                "AND i.missing_at IS NULL",
                [*chunk, *active_source_ids],
            )
            active_ids.update(int(row["id"]) for row in await cursor.fetchall())
        return active_ids
    except Exception:
        return None
    finally:
        await connection.close_async(conn, db_path=db_path)


async def metadata_search_ranked_image_ids(
    db_path: str,
    text_query: str,
    *,
    active_source_ids,
    max_results: int = 5000,
) -> list[tuple[int, float]]:
    query = (text_query or "").strip()
    if len(query) < 3:
        return []
    active_source_ids = sorted(int(source_id) for source_id in active_source_ids)
    if not active_source_ids:
        return []

    conn = await connection.open_async(db_path)
    try:
        source_placeholders = ",".join("?" for _ in active_source_ids)
        cursor = await conn.execute(
            "SELECT f.rowid AS id, bm25(images_metadata_fts) AS score "
            "FROM images_metadata_fts f "
            "JOIN images i ON i.id = f.rowid "
            f"WHERE i.source_id IN ({source_placeholders}) "
            "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
            "AND images_metadata_fts MATCH ? "
            "ORDER BY score ASC LIMIT ?",
            (*active_source_ids, metadata_fts_query(query), int(max_results)),
        )
        return [(int(row["id"]), -float(row["score"] or 0.0)) for row in await cursor.fetchall()]
    except Exception:
        return []
    finally:
        await connection.close_async(conn, db_path=db_path)
