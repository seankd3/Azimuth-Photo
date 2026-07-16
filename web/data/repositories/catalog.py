"""Catalog-source query helpers and cached catalog summaries."""

import asyncio
import os
import time as _time

from date_inference import infer_image_date
from data import connection
from data.repositories import image_deletion
from data.repositories.common import chunked as _chunked
from core.path_groups import safe_commonpath


CATALOG_CACHE_TTL_SECONDS = 10.0
_catalog_sources_cache = {"data": None, "expires": 0}
_catalog_summary_cache = {"data": None, "expires": 0}
_catalog_light_summary_cache = {"data": None, "expires": 0}
_active_source_ids_cache = {"ids": frozenset(), "expires": 0}
ACTIVE_SOURCE_IDS_TTL_SECONDS = 5.0
MISSING_MARK_BATCH_SIZE = 128
MISSING_MARK_BUSY_TIMEOUT_SECONDS = 0.1
MISSING_MARK_RETRY_BACKOFF_SECONDS = 0.1
_missing_mark_states: dict[tuple[int, str], dict] = {}


class SourceOfflineDuringScan(RuntimeError):
    """Raised when a source disappears before a scan can be finalized safely."""


class SuspiciousEmptyScan(RuntimeError):
    """Raised when an empty online scan is unsafe to apply to existing images."""


def normalize_source_path(path: str) -> str:
    """Return the canonical local path used as a catalog source key."""

    return os.path.realpath(os.path.abspath(os.path.expanduser(path or "")))


def source_display_name(path: str) -> str:
    normalized = normalize_source_path(path)
    return os.path.basename(normalized.rstrip(os.sep)) or normalized


def active_source_join(image_alias: str = "i", source_alias: str = "s") -> str:
    return f"JOIN catalog_sources {source_alias} ON {source_alias}.id = {image_alias}.source_id"


def active_source_condition(source_alias: str = "s") -> str:
    return f"{source_alias}.included = 1"


def active_image_condition(image_alias: str = "i", source_alias: str = "s") -> str:
    return (
        f"{source_alias}.included = 1 "
        f"AND {image_alias}.status IN ('kept', 'maybe') "
        f"AND {image_alias}.missing_at IS NULL"
    )


async def active_source_id_set(db_path: str) -> frozenset[int]:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT id FROM catalog_sources WHERE included = 1"
        )
        return frozenset(int(row["id"]) for row in await cursor.fetchall())
    finally:
        await connection.close_async(conn, db_path=db_path)


def invalidate_active_source_ids_cache() -> None:
    _active_source_ids_cache["ids"] = frozenset()
    _active_source_ids_cache["expires"] = 0


def invalidate_catalog_summary_cache() -> None:
    _catalog_summary_cache["data"] = None
    _catalog_summary_cache["expires"] = 0
    _catalog_light_summary_cache["data"] = None
    _catalog_light_summary_cache["expires"] = 0


def invalidate_catalog_cache() -> None:
    _catalog_sources_cache["data"] = None
    _catalog_sources_cache["expires"] = 0
    invalidate_catalog_summary_cache()


async def active_source_id_set_cached(
    db_path: str,
    *,
    ttl_seconds: float = ACTIVE_SOURCE_IDS_TTL_SECONDS,
) -> frozenset[int]:
    now = _time.time()
    if now < _active_source_ids_cache["expires"]:
        return _active_source_ids_cache["ids"]
    frozen = await active_source_id_set(db_path)
    _active_source_ids_cache["ids"] = frozen
    _active_source_ids_cache["expires"] = _time.time() + ttl_seconds
    return frozen


def insert_row_with_file_metadata(row):
    if len(row) >= 5:
        return row[:5]
    filename, filepath = row[:2]
    file_ext = os.path.splitext(filename)[1].lower()
    file_size = None
    file_modified_at = None
    try:
        stat = os.stat(filepath)
        file_size = int(stat.st_size)
        file_modified_at = float(stat.st_mtime)
    except Exception:
        pass
    return filename, filepath, file_ext, file_size, file_modified_at


