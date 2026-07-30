"""Rating write queries for compare, mosaic, and undo workflows."""

import asyncio
import random
import time as _time

from data import connection
from data.repositories.common import chunked as _chunked

VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS = 30.0
# Sampler-only orders: the caller wants a bounded window that moves between
# calls, not a stable page of a ranking. Never use them for grid pagination.
PAIRING_SAMPLE_ORDERS = ("least_compared_shuffled", "random", "near_elo")
PAIRING_SAMPLE_ROUNDS = 3
PAIRING_SAMPLE_OVERSAMPLE = 2.0
PAIRING_SAMPLE_MAX_OVERSAMPLE = 20.0
PAIRING_SAMPLE_MAX_DRAW = 20000
_PAIRING_ID_CHUNK = 900
_visible_pairing_pool_counts_cache: dict[tuple, dict] = {}
_past_matchups_cache = {"data": None, "signature": None}


def _cache_scope_matches(
    cache_root: str,
    size: str,
    target_root: str | None,
    target_size: str | None,
) -> bool:
    return (
        (target_root is None or cache_root == target_root)
        and (target_size is None or size == target_size)
    )


def invalidate_visible_pairing_pool_counts_cache(
    cache_root: str | None = None,
    size: str | None = None,
) -> None:
    if cache_root is None and size is None:
        _visible_pairing_pool_counts_cache.clear()
        return
    for key in list(_visible_pairing_pool_counts_cache.keys()):
        key_root, key_size = key[:2]
        if _cache_scope_matches(key_root, key_size, cache_root, size):
            _visible_pairing_pool_counts_cache.pop(key, None)


def invalidate_past_matchups_cache() -> None:
    _past_matchups_cache["data"] = None
    _past_matchups_cache["signature"] = None


def _was_rated(row: dict) -> bool:
    return (
        int(row.get("comparisons") or 0) > 0
        or int(row.get("propagated_updates") or 0) > 0
        or abs(float(row.get("elo") or 1200.0) - 1200.0) > 0.0001
    )


def _all_catalog_images_active(catalog_counts: dict) -> bool:
    return (
        int(catalog_counts.get("active_images") or 0) > 0
        and int(catalog_counts.get("active_images") or 0)
        == int(catalog_counts.get("total_catalog_images") or 0)
        and int(catalog_counts.get("removed_images") or 0) == 0
    )


async def active_images_for_pairing(db_path: str, *, catalog_counts: dict):
    active_images = int(catalog_counts.get("active_images") or 0)
    if active_images <= 0:
        return []
    all_catalog_images_active = _all_catalog_images_active(catalog_counts)
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
            "i.propagated_updates, i.status, i.flag, i.orientation, "
            "i.aspect_ratio, i.date_taken, i.date_source, i.camera_make, i.camera_model, "
            "i.lens, i.file_ext FROM images i "
            "WHERE i.status IN ('kept', 'maybe') "
            f"{source_filter}"
            "AND i.missing_at IS NULL"
        )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_active_images_for_pairing(db_path: str, *, get_catalog_image_counts):
    """Get active images sorted by Elo for Swiss-system pairing."""
    return await active_images_for_pairing(
        db_path,
        catalog_counts=await get_catalog_image_counts(),
    )


_PAIRING_COLUMNS = (
    "i.id, i.filename, i.filepath, i.elo, i.comparisons, "
    "i.propagated_updates, i.status, i.flag, i.orientation, "
    "i.aspect_ratio, i.date_taken, i.date_source, i.camera_make, i.camera_model, "
    "i.lens, i.file_ext"
)
_PAIRING_CARD_COLUMNS = (
    "i.file_size, i.file_modified_at, i.width, i.height, "
    "i.latitude, i.longitude, i.created_at"
)
_PAIRING_VISIBLE_WHERE = (
    "WHERE i.status IN ('kept', 'maybe') "
    "AND s.included = 1 AND i.missing_at IS NULL "
    "AND EXISTS ("
    "  SELECT 1 FROM cache_entries c "
    "  WHERE c.cache_root = ? AND c.size = ? AND c.image_id = i.id"
    ") "
)


def _pairing_projection(include_card_metadata: bool) -> str:
    if include_card_metadata:
        return f"SELECT {_PAIRING_COLUMNS}, {_PAIRING_CARD_COLUMNS} "
    return f"SELECT {_PAIRING_COLUMNS} "


def _pairing_sample_source(index: str) -> str:
    return (
        f"FROM images i INDEXED BY {index} "
        "JOIN catalog_sources s ON s.id = i.source_id "
        f"{_PAIRING_VISIBLE_WHERE}"
    )


async def _pairing_rows(conn, sql: str, params: list) -> list:
    cursor = await conn.execute(sql, params)
    return list(await cursor.fetchall())


async def _max_image_id(conn) -> int:
    cursor = await conn.execute("SELECT MAX(id) AS max_id FROM images")
    row = await cursor.fetchone()
    if row is None:
        return 0
    return int(row["max_id"] or 0)


