"""Standing latency budgets for the browse paths Azimuth Photo must keep fast."""

import asyncio
import os
import sqlite3
import statistics
import tempfile
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import settings
import thumbnails
from features.library import service as library_service
from features.collections import suggestions as collection_suggestions
from thumbnails import cache_entries as thumbnail_cache_entries


pytestmark = [pytest.mark.perf, pytest.mark.slow]

IMAGE_COUNT = 2_000
RUNS = 3

# Re-baselined from the median of three uncached route/service calls on omarchy.
# Values leave headroom for parallel-suite contention on a busy host.
BUDGET_MS = {
    "rankings_first_page": 200,
    "date_histogram": 80,
    "counts": 50,
    "filter_options": 125,
    "visible_library_query": 125,
    "thumbnail_signatures": 20,
    "collection_suggestions_cold": 750,
    "collection_suggestions_cached": 30,
}


def _clear_query_caches() -> None:
    db.invalidate_stats_cache()
    db.invalidate_cached_image_ids_cache()
    db._invalidate_ranking_count_cache()
    db.clear_filter_options_cache()
    library_service.invalidate_rankings_response_cache()
    collection_suggestions.invalidate_cache()


def _median_ms(label, operation, *, clear_caches=None) -> float:
    samples = []
    for _ in range(RUNS):
        if clear_caches is not None:
            clear_caches()
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1_000)
    median = statistics.median(samples)
    print(f"perf {label}: {median:.1f} ms (runs: {', '.join(f'{sample:.1f}' for sample in samples)})")
    return median


def _assert_budget(label: str, measured_ms: float) -> None:
    assert measured_ms <= BUDGET_MS[label], (
        f"{label} median was {measured_ms:.1f} ms; budget is {BUDGET_MS[label]} ms"
    )


@pytest.fixture(scope="module")
def perf_catalog():
    """One realistic, fully cached catalog shared by every perf-budget case."""
    tempdir = tempfile.TemporaryDirectory(prefix="azimuth-perf-")
    old_db_path = db.DB_PATH
    old_settings_path = settings.SETTINGS_PATH
    old_settings_state = settings._settings
    old_cache_dir = thumbnails.SSD_CACHE_DIR
    old_persistent_conn = thumbnail_cache_entries._persistent_conn
    old_prefetch = library_service._schedule_thumbnail_prefetch
    old_memory_warm = library_service._schedule_result_thumbnail_memory_warm
    old_smoke_mode = os.environ.get("PHOTOARCHIVE_SMOKE_MODE")
    old_mode = os.environ.get("PHOTOARCHIVE_MODE")

    db.DB_PATH = os.path.join(tempdir.name, "perf.db")
    settings.SETTINGS_PATH = os.path.join(tempdir.name, "settings.local.json")
    settings._settings = None
    thumbnails.SSD_CACHE_DIR = os.path.join(tempdir.name, "thumbs")
    thumbnail_cache_entries._persistent_conn = None
    os.environ["PHOTOARCHIVE_SMOKE_MODE"] = "1"
    os.environ.pop("PHOTOARCHIVE_MODE", None)
    thumbnails._clear_disk_index()
    _clear_query_caches()
    asyncio.run(db.init_db())
    settings.save_settings({})

    source_path = os.path.join(tempdir.name, "catalog")
    os.makedirs(source_path)
    now = 1_720_000_000.0
    conn = sqlite3.connect(db.DB_PATH)
    try:
        source_id = conn.execute(
            "INSERT INTO catalog_sources "
            "(path, display_name, included, online, image_count, active_image_count, created_at, last_seen_at) "
            "VALUES (?, 'Perf catalog', 1, 1, ?, ?, ?, ?)",
            (source_path, IMAGE_COUNT, IMAGE_COUNT, now, now),
        ).lastrowid
        image_rows = [
            (
                source_id,
                f"image-{index:05d}.jpg",
                os.path.join(source_path, f"{2023 + index % 3}", f"image-{index:05d}.jpg"),
                1_000.0 + index,
                index % 20,
                "maybe" if index % 17 == 0 else "kept",
                "picked" if index % 97 == 0 else "unflagged",
                "portrait" if index % 3 == 0 else "landscape",
                f"{2023 + index % 3}-{(index % 12) + 1:02d}-{(index % 28) + 1:02d} 12:00:00",
                "Canon" if index % 2 else "Sony",
                "R5" if index % 2 else "A7 IV",
                "35mm" if index % 4 else "50mm",
                "jpg" if index % 9 else "cr3",
                2_000_000 + index,
                now + index,
                6_000,
                4_000,
            )
            for index in range(IMAGE_COUNT)
        ]
        conn.executemany(
            "INSERT INTO images "
            "(source_id, filename, filepath, elo, comparisons, status, flag, orientation, date_taken, "
            "camera_make, camera_model, lens, file_ext, file_size, file_modified_at, width, height) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            image_rows,
        )
        conn.executemany(
            "INSERT INTO cache_entries "
            "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
            "VALUES (?, 'sm', ?, ?, ?, ?, ?, ?)",
            [
                (
                    thumbnails.SSD_CACHE_DIR,
                    index + 1,
                    os.path.join(tempdir.name, "thumbs", f"{index + 1}.jpg"),
                    f"signature-{index + 1}",
                    10_000,
                    now,
                    now,
                )
                for index in range(IMAGE_COUNT)
            ],
        )
        conn.commit()
    finally:
        conn.close()

    def no_prefetch(*_args, **_kwargs):
        return 0

    library_service._schedule_thumbnail_prefetch = no_prefetch
    library_service._schedule_result_thumbnail_memory_warm = lambda *_args, **_kwargs: None
    _clear_query_caches()
    client = TestClient(app_module.app)
    catalog = SimpleNamespace(client=client, source_path=source_path)
    try:
        yield catalog
    finally:
        client.close()
        library_service._schedule_thumbnail_prefetch = old_prefetch
        library_service._schedule_result_thumbnail_memory_warm = old_memory_warm
        if thumbnail_cache_entries._persistent_conn is not None:
            thumbnail_cache_entries._persistent_conn.close()
        thumbnail_cache_entries._persistent_conn = old_persistent_conn
        thumbnails.SSD_CACHE_DIR = old_cache_dir
        thumbnails._clear_disk_index()
        settings.SETTINGS_PATH = old_settings_path
        settings._settings = old_settings_state
        db.DB_PATH = old_db_path
        if old_smoke_mode is None:
            os.environ.pop("PHOTOARCHIVE_SMOKE_MODE", None)
        else:
            os.environ["PHOTOARCHIVE_SMOKE_MODE"] = old_smoke_mode
        if old_mode is None:
            os.environ.pop("PHOTOARCHIVE_MODE", None)
        else:
            os.environ["PHOTOARCHIVE_MODE"] = old_mode
        _clear_query_caches()
        tempdir.cleanup()