def _insert_row_with_inferred_date(row, source_root: str | None = None):
    filename, filepath, file_ext, file_size, file_modified_at = insert_row_with_file_metadata(row)
    inferred = infer_image_date(
        filename=filename,
        filepath=filepath,
        file_modified_at=file_modified_at,
        source_root=source_root,
    )
    return (
        filename,
        filepath,
        file_ext,
        file_size,
        file_modified_at,
        inferred.date_taken if inferred else None,
        inferred.date_source if inferred else None,
    )


async def ensure_catalog_source_on_conn(conn, path: str, *, included: bool = True, last_scan_at=None):
    normalized = normalize_source_path(path)
    display_name = source_display_name(normalized)
    online = 1 if os.path.isdir(normalized) else 0
    now = _time.time()
    await conn.execute(
        "INSERT INTO catalog_sources "
        "(path, display_name, included, online, created_at, last_scan_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "display_name = excluded.display_name, "
        "included = CASE WHEN excluded.included = 1 THEN 1 ELSE catalog_sources.included END, "
        "online = excluded.online, "
        "last_scan_at = COALESCE(excluded.last_scan_at, catalog_sources.last_scan_at), "
        "last_seen_at = excluded.last_seen_at, "
        "removed_at = CASE WHEN excluded.included = 1 THEN NULL ELSE catalog_sources.removed_at END",
        (normalized, display_name, 1 if included else 0, online, now, last_scan_at, now),
    )
    cursor = await conn.execute("SELECT * FROM catalog_sources WHERE path = ?", (normalized,))
    return await cursor.fetchone()


async def update_source_counts_on_conn(conn, source_id: int | None = None):
    params = []
    where = ""
    if source_id is not None:
        where = " WHERE id = ?"
        params.append(source_id)
    await conn.execute(
        "UPDATE catalog_sources SET image_count = ("
        "  SELECT COUNT(*) FROM images WHERE images.source_id = catalog_sources.id"
        f"){where}",
        params,
    )
    await conn.execute(
        "UPDATE catalog_sources SET active_image_count = CASE "
        "WHEN included = 1 THEN ("
        "  SELECT COUNT(*) FROM images "
        "  WHERE images.source_id = catalog_sources.id "
        "  AND images.status IN ('kept', 'maybe') "
        "  AND images.missing_at IS NULL"
        ") ELSE 0 END"
        f"{where}",
        params,
    )


HUB_MIRROR_SOURCE_PATH = "hub://"