async def _rotated_least_compared_sample(
    conn,
    projection: str,
    cache_root: str,
    size: str,
    limit: int,
) -> list:
    """Explore reservoir: rotate the least-compared window by a random rowid.

    Tens of thousands of images tie at comparisons=0 and elo=1200.0, so a plain
    `comparisons ASC, elo DESC LIMIT n` returns the same ids on every call and
    Explore samples the same sliver of the archive forever. Rotating the window
    keeps the least-compared bias and the covering index; a RANDOM() tie-break
    instead measured 254ms against 11ms because it sorts the whole tie block.
    """
    pivot = random.randint(1, max(1, await _max_image_id(conn)))
    sql = (
        f"{projection}{_pairing_sample_source('idx_images_visible_comparisons_elo')}"
        "AND i.id {operator} ? ORDER BY i.comparisons ASC, i.elo DESC LIMIT ?"
    )
    rows = await _pairing_rows(
        conn, sql.format(operator=">="), [cache_root, size, pivot, limit]
    )
    if len(rows) < limit:
        rows += await _pairing_rows(
            conn, sql.format(operator="<"), [cache_root, size, pivot, limit - len(rows)]
        )
    return rows


async def _cached_ids_sample(
    conn,
    cache_root: str,
    size: str,
    limit: int,
    max_id: int,
) -> list[list[int]]:
    """Random-pivot window of ids that already have a preview in this tier.

    Returns one or two id batches (the second is the wrap-around arm) taken
    straight off the cache_entries index, so every id handed back is eligible
    for the tier before the visibility filters run.
    """
    pivot = random.randint(1, max(1, max_id))
    # Oversample lightly: a few cached ids are hidden, trashed, or missing.
    want = max(limit, int(limit * 1.3))
    sql = (
        "SELECT image_id FROM cache_entries "
        "WHERE cache_root = ? AND size = ? AND image_id >= ? "
        "ORDER BY image_id LIMIT ?"
    )
    cursor = await conn.execute(sql, (cache_root, size, pivot, want))
    ahead = [int(row["image_id"]) for row in await cursor.fetchall()]
    batches = [ahead] if ahead else []
    if len(ahead) < want:
        cursor = await conn.execute(sql, (cache_root, size, 1, want - len(ahead)))
        behind = [int(row["image_id"]) for row in await cursor.fetchall()]
        if behind:
            batches.append(behind)
    return batches


async def _random_visible_sample(
    conn,
    projection: str,
    cache_root: str,
    size: str,
    limit: int,
) -> list:
    """Random reservoir: draw random rowids instead of the whole universe.

    The random strategy weights every candidate equally, so a bounded random
    draw is equivalent to materializing every visible row and far cheaper
    (20ms against 1107ms for a 103k-image catalog). Oversample because some
    drawn ids are absent, hidden, or not cached in this tier yet.
    """
    max_id = await _max_image_id(conn)
    if max_id <= 0:
        return []
    sql_template = (
        f"{projection}FROM images i "
        "JOIN catalog_sources s ON s.id = i.source_id "
        f"{_PAIRING_VISIBLE_WHERE}AND i.id IN ({{placeholders}})"
    )
    found: dict[int, object] = {}
    # Draw from the cache table, which *is* the eligibility set for this tier.
    # Guessing ids out of the whole id space needs one round per miss, and a
    # satellite whose md coverage sits near a quarter of the catalog paid
    # seconds for it (measured 2.6s). One indexed range scan at a random pivot
    # is dense by construction, wherever coverage stands.
    for cached in await _cached_ids_sample(conn, cache_root, size, limit, max_id):
        for chunk in _chunked(cached, _PAIRING_ID_CHUNK):
            sql = sql_template.format(placeholders=",".join("?" for _ in chunk))
            for row in await _pairing_rows(conn, sql, [cache_root, size, *chunk]):
                found[int(row["id"])] = row
        if len(found) >= limit:
            return list(found.values())[:limit]
    drawn = 0
    oversample = PAIRING_SAMPLE_OVERSAMPLE
    for _round in range(PAIRING_SAMPLE_ROUNDS):
        need = limit - len(found)
        if need <= 0 or drawn >= PAIRING_SAMPLE_MAX_DRAW:
            break
        draw_size = min(
            max_id,
            max(need, int(need * oversample)),
            PAIRING_SAMPLE_MAX_DRAW - drawn,
        )
        drawn += draw_size
        for chunk in _chunked(random.sample(range(1, max_id + 1), draw_size), _PAIRING_ID_CHUNK):
            sql = sql_template.format(placeholders=",".join("?" for _ in chunk))
            for row in await _pairing_rows(conn, sql, [cache_root, size, *chunk]):
                found[int(row["id"])] = row
            if len(found) >= limit:
                break
        if not found:
            break
        # Widen the next draw for a tier that only covers part of the catalog.
        oversample = min(PAIRING_SAMPLE_MAX_OVERSAMPLE, max(oversample, 1.5 * drawn / len(found)))
    if len(found) < limit:
        # Too sparse to fill by drawing ids: top up from a dense rotated window
        # so the grid still fills, and still from a window that moves.
        for row in await _rotated_least_compared_sample(
            conn, projection, cache_root, size, limit
        ):
            found.setdefault(int(row["id"]), row)
            if len(found) >= limit:
                break
    return list(found.values())[:limit]


