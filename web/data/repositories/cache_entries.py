"""Which of these photographs already have a thumbnail at this size.

One question, one query. Around it sat two TTL dictionaries, a scope matcher, an
invalidator, a set-union patcher for ids that had just arrived, and full-set and
count variants of the query — none of which anything called. What survived is
the targeted lookup, which never used the caches: it took a `ttl_seconds` it
deleted on its first line.
"""

from data import connection


async def cached_image_ids(db_path: str, image_ids: list[int], size: str, cache_root: str) -> set[int]:
    if not image_ids or not size or not cache_root:
        return set()
    unique_ids = list(dict.fromkeys(int(image_id) for image_id in image_ids))
    present: set[int] = set()
    conn = await connection.open_async(db_path)
    try:
        for start in range(0, len(unique_ids), 900):
            chunk = unique_ids[start:start + 900]
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                "SELECT image_id FROM cache_entries "
                f"WHERE cache_root = ? AND size = ? AND image_id IN ({placeholders})",
                (cache_root, size, *chunk),
            )
            present.update(int(row["image_id"]) for row in await cursor.fetchall())
    finally:
        await connection.close_async(conn, db_path=db_path)
    return present