def test_rankings_first_page_budget(perf_catalog):
    def operation():
        response = perf_catalog.client.get("/api/rankings?limit=100&sort=elo")
        assert response.status_code == 200
        assert response.json()["total_images"] == IMAGE_COUNT

    measured_ms = _median_ms("rankings first page", operation, clear_caches=_clear_query_caches)
    _assert_budget("rankings_first_page", measured_ms)


def test_date_histogram_budget(perf_catalog):
    def operation():
        response = perf_catalog.client.get("/api/date-histogram")
        assert response.status_code == 200
        assert response.json()["total"] == IMAGE_COUNT

    measured_ms = _median_ms("date histogram", operation, clear_caches=_clear_query_caches)
    _assert_budget("date_histogram", measured_ms)


def test_counts_budget(perf_catalog):
    def operation():
        response = perf_catalog.client.get("/api/counts")
        assert response.status_code == 200
        assert response.json()["total"] == IMAGE_COUNT

    measured_ms = _median_ms("counts", operation, clear_caches=_clear_query_caches)
    _assert_budget("counts", measured_ms)


def test_filter_options_budget(perf_catalog):
    def operation():
        response = perf_catalog.client.get("/api/filter-options")
        assert response.status_code == 200
        assert response.json()["cameras"]

    measured_ms = _median_ms("filter options", operation, clear_caches=_clear_query_caches)
    _assert_budget("filter_options", measured_ms)


def test_library_visible_query_budget(perf_catalog):
    async def operation():
        response = await library_service.api_rankings_impl(limit=100, sort="filename")
        assert response["total_images"] == IMAGE_COUNT
        assert len(response["images"]) == 100

    _assert_budget(
        "visible_library_query",
        _median_ms("library visible query", lambda: asyncio.run(operation()), clear_caches=_clear_query_caches),
    )


def test_thumbnail_signature_budget(perf_catalog):
    paths = [os.path.join(perf_catalog.source_path, f"2024/image-{index:05d}.jpg") for index in range(IMAGE_COUNT)]

    def operation():
        signatures = [
            thumbnails._build_catalog_source_signature(path, "sm", index + 1, 2_000_000 + index, 1_720_000_000.0 + index)
            for index, path in enumerate(paths)
        ]
        assert all(signature[0] for signature in signatures)

    measured_ms = _median_ms("thumbnail signatures", operation)
    _assert_budget("thumbnail_signatures", measured_ms)


def test_collection_suggestions_cold_budget(perf_catalog):
    def operation():
        response = asyncio.run(
            collection_suggestions.collection_suggestions(
                db.DB_PATH,
                db_signature=db.DB_PATH,
            )
        )
        assert isinstance(response["suggestions"], list)

    measured_ms = _median_ms(
        "collection suggestions cold",
        operation,
        clear_caches=collection_suggestions.invalidate_cache,
    )
    _assert_budget("collection_suggestions_cold", measured_ms)


def test_collection_suggestions_cached_budget(perf_catalog):
    asyncio.run(
        collection_suggestions.collection_suggestions(
            db.DB_PATH,
            db_signature=db.DB_PATH,
        )
    )

    def operation():
        response = asyncio.run(
            collection_suggestions.collection_suggestions(
                db.DB_PATH,
                db_signature=db.DB_PATH,
            )
        )
        assert isinstance(response["suggestions"], list)

    measured_ms = _median_ms("collection suggestions cached", operation)
    _assert_budget("collection_suggestions_cached", measured_ms)