async def repair_hub_mirror_source_counts_on_conn(conn) -> bool:
    """Resync hub:// image_count/active_image_count with live mirrored rows.

    Rankings / All Photos short-circuit on SUM(active_image_count). A hub
    mirror that left those denormalized counters at 0 makes All Photos look
    like only the local/recent imports even when hub_remote rows exist.

    Full COUNT(*) over 100k+ hub rows is too expensive to run on every
    /api/stats cold miss. Only recount when the denormalized counters look
    empty (the known drift failure) while hub rows exist.
    """
    cursor = await conn.execute(
        "SELECT id, image_count, active_image_count FROM catalog_sources WHERE path = ?",
        (HUB_MIRROR_SOURCE_PATH,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False
    source_id = int(row["id"])
    image_count = int(row["image_count"] or 0)
    active_image_count = int(row["active_image_count"] or 0)
    if image_count > 0:
        # Trust denormalized counters; mirror refresh keeps them honest.
        return False
    probe = await (
        await conn.execute(
            "SELECT 1 AS present FROM images WHERE source_id = ? LIMIT 1",
            (source_id,),
        )
    ).fetchone()
    if probe is None:
        return False
    live = await (
        await conn.execute(
            "SELECT "
            "COUNT(*) AS image_count, "
            "COALESCE(SUM(CASE WHEN status IN ('kept', 'maybe') AND missing_at IS NULL "
            "THEN 1 ELSE 0 END), 0) AS active_image_count "
            "FROM images WHERE source_id = ?",
            (source_id,),
        )
    ).fetchone()
    live_image_count = int(live["image_count"] or 0)
    live_active_image_count = int(live["active_image_count"] or 0)
    if image_count == live_image_count and active_image_count == live_active_image_count:
        return False
    await conn.execute(
        "UPDATE catalog_sources SET image_count = ?, active_image_count = ? WHERE id = ?",
        (live_image_count, live_active_image_count, source_id),
    )
    return True


async def repair_hub_mirror_source_counts(db_path: str) -> bool:
    conn = await connection.open_async(db_path)
    try:
        repaired = await repair_hub_mirror_source_counts_on_conn(conn)
        if repaired:
            await conn.commit()
        return repaired
    finally:
        await connection.close_async(conn, db_path=db_path)


async def refresh_source_online_states_on_conn(conn) -> bool:
    cursor = await conn.execute("SELECT id, path, online FROM catalog_sources")
    rows = await cursor.fetchall()
    now = _time.time()
    updates = []
    for row in rows:
        online = 1 if str(row["path"]) == "hub://" else 1 if os.path.isdir(row["path"]) else 0
        if int(row["online"] or 0) != online:
            updates.append((online, now, row["id"]))
    if not updates:
        return False
    await conn.executemany(
        "UPDATE catalog_sources SET online = ?, last_seen_at = ? WHERE id = ?",
        updates,
    )
    await update_source_counts_on_conn(conn)
    return True


async def refresh_source_online_states(db_path: str) -> bool:
    conn = await connection.open_async(db_path)
    try:
        changed = await refresh_source_online_states_on_conn(conn)
        if changed:
            await conn.commit()
        return changed
    finally:
        await connection.close_async(conn, db_path=db_path)


async def insert_images_batch(db_path: str, rows: list[tuple], source_id: int | None = None):
    if not rows:
        return
    conn = await connection.open_async(db_path)
    try:
        source_root = None
        if source_id is not None:
            cursor = await conn.execute("SELECT path FROM catalog_sources WHERE id = ?", (source_id,))
            source = await cursor.fetchone()
            if source:
                source_root = source["path"]
        normalized_rows = [_insert_row_with_inferred_date(row, source_root) for row in rows]
        if source_id is not None:
            await conn.executemany(
                "INSERT OR IGNORE INTO images "
                "(source_id, filename, filepath, status, file_ext, file_size, file_modified_at, date_taken, date_source) "
                "VALUES (?, ?, ?, 'kept', ?, ?, ?, ?, ?)",
                [(source_id, *row) for row in normalized_rows],
            )
            await conn.executemany(
                "UPDATE images SET "
                "source_id = CASE WHEN source_id IS NULL THEN ? ELSE source_id END, "
                "filename = ?, "
                "file_ext = COALESCE(?, file_ext), "
                "file_size = COALESCE(?, file_size), "
                "file_modified_at = COALESCE(?, file_modified_at), "
                "date_taken = CASE WHEN date_taken IS NULL OR date_taken = '' THEN ? ELSE date_taken END, "
                "date_source = CASE WHEN date_taken IS NULL OR date_taken = '' THEN ? ELSE date_source END, "
                "missing_at = CASE WHEN ? = 0 THEN missing_at ELSE NULL END "
                "WHERE filepath = ? AND (source_id = ? OR source_id IS NULL)",
                [
                    (
                        source_id,
                        row[0],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        row[3],
                        row[1],
                        source_id,
                    )
                    for row in normalized_rows
                ],
            )
            await update_source_counts_on_conn(conn, source_id)
        else:
            await conn.executemany(
                "INSERT OR IGNORE INTO images "
                "(filename, filepath, status, file_ext, file_size, file_modified_at, date_taken, date_source) "
                "VALUES (?, ?, 'kept', ?, ?, ?, ?, ?)",
                normalized_rows,
            )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def image_signatures_for_source(db_path: str, source_id: int) -> set[tuple[str, str, int]]:
    """Known live listing entries for an incremental watched-folder scan."""

    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT filename, filepath, file_size FROM images "
            "WHERE source_id = ? AND missing_at IS NULL",
            (int(source_id),),
        )
        return {
            (str(row["filename"]), str(row["filepath"]), int(row["file_size"] or 0))
            for row in await cursor.fetchall()
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def mark_source_missing_files_on_conn(
    conn,
    source_id: int,
    seen_filepaths: list[str],
    missing_at: float,
    excluded_directory_paths: list[str] | None = None,
):
    await conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS source_scan_seen (filepath TEXT PRIMARY KEY)"
    )
    await conn.execute("DELETE FROM source_scan_seen")
    unique_seen = list(dict.fromkeys(seen_filepaths))
    if unique_seen:
        await conn.executemany(
            "INSERT OR IGNORE INTO source_scan_seen(filepath) VALUES (?)",
            [(filepath,) for filepath in unique_seen],
        )
    await conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS source_scan_excluded (directory_prefix TEXT PRIMARY KEY)"
    )
    await conn.execute("DELETE FROM source_scan_excluded")
    unique_excluded = list(dict.fromkeys(excluded_directory_paths or []))
    if unique_excluded:
        await conn.executemany(
            "INSERT OR IGNORE INTO source_scan_excluded(directory_prefix) VALUES (?)",
            [(os.path.join(path, ""),) for path in unique_excluded],
        )
    await conn.execute(
        "UPDATE images SET missing_at = NULL "
        "WHERE source_id = ? AND filepath IN (SELECT filepath FROM source_scan_seen) "
        "AND COALESCE(file_size, -1) != 0",
        (source_id,),
    )
    await conn.execute(
        "UPDATE images SET missing_at = ? "
        "WHERE source_id = ? AND missing_at IS NULL "
        "AND filepath NOT IN (SELECT filepath FROM source_scan_seen) "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM source_scan_excluded "
        "  WHERE instr(images.filepath, source_scan_excluded.directory_prefix) = 1"
        ")",
        (missing_at, source_id),
    )
    await conn.execute("DELETE FROM source_scan_seen")
    await conn.execute("DELETE FROM source_scan_excluded")