async def _near_elo_visible_sample(
    conn,
    projection: str,
    cache_root: str,
    size: str,
    limit: int,
    elo_pivot: float | None,
) -> list:
    """Compete reservoir: two index range scans around the grid's rating.

    Compete wants opponents rated like the grid, which the full-universe scan
    paid 1107ms to find. Range scans either side of the pivot cost 17-34ms, and
    the random rowid rotation stops the images tied at 1200.0 from returning
    the same window on every call.
    """
    if elo_pivot is None:
        return await _random_visible_sample(conn, projection, cache_root, size, limit)
    pivot_id = random.randint(1, max(1, await _max_image_id(conn)))
    below = _pairing_sample_source("idx_images_active_elo")
    above = _pairing_sample_source("idx_images_active_elo_asc")
    arms = (
        (f"{projection}{below}AND i.elo <= ? AND i.id >= ? ORDER BY i.elo DESC LIMIT ?", max(1, limit // 2)),
        (f"{projection}{above}AND i.elo > ? AND i.id >= ? ORDER BY i.elo ASC LIMIT ?", limit),
        (f"{projection}{below}AND i.elo <= ? AND i.id < ? ORDER BY i.elo DESC LIMIT ?", limit),
        (f"{projection}{above}AND i.elo > ? AND i.id < ? ORDER BY i.elo ASC LIMIT ?", limit),
    )
    rows: list = []
    for sql, arm_limit in arms:
        remaining = min(arm_limit, limit - len(rows))
        if remaining <= 0:
            break
        rows += await _pairing_rows(
            conn, sql, [cache_root, size, float(elo_pivot), pivot_id, remaining]
        )
    return rows


async def visible_images_for_pairing(
    db_path: str,
    size: str,
    cache_root: str,
    *,
    include_card_metadata: bool = True,
    limit: int | None = None,
    order: str = "elo",
    elo_pivot: float | None = None,
):
    if not size or not cache_root:
        return []
    bounded_limit = int(limit) if limit and int(limit) > 0 else 0
    if order in PAIRING_SAMPLE_ORDERS:
        if bounded_limit <= 0:
            # A sampler order without a window has no meaning; fall back to the
            # deterministic order with the same bias.
            order = "least_compared" if order == "least_compared_shuffled" else "elo"
        else:
            projection = _pairing_projection(include_card_metadata)
            conn = await connection.open_async(db_path)
            try:
                if order == "least_compared_shuffled":
                    return await _rotated_least_compared_sample(
                        conn, projection, cache_root, size, bounded_limit
                    )
                if order == "random":
                    return await _random_visible_sample(
                        conn, projection, cache_root, size, bounded_limit
                    )
                return await _near_elo_visible_sample(
                    conn, projection, cache_root, size, bounded_limit, elo_pivot
                )
            finally:
                await connection.close_async(conn, db_path=db_path)
    limit_sql = " LIMIT ?" if limit and limit > 0 else ""
    params = [cache_root, size]
    if limit_sql:
        params.append(int(limit))
    if order == "least_compared":
        order_sql = " ORDER BY i.comparisons ASC, i.elo DESC"
    elif order == "cache":
        order_sql = ""
    else:
        order_sql = " ORDER BY i.elo DESC"
    if order in {"elo", "least_compared"} and limit and limit > 0:
        image_source = (
            "images i INDEXED BY idx_images_visible_comparisons_elo"
            if order == "least_compared"
            else "images i INDEXED BY idx_images_active_elo"
        )
        from_sql = (
            f"FROM {image_source} "
            "JOIN catalog_sources s ON s.id = i.source_id "
        )
        where_sql = (
            "WHERE i.status IN ('kept', 'maybe') "
            "AND s.included = 1 AND i.missing_at IS NULL "
            "AND EXISTS ("
            "  SELECT 1 FROM cache_entries c "
            "  WHERE c.cache_root = ? AND c.size = ? AND c.image_id = i.id"
            ") "
        )
    else:
        from_sql = (
            "FROM cache_entries c "
            "JOIN images i ON i.id = c.image_id "
            "JOIN catalog_sources s ON s.id = i.source_id "
        )
        where_sql = (
            "WHERE c.cache_root = ? AND c.size = ? "
            "AND i.status IN ('kept', 'maybe') "
            "AND s.included = 1 AND i.missing_at IS NULL "
        )
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            f"{_pairing_projection(include_card_metadata)}"
            f"{from_sql}"
            f"{where_sql}"
            f"{order_sql}{limit_sql}",
            params,
        )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def get_visible_images_for_pairing(
    db_path: str,
    size: str,
    cache_root: str,
    *,
    include_card_metadata: bool = True,
    limit: int | None = None,
    order: str = "elo",
    elo_pivot: float | None = None,
):
    """Return visible active pairing rows for one thumbnail tier, sorted by Elo."""
    return await visible_images_for_pairing(
        db_path,
        size,
        cache_root,
        include_card_metadata=include_card_metadata,
        limit=limit,
        order=order,
        elo_pivot=elo_pivot,
    )


async def visible_pairing_pool_counts(
    db_path: str,
    *,
    catalog_counts: dict,
    size: str,
    cache_root: str,
) -> dict:
    active_images = int(catalog_counts.get("active_images") or 0)
    all_catalog_images_active = _all_catalog_images_active(catalog_counts)
    all_sources_available = int(catalog_counts.get("removed_images") or 0) == 0
    conn = await connection.open_async(db_path)
    try:
        if all_catalog_images_active or all_sources_available:
            cursor = await conn.execute(
                "SELECT COUNT(*) AS count FROM cache_entries c "
                "JOIN images i ON i.id = c.image_id "
                "WHERE c.cache_root = ? AND c.size = ? "
                "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
                (cache_root, size),
            )
        else:
            cursor = await conn.execute(
                "SELECT COUNT(*) AS count FROM cache_entries c "
                "JOIN images i ON i.id = c.image_id "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE c.cache_root = ? AND c.size = ? "
                "AND s.included = 1 "
                "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
                (cache_root, size),
            )
        visible_images = min(active_images, int((await cursor.fetchone())["count"] or 0))
        return {"active_images": active_images, "visible_images": visible_images}
    finally:
        await connection.close_async(conn, db_path=db_path)


async def visible_pairing_pool_counts_cached(
    db_path: str,
    *,
    get_catalog_image_counts,
    size: str,
    cache_root: str,
    ttl_seconds: float = VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS,
) -> dict:
    if not size or not cache_root:
        counts = await get_catalog_image_counts()
        return {"active_images": int(counts.get("active_images") or 0), "visible_images": 0}
    cache_key = (cache_root, size)
    now = _time.time()
    cached = _visible_pairing_pool_counts_cache.get(cache_key)
    if cached and cached["expires"] > now:
        return dict(cached["data"])
    counts = await get_catalog_image_counts()
    result = await visible_pairing_pool_counts(
        db_path,
        catalog_counts=counts,
        size=size,
        cache_root=cache_root,
    )
    _visible_pairing_pool_counts_cache[cache_key] = {
        "data": result,
        "expires": _time.time() + ttl_seconds,
    }
    return dict(result)


async def get_visible_pairing_pool_counts(
    db_path: str,
    *,
    get_catalog_image_counts,
    size: str,
    cache_root: str,
    ttl_seconds: float = VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS,
) -> dict:
    """Return active and visible counts for the default Compare/Mosaic pool."""
    return await visible_pairing_pool_counts_cached(
        db_path,
        get_catalog_image_counts=get_catalog_image_counts,
        size=size,
        cache_root=cache_root,
        ttl_seconds=ttl_seconds,
    )


async def visible_orientation_pairing_pool_counts(
    db_path: str,
    *,
    catalog_counts: dict,
    size: str,
    cache_root: str,
    orientation: str,
) -> dict:
    all_catalog_images_active = _all_catalog_images_active(catalog_counts)
    all_sources_available = int(catalog_counts.get("removed_images") or 0) == 0
    conn = await connection.open_async(db_path)
    try:
        if all_catalog_images_active or all_sources_available:
            active_cursor = await conn.execute(
                "SELECT COUNT(*) AS count FROM images INDEXED BY idx_images_active_orientation_count "
                "WHERE orientation = ? AND status IN ('kept', 'maybe') AND missing_at IS NULL",
                (orientation,),
            )
            visible_cursor = await conn.execute(
                "SELECT COUNT(*) AS count FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                "CROSS JOIN images i "
                "WHERE c.cache_root = ? AND c.size = ? "
                "AND i.id = c.image_id "
                "AND i.orientation = ? AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
                (cache_root, size, orientation),
            )
        else:
            active_cursor = await conn.execute(
                "SELECT COUNT(*) AS count FROM images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 "
                "AND i.orientation = ? AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
                (orientation,),
            )
            visible_cursor = await conn.execute(
                "SELECT COUNT(*) AS count FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                "CROSS JOIN images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE c.cache_root = ? AND c.size = ? "
                "AND i.id = c.image_id "
                "AND s.included = 1 "
                "AND i.orientation = ? AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
                (cache_root, size, orientation),
            )
        return {
            "active_images": int((await active_cursor.fetchone())["count"] or 0),
            "visible_images": int((await visible_cursor.fetchone())["count"] or 0),
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def visible_orientation_pairing_pool_counts_cached(
    db_path: str,
    *,
    get_catalog_image_counts,
    count_rankings,
    size: str,
    cache_root: str,
    orientation: str,
    ttl_seconds: float = VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS,
) -> dict:
    orientation = (orientation or "").strip()
    if not orientation:
        return await visible_pairing_pool_counts_cached(
            db_path,
            get_catalog_image_counts=get_catalog_image_counts,
            size=size,
            cache_root=cache_root,
            ttl_seconds=ttl_seconds,
        )
    if not size or not cache_root:
        active = await count_rankings(orientation=orientation)
        return {"active_images": int(active), "visible_images": 0}
    cache_key = (cache_root, size, "orientation", orientation)
    now = _time.time()
    cached = _visible_pairing_pool_counts_cache.get(cache_key)
    if cached and cached["expires"] > now:
        return dict(cached["data"])
    counts = await get_catalog_image_counts()
    result = await visible_orientation_pairing_pool_counts(
        db_path,
        catalog_counts=counts,
        size=size,
        cache_root=cache_root,
        orientation=orientation,
    )
    _visible_pairing_pool_counts_cache[cache_key] = {
        "data": result,
        "expires": _time.time() + ttl_seconds,
    }
    return dict(result)


async def get_visible_orientation_pairing_pool_counts(
    db_path: str,
    *,
    get_catalog_image_counts,
    count_rankings,
    size: str,
    cache_root: str,
    orientation: str,
    ttl_seconds: float = VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS,
) -> dict:
    """Return active and visible counts for a simple orientation-filtered pool."""
    return await visible_orientation_pairing_pool_counts_cached(
        db_path,
        get_catalog_image_counts=get_catalog_image_counts,
        count_rankings=count_rankings,
        size=size,
        cache_root=cache_root,
        orientation=orientation,
        ttl_seconds=ttl_seconds,
    )


def load_past_matchups(db_path: str) -> tuple[tuple[int, int | None], set[tuple[int, int]]]:
    conn = connection.open_sync(db_path)
    try:
        signature_key = _past_matchups_signature_on_conn(conn)
        rows = conn.execute("SELECT winner_id, loser_id FROM comparisons").fetchall()
        return signature_key, {
            (min(winner_id, loser_id), max(winner_id, loser_id))
            for winner_id, loser_id in rows
        }
    finally:
        connection.close_sync(conn, db_path=db_path)


def past_matchups_cached(db_path: str, *, active_source_ids) -> set[tuple[int, int]]:
    if not active_source_ids:
        return set()

    if _past_matchups_cache["data"] is not None:
        signature = past_matchups_signature(db_path)
        if _past_matchups_cache["signature"] == signature:
            return set(_past_matchups_cache["data"])

    signature, loaded = load_past_matchups(db_path)
    _past_matchups_cache["signature"] = signature
    _past_matchups_cache["data"] = loaded
    return set(loaded)


async def get_past_matchups(db_path: str, *, get_active_source_id_set) -> set[tuple[int, int]]:
    """Return set of (min_id, max_id) tuples for all past matchups."""
    return await asyncio.to_thread(
        past_matchups_cached,
        db_path,
        active_source_ids=await get_active_source_id_set(),
    )


def _past_matchups_signature_on_conn(conn) -> tuple[int, int | None]:
    signature = conn.execute(
        "SELECT COUNT(*) AS count, MAX(id) AS max_id FROM comparisons"
    ).fetchone()
    return (
        int(signature[0] or 0),
        int(signature[1]) if signature[1] is not None else None,
    )


def past_matchups_signature(db_path: str) -> tuple[int, int | None]:
    conn = connection.open_sync(db_path)
    try:
        return _past_matchups_signature_on_conn(conn)
    finally:
        connection.close_sync(conn, db_path=db_path)


def load_visible_past_matchups(
    db_path: str,
    *,
    size: str,
    cache_root: str,
) -> set[tuple[int, int]]:
    conn = connection.open_sync(db_path)
    try:
        rows = conn.execute(
            "SELECT c.winner_id, c.loser_id FROM comparisons c "
            "JOIN cache_entries cw ON cw.image_id = c.winner_id "
            "AND cw.cache_root = ? AND cw.size = ? "
            "JOIN cache_entries cl ON cl.image_id = c.loser_id "
            "AND cl.cache_root = ? AND cl.size = ?",
            (cache_root, size, cache_root, size),
        ).fetchall()
        return {(min(winner_id, loser_id), max(winner_id, loser_id)) for winner_id, loser_id in rows}
    finally:
        connection.close_sync(conn, db_path=db_path)


async def get_visible_past_matchups(
    db_path: str,
    size: str,
    cache_root: str,
) -> set[tuple[int, int]]:
    """Return past matchup pairs where both images are visible in one cache tier."""
    if not size or not cache_root:
        return set()
    return await asyncio.to_thread(
        load_visible_past_matchups,
        db_path,
        size=size,
        cache_root=cache_root,
    )


def load_past_matchups_for_image_ids(
    db_path: str,
    *,
    image_ids: list[int],
) -> set[tuple[int, int]]:
    candidate_ids = list(dict.fromkeys(int(image_id) for image_id in image_ids or [] if int(image_id) > 0))
    if len(candidate_ids) < 2:
        return set()

    if len(candidate_ids) <= 2000:
        candidate_set = set(candidate_ids)
        matchups = set()
        conn = connection.open_sync(db_path)
        try:
            for chunk in _chunked(candidate_ids, 900):
                placeholders = ",".join("?" for _ in chunk)
                for winner_id, loser_id in conn.execute(
                    f"SELECT winner_id, loser_id FROM comparisons WHERE winner_id IN ({placeholders})",
                    chunk,
                ):
                    if loser_id in candidate_set:
                        matchups.add((min(winner_id, loser_id), max(winner_id, loser_id)))
                for winner_id, loser_id in conn.execute(
                    f"SELECT winner_id, loser_id FROM comparisons WHERE loser_id IN ({placeholders})",
                    chunk,
                ):
                    if winner_id in candidate_set:
                        matchups.add((min(winner_id, loser_id), max(winner_id, loser_id)))
            return matchups
        finally:
            connection.close_sync(conn, db_path=db_path)

    conn = connection.open_sync(db_path)
    try:
        conn.execute("CREATE TEMP TABLE candidate_ids(id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO candidate_ids(id) VALUES (?)", [(image_id,) for image_id in candidate_ids])
        rows = conn.execute(
            "SELECT c.winner_id, c.loser_id FROM comparisons c "
            "JOIN candidate_ids w ON w.id = c.winner_id "
            "JOIN candidate_ids l ON l.id = c.loser_id"
        ).fetchall()
        return {(min(winner_id, loser_id), max(winner_id, loser_id)) for winner_id, loser_id in rows}
    finally:
        connection.close_sync(conn, db_path=db_path)


async def get_past_matchups_for_image_ids(
    db_path: str,
    image_ids: list[int],
) -> set[tuple[int, int]]:
    """Return past matchup pairs where both images are in a bounded candidate set."""
    ids = list(dict.fromkeys(int(image_id) for image_id in image_ids or [] if int(image_id) > 0))
    if len(ids) < 2:
        return set()
    return await asyncio.to_thread(
        load_past_matchups_for_image_ids,
        db_path,
        image_ids=ids,
    )


async def record_comparison(
    db_path: str,
    *,
    winner_id: int,
    loser_id: int,
    mode: str,
    elo_before_winner: float,
    elo_before_loser: float,
    new_winner_elo: float,
    new_loser_elo: float,
    action_id: str | None = None,
) -> None:
    conn = await connection.open_async(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        await conn.execute(
            "INSERT INTO comparisons "
            "(winner_id, loser_id, mode, elo_before_winner, elo_before_loser, "
            "elo_delta_winner, elo_delta_loser, action_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                winner_id,
                loser_id,
                mode,
                elo_before_winner,
                elo_before_loser,
                new_winner_elo - elo_before_winner,
                new_loser_elo - elo_before_loser,
                action_id,
            ),
        )
        await conn.execute(
            "UPDATE images SET elo = ?, comparisons = COALESCE(comparisons, 0) + 1 WHERE id = ?",
            (new_winner_elo, winner_id),
        )
        await conn.execute(
            "UPDATE images SET elo = ?, comparisons = COALESCE(comparisons, 0) + 1 WHERE id = ?",
            (new_loser_elo, loser_id),
        )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=db_path)


async def record_active_comparison(
    db_path: str,
    *,
    winner_id: int,
    loser_id: int,
    mode: str,
    action_id: str | None = None,
    catalog_counts: dict,
) -> dict | None:
    if winner_id == loser_id:
        return None

    import pairing

    conn = await connection.open_async(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        if _all_catalog_images_active(catalog_counts):
            cursor = await conn.execute(
                "SELECT i.id, i.elo, COALESCE(i.comparisons, 0) AS comparisons, "
                "COALESCE(i.propagated_updates, 0) AS propagated_updates "
                "FROM images i NOT INDEXED "
                "WHERE i.status IN ('kept', 'maybe') "
                "AND i.missing_at IS NULL AND i.id IN (?, ?)",
                (winner_id, loser_id),
            )
        else:
            cursor = await conn.execute(
                "SELECT i.id, i.elo, COALESCE(i.comparisons, 0) AS comparisons, "
                "COALESCE(i.propagated_updates, 0) AS propagated_updates "
                "FROM images i NOT INDEXED "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 "
                "AND i.status IN ('kept', 'maybe') "
                "AND i.missing_at IS NULL "
                "AND i.id IN (?, ?)",
                (winner_id, loser_id),
            )
        rows = {row["id"]: dict(row) for row in await cursor.fetchall()}
        winner = rows.get(winner_id)
        loser = rows.get(loser_id)
        if not winner or not loser:
            await conn.rollback()
            return None

        k = pairing.get_k_factor(min(winner["comparisons"], loser["comparisons"]), mode)
        new_winner_elo, new_loser_elo = pairing.update_elo(winner["elo"], loser["elo"], k)
        await conn.execute(
            "INSERT INTO comparisons "
            "(winner_id, loser_id, mode, elo_before_winner, elo_before_loser, "
            "elo_delta_winner, elo_delta_loser, action_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                winner_id,
                loser_id,
                mode,
                winner["elo"],
                loser["elo"],
                new_winner_elo - winner["elo"],
                new_loser_elo - loser["elo"],
                action_id,
            ),
        )
        await conn.execute(
            "UPDATE images SET "
            "elo = CASE id WHEN ? THEN ? WHEN ? THEN ? ELSE elo END, "
            "comparisons = COALESCE(comparisons, 0) + 1 "
            "WHERE id IN (?, ?)",
            (winner_id, new_winner_elo, loser_id, new_loser_elo, winner_id, loser_id),
        )
        await conn.commit()
        rated_delta = (0 if _was_rated(winner) else 1) + (0 if _was_rated(loser) else 1)
        return {
            "winner_elo_before": winner["elo"],
            "loser_elo_before": loser["elo"],
            "winner_elo": new_winner_elo,
            "loser_elo": new_loser_elo,
            "k": k,
            "_rated_delta": rated_delta,
        }
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=db_path)


