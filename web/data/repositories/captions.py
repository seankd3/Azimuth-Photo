"""Caption storage, scan ledger, and caption FTS helpers."""

from __future__ import annotations

import json
import time

from data import connection
from data.repositories.common import chunked as _chunked
from data.repositories.metadata_search import metadata_fts_query


ERROR_RETRY_AFTER_SECONDS = 24 * 60 * 60
TAGS_CACHE_TTL_SECONDS = 2.0
_tags_cache: dict[tuple, dict] = {}


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


def _tags_signature_key(db_path: str, model_key: str, q: str, limit: int, signature: int) -> tuple:
    return (db_path, model_key, q.casefold(), int(limit), int(signature))


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def invalidate_tags_cache() -> None:
    _tags_cache.clear()


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
        if status != "done":
            cursor = await conn.execute(
                "SELECT user_edited FROM image_captions WHERE image_id = ? AND model_key = ?",
                (int(image_id), model_key),
            )
            existing = await cursor.fetchone()
            if existing and int(existing["user_edited"] or 0):
                status = "done"
                error = ""
        if status == "done":
            await conn.execute(
                "INSERT INTO image_captions "
                "(image_id, model_key, caption, tags, quality, user_edited, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?) "
                "ON CONFLICT(model_key, image_id) DO UPDATE SET "
                "caption = excluded.caption, tags = excluded.tags, "
                "quality = excluded.quality, created_at = excluded.created_at "
                "WHERE COALESCE(image_captions.user_edited, 0) = 0",
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


async def owner_update_caption(
    db_path: str,
    *,
    image_id: int,
    model_key: str,
    caption: str | None = None,
    tags=None,
) -> dict | None:
    now = time.time()
    clean_caption = str(caption or "") if caption is not None else None
    clean_tags = normalize_tags(tags) if tags is not None else None
    conn = await connection.open_async(db_path)
    try:
        await conn.execute("BEGIN")
        cursor = await conn.execute("SELECT id FROM images WHERE id = ?", (int(image_id),))
        if await cursor.fetchone() is None:
            await conn.rollback()
            return None
        cursor = await conn.execute(
            "SELECT caption, tags, quality FROM image_captions WHERE image_id = ? AND model_key = ?",
            (int(image_id), model_key),
        )
        existing = await cursor.fetchone()
        next_caption = clean_caption if clean_caption is not None else str(existing["caption"] if existing else "")
        existing_tags = []
        if existing:
            try:
                existing_tags = json.loads(existing["tags"] or "[]")
            except (TypeError, ValueError):
                existing_tags = []
        next_tags = clean_tags if clean_tags is not None else normalize_tags(existing_tags)
        next_quality = str(existing["quality"] or "") if existing else "user"
        await conn.execute(
            "INSERT INTO image_captions "
            "(image_id, model_key, caption, tags, quality, user_edited, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(model_key, image_id) DO UPDATE SET "
            "caption = excluded.caption, tags = excluded.tags, quality = excluded.quality, "
            "user_edited = 1, created_at = excluded.created_at",
            (int(image_id), model_key, next_caption, tags_json(next_tags), next_quality, now),
        )
        await conn.execute(
            "INSERT INTO caption_scan_images "
            "(image_id, model_key, status, last_error, scanned_at) "
            "VALUES (?, ?, 'done', '', ?) "
            "ON CONFLICT(image_id, model_key) DO UPDATE SET "
            "status = 'done', last_error = '', scanned_at = excluded.scanned_at",
            (int(image_id), model_key, now),
        )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=db_path)
    invalidate_tags_cache()
    return await get_image_caption(db_path, image_id=image_id, model_key=model_key)


