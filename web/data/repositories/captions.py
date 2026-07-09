"""Caption storage, scan ledger, and caption FTS helpers."""

from __future__ import annotations

import json
import time

from data import connection
from data.repositories.common import chunked as _chunked
from data.repositories.metadata_search import metadata_fts_query


ERROR_RETRY_AFTER_SECONDS = 24 * 60 * 60


def normalize_tags(tags) -> list[str]:
    if not isinstance(tags, list):
        return []
    normalized: list[str] = []
    for tag in tags:
        text = str(tag or "").strip().lower()
        if text and text not in normalized:
            normalized.append(text)
    return normalized[:20]


def tags_json(tags) -> str:
    return json.dumps(normalize_tags(tags), ensure_ascii=True)


async def ensure_active_caption_fts_model(db_path: str, model_key: str) -> None:
    model_key = str(model_key or "")
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT model_key FROM caption_fts_model WHERE id = 1"
        )
        row = await cursor.fetchone()
        current = str(row["model_key"] if row else "")
        if current == model_key:
            return
        await conn.execute("BEGIN")
        await conn.execute(
            "INSERT OR REPLACE INTO caption_fts_model (id, model_key) VALUES (1, ?)",
            (model_key,),
        )
        await conn.execute("DELETE FROM image_captions_fts")
        cursor = await conn.execute(
            "SELECT image_id, caption, tags FROM image_captions WHERE model_key = ?",
            (model_key,),
        )
        rows = await cursor.fetchall()
        if rows:
            await conn.executemany(
                "INSERT INTO image_captions_fts(rowid, caption, tags) VALUES (?, ?, ?)",
                [(row["image_id"], row["caption"], row["tags"]) for row in rows],
            )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=db_path)


async def store_caption_result(
    db_path: str,
    *,
    image_id: int,
    model_key: str,
    caption: str,
    tags,
    quality: str | None = None,
    status: str = "done",
    error: str = "",
) -> None:
    now = time.time()
    tags_text = tags_json(tags)
    conn = await connection.open_async(db_path)
    try:
        await conn.execute("BEGIN")
        if status == "done":
            await conn.execute(
                "INSERT INTO image_captions "
                "(image_id, model_key, caption, tags, quality, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(model_key, image_id) DO UPDATE SET "
                "caption = excluded.caption, tags = excluded.tags, "
                "quality = excluded.quality, created_at = excluded.created_at",
                (int(image_id), model_key, str(caption or ""), tags_text, quality, now),
            )
        await conn.execute(
            "INSERT INTO caption_scan_images "
            "(image_id, model_key, status, last_error, scanned_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(image_id, model_key) DO UPDATE SET "
            "status = excluded.status, last_error = excluded.last_error, "
            "scanned_at = excluded.scanned_at",
            (int(image_id), model_key, status, str(error or ""), now),
        )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_images_needing_captions(
    db_path: str,
    *,
    model_key: str,
    cache_root: str,
    cache_size: str = "md",
    limit: int = 8,
) -> list[dict]:
    retry_before = time.time() - ERROR_RETRY_AFTER_SECONDS
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT i.id, i.filepath, i.filename, ce.path AS cache_path "
            "FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "JOIN cache_entries ce ON ce.image_id = i.id "
            "AND ce.cache_root = ? AND ce.size = ? "
            "LEFT JOIN caption_scan_images csi "
            "ON csi.image_id = i.id AND csi.model_key = ? "
            "WHERE s.included = 1 AND i.status IN ('kept', 'maybe') "
            "AND i.missing_at IS NULL "
            "AND (csi.status IS NULL OR csi.status = 'pending' "
            "OR (csi.status = 'error' AND csi.scanned_at <= ?)) "
            "ORDER BY CASE WHEN i.flag = 'picked' THEN 0 ELSE 1 END, "
            "(i.date_taken IS NULL), i.date_taken DESC, i.id DESC "
            "LIMIT ?",
            (cache_root, cache_size, model_key, retry_before, max(1, int(limit))),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)


async def count_images_needing_captions(
    db_path: str,
    *,
    model_key: str,
    cache_root: str,
    cache_size: str = "md",
) -> int:
    retry_before = time.time() - ERROR_RETRY_AFTER_SECONDS
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS c "
            "FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "JOIN cache_entries ce ON ce.image_id = i.id "
            "AND ce.cache_root = ? AND ce.size = ? "
            "LEFT JOIN caption_scan_images csi "
            "ON csi.image_id = i.id AND csi.model_key = ? "
            "WHERE s.included = 1 AND i.status IN ('kept', 'maybe') "
            "AND i.missing_at IS NULL "
            "AND (csi.status IS NULL OR csi.status = 'pending' "
            "OR (csi.status = 'error' AND csi.scanned_at <= ?))",
            (cache_root, cache_size, model_key, retry_before),
        )
        row = await cursor.fetchone()
        return int(row["c"] if row else 0)
    finally:
        await connection.close_async(conn, db_path=db_path)


async def caption_status_counts(db_path: str, *, model_key: str, cache_root: str) -> dict:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS c FROM image_captions WHERE model_key = ?",
            (model_key,),
        )
        captioned = int((await cursor.fetchone())["c"])
        cursor = await conn.execute(
            "SELECT status, COUNT(*) AS c FROM caption_scan_images "
            "WHERE model_key = ? GROUP BY status",
            (model_key,),
        )
        by_status = {row["status"]: int(row["c"]) for row in await cursor.fetchall()}
        pending = await count_images_needing_captions(
            db_path,
            model_key=model_key,
            cache_root=cache_root,
            cache_size="md",
        )
        return {
            "captioned": captioned,
            "pending_cached_images": pending,
            "done": by_status.get("done", 0),
            "error": by_status.get("error", 0),
            "pending": by_status.get("pending", 0),
            "scan": by_status,
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def caption_search_ranked_image_ids(
    db_path: str,
    text_query: str,
    *,
    active_source_ids,
    model_key: str,
    max_results: int = 5000,
) -> list[tuple[int, float]]:
    query = (text_query or "").strip()
    if len(query) < 2:
        return []
    active_source_ids = sorted(int(source_id) for source_id in active_source_ids)
    if not active_source_ids:
        return []
    await ensure_active_caption_fts_model(db_path, model_key)
    conn = await connection.open_async(db_path)
    try:
        source_placeholders = ",".join("?" for _ in active_source_ids)
        cursor = await conn.execute(
            "SELECT f.rowid AS id, bm25(image_captions_fts) AS score "
            "FROM image_captions_fts f "
            "JOIN images i ON i.id = f.rowid "
            "JOIN image_captions c ON c.image_id = f.rowid AND c.model_key = ? "
            f"WHERE i.source_id IN ({source_placeholders}) "
            "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
            "AND image_captions_fts MATCH ? "
            "ORDER BY score ASC LIMIT ?",
            (model_key, *active_source_ids, metadata_fts_query(query), int(max_results)),
        )
        return [(int(row["id"]), -float(row["score"] or 0.0)) for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)


async def caption_count_for_signature(db_path: str, *, model_key: str) -> int:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS c FROM image_captions WHERE model_key = ?",
            (model_key,),
        )
        row = await cursor.fetchone()
        return int(row["c"] if row else 0)
    finally:
        await connection.close_async(conn, db_path=db_path)