async def add_or_restore_source(db_path: str, path: str):
    conn = await connection.open_async(db_path)
    try:
        source = await ensure_catalog_source_on_conn(conn, path, included=True)
        await update_source_counts_on_conn(conn, source["id"])
        await conn.commit()
        cursor = await conn.execute("SELECT * FROM catalog_sources WHERE id = ?", (source["id"],))
        return await cursor.fetchone()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def mark_source_scan_started(db_path: str, source_id: int) -> bool:
    conn = await connection.open_async(db_path)
    try:
        now = _time.time()
        cursor = await conn.execute(
            "UPDATE catalog_sources SET included = 1, online = 1, removed_at = NULL, last_seen_at = ? "
            "WHERE id = ?",
            (now, source_id),
        )
        await conn.commit()
        return bool(cursor.rowcount)
    finally:
        await connection.close_async(conn, db_path=db_path)


async def mark_source_scan_finished(
    db_path: str,
    source_id: int,
    seen_filepaths: list[str] | None = None,
    excluded_directory_paths: list[str] | None = None,
):
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT path FROM catalog_sources WHERE id = ?",
            (int(source_id),),
        )
        source = await cursor.fetchone()
        source_path = str(source["path"] or "") if source is not None else ""
        if not source_path or not await asyncio.to_thread(os.path.isdir, source_path):
            await conn.execute(
                "UPDATE catalog_sources SET online = 0 WHERE id = ?",
                (int(source_id),),
            )
            await conn.commit()
            raise SourceOfflineDuringScan(
                "Source drive went offline during scan; existing catalog entries were preserved"
            )
        suspicious_empty_scan = False
        if seen_filepaths == []:
            cursor = await conn.execute(
                "SELECT 1 FROM images WHERE source_id = ? AND missing_at IS NULL LIMIT 1",
                (int(source_id),),
            )
            suspicious_empty_scan = await cursor.fetchone() is not None
        now = _time.time()
        await conn.execute(
            "UPDATE catalog_sources SET last_scan_at = ?, last_seen_at = ?, online = ? WHERE id = ?",
            (now, now, 1, source_id),
        )
        if seen_filepaths is not None and not suspicious_empty_scan:
            await mark_source_missing_files_on_conn(
                conn,
                source_id,
                seen_filepaths,
                now,
                excluded_directory_paths=excluded_directory_paths,
            )
        await update_source_counts_on_conn(conn, source_id)
        await conn.commit()
        if suspicious_empty_scan:
            raise SuspiciousEmptyScan(
                "Scan found no files; existing catalog entries were preserved and were not marked missing"
            )
    finally:
        await connection.close_async(conn, db_path=db_path)