async def record_active_mosaic_pick(
    db_path: str,
    *,
    picked_id: int,
    other_ids: list[int],
    action_id: str,
    catalog_counts: dict,
) -> dict:
    if not other_ids:
        return {"ok": False, "missing_ids": []}
    all_ids = [picked_id] + list(other_ids)
    unique_ids = list(dict.fromkeys(int(image_id) for image_id in all_ids))

    import pairing

    conn = await connection.open_async(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in unique_ids)
        if _all_catalog_images_active(catalog_counts):
            cursor = await conn.execute(
                "SELECT i.id, i.elo, COALESCE(i.comparisons, 0) AS comparisons, "
                "COALESCE(i.propagated_updates, 0) AS propagated_updates "
                "FROM images i NOT INDEXED "
                "WHERE i.status IN ('kept', 'maybe') "
                "AND i.missing_at IS NULL "
                f"AND i.id IN ({placeholders})",
                unique_ids,
            )
        else:
            cursor = await conn.execute(
                "SELECT i.id, i.elo, COALESCE(i.comparisons, 0) AS comparisons, "
                "COALESCE(i.propagated_updates, 0) AS propagated_updates "
                "FROM images i NOT INDEXED "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 "
                "AND i.status IN ('kept', 'maybe') "
                "AND i.missing_at IS NULL "
                f"AND i.id IN ({placeholders})",
                unique_ids,
            )
        images = {row["id"]: dict(row) for row in await cursor.fetchall()}
        missing_ids = [image_id for image_id in all_ids if image_id not in images]
        if missing_ids:
            await conn.rollback()
            return {"ok": False, "missing_ids": missing_ids}

        picked_elo = images[picked_id]["elo"]
        comparison_rows = []
        loser_updates = []
        for other_id in other_ids:
            other = images[other_id]
            new_picked, new_other = pairing.update_elo(picked_elo, other["elo"], k=12.0)
            comparison_rows.append(
                (
                    picked_id,
                    other_id,
                    picked_elo,
                    other["elo"],
                    new_picked - picked_elo,
                    new_other - other["elo"],
                    action_id,
                )
            )
            loser_updates.append((other_id, new_other))
            picked_elo = new_picked

        if comparison_rows:
            row_placeholders = ",".join(
                "(?, ?, 'mosaic', ?, ?, ?, ?, ?)" for _row in comparison_rows
            )
            comparison_params = [
                value
                for row in comparison_rows
                for value in row
            ]
            await conn.execute(
                "INSERT INTO comparisons "
                "(winner_id, loser_id, mode, elo_before_winner, elo_before_loser, "
                "elo_delta_winner, elo_delta_loser, action_id) "
                f"VALUES {row_placeholders}",
                comparison_params,
            )
            loser_case_parts = []
            loser_params = []
            loser_ids = []
            for image_id, new_elo in loser_updates:
                loser_case_parts.append("WHEN ? THEN ?")
                loser_params.extend([image_id, new_elo])
                loser_ids.append(image_id)
            all_update_ids = [picked_id] + loser_ids
            update_placeholders = ",".join("?" for _ in all_update_ids)
            await conn.execute(
                "UPDATE images SET "
                f"elo = CASE id WHEN ? THEN ? {' '.join(loser_case_parts)} ELSE elo END, "
                "comparisons = COALESCE(comparisons, 0) + CASE id WHEN ? THEN ? ELSE 1 END "
                f"WHERE id IN ({update_placeholders})",
                [picked_id, picked_elo] + loser_params
                + [picked_id, len(comparison_rows)]
                + all_update_ids,
            )
        await conn.commit()
        rated_delta = sum(0 if _was_rated(images[image_id]) else 1 for image_id in unique_ids)
        return {
            "ok": True,
            "new_elo": picked_elo,
            "pairs_recorded": len(comparison_rows),
            "loser_updates": loser_updates,
            "_rated_delta": rated_delta,
        }
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=db_path)


