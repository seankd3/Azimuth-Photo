"""
Local performance smoke benchmarks for photoArchive.

Run from the repo root:
    web/.venv/bin/python web/bench_perf.py

Run against a temporary active catalog when the real photo drive is offline:
    web/.venv/bin/python web/bench_perf.py --synthetic-active 50000 --skip-embeddings --skip-thumbnails

The default run measures hot DB/API helper paths, embedding-matrix cache
behavior, and already-cached thumbnail reads. It does not generate thumbnails
or start the FastAPI server.
"""

import argparse
import asyncio
import heapq
import os
import sqlite3
import statistics
import sys
import tempfile
import time
import urllib.request

import numpy as np

import app as app_module
import db
import embed_cache
import settings
import thumbnails
from core import query_constraints
from features.ai import routes as ai_routes
from features.cache import status as cache_status_service
from features.catalog import routes as catalog_routes
from features.compare import routes as compare_routes
from features.compare import service as compare_service
from features.library import routes as library_routes
from features.library import service as library_service
from features.media import routes as media_routes
from features.media import warm as media_warm
from features.settings import routes as settings_routes
from features.settings import status as settings_status

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass


class JsonRequest:
    def __init__(self, payload: dict):
        self.payload = payload

    async def json(self):
        return self.payload


def ms(seconds: float) -> float:
    return round(seconds * 1000.0, 3)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


async def time_async(label: str, iterations: int, fn):
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        await fn()
        times.append(time.perf_counter() - start)
    print(
        f"{label:<34} avg={ms(statistics.mean(times)):>8}ms "
        f"p95={ms(percentile(times, 95)):>8}ms "
        f"min={ms(min(times)):>8}ms"
    )


def time_sync(label: str, iterations: int, fn):
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    print(
        f"{label:<34} avg={ms(statistics.mean(times)):>8}ms "
        f"p95={ms(percentile(times, 95)):>8}ms "
        f"min={ms(min(times)):>8}ms"
    )


def print_counts():
    conn = sqlite3.connect(db.DB_PATH)
    try:
        model_key = db.active_embedding_model_key()
        image_count = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        active_count = conn.execute(
            "SELECT COUNT(*) FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND s.online = 1 AND i.missing_at IS NULL"
        ).fetchone()[0]
        source_rows = conn.execute(
            "SELECT included, online, COUNT(*) FROM catalog_sources GROUP BY included, online"
        ).fetchall()
        comparison_count = conn.execute("SELECT COUNT(*) FROM comparisons").fetchone()[0]
        embedding_count = conn.execute(
            "SELECT COUNT(*) FROM embeddings_by_model WHERE model_key = ?",
            (model_key,),
        ).fetchone()[0]
        active_embedding_count = conn.execute(
            "SELECT COUNT(*) FROM embeddings_by_model e "
            "JOIN images i ON e.image_id = i.id "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE e.model_key = ? AND s.included = 1 AND s.online = 1 AND i.missing_at IS NULL",
            (model_key,),
        ).fetchone()[0]
        cache_count = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
        source_state = ", ".join(
            f"included={row[0]} online={row[1]} count={row[2]}" for row in source_rows
        ) or "none"
        print(
            f"Dataset: images={image_count:,} active_images={active_count:,} "
            f"comparisons={comparison_count:,} embeddings={embedding_count:,} "
            f"active_embeddings={active_embedding_count:,} cache_entries={cache_count:,}"
        )
        print(f"Sources: {source_state}")
    finally:
        conn.close()


def print_query_plan(name: str, sql: str, params: tuple = ()):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    finally:
        conn.close()
    plan = " | ".join(row[3] for row in rows)
    print(f"{name:<34} {plan}")


def bench_quick_http(iterations: int):
    base = os.environ.get("PHOTOARCHIVE_BENCH_URL", "http://127.0.0.1:8000").rstrip("/")
    endpoints = [
        "/api/rankings?limit=60",
        "/api/rankings?q=nonexistentsearchterm&limit=60",
        "/api/mosaic/next?n=12&strategy=diverse",
        "/api/stats",
        "/api/date-groups",
        "/api/folders?max_depth=0",
        "/api/settings",
        "/api/cache/status",
        "/api/ai/status",
    ]
    print(f"\nQuick live HTTP timings ({base})")
    for endpoint in endpoints:
        times = []
        for _ in range(max(1, iterations)):
            start = time.perf_counter()
            with urllib.request.urlopen(f"{base}{endpoint}", timeout=10) as response:
                response.read()
            times.append(time.perf_counter() - start)
        print(
            f"{endpoint:<34} avg={ms(statistics.mean(times)):>8}ms "
            f"p95={ms(percentile(times, 95)):>8}ms "
            f"max={ms(max(times)):>8}ms"
        )


