"""Import batch persistence helpers."""

import time as _time

from data import connection
from data.repositories.common import chunked as _chunked


async def create_import_batch(db_path: str, payload: dict) -> int:
    now = _time.time()
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "INSERT INTO import_batches "
            "(name, status, destination_mode, destination_root, destination_path, "
            "preserve_structure, total_files, created_at) "
            "VALUES (?, 'copying', ?, ?, ?, ?, ?, ?)",
            (
                payload.get("name", ""),
                payload.get("destination_mode", "date_shoot"),
                payload.get("destination_root", ""),
                payload.get("destination_path", ""),
                1 if payload.get("preserve_structure") else 0,
                int(payload.get("total_files") or 0),
                now,
            ),
        )
        await conn.commit()
        return int(cursor.lastrowid)
    finally:
        await connection.close_async(conn, db_path=db_path)


async def complete_import_batch(
    db_path: str,
    batch_id: int,
    *,
    source_id: int | None,
    image_rows: list[dict],
    imported_files: int,
    skipped_files: int,
    collision_count: int,
) -> None:
    now = _time.time()
    conn = await connection.open_async(db_path)
    try:
        if image_rows:
            await conn.executemany(
                "INSERT OR IGNORE INTO import_batch_images "
                "(batch_id, image_id, filepath, original_name, imported_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        int(batch_id),
                        int(row["image_id"]),
                        row.get("filepath", ""),
                        row.get("original_name", ""),
                        now,
                    )
                    for row in image_rows
                ],
            )
        await conn.execute(
            "UPDATE import_batches SET status = 'complete', source_id = ?, "
            "imported_files = ?, skipped_files = ?, collision_count = ?, completed_at = ? "
            "WHERE id = ?",
            (source_id, int(imported_files), int(skipped_files), int(collision_count), now, int(batch_id)),
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def fail_import_batch(db_path: str, batch_id: int, error: str) -> None:
    conn = await connection.open_async(db_path)
    try:
        await conn.execute(
            "UPDATE import_batches SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
            (str(error or "Import failed")[:500], _time.time(), int(batch_id)),
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def image_ids_by_filepaths(db_path: str, filepaths: list[str]) -> dict[str, int]:
    unique_paths = list(dict.fromkeys(path for path in filepaths if path))
    if not unique_paths:
        return {}
    found = {}
    conn = await connection.open_async(db_path)
    try:
        for chunk in _chunked(unique_paths, 900):
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                f"SELECT id, filepath FROM images WHERE filepath IN ({placeholders})",
                chunk,
            )
            for row in await cursor.fetchall():
                found[row["filepath"]] = int(row["id"])
        return found
    finally:
        await connection.close_async(conn, db_path=db_path)


async def import_batch(db_path: str, batch_id: int) -> dict | None:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT * FROM import_batches WHERE id = ?", (int(batch_id),))
        row = await cursor.fetchone()
        if row is None:
            return None
        batch = dict(row)
        cursor = await conn.execute(
            "SELECT image_id, filepath, original_name FROM import_batch_images "
            "WHERE batch_id = ? ORDER BY imported_at ASC, image_id ASC",
            (int(batch_id),),
        )
        batch["images"] = [dict(image_row) for image_row in await cursor.fetchall()]
        return batch
    finally:
        await connection.close_async(conn, db_path=db_path)


async def import_batch_image_ids(db_path: str, batch_id: int) -> set[int] | None:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT 1 FROM import_batches WHERE id = ?", (int(batch_id),))
        if await cursor.fetchone() is None:
            return None
        cursor = await conn.execute(
            "SELECT ibi.image_id FROM import_batch_images ibi "
            "JOIN images i ON i.id = ibi.image_id "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE ibi.batch_id = ? AND s.included = 1 "
            "AND i.status IN ('kept', 'maybe') "
            "AND i.missing_at IS NULL",
            (int(batch_id),),
        )
        return {int(row["image_id"]) for row in await cursor.fetchall()}
    finally:
        await connection.close_async(conn, db_path=db_path)