async def undo_last_comparison(db_path: str) -> dict | None:
    conn = await connection.open_async(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        cursor = await conn.execute(
            "SELECT id, action_id FROM comparisons ORDER BY id DESC LIMIT 1"
        )
        latest = await cursor.fetchone()
        if not latest:
            await conn.rollback()
            return None

        action_id = latest["action_id"]
        if action_id:
            cursor = await conn.execute(
                "SELECT id, winner_id, loser_id, elo_before_winner, elo_before_loser, "
                "elo_delta_winner, elo_delta_loser, action_id "
                "FROM comparisons WHERE action_id = ? ORDER BY id ASC",
                (action_id,),
            )
            rows = await cursor.fetchall()
        else:
            cursor = await conn.execute(
                "SELECT id, winner_id, loser_id, elo_before_winner, elo_before_loser, "
                "elo_delta_winner, elo_delta_loser, action_id "
                "FROM comparisons WHERE id = ?",
                (latest["id"],),
            )
            rows = await cursor.fetchall()

        if not rows:
            await conn.rollback()
            return None

        propagation_rows = []
        if action_id:
            cursor = await conn.execute(
                "SELECT image_id, elo_before, propagated_updates_before, delta "
                "FROM propagation_updates WHERE action_id = ? ORDER BY id ASC",
                (action_id,),
            )
            propagation_rows = await cursor.fetchall()

        direct_deltas: dict[int, float] = {}
        relative_expected_elo: dict[int, float] = {}
        legacy_restore_elo: dict[int, float] = {}
        comparison_decrements: dict[int, int] = {}
        for row in rows:
            winner_id = int(row["winner_id"])
            loser_id = int(row["loser_id"])
            if row["elo_delta_winner"] is None:
                legacy_restore_elo.setdefault(winner_id, float(row["elo_before_winner"]))
            else:
                winner_delta = float(row["elo_delta_winner"])
                direct_deltas[winner_id] = direct_deltas.get(winner_id, 0.0) + winner_delta
                relative_expected_elo[winner_id] = (
                    float(row["elo_before_winner"]) + winner_delta
                )
            if row["elo_delta_loser"] is None:
                legacy_restore_elo.setdefault(loser_id, float(row["elo_before_loser"]))
            else:
                loser_delta = float(row["elo_delta_loser"])
                direct_deltas[loser_id] = direct_deltas.get(loser_id, 0.0) + loser_delta
                relative_expected_elo[loser_id] = (
                    float(row["elo_before_loser"]) + loser_delta
                )
            comparison_decrements[winner_id] = comparison_decrements.get(winner_id, 0) + 1
            comparison_decrements[loser_id] = comparison_decrements.get(loser_id, 0) + 1

        relative_updates = {
            image_id: delta
            for image_id, delta in direct_deltas.items()
            if image_id not in legacy_restore_elo
        }
        skipped_drift: list[int] = []
        if relative_updates:
            placeholders = ",".join("?" for _ in relative_updates)
            cursor = await conn.execute(
                f"SELECT id, elo FROM images WHERE id IN ({placeholders})",
                tuple(relative_updates),
            )
            current_elo = {
                int(row["id"]): None if row["elo"] is None else float(row["elo"])
                for row in await cursor.fetchall()
            }
            applicable_updates = {
                image_id: delta
                for image_id, delta in relative_updates.items()
                if current_elo.get(image_id) == relative_expected_elo[image_id]
            }
            skipped_drift = sorted(set(relative_updates) - set(applicable_updates))
            if skipped_drift:
                await conn.rollback()
                last_row = rows[-1]
                return {
                    "winner_id": last_row["winner_id"],
                    "loser_id": last_row["loser_id"],
                    "comparisons_undone": 0,
                    "propagations_undone": 0,
                    "action_id": action_id,
                    "skipped_drift": skipped_drift,
                    "partial": True,
                }
            if applicable_updates:
                await conn.executemany(
                    "UPDATE images SET elo = COALESCE(elo, 0) - ?, "
                    "comparisons = MAX(COALESCE(comparisons, 0) - ?, 0) WHERE id = ?",
                    [
                        (delta, comparison_decrements.get(image_id, 0), image_id)
                        for image_id, delta in applicable_updates.items()
                    ],
                )

        # Subtract the recorded delta instead of restoring the absolute
        # elo_before snapshot, so ratings written by other actions between
        # propagation and undo are preserved. This runs only after every
        # direct side passed the drift check above.
        if propagation_rows:
            await conn.executemany(
                "UPDATE images SET elo = COALESCE(elo, 0) - ?, "
                "propagated_updates = MAX(COALESCE(propagated_updates, 1) - 1, 0) WHERE id = ?",
                [
                    (float(row["delta"]), int(row["image_id"]))
                    for row in propagation_rows
                ],
            )
        if legacy_restore_elo:
            await conn.executemany(
                "UPDATE images SET elo = ?, comparisons = MAX(COALESCE(comparisons, 0) - ?, 0) WHERE id = ?",
                [
                    (elo, comparison_decrements.get(image_id, 0), image_id)
                    for image_id, elo in legacy_restore_elo.items()
                ],
            )

        if action_id:
            await conn.execute("DELETE FROM comparisons WHERE action_id = ?", (action_id,))
            await conn.execute("DELETE FROM propagation_updates WHERE action_id = ?", (action_id,))
        else:
            await conn.execute("DELETE FROM comparisons WHERE id = ?", (latest["id"],))
        await conn.commit()
        last_row = rows[-1]
        return {
            "winner_id": last_row["winner_id"],
            "loser_id": last_row["loser_id"],
            "comparisons_undone": len(rows),
            "propagations_undone": len(propagation_rows),
            "action_id": action_id,
            "skipped_drift": skipped_drift,
        }
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=db_path)