def reset_app_caches():
    db.invalidate_stats_cache()
    db.invalidate_cached_image_ids_cache()
    clear_filter_options = getattr(db, "clear_filter_options_cache", None)
    if clear_filter_options is not None:
        clear_filter_options()
    compare_service._pairing_cache.update({"data": None, "valid": False})
    compare_service._matchups_cache.update({"data": None, "valid": False})
    compare_service._visible_matchups_cache.clear()
    compare_service._visible_pairing_candidates_cache.clear()
    compare_service._visible_pairing_candidates_refreshing.clear()
    library_service._rankings_response_cache.clear()
    query_constraints._text_search_resolution_cache.clear()
    compare_service._interaction_response_cache.clear()
    media_warm._thumbnail_memory_warm_inflight.clear()
    settings_status.invalidate_settings_response_cache()
    ai_routes.invalidate_ai_status_response_cache()
    cache_status_service._cache_status_cache.clear()
    cache_status_service._cache_status_refreshing.clear()
    cache_status_service._browser_original_count_cache.update({"value": None, "bytes": 0, "expires": 0.0})
    app_module._clear_folders_cache()


async def create_synthetic_active_catalog(
    root_dir: str,
    image_count: int,
    cached_count: int,
    comparison_count: int,
):
    source_dir = os.path.join(root_dir, "photos")
    os.makedirs(source_dir, exist_ok=True)
    await db.init_db()
    now = time.time()
    cached_count = min(cached_count, image_count)
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(
            "INSERT INTO catalog_sources "
            "(path, display_name, included, online, image_count, active_image_count, created_at, last_seen_at) "
            "VALUES (?, ?, 1, 1, ?, ?, ?, ?)",
            (source_dir, "synthetic photos", image_count, image_count, now, now),
        )
        source_id = conn.execute("SELECT id FROM catalog_sources LIMIT 1").fetchone()[0]
        conn.executemany(
            "INSERT INTO images "
            "(source_id, filename, filepath, elo, comparisons, propagated_updates, status, flag, "
            "orientation, aspect_ratio, date_taken, camera_make, camera_model, lens, file_ext, "
            "file_size, file_modified_at, width, height, latitude, longitude, missing_at) "
            "VALUES (?, ?, ?, ?, ?, 0, 'kept', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            [
                (
                    source_id,
                    f"img-{idx:06d}.jpg",
                    os.path.join(source_dir, f"img-{idx:06d}.jpg"),
                    1200.0 + (idx % 1000),
                    idx % 8,
                    "picked" if idx % 997 == 0 else "unflagged",
                    "landscape" if idx % 3 else "portrait",
                    1.5 if idx % 3 else 0.75,
                    None if idx % 11 == 0 else f"2024-{(idx % 12) + 1:02d}-{(idx % 28) + 1:02d}",
                    "Canon" if idx % 7 == 0 else "Sony",
                    "R5" if idx % 7 == 0 else "A7",
                    "35mm" if idx % 5 else "50mm",
                    "png" if idx % 10 == 0 else "jpg",
                    1_000_000 + idx,
                    now + idx,
                    4000,
                    3000,
                    40.0 + (idx % 100) / 100.0 if idx % 4 == 0 else None,
                    -93.0 - (idx % 100) / 100.0 if idx % 4 == 0 else None,
                )
                for idx in range(1, image_count + 1)
            ],
        )
        cache_root = thumbnails.SSD_CACHE_DIR
        cache_rows = []
        for idx in range(1, cached_count + 1):
            for size in ("sm", "md"):
                cache_rows.append(
                    (
                        cache_root,
                        size,
                        idx,
                        os.path.join(root_dir, f"{size}-{idx}.jpg"),
                        f"sig-{size}-{idx}",
                        1234,
                        now,
                        now,
                    )
                )
        conn.executemany(
            "INSERT INTO cache_entries "
            "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            cache_rows,
        )
        comparison_rows = []
        for idx in range(1, comparison_count + 1):
            winner = (idx * 17) % image_count + 1
            loser = (idx * 37) % image_count + 1
            if winner == loser:
                loser = loser % image_count + 1
            comparison_rows.append((winner, loser, "compare", 1500.0, 1400.0, f"synthetic-{idx}", now + idx))
        conn.executemany(
            "INSERT INTO comparisons "
            "(winner_id, loser_id, mode, elo_before_winner, elo_before_loser, action_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            comparison_rows,
        )
        conn.commit()
    finally:
        conn.close()
    reset_app_caches()