_REPAIR_COLLECTION_COVER_SQL = (
    "UPDATE collections SET cover_image_id = ("
    "  SELECT ci.image_id FROM collection_images ci "
    "  JOIN images i ON i.id = ci.image_id "
    "  WHERE ci.collection_id = collections.id "
    "    AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
    "  ORDER BY ci.position ASC, ci.added_at ASC, ci.image_id ASC LIMIT 1"
    ") WHERE cover_image_id = ?"
)


def mark_image_missing_sync(db_path: str, image_id: int, missing_at: float | None = None) -> bool:
    when = _time.time() if missing_at is None else float(missing_at)
    conn = connection.open_sync(db_path)
    try:
        cursor = conn.execute(
            "UPDATE images SET missing_at = ? WHERE id = ? AND missing_at IS NULL "
            "AND COALESCE(hub_remote, 0) = 0",
            (when, int(image_id)),
        )
        if cursor.rowcount > 0:
            source = conn.execute("SELECT source_id FROM images WHERE id = ?", (int(image_id),)).fetchone()
            conn.execute(_REPAIR_COLLECTION_COVER_SQL, (int(image_id),))
            if source is not None and source["source_id"] is not None:
                conn.execute(
                    "UPDATE catalog_sources SET active_image_count = ("
                    "SELECT COUNT(*) FROM images WHERE source_id = ? "
                    "AND status IN ('kept', 'maybe') AND missing_at IS NULL"
                    ") WHERE id = ?",
                    (int(source["source_id"]), int(source["source_id"])),
                )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        connection.close_sync(conn, db_path=db_path)


async def _write_missing_mark_batch(db_path: str, requests: list[tuple[int, float]]) -> set[int]:
    timestamps: dict[int, float] = {}
    for image_id, missing_at in requests:
        timestamps.setdefault(int(image_id), float(missing_at))
    ids = list(timestamps)

    async def _write() -> set[int]:
        conn = await connection.open_async(db_path)
        try:
            await conn.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in ids)
            cursor = await conn.execute(
                "SELECT id, source_id FROM images "
                f"WHERE id IN ({placeholders}) AND missing_at IS NULL "
                "AND COALESCE(hub_remote, 0) = 0",
                ids,
            )
            rows = await cursor.fetchall()
            changed_ids = {int(row["id"]) for row in rows}
            if changed_ids:
                await conn.executemany(
                    "UPDATE images SET missing_at = ? WHERE id = ? AND missing_at IS NULL",
                    [(timestamps[image_id], image_id) for image_id in changed_ids],
                )
                await conn.executemany(
                    _REPAIR_COLLECTION_COVER_SQL,
                    [(image_id,) for image_id in changed_ids],
                )
                source_ids = {
                    int(row["source_id"])
                    for row in rows
                    if row["source_id"] is not None
                }
                for source_id in source_ids:
                    await update_source_counts_on_conn(conn, source_id)
            await conn.commit()
            return changed_ids
        except Exception:
            await conn.rollback()
            raise
        finally:
            await connection.close_async(conn, db_path=db_path)

    with connection.sqlite_timeout(MISSING_MARK_BUSY_TIMEOUT_SECONDS):
        return await connection.run_with_busy_retry(
            _write,
            backoff_seconds=MISSING_MARK_RETRY_BACKOFF_SECONDS,
        )


async def _flush_missing_marks(key: tuple[int, str], state: dict) -> None:
    await asyncio.sleep(0)
    try:
        while state["requests"]:
            requests = state["requests"][:MISSING_MARK_BATCH_SIZE]
            del state["requests"][:MISSING_MARK_BATCH_SIZE]
            try:
                changed_ids = await _write_missing_mark_batch(key[1], [item[:2] for item in requests])
            except Exception as exc:
                for _image_id, _missing_at, future in requests:
                    if not future.done():
                        future.set_exception(exc)
            else:
                for image_id, _missing_at, future in requests:
                    if not future.done():
                        future.set_result(image_id in changed_ids)
    finally:
        if _missing_mark_states.get(key) is state:
            _missing_mark_states.pop(key, None)


