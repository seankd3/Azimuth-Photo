"""Caption storage, scan ledger, and caption FTS helpers."""

from __future__ import annotations

import json
import time

from data import connection
from data.repositories.common import chunked as _chunked
from data.repositories.metadata_search import metadata_fts_query
from core.ai_failures import caption_ledger_status
from photo.visibility import visible_image_condition


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


def normalize_understanding(value) -> dict:
    if not isinstance(value, dict):
        return {}
    visible_text = " ".join(str(value.get("visible_text") or "").split())[:4000]
    entities = normalize_tags(value.get("entities"))[:30]
    raw_attributes = value.get("attributes")
    attributes = {}
    if isinstance(raw_attributes, dict):
        for key, raw in raw_attributes.items():
            clean_key = str(key or "").strip().casefold()[:40]
            if not clean_key:
                continue
            if isinstance(raw, list):
                clean_value = [str(item or "").strip()[:120] for item in raw if str(item or "").strip()]
            else:
                clean_value = str(raw or "").strip()[:240]
            if clean_value:
                attributes[clean_key] = clean_value
    return {
        "visible_text": visible_text,
        "entities": entities,
        "attributes": attributes,
    }


def understanding_search_text(understanding: dict) -> str:
    values = list(understanding.get("entities") or [])
    for value in (understanding.get("attributes") or {}).values():
        values.extend(value if isinstance(value, list) else [value])
    return " ".join(str(value) for value in values if value)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
        await conn.execute("DELETE FROM image_understanding_fts")
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
        cursor = await conn.execute(
            "SELECT image_id, visible_text, entities, attributes, search_text "
            "FROM image_understanding WHERE model_key = ?",
            (model_key,),
        )
        understanding_rows = await cursor.fetchall()
        if understanding_rows:
            await conn.executemany(
                "INSERT INTO image_understanding_fts"
                "(rowid, visible_text, entities, attributes, search_text) VALUES (?, ?, ?, ?, ?)",
                [tuple(row) for row in understanding_rows],
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
    understanding: dict | None = None,
    status: str = "done",
    error: str = "",
) -> None:
    now = time.time()
    tags_text = tags_json(tags)
    structured = normalize_understanding(understanding)

    async def _write() -> None:
        write_status = caption_ledger_status(requested_status=status, error=error)
        write_error = error
        conn = await connection.open_async(db_path)
        try:
            await conn.execute("BEGIN IMMEDIATE")
            if write_status != "done":
                cursor = await conn.execute(
                    "SELECT user_edited FROM image_captions WHERE image_id = ? AND model_key = ?",
                    (int(image_id), model_key),
                )
                existing = await cursor.fetchone()
                if existing and int(existing["user_edited"] or 0):
                    write_status = "done"
                    write_error = ""
            if write_status == "done":
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
                if structured:
                    await conn.execute(
                        "INSERT INTO image_understanding "
                        "(image_id, model_key, visible_text, entities, attributes, search_text, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(model_key, image_id) DO UPDATE SET "
                        "visible_text = excluded.visible_text, entities = excluded.entities, "
                        "attributes = excluded.attributes, search_text = excluded.search_text, "
                        "created_at = excluded.created_at",
                        (
                            int(image_id), model_key, structured["visible_text"],
                            json.dumps(structured["entities"], ensure_ascii=True),
                            json.dumps(structured["attributes"], ensure_ascii=True, sort_keys=True),
                            understanding_search_text(structured), now,
                        ),
                    )
            await conn.execute(
                "INSERT INTO caption_scan_images "
                "(image_id, model_key, status, last_error, scanned_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(image_id, model_key) DO UPDATE SET "
                "status = excluded.status, last_error = excluded.last_error, "
                "scanned_at = excluded.scanned_at",
                (int(image_id), model_key, write_status, str(write_error or ""), now),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await connection.close_async(conn, db_path=db_path)

    with connection.sqlite_timeout(0.25):
        await connection.run_with_busy_retry(_write)


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
    return await get_image_caption(db_path, image_id=image_id, model_key=model_key)


async def get_image_caption(db_path: str, *, image_id: int, model_key: str) -> dict | None:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT c.image_id, c.model_key, c.caption, c.tags, c.quality, "
            "c.user_edited, c.created_at, u.visible_text, u.entities, u.attributes "
            "FROM image_captions c LEFT JOIN image_understanding u "
            "ON u.image_id = c.image_id AND u.model_key = c.model_key "
            "WHERE c.image_id = ? AND c.model_key = ?",
            (int(image_id), model_key),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        try:
            parsed_tags = json.loads(row["tags"] or "[]")
        except (TypeError, ValueError):
            parsed_tags = []
        try:
            entities = json.loads(row["entities"] or "[]")
        except (TypeError, ValueError):
            entities = []
        try:
            attributes = json.loads(row["attributes"] or "{}")
        except (TypeError, ValueError):
            attributes = {}
        return {
            "image_id": int(row["image_id"]),
            "model_key": row["model_key"],
            "caption": row["caption"] or "",
            "tags": normalize_tags(parsed_tags),
            "quality": row["quality"] or "",
            "user_edited": bool(row["user_edited"]),
            "created_at": row["created_at"],
            "understanding": {
                "visible_text": row["visible_text"] or "",
                "entities": entities,
                "attributes": attributes,
            } if row["visible_text"] is not None else {},
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


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


async def get_images_needing_captions(
    db_path: str,
    *,
    model_key: str,
    cache_root: str,
    cache_size: str = "md",
    limit: int = 8,
    include_understanding_backfill: bool = False,
) -> list[dict]:
    retry_before = time.time() - ERROR_RETRY_AFTER_SECONDS
    understanding_clause = (
        " OR (csi.status = 'done' AND iu.image_id IS NULL)"
        if include_understanding_backfill else ""
    )
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
            "LEFT JOIN image_understanding iu "
            "ON iu.image_id = i.id AND iu.model_key = ? "
            "WHERE s.included = 1 AND i.status IN ('kept', 'maybe') "
            "AND i.missing_at IS NULL "
            "AND (csi.status IS NULL OR csi.status = 'pending' "
            "OR (csi.status = 'error' AND (csi.scanned_at <= ? "
            "OR instr(lower(csi.last_error), 'out of memory') > 0))"
            f"{understanding_clause}) "
            "ORDER BY CASE WHEN i.flag = 'picked' THEN 0 ELSE 1 END, "
            "(i.date_taken IS NULL), i.date_taken DESC, i.id DESC "
            "LIMIT ?",
            (cache_root, cache_size, model_key, model_key, retry_before, max(1, int(limit))),
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
    include_understanding_backfill: bool = False,
) -> int:
    retry_before = time.time() - ERROR_RETRY_AFTER_SECONDS
    understanding_clause = (
        " OR (csi.status = 'done' AND iu.image_id IS NULL)"
        if include_understanding_backfill else ""
    )
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
            "LEFT JOIN image_understanding iu "
            "ON iu.image_id = i.id AND iu.model_key = ? "
            "WHERE s.included = 1 AND i.status IN ('kept', 'maybe') "
            "AND i.missing_at IS NULL "
            "AND (csi.status IS NULL OR csi.status = 'pending' "
            "OR (csi.status = 'error' AND (csi.scanned_at <= ? "
            "OR instr(lower(csi.last_error), 'out of memory') > 0))"
            f"{understanding_clause})",
            (cache_root, cache_size, model_key, model_key, retry_before),
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
        cursor = await conn.execute(
            "SELECT COUNT(*) AS c FROM caption_scan_images "
            "WHERE model_key = ? AND status = 'error' "
            "AND instr(lower(last_error), 'out of memory') > 0",
            (model_key,),
        )
        system_deferred = int((await cursor.fetchone())["c"])
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
            "error": max(0, by_status.get("error", 0) - system_deferred),
            "system_deferred": system_deferred,
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
        fts_query = metadata_fts_query(query)
        cursor = await conn.execute(
            "WITH matches AS ("
            "SELECT f.rowid AS id, bm25(image_captions_fts) AS score "
            "FROM image_captions_fts f "
            "JOIN image_captions c ON c.image_id = f.rowid AND c.model_key = ? "
            "WHERE image_captions_fts MATCH ? "
            "UNION ALL "
            "SELECT u.rowid AS id, bm25(image_understanding_fts) AS score "
            "FROM image_understanding_fts u "
            "JOIN image_understanding d ON d.image_id = u.rowid AND d.model_key = ? "
            "WHERE image_understanding_fts MATCH ?"
            ") SELECT matches.id, MIN(matches.score) AS score FROM matches "
            "JOIN images i ON i.id = matches.id "
            f"WHERE i.source_id IN ({source_placeholders}) "
            f"AND {visible_image_condition()} "
            "GROUP BY matches.id ORDER BY score ASC LIMIT ?",
            (model_key, fts_query, model_key, fts_query, *active_source_ids, int(max_results)),
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