def active_source_exists(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM catalog_sources WHERE included = 1 AND online = 1 LIMIT 1"
    ).fetchone())


RANKING_SQL = {
    "elo": "i.elo DESC",
    "least_compared": "i.comparisons ASC",
    "filename": "i.filename ASC",
    "newest": "i.id DESC",
    "date_taken": "i.date_taken IS NULL ASC, i.date_taken DESC, i.id DESC",
    "date_modified": "i.file_modified_at IS NULL ASC, i.file_modified_at DESC, i.id DESC",
    "file_size": "i.file_size IS NULL ASC, i.file_size DESC, i.id DESC",
    "resolution": "(i.width * i.height) IS NULL ASC, (i.width * i.height) DESC, i.id DESC",
    "camera": "i.camera_make IS NULL ASC, i.camera_make ASC, i.camera_model ASC, i.id ASC",
}
RANKING_BENCH_INDEXES = {
    "elo": "idx_images_active_elo",
    "least_compared": "idx_images_active_comparisons_asc",
    "filename": "idx_images_active_filename",
    "newest": "idx_images_active_id",
    "date_taken": "idx_images_active_date_taken_sort_desc",
    "date_modified": "idx_images_active_modified_sort_desc",
    "file_size": "idx_images_active_file_size_sort_desc",
    "resolution": "idx_images_active_resolution_sort_desc",
    "camera": "idx_images_active_camera_sort_asc",
}


def fetch_rankings_sync(sort: str):
    order = RANKING_SQL[sort]
    index_name = RANKING_BENCH_INDEXES[sort]
    conn = sqlite3.connect(db.DB_PATH)
    try:
        if not active_source_exists(conn):
            return []
        return conn.execute(
            "SELECT i.id, i.filename, i.filepath, i.elo, i.comparisons, i.status, i.aspect_ratio "
            f"FROM images i INDEXED BY {index_name} "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND s.online = 1 "
            "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
            f"ORDER BY {order} LIMIT 100"
        ).fetchall()
    finally:
        conn.close()


def fetch_visible_rankings_sync(sort: str):
    order = RANKING_SQL[sort]
    index_name = RANKING_BENCH_INDEXES[sort]
    cache_root = thumbnails.SSD_CACHE_DIR
    conn = sqlite3.connect(db.DB_PATH)
    try:
        if not active_source_exists(conn):
            return []
        cached_count = conn.execute(
            "SELECT COUNT(*) FROM cache_entries WHERE cache_root = ? AND size = ?",
            (cache_root, "sm"),
        ).fetchone()[0]
        if sort in db.VISIBLE_CACHE_FIRST_SORTS and cached_count <= db.RANKING_CACHE_FIRST_VISIBLE_LIMIT:
            return conn.execute(
                "SELECT i.id, i.filename, i.filepath, i.elo, i.comparisons, i.status, i.aspect_ratio "
                "FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                "CROSS JOIN images i ON i.id = c.image_id "
                "WHERE c.cache_root = ? AND c.size = ? "
                "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
                f"ORDER BY {order} LIMIT 100",
                (cache_root, "sm"),
            ).fetchall()
        return conn.execute(
            "SELECT i.id, i.filename, i.filepath, i.elo, i.comparisons, i.status, i.aspect_ratio "
            f"FROM images i INDEXED BY {index_name} "
            "WHERE i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
            "AND EXISTS ("
            "  SELECT 1 FROM cache_entries c "
            "  WHERE c.cache_root = ? AND c.size = ? AND c.image_id = i.id"
            ") "
            f"ORDER BY {order} LIMIT 100",
            (cache_root, "sm"),
        ).fetchall()
    finally:
        conn.close()


def fetch_matchups_sync():
    conn = sqlite3.connect(db.DB_PATH)
    try:
        if not active_source_exists(conn):
            return []
        return conn.execute("SELECT winner_id, loser_id FROM comparisons").fetchall()
    finally:
        conn.close()


