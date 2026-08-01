"""Image lookup queries used by media, export, search, and compare flows."""


from data import connection
from data.repositories.catalog import active_image_condition, visible_image_condition
from data.repositories.common import chunked


async def get_image_by_id(db_path: str, image_id: int):
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT * FROM images WHERE id = ?", (image_id,))
        return await cursor.fetchone()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_media_image_by_id(db_path: str, image_id: int):
    """Load media-serving state together with its source availability context.

    Every tile the grid paints comes through here, so the primary-key fetch
    runs inline on the loop — a thread hop costs more than the query does,
    and under boot load a shared worker thread queues into whole seconds.
    """
    sql = (
        "SELECT i.*, s.path AS source_path, s.online AS source_online "
        "FROM images i LEFT JOIN catalog_sources s ON s.id = i.source_id "
        "WHERE i.id = ?"
    )
    if not connection.is_ephemeral_db_path(db_path):
        # Durable catalogs are WAL: reads never block, so the loop is the
        # fastest executor. Temp catalogs run without WAL (no sidecars), where
        # a busy writer blocks readers — those stay on the async path below,
        # which also keeps them deletable on Windows.
        try:
            return connection.inline_reader(db_path).execute(sql, (int(image_id),)).fetchone()
        except Exception:
            # A broken reader must never cost the caller its photo.
            connection.drop_inline_reader(db_path)
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(sql, (int(image_id),))
        return await cursor.fetchone()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_images_by_ids(db_path: str, image_ids: list[int]) -> dict[int, dict]:
    if not image_ids:
        return {}
    ids = list(image_ids)
    conn = await connection.open_async(db_path)
    try:
        results: dict[int, dict] = {}
        for chunk in chunked(ids):
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(f"SELECT * FROM images WHERE id IN ({placeholders})", chunk)
            for row in await cursor.fetchall():
                results[row["id"]] = dict(row)
        return results
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_active_images_by_ids(db_path: str, image_ids: list[int]) -> dict[int, dict]:
    if not image_ids:
        return {}
    unique_ids = list(dict.fromkeys(int(image_id) for image_id in image_ids))
    conn = await connection.open_async(db_path)
    try:
        results: dict[int, dict] = {}
        for chunk in chunked(unique_ids):
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                f"SELECT i.* FROM images i NOT INDEXED "
                f"JOIN catalog_sources s ON s.id = i.source_id "
                f"WHERE {active_image_condition()} "
                f"AND i.id IN ({placeholders})",
                chunk,
            )
            for row in await cursor.fetchall():
                results[row["id"]] = dict(row)
        return results
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_top_images(db_path: str, *, limit: int, catalog_counts: dict):
    active_images = int(catalog_counts.get("active_images") or 0)
    if active_images <= 0:
        return []
    all_catalog_images_active = (
        active_images == int(catalog_counts.get("total_catalog_images") or 0)
        and int(catalog_counts.get("removed_images") or 0) == 0
    )
    source_filter = (
        "AND i.source_id IN ("
        "SELECT id FROM catalog_sources WHERE included = 1"
        ") "
        if not all_catalog_images_active
        else ""
    )
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT i.id, i.filename, i.filepath, i.elo, i.comparisons, "
            "i.propagated_updates, COALESCE(i.stars, 0) AS stars, "
            "i.status, i.flag, i.orientation, i.aspect_ratio, i.date_taken, i.date_source, "
            "i.camera_make, i.camera_model, i.lens, i.file_ext, i.file_size, "
            "i.width, i.height, i.file_modified_at, i.latitude, i.longitude, i.created_at "
            "FROM images i INDEXED BY idx_images_active_elo "
            f"WHERE {visible_image_condition()} "
            f"{source_filter}"
            "ORDER BY i.elo DESC LIMIT ?",
            (limit,),
        )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def set_image_orientation(db_path: str, image_id: int, orientation: str):
    conn = await connection.open_async(db_path)
    try:
        await conn.execute(
            "UPDATE images SET orientation = ? WHERE id = ?",
            (orientation, image_id),
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_unclassified_images(
    db_path: str,
    limit: int = 200,
    *,
    file_extensions: set[str] | frozenset[str] | None = None,
):
    extension_clause = ""
    params: list = []
    if file_extensions:
        normalized_extensions = sorted({
            form
            for extension in file_extensions
            for form in {
                str(extension).strip().lower().lstrip("."),
                f".{str(extension).strip().lower().lstrip('.')}",
            }
            if form and form != "."
        })
        placeholders = ",".join("?" for _extension in normalized_extensions)
        extension_clause = f"AND LOWER(COALESCE(i.file_ext, '')) IN ({placeholders}) "
        params.extend(normalized_extensions)
    params.append(limit)
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT i.id, i.filepath, s.path AS source_root FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            f"WHERE i.orientation IS NULL AND {active_image_condition()} "
            "AND s.online = 1 "
            "AND COALESCE(i.hub_remote, 0) = 0 "
            f"{extension_clause}"
            "LIMIT ?",
            params,
        )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def batch_set_orientations(db_path: str, updates: list[tuple[str, float, int]]):
    conn = await connection.open_async(db_path)
    try:
        await conn.executemany(
            "UPDATE images SET orientation = ?, aspect_ratio = ? WHERE id = ?",
            updates,
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_images_needing_metadata(
    db_path: str,
    limit: int = 100,
    metadata_version: int = 1,
):
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT i.id, i.filepath, s.path AS source_root FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND s.online = 1 AND ("
            "i.metadata_scanned_at IS NULL "
            "OR i.metadata_version IS NULL "
            "OR i.metadata_version < ?) "
            f"AND {visible_image_condition()} "
            "AND COALESCE(i.hub_remote, 0) = 0 "
            "LIMIT ?",
            (metadata_version, limit),
        )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def batch_update_metadata(db_path: str, updates: list[tuple]):
    if not updates:
        return
    conn = await connection.open_async(db_path)
    try:
        await conn.executemany(
            "UPDATE images SET "
            "date_taken = CASE "
            "WHEN ? = 'exif' AND ? IS NOT NULL THEN ? "
            "WHEN date_taken IS NULL OR date_taken = '' THEN ? "
            "ELSE date_taken END, "
            "date_source = CASE "
            "WHEN ? = 'exif' AND ? IS NOT NULL THEN 'exif' "
            "WHEN (date_taken IS NULL OR date_taken = '') AND ? IS NOT NULL THEN ? "
            "WHEN (date_source IS NULL OR date_source = '') AND date_taken IS NOT NULL AND date_taken != '' THEN 'exif' "
            "ELSE date_source END, "
            "camera_make = COALESCE(?, camera_make), "
            "camera_model = COALESCE(?, camera_model), "
            "lens = COALESCE(?, lens), "
            "file_ext = COALESCE(?, file_ext), "
            "file_size = COALESCE(?, file_size), "
            "file_modified_at = COALESCE(?, file_modified_at), "
            "width = COALESCE(?, width), "
            "height = COALESCE(?, height), "
            "metadata_scanned_at = ?, metadata_version = ?, "
            "orientation = COALESCE(orientation, ?), "
            "aspect_ratio = COALESCE(aspect_ratio, ?), "
            "latitude = COALESCE(?, latitude), "
            "longitude = COALESCE(?, longitude) "
            "WHERE id = ?",
            updates,
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_recent_active_images(db_path: str, limit: int = 10):
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT i.id, i.filename, i.filepath FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            f"WHERE {active_image_condition()} "
            "ORDER BY i.id DESC LIMIT ?",
            (limit,),
        )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def set_image_status(db_path: str, image_id: int, status: str):
    conn = await connection.open_async(db_path)
    try:
        await conn.execute(
            "UPDATE images SET status = ? WHERE id = ?",
            (status, image_id),
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def set_image_flag(db_path: str, image_id: int, flag: str):
    async def _write() -> None:
        conn = await connection.open_async(db_path)
        try:
            await conn.execute(
                "UPDATE images SET flag = ? WHERE id = ?",
                (flag, image_id),
            )
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=db_path)

    await connection.run_with_busy_retry(_write)


async def batch_set_image_flags(
    db_path: str,
    image_ids: list[int],
    flag: str,
    chunk_size: int = 500,
) -> int:
    if not image_ids:
        return 0

    async def _write() -> int:
        conn = await connection.open_async(db_path)
        updated = 0
        try:
            for start in range(0, len(image_ids), chunk_size):
                chunk = image_ids[start:start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                await conn.execute(
                    f"UPDATE images SET flag = ? WHERE id IN ({placeholders})",
                    [flag] + chunk,
                )
                updated += len(chunk)
                await conn.commit()
            return updated
        finally:
            await connection.close_async(conn, db_path=db_path)

    return await connection.run_with_busy_retry(_write)