async def mark_image_missing(db_path: str, image_id: int, missing_at: float | None = None) -> bool:
    """Coalesce concurrent media misses into one short, bounded write transaction."""

    loop = asyncio.get_running_loop()
    key = (id(loop), db_path)
    state = _missing_mark_states.get(key)
    if state is None:
        state = {"requests": [], "task": None}
        _missing_mark_states[key] = state
    future = loop.create_future()
    when = _time.time() if missing_at is None else float(missing_at)
    state["requests"].append((int(image_id), when, future))
    if state["task"] is None:
        state["task"] = loop.create_task(_flush_missing_marks(key, state))
    return bool(await future)


async def mark_zero_byte_images_missing(db_path: str, filepaths: list[str]) -> list[dict]:
    """Quarantine newly seen zero-byte files and return only rows changed now."""
    if not filepaths:
        return []
    unique_paths = list(dict.fromkeys(str(path) for path in filepaths if path))
    if not unique_paths:
        return []
    conn = await connection.open_async(db_path)
    try:
        changed: list[dict] = []
        now = _time.time()
        for paths in _chunked(unique_paths):
            placeholders = ",".join("?" for _ in paths)
            cursor = await conn.execute(
                f"SELECT id, source_id, filepath FROM images WHERE missing_at IS NULL "
                f"AND file_size = 0 AND filepath IN ({placeholders})",
                paths,
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            if rows:
                await conn.executemany(
                    "UPDATE images SET missing_at = ? WHERE id = ? AND missing_at IS NULL",
                    [(now, int(row["id"])) for row in rows],
                )
                for row in rows:
                    await conn.execute(_REPAIR_COLLECTION_COVER_SQL, (int(row["id"]),))
                changed.extend(rows)
        for source_id in sorted(
            {int(row["source_id"]) for row in changed if row.get("source_id") is not None}
        ):
            await update_source_counts_on_conn(conn, source_id)
        await conn.commit()
        return changed
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_source(db_path: str, source_id: int):
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT * FROM catalog_sources WHERE id = ?", (source_id,))
        return await cursor.fetchone()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_source_by_path(db_path: str, path: str):
    normalized = normalize_source_path(path)
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT * FROM catalog_sources WHERE path = ?", (normalized,))
        return await cursor.fetchone()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_catalog_sources(db_path: str):
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT id, path, display_name, included, online, image_count, active_image_count, "
            "created_at, last_scan_at, last_seen_at, removed_at "
            "FROM catalog_sources ORDER BY included DESC, display_name COLLATE NOCASE ASC, path ASC"
        )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def catalog_sources_cached(
    db_path: str,
    *,
    refresh_source_online_states,
    ttl_seconds: float = CATALOG_CACHE_TTL_SECONDS,
):
    now = _time.time()
    if _catalog_sources_cache["data"] and now < _catalog_sources_cache["expires"]:
        return _catalog_sources_cache["data"]
    await refresh_source_online_states()
    rows = await get_catalog_sources(db_path)
    _catalog_sources_cache["data"] = rows
    _catalog_sources_cache["expires"] = _time.time() + ttl_seconds
    return rows


async def catalog_summary_cached(
    db_path: str,
    *,
    get_stats,
    refresh_source_online_states,
    ttl_seconds: float = CATALOG_CACHE_TTL_SECONDS,
) -> dict:
    now = _time.time()
    if _catalog_summary_cache["data"] and now < _catalog_summary_cache["expires"]:
        return _catalog_summary_cache["data"]
    sources = [
        dict(row)
        for row in await catalog_sources_cached(
            db_path,
            refresh_source_online_states=refresh_source_online_states,
            ttl_seconds=ttl_seconds,
        )
    ]
    stats = await get_stats()
    result = {"sources": sources, "stats": stats}
    _catalog_summary_cache["data"] = result
    _catalog_summary_cache["expires"] = _time.time() + ttl_seconds
    return result