def active_image_id_sample(limit: int = 500) -> list[int]:
    conn = sqlite3.connect(db.DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        return [
            int(row[0])
            for row in conn.execute(
                "SELECT i.id FROM images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 AND s.online = 1 "
                "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
                "ORDER BY i.elo DESC LIMIT ?",
                (int(limit),),
            )
        ]
    finally:
        conn.close()


def bench_db(iterations: int):
    print("\nDB/API helper timings")
    for sort in ("elo", "least_compared", "filename", "newest", "date_taken", "date_modified", "file_size", "resolution", "camera"):
        time_sync(
            f"get_rankings sort={sort}",
            iterations,
            lambda sort=sort: fetch_rankings_sync(sort),
        )
    for sort in ("elo", "filename", "newest", "resolution", "camera"):
        time_sync(
            f"visible rankings sort={sort}",
            iterations,
            lambda sort=sort: fetch_visible_rankings_sync(sort),
        )
    time_sync("raw comparison pair scan", max(1, iterations // 3), fetch_matchups_sync)

    print("\nQuery plans")
    print_query_plan(
        "rankings elo",
        "SELECT id, filename, filepath, elo, comparisons, status, aspect_ratio "
        "FROM images INDEXED BY idx_images_active_elo "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL "
        "ORDER BY elo DESC LIMIT 100",
    )
    print_query_plan(
        "rankings date_taken",
        "SELECT id, filename, filepath, elo, comparisons, status, aspect_ratio "
        "FROM images INDEXED BY idx_images_active_date_taken_sort_desc "
        "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL "
        "ORDER BY date_taken IS NULL ASC, date_taken DESC, id DESC LIMIT 100",
    )
    print_query_plan(
        "pregen md candidates",
        "SELECT i.id, i.source_id, i.filepath, i.file_size, i.file_modified_at "
        "FROM images i INDEXED BY idx_images_missing_source_filepath_id "
        "JOIN catalog_sources s ON s.id = i.source_id "
        "WHERE s.included = 1 AND s.online = 1 AND i.missing_at IS NULL "
        "AND (i.source_id > ? OR (i.source_id = ? AND (i.filepath > ? OR (i.filepath = ? AND i.id > ?)))) "
        "ORDER BY i.source_id ASC, i.filepath ASC, i.id ASC LIMIT 1024",
        (0, 0, "", "", 0),
    )
    print_query_plan(
        "embedding md-ready",
        "SELECT i.id, i.filepath FROM images i "
        "INDEXED BY idx_images_active_id "
        "WHERE i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
        "AND EXISTS ("
        "  SELECT 1 FROM cache_entries c "
        "  WHERE c.cache_root = ? AND c.size = 'md' AND c.image_id = i.id"
        ") "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM embeddings_by_model e WHERE e.model_key = ? AND e.image_id = i.id"
        ") "
        "ORDER BY i.id ASC LIMIT 64",
        (settings.get_settings()["ssd_cache_dir"], db.active_embedding_model_key()),
    )


async def bench_db_async_helpers(iterations: int):
    print("\nCached DB helper timings")
    await db.get_past_matchups()
    await time_async("get_past_matchups cached", iterations, db.get_past_matchups)

    image_ids = active_image_id_sample(500)
    if image_ids:
        await db.get_past_matchups_for_image_ids(image_ids)
        await time_async(
            "matchups_for_500_ids",
            iterations,
            lambda: db.get_past_matchups_for_image_ids(image_ids),
        )


def top_indices_desc(values, limit: int):
    if len(values) <= limit:
        return np.argsort(values)[::-1]
    candidates = np.argpartition(values, -limit)[-limit:]
    return candidates[np.argsort(values[candidates])[::-1]]


def cached_ids_for_size(size: str = "sm") -> set[int]:
    conn = sqlite3.connect(db.DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        return {
            int(row[0])
            for row in conn.execute(
                "SELECT image_id FROM cache_entries WHERE cache_root = ? AND size = ?",
                (thumbnails.SSD_CACHE_DIR, size),
            )
        }
    finally:
        conn.close()


def cached_ids_sample(size: str = "sm", limit: int = 24) -> list[int]:
    conn = sqlite3.connect(db.DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        return [
            int(row[0])
            for row in conn.execute(
                "SELECT image_id FROM cache_entries WHERE cache_root = ? AND size = ? LIMIT ?",
                (thumbnails.SSD_CACHE_DIR, size, int(limit)),
            )
        ]
    finally:
        conn.close()


def visible_embedding_page(scores, id_to_idx: dict[int, int], cached_ids: set[int], limit: int = 50):
    if not cached_ids:
        return [], 0
    visible_pairs = []
    for image_id in cached_ids:
        idx = id_to_idx.get(int(image_id))
        if idx is not None:
            visible_pairs.append((int(image_id), float(scores[idx])))
    visible_count = len(visible_pairs)
    if len(visible_pairs) > limit:
        visible_pairs = heapq.nlargest(limit, visible_pairs, key=lambda item: item[1])
    else:
        visible_pairs.sort(key=lambda item: item[1], reverse=True)
    return [image_id for image_id, _score in visible_pairs[:limit]], visible_count


async def bench_embeddings(iterations: int, *, include_visible_page: bool = True):
    print("\nEmbedding matrix timings")
    start = time.perf_counter()
    image_ids, matrix = await embed_cache.get_matrix()
    cold = time.perf_counter() - start
    if image_ids is None or matrix is None:
        print("embedding matrix unavailable")
        return
    print(f"get_matrix cold-ish                 {ms(cold):>8}ms shape={matrix.shape}")

    await time_async("get_matrix warm", iterations, embed_cache.get_matrix)

    source = matrix[0]
    time_sync("similarity matmul", iterations, lambda: matrix @ source)
    scores = matrix @ source
    time_sync("top-k argpartition", iterations, lambda: top_indices_desc(scores, 50))
    time_sync("top-k full argsort", iterations, lambda: np.argsort(scores)[::-1][:50])
    if include_visible_page:
        cached_sm_ids = cached_ids_for_size("sm")
        id_to_idx = {int(image_id): idx for idx, image_id in enumerate(image_ids)}
        time_sync("visible embedding page", iterations, lambda: visible_embedding_page(scores, id_to_idx, cached_sm_ids, 50))


async def bench_app_endpoints(iterations: int):
    print("\nApp endpoint timings")
    media_status_ids = cached_ids_sample("sm", 24)
    await asyncio.gather(
        db.get_stats(),
        db.get_filter_options(),
        library_routes.api_rankings(limit=50),
        library_routes.api_rankings(limit=50, q="jpg"),
        compare_routes.compare_next(n=2),
        compare_routes.mosaic_next(n=6),
        compare_routes.mosaic_next(n=12, strategy="diverse"),
        compare_routes.mosaic_next(n=12, strategy="diverse", orientation="landscape"),
        library_routes.api_date_groups(),
        library_routes.api_map_markers(),
        catalog_routes.api_folders(max_depth=1),
        cache_status_service.build_cache_status(ahead=0),
        ai_routes.build_ai_status(),
        media_routes.images_media_status(JsonRequest({"ids": media_status_ids})) if media_status_ids else asyncio.sleep(0),
        return_exceptions=True,
    )
    # Several endpoints intentionally schedule thumbnail prefetch after returning.
    # Let those background tasks clear so hot endpoint timings measure the request path.
    await asyncio.sleep(0.05)

    async def endpoint(label: str, fn):
        await fn()
        await asyncio.sleep(0.01)
        await time_async(label, iterations, fn)

    await endpoint("api_rankings default", lambda: library_routes.api_rankings(limit=50))
    await endpoint("api_rankings metadata search", lambda: library_routes.api_rankings(limit=50, q="jpg"))
    await endpoint(
        "api_rankings metadata empty",
        lambda: library_routes.api_rankings(limit=50, q="nonexistentsearchterm"),
    )
    await endpoint("compare_next default", lambda: compare_routes.compare_next(n=2))
    await endpoint("compare_next filtered", lambda: compare_routes.compare_next(n=2, orientation="landscape"))
    await endpoint("mosaic_next default", lambda: compare_routes.mosaic_next(n=6))
    await endpoint("mosaic_next filtered", lambda: compare_routes.mosaic_next(n=6, orientation="landscape"))
    await endpoint("mosaic_next diverse", lambda: compare_routes.mosaic_next(n=12, strategy="diverse"))
    await endpoint(
        "mosaic_next diverse filtered",
        lambda: compare_routes.mosaic_next(n=12, strategy="diverse", orientation="landscape"),
    )
    await endpoint("filter_options", library_routes.api_filter_options)
    await endpoint("date_groups", library_routes.api_date_groups)
    await endpoint("map_markers", library_routes.api_map_markers)
    await endpoint("folders shallow", lambda: catalog_routes.api_folders(max_depth=1))
    await endpoint("folders full", catalog_routes.api_folders)
    await endpoint("cache_status", lambda: cache_status_service.build_cache_status(ahead=0))
    await endpoint("cache_status ahead", lambda: cache_status_service.build_cache_status(ahead=1000))
    await endpoint("ai_status", ai_routes.build_ai_status)
    await endpoint("settings", settings_routes.api_settings)
    if media_status_ids:
        await endpoint("media_status batch", lambda: media_routes.images_media_status(JsonRequest({"ids": media_status_ids})))


async def bench_cold_app_endpoints(iterations: int):
    print("\nCold app endpoint timings")
    media_status_ids = cached_ids_sample("sm", 24)

    endpoints = [
        ("api_rankings default", lambda: library_routes.api_rankings(limit=50)),
        ("api_rankings metadata search", lambda: library_routes.api_rankings(limit=50, q="jpg")),
        (
            "api_rankings metadata empty",
            lambda: library_routes.api_rankings(limit=50, q="nonexistentsearchterm"),
        ),
        ("compare_next default", lambda: compare_routes.compare_next(n=2)),
        ("compare_next filtered", lambda: compare_routes.compare_next(n=2, orientation="landscape")),
        ("mosaic_next default", lambda: compare_routes.mosaic_next(n=6)),
        ("mosaic_next filtered", lambda: compare_routes.mosaic_next(n=6, orientation="landscape")),
        ("mosaic_next diverse", lambda: compare_routes.mosaic_next(n=12, strategy="diverse")),
        (
            "mosaic_next diverse filtered",
            lambda: compare_routes.mosaic_next(n=12, strategy="diverse", orientation="landscape"),
        ),
        ("filter_options", library_routes.api_filter_options),
        ("date_groups", library_routes.api_date_groups),
        ("map_markers", library_routes.api_map_markers),
        ("folders shallow", lambda: catalog_routes.api_folders(max_depth=1)),
        ("folders full", catalog_routes.api_folders),
        ("cache_status", lambda: cache_status_service.build_cache_status(ahead=0)),
        ("cache_status ahead", lambda: cache_status_service.build_cache_status(ahead=1000)),
        ("ai_status", ai_routes.build_ai_status),
        ("settings", settings_routes.api_settings),
    ]
    if media_status_ids:
        endpoints.append(
            ("media_status batch", lambda: media_routes.images_media_status(JsonRequest({"ids": media_status_ids})))
        )

    for label, fn in endpoints:
        times = []
        for _ in range(iterations):
            reset_app_caches()
            start = time.perf_counter()
            await fn()
            times.append(time.perf_counter() - start)
            await asyncio.sleep(0.01)
        print(
            f"{label:<34} avg={ms(statistics.mean(times)):>8}ms "
            f"p95={ms(percentile(times, 95)):>8}ms "
            f"min={ms(min(times)):>8}ms"
        )


async def bench_startup_warmed_interactions(iterations: int):
    print("\nStartup-warmed interaction timings")
    reset_app_caches()
    settings.get_settings()
    await db.get_stats()
    await asyncio.gather(
        compare_service.warm_filtered_visible_ranked_candidates(
            "md",
            limit=compare_service._FILTERED_SWISS_PAIR_WINDOW,
            orientation="landscape",
            warm_matchups=True,
        ),
        compare_service.warm_filtered_visible_ranked_candidates(
            "sm",
            limit=compare_service._FILTERED_MOSAIC_WINDOW,
            orientation="landscape",
        ),
        compare_routes.mosaic_next(n=12, strategy="diverse"),
        compare_routes.mosaic_next(n=12, strategy="diverse", orientation="landscape"),
        return_exceptions=True,
    )
    await compare_routes.mosaic_next(n=6, orientation="landscape")

    async def first_then_hot(label: str, fn):
        start = time.perf_counter()
        await fn()
        first = time.perf_counter() - start
        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            await fn()
            times.append(time.perf_counter() - start)
        print(
            f"{label:<34} first={ms(first):>6}ms "
            f"avg={ms(statistics.mean(times)):>8}ms "
            f"p95={ms(percentile(times, 95)):>8}ms"
        )

    await first_then_hot("compare filtered warmed", lambda: compare_routes.compare_next(n=2, orientation="landscape"))
    await first_then_hot("mosaic filtered warmed", lambda: compare_routes.mosaic_next(n=6, orientation="landscape"))
    await first_then_hot(
        "mosaic diverse warmed",
        lambda: compare_routes.mosaic_next(n=12, strategy="diverse"),
    )
    await first_then_hot(
        "mosaic diverse filtered warmed",
        lambda: compare_routes.mosaic_next(n=12, strategy="diverse", orientation="landscape"),
    )


async def bench_compare_rating_loop(iterations: int):
    print("\nCompare rating loop timings")
    reset_app_caches()
    old_schedule = compare_routes._schedule_pairing_propagation
    human_delay = float(os.environ.get("PHOTOARCHIVE_BENCH_COMPARE_DELAY", "0") or 0)

    def close_scheduled(coro):
        coro.close()

    compare_routes._schedule_pairing_propagation = close_scheduled
    try:
        initial = await compare_routes.compare_next(n=2)
        pairs = initial.get("pairs") or []
        if not pairs:
            print("compare submit+next              skipped (no visible pairs)")
            return

        submit_times = []
        next_times = []
        loop_times = []
        pair = pairs[0]
        for idx in range(iterations):
            winner = pair["left"]["id"] if idx % 2 == 0 else pair["right"]["id"]
            loser = pair["right"]["id"] if idx % 2 == 0 else pair["left"]["id"]

            loop_start = time.perf_counter()
            start = time.perf_counter()
            await compare_routes.submit_comparison(
                JsonRequest({"winner_id": winner, "loser_id": loser})
            )
            submit_times.append(time.perf_counter() - start)

            start = time.perf_counter()
            next_result = await compare_routes.compare_next(n=2)
            next_times.append(time.perf_counter() - start)
            loop_times.append(time.perf_counter() - loop_start)

            next_pairs = next_result.get("pairs") or []
            if next_pairs:
                pair = next_pairs[0]
            await asyncio.sleep(0.01)

        for label, times in (
            ("compare submit", submit_times),
            ("compare next after submit", next_times),
            ("compare submit+next", loop_times),
        ):
            print(
                f"{label:<34} avg={ms(statistics.mean(times)):>8}ms "
                f"p95={ms(percentile(times, 95)):>8}ms "
                f"min={ms(min(times)):>8}ms"
            )

        if human_delay > 0:
            reset_app_caches()
            initial = await compare_routes.compare_next(n=2)
            pairs = initial.get("pairs") or []
            if pairs:
                pair = pairs[0]
                winner = pair["left"]["id"]
                loser = pair["right"]["id"]
                await asyncio.sleep(human_delay)
                await compare_routes.submit_comparison(
                    JsonRequest({"winner_id": winner, "loser_id": loser})
                )
                start = time.perf_counter()
                await compare_routes.compare_next(n=2)
                elapsed = time.perf_counter() - start
                label = f"compare next after {human_delay:.1f}s think"
                print(
                    f"{label:<34} avg={ms(elapsed):>8}ms "
                    f"p95={ms(elapsed):>8}ms "
                    f"min={ms(elapsed):>8}ms"
                )
    finally:
        compare_routes._schedule_pairing_propagation = old_schedule


async def bench_mosaic_pick_loop(iterations: int):
    print("\nMosaic pick timings")
    reset_app_caches()
    old_schedule = compare_routes._schedule_pairing_propagation

    def close_scheduled(coro):
        coro.close()

    compare_routes._schedule_pairing_propagation = close_scheduled
    try:
        initial = await compare_routes.mosaic_next(n=12)
        images = initial.get("images") or []
        if len(images) < 2:
            print("mosaic pick                     skipped (not enough visible images)")
            return

        pick_times = []
        next_times = []
        loop_times = []
        for idx in range(iterations):
            winner = images[idx % len(images)]["id"]
            losers = [image["id"] for image in images if image["id"] != winner][:11]
            if not losers:
                break

            loop_start = time.perf_counter()
            start = time.perf_counter()
            await compare_routes.mosaic_pick(
                JsonRequest({"winner_id": winner, "loser_ids": losers})
            )
            pick_times.append(time.perf_counter() - start)

            start = time.perf_counter()
            next_result = await compare_routes.mosaic_next(n=12)
            next_times.append(time.perf_counter() - start)
            loop_times.append(time.perf_counter() - loop_start)

            next_images = next_result.get("images") or []
            if next_images:
                images = next_images

        if pick_times:
            for label, times in (
                ("mosaic pick", pick_times),
                ("mosaic next after pick", next_times),
                ("mosaic pick+next", loop_times),
            ):
                print(
                    f"{label:<34} avg={ms(statistics.mean(times)):>8}ms "
                    f"p95={ms(percentile(times, 95)):>8}ms "
                    f"min={ms(min(times)):>8}ms"
                )
    finally:
        compare_routes._schedule_pairing_propagation = old_schedule


def cached_thumbnail_rows():
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = {}
        for size in thumbnails.THUMB_TIERS:
            row = conn.execute(
                "SELECT c.size, c.image_id, c.source_signature, c.path, i.filepath "
                "FROM cache_entries c JOIN images i ON i.id = c.image_id "
                "WHERE c.cache_root = ? AND c.size = ? LIMIT 1",
                (thumbnails.SSD_CACHE_DIR, size),
            ).fetchone()
            if row:
                rows[size] = row
        return rows
    finally:
        conn.close()


def bench_thumbnails(iterations: int):
    print("\nThumbnail cache timings")
    thumbnails.configure(settings.load_settings())
    rows = cached_thumbnail_rows()
    if not rows:
        print("no cached thumbnail rows found")
        return

    for size, row in rows.items():
        image_id = int(row["image_id"])
        signature = row["source_signature"]
        time_sync(
            f"fast_disk_read_entry {size}",
            iterations,
            lambda size=size, image_id=image_id, signature=signature: thumbnails.fast_disk_read_entry(
                size,
                image_id,
                signature,
            ),
        )
        entry = thumbnails.fast_disk_read_entry(size, image_id, signature, populate_memory=True)
        if entry is not None:
            time_sync(
                f"memory_get_entry {size}",
                iterations * 20,
                lambda size=size, image_id=image_id: thumbnails._memory_get_entry_fast(size, image_id),
            )
        time_sync(
            f"has_cached {size}",
            iterations,
            lambda size=size, row=row: thumbnails.has_cached(size, row["filepath"], int(row["image_id"])),
        )


async def main():
    parser = argparse.ArgumentParser(description="Run local photoArchive performance smoke benchmarks.")
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--quick", action="store_true",
                        help="Run a short app-endpoint smoke benchmark only.")
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("--skip-app", action="store_true")
    parser.add_argument("--cold-app", action="store_true",
                        help="Also time app endpoints with in-process caches reset before each call.")
    parser.add_argument("--skip-embeddings", action="store_true")
    parser.add_argument("--skip-thumbnails", action="store_true")
    parser.add_argument("--synthetic-active", type=int, default=0, metavar="N",
                        help="Run against a temporary active catalog with N synthetic images.")
    parser.add_argument("--synthetic-cached", type=int, default=12000,
                        help="Number of synthetic images with sm/md cache entries.")
    parser.add_argument("--synthetic-comparisons", type=int, default=30000,
                        help="Number of synthetic comparison rows.")
    args = parser.parse_args()
    if args.quick:
        args.iterations = min(args.iterations, 5)
        args.skip_db = True
        args.skip_embeddings = True
        args.skip_thumbnails = True

    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

    original_db_path = db.DB_PATH
    synthetic_temp = None
    try:
        if args.synthetic_active > 0:
            synthetic_temp = tempfile.TemporaryDirectory()
            db.DB_PATH = os.path.join(synthetic_temp.name, "synthetic-active.db")
            await create_synthetic_active_catalog(
                synthetic_temp.name,
                args.synthetic_active,
                args.synthetic_cached,
                args.synthetic_comparisons,
            )
            print(
                f"Synthetic active catalog: images={args.synthetic_active:,} "
                f"cached={min(args.synthetic_cached, args.synthetic_active):,} "
                f"comparisons={args.synthetic_comparisons:,}"
            )
        else:
            await db.init_db()
        print_counts()
        if args.quick:
            bench_quick_http(args.iterations)
            return
        if not args.skip_db:
            bench_db(args.iterations)
            await bench_db_async_helpers(args.iterations)
        if not args.skip_app:
            if args.cold_app:
                await bench_cold_app_endpoints(max(1, min(args.iterations, 5)))
            await bench_app_endpoints(args.iterations)
            await bench_startup_warmed_interactions(args.iterations)
            if synthetic_temp is not None:
                await bench_compare_rating_loop(max(1, min(args.iterations, 10)))
                await bench_mosaic_pick_loop(max(1, min(args.iterations, 10)))
        if not args.skip_embeddings:
            await bench_embeddings(max(3, args.iterations // 3), include_visible_page=not args.skip_thumbnails)
        if not args.skip_thumbnails:
            bench_thumbnails(args.iterations)
    finally:
        db.DB_PATH = original_db_path
        reset_app_caches()
        if synthetic_temp is not None:
            synthetic_temp.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