async def get_image_caption(db_path: str, *, image_id: int, model_key: str) -> dict | None:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT image_id, model_key, caption, tags, quality, user_edited, created_at "
            "FROM image_captions WHERE image_id = ? AND model_key = ?",
            (int(image_id), model_key),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        try:
            parsed_tags = json.loads(row["tags"] or "[]")
        except (TypeError, ValueError):
            parsed_tags = []
        return {
            "image_id": int(row["image_id"]),
            "model_key": row["model_key"],
            "caption": row["caption"] or "",
            "tags": normalize_tags(parsed_tags),
            "quality": row["quality"] or "",
            "user_edited": bool(row["user_edited"]),
            "created_at": row["created_at"],
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def image_caption_presence(db_path: str, *, model_key: str, image_ids) -> dict[int, bool]:
    ids = []
    for image_id in dict.fromkeys(image_ids or []):
        try:
            normalized = int(image_id)
        except (TypeError, ValueError):
            continue
        if normalized > 0:
            ids.append(normalized)
    if not ids:
        return {}
    result: dict[int, bool] = {}
    conn = await connection.open_async(db_path)
    try:
        for chunk in _chunked(ids, 900):
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                "SELECT image_id FROM image_captions "
                f"WHERE model_key = ? AND image_id IN ({placeholders})",
                (model_key, *chunk),
            )
            for row in await cursor.fetchall():
                result[int(row["image_id"])] = True
    finally:
        await connection.close_async(conn, db_path=db_path)
    return result


async def image_caption_summaries(db_path: str, *, model_key: str, image_ids) -> dict[int, dict]:
    ids = []
    for image_id in dict.fromkeys(image_ids or []):
        try:
            normalized = int(image_id)
        except (TypeError, ValueError):
            continue
        if normalized > 0:
            ids.append(normalized)
    if not ids:
        return {}
    result: dict[int, dict] = {}
    conn = await connection.open_async(db_path)
    try:
        for chunk in _chunked(ids, 900):
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                "SELECT image_id, tags FROM image_captions "
                f"WHERE model_key = ? AND image_id IN ({placeholders})",
                (model_key, *chunk),
            )
            for row in await cursor.fetchall():
                try:
                    parsed_tags = json.loads(row["tags"] or "[]")
                except (TypeError, ValueError):
                    parsed_tags = []
                result[int(row["image_id"])] = {"has_caption": True, "caption_tags": normalize_tags(parsed_tags)}
    finally:
        await connection.close_async(conn, db_path=db_path)
    return result


async def tag_signature(db_path: str, *, model_key: str) -> int:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS c FROM image_tags WHERE model_key = ?",
            (model_key,),
        )
        row = await cursor.fetchone()
        return int(row["c"] if row else 0)
    finally:
        await connection.close_async(conn, db_path=db_path)


async def list_tags(
    db_path: str,
    *,
    model_key: str,
    q: str = "",
    limit: int = 100,
    signature: int | None = None,
    ttl_seconds: float = TAGS_CACHE_TTL_SECONDS,
) -> list[dict]:
    query = str(q or "").strip().lower()
    capped_limit = max(1, min(int(limit or 100), 500))
    sig = await tag_signature(db_path, model_key=model_key) if signature is None else int(signature)
    cache_key = _tags_signature_key(db_path, model_key, query, capped_limit, sig)
    now = time.time()
    cached = _tags_cache.get(cache_key)
    if cached and cached["expires"] > now:
        return [dict(item) for item in cached["data"]]
    where = ["it.model_key = ?", "i.status IN ('kept', 'maybe')", "i.missing_at IS NULL", "s.included = 1"]
    params: list = [model_key]
    if query:
        where.append("it.tag LIKE ? ESCAPE '\\'")
        params.append(f"{escape_like(query)}%")
    params.append(capped_limit)
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT it.tag, COUNT(DISTINCT it.image_id) AS count "
            "FROM image_tags it "
            "JOIN images i ON i.id = it.image_id "
            "JOIN catalog_sources s ON s.id = i.source_id "
            f"WHERE {' AND '.join(where)} "
            "GROUP BY it.tag ORDER BY count DESC, it.tag ASC LIMIT ?",
            params,
        )
        rows = [{"tag": row["tag"], "count": int(row["count"] or 0)} for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)
    _tags_cache[cache_key] = {"data": [dict(item) for item in rows], "expires": now + ttl_seconds}
    return rows


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