async def catalog_light_summary_cached(
    db_path: str,
    *,
    get_catalog_image_counts,
    refresh_source_online_states,
    ttl_seconds: float = CATALOG_CACHE_TTL_SECONDS,
) -> dict:
    now = _time.time()
    if _catalog_light_summary_cache["data"] and now < _catalog_light_summary_cache["expires"]:
        return _catalog_light_summary_cache["data"]
    sources, counts = await asyncio.gather(
        catalog_sources_cached(
            db_path,
            refresh_source_online_states=refresh_source_online_states,
            ttl_seconds=ttl_seconds,
        ),
        get_catalog_image_counts(),
    )
    active = int(counts.get("active_images") or 0)
    total = int(counts.get("total_catalog_images") or 0)
    stats = {
        **counts,
        "total_images": active,
        "active_images": active,
        "kept": active,
        "maybe": 0,
        "removed_images": int(counts.get("removed_images") or 0),
        "offline_images": int(counts.get("offline_images") or 0),
        "total_catalog_images": total,
    }
    result = {"sources": [dict(row) for row in sources], "stats": stats}
    _catalog_light_summary_cache["data"] = result
    _catalog_light_summary_cache["expires"] = _time.time() + ttl_seconds
    return result


def folder_source_rows(db_path: str) -> list[tuple[int, str, int]]:
    conn = connection.open_sync(db_path)
    try:
        return [
            (int(row[0]), row[1], int(row[2] or 0))
            for row in conn.execute(
                "SELECT id, path, active_image_count FROM catalog_sources WHERE included = 1"
            ).fetchall()
        ]
    finally:
        connection.close_sync(conn, db_path=db_path)


def folder_tree_source_rows(db_path: str) -> list[dict]:
    conn = connection.open_sync(db_path)
    try:
        rows = conn.execute(
            "SELECT id, path, display_name, online, active_image_count "
            "FROM catalog_sources WHERE included = 1 "
            "ORDER BY display_name COLLATE NOCASE ASC, path ASC"
        ).fetchall()
        return [
            {
                "id": int(row[0]),
                "path": row[1] or "",
                "display_name": row[2] or source_display_name(row[1] or ""),
                "online": int(row[3] or 0),
                "active_image_count": int(row[4] or 0),
            }
            for row in rows
        ]
    finally:
        connection.close_sync(conn, db_path=db_path)


def folder_directory_counts_by_source(db_path: str, source_ids: list[int]) -> dict[int, dict[str, int]]:
    ids = list(dict.fromkeys(int(source_id) for source_id in source_ids if int(source_id) > 0))
    if not ids:
        return {}
    conn = connection.open_sync(db_path)
    try:
        counts_by_source: dict[int, dict[str, int]] = {source_id: {} for source_id in ids}
        for chunk in _chunked(ids, 900):
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                "SELECT source_id, "
                "RTRIM(SUBSTR(filepath, 1, LENGTH(filepath) - LENGTH(filename)), ?) AS directory, "
                "COUNT(*) AS count "
                "FROM images "
                f"WHERE source_id IN ({placeholders}) "
                "AND status IN ('kept', 'maybe') "
                "AND missing_at IS NULL "
                "AND filepath IS NOT NULL "
                "AND filename IS NOT NULL "
                "AND filename != '' "
                "GROUP BY source_id, directory",
                (os.sep, *chunk),
            ).fetchall()
            for source_id, directory, count in rows:
                directory_counts = counts_by_source.setdefault(int(source_id), {})
                directory_counts[directory or os.sep] = int(count or 0)
        return counts_by_source
    finally:
        connection.close_sync(conn, db_path=db_path)


def folder_image_filepaths_by_source(db_path: str, source_ids: list[int]) -> dict[int, list[str]]:
    ids = list(dict.fromkeys(int(source_id) for source_id in source_ids if int(source_id) > 0))
    if not ids:
        return {}
    conn = connection.open_sync(db_path)
    try:
        paths_by_source: dict[int, list[str]] = {source_id: [] for source_id in ids}
        for chunk in _chunked(ids, 900):
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                "SELECT source_id, filepath FROM images "
                f"WHERE source_id IN ({placeholders}) "
                "AND status IN ('kept', 'maybe') "
                "AND missing_at IS NULL",
                chunk,
            ).fetchall()
            for source_id, filepath in rows:
                paths_by_source.setdefault(int(source_id), []).append(filepath)
        return paths_by_source
    finally:
        connection.close_sync(conn, db_path=db_path)


async def remove_source_keep_data(db_path: str, source_id: int):
    conn = await connection.open_async(db_path)
    try:
        now = _time.time()
        await conn.execute(
            "UPDATE catalog_sources SET included = 0, removed_at = ?, last_seen_at = ? WHERE id = ?",
            (now, now, source_id),
        )
        await update_source_counts_on_conn(conn, source_id)
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_source_image_ids(db_path: str, source_id: int) -> list[int]:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT id FROM images WHERE source_id = ?", (source_id,))
        return [int(row["id"]) for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)


async def delete_image_catalog_rows_on_conn(conn, image_ids: list[int]) -> dict:
    image_ids = [int(image_id) for image_id in dict.fromkeys(image_ids or []) if int(image_id) > 0]
    if not image_ids:
        return {"images_deleted": 0, "comparisons_deleted": 0}

    expanded_image_ids = await image_deletion.expand_image_deletion_ids(conn, image_ids)
    comparison_count = 0
    comparison_decrements: dict[int, int] = {}
    image_id_set = set(expanded_image_ids)
    seen_comparison_ids: set[int] = set()
    for chunk in _chunked(expanded_image_ids):
        placeholders = ",".join("?" for _ in chunk)
        cursor = await conn.execute(
            f"SELECT id, winner_id, loser_id FROM comparisons "
            f"WHERE winner_id IN ({placeholders}) OR loser_id IN ({placeholders})",
            chunk + chunk,
        )
        for row in await cursor.fetchall():
            comparison_id = int(row["id"])
            if comparison_id in seen_comparison_ids:
                continue
            seen_comparison_ids.add(comparison_id)
            winner_id = int(row["winner_id"])
            loser_id = int(row["loser_id"])
            if winner_id in image_id_set and loser_id not in image_id_set:
                comparison_decrements[loser_id] = comparison_decrements.get(loser_id, 0) + 1
            elif loser_id in image_id_set and winner_id not in image_id_set:
                comparison_decrements[winner_id] = comparison_decrements.get(winner_id, 0) + 1
    comparison_count = len(seen_comparison_ids)
    await image_deletion.prepare_image_deletion(conn, expanded_image_ids)
    for chunk in _chunked(expanded_image_ids):
        placeholders = ",".join("?" for _ in chunk)
        await conn.execute(f"DELETE FROM images WHERE id IN ({placeholders})", chunk)
    if comparison_decrements:
        await conn.executemany(
            "UPDATE images SET comparisons = MAX(COALESCE(comparisons, 0) - ?, 0) WHERE id = ?",
            [(count, image_id) for image_id, count in comparison_decrements.items()],
        )
    return {"images_deleted": len(image_ids), "comparisons_deleted": comparison_count}


async def delete_image_catalog_rows(db_path: str, image_ids: list[int]) -> dict:
    conn = await connection.open_async(db_path)
    try:
        result = await delete_image_catalog_rows_on_conn(conn, image_ids)
        await update_source_counts_on_conn(conn)
        await conn.commit()
        return result
    finally:
        await connection.close_async(conn, db_path=db_path)


async def purge_source_catalog_data(db_path: str, source_id: int) -> dict:
    image_ids = await get_source_image_ids(db_path, source_id)
    conn = await connection.open_async(db_path)
    try:
        result = await delete_image_catalog_rows_on_conn(conn, image_ids)
        await conn.execute("DELETE FROM catalog_sources WHERE id = ?", (source_id,))
        await update_source_counts_on_conn(conn)
        await conn.commit()
        return {
            "images_deleted": result["images_deleted"],
            "comparisons_deleted": result["comparisons_deleted"],
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_scan_folder(db_path: str):
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT path FROM catalog_sources WHERE included = 1 "
            "ORDER BY last_scan_at IS NULL ASC, last_scan_at DESC, id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            return row["path"]
        cursor = await conn.execute(
            "SELECT filepath FROM images "
            "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL "
            "ORDER BY RANDOM() LIMIT 50"
        )
        rows = await cursor.fetchall()
        if not rows:
            return None
        dirs = [os.path.dirname(row["filepath"]) for row in rows]
        return safe_commonpath(dirs) or dirs[0]
    finally:
        await connection.close_async(conn, db_path=db_path)
