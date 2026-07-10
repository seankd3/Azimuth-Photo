import asyncio
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing

from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import thumbnails  # noqa: E402
from data import schema as data_schema  # noqa: E402
from thumbnails import budget as thumbnail_budget  # noqa: E402
from thumbnails import cache_entries as thumbnail_cache_entries  # noqa: E402
from thumbnails import config as thumbnail_config  # noqa: E402
from thumbnails import config_metadata as thumbnail_config_metadata  # noqa: E402
from thumbnails import full_cache as thumbnail_full_cache  # noqa: E402
from thumbnails import generation as thumbnail_generation  # noqa: E402
from thumbnails import jobs as thumbnail_jobs  # noqa: E402
from thumbnails import maintenance as thumbnail_maintenance  # noqa: E402
from thumbnails import pregen as thumbnail_pregen  # noqa: E402
from thumbnails import runtime as thumbnail_runtime  # noqa: E402
from thumbnails import status as thumbnail_status  # noqa: E402


class RawPreviewOrientationTests(unittest.TestCase):
    def test_raw_container_flip_rotates_untagged_portrait_preview_upright(self):
        preview = Image.new("RGB", (24, 12), color=(20, 40, 60))
        try:
            self.assertEqual(thumbnail_generation.apply_raw_orientation(preview, 6).size, (12, 24))
            self.assertEqual(thumbnail_generation.apply_raw_orientation(preview, 5).size, (12, 24))
            self.assertEqual(thumbnail_generation.apply_raw_orientation(preview, 4).size, (12, 24))
            self.assertEqual(thumbnail_generation.apply_raw_orientation(preview, 7).size, (12, 24))
            self.assertEqual(thumbnail_generation.apply_raw_orientation(preview, 3).size, (24, 12))
        finally:
            preview.close()


class ThumbnailConfigFacadeTests(unittest.TestCase):
    def test_thumbnail_defaults_remain_available_from_facade(self):
        for name in thumbnail_config.DEFAULT_EXPORT_NAMES:
            self.assertTrue(hasattr(thumbnails, name), name)

        for name in ("SIZES", "JPEG_EXTENSIONS", "BROWSER_ORIGINAL_EXTENSIONS", "RAW_EXTENSIONS"):
            self.assertIs(getattr(thumbnails, name), getattr(thumbnail_config, name), name)
        self.assertEqual(thumbnail_config.SSD_CACHE_BYTES, 10 * 1024 * 1024 * 1024)
        self.assertIsInstance(thumbnails.SSD_CACHE_BYTES, int)

    def test_config_module_parses_runtime_settings_like_facade(self):
        parsed = thumbnail_config.runtime_config_values(
            {
                "_replace_thumbnail_cache": "yes",
                "thumb_size_sm": "320",
                "thumb_size_md": "1440",
                "thumb_size_lg": "2880",
                "jpeg_quality": "88",
                "browser_cache_max_age": "12",
                "browser_cache_stale_while_revalidate": "34",
                "memory_cache_mb": "256",
                "ssd_cache_gb": "3",
                "cache_profile": "not-a-profile",
                "pregenerate_on_idle": "false",
                "pregen_generate_batch": "99",
                "pregen_batch_pause_ms": "9000",
                "disk_cache_dir": "relative-cache",
                "user_workers": "7",
                "prefetch_workers": "8",
            },
            current_sizes={"sm": 400, "md": 1920, "lg": 3840},
            current_thumb_quality=92,
            current_browser_cache_max_age=100,
            current_browser_cache_stale_while_revalidate=200,
            current_cache_profile="balanced",
            current_pregenerate_on_idle=True,
            current_generate_batch=16,
            current_ssd_cache_dir="/tmp/current-cache",
            current_executor_workers=4,
            current_prefetch_workers=6,
            as_bool=thumbnails._as_bool,
        )

        self.assertTrue(parsed["replace_thumbnail_cache"])
        self.assertEqual(parsed["sizes"], {"sm": 320, "md": 1440, "lg": 2880})
        self.assertEqual(parsed["thumb_quality"], 88)
        self.assertEqual(parsed["browser_cache_max_age"], 12)
        self.assertEqual(parsed["browser_cache_stale_while_revalidate"], 34)
        self.assertEqual(parsed["memory_cache_bytes"], 256 * 1024 * 1024)
        self.assertEqual(parsed["ssd_cache_bytes"], 3 * 1024 * 1024 * 1024)
        self.assertEqual(parsed["cache_profile"], "original_heavy")
        self.assertFalse(parsed["pregenerate_on_idle"])
        self.assertEqual(parsed["pregenerate_generate_batch"], 64)
        self.assertEqual(parsed["pregenerate_batch_pause_seconds"], 5.0)
        self.assertEqual(parsed["ssd_cache_dir"], os.path.abspath("relative-cache"))
        self.assertEqual(parsed["user_workers"], 7)
        self.assertEqual(parsed["prefetch_workers"], 8)

    def test_config_metadata_module_owns_signature_transition(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = os.path.join(tempdir, "metadata.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute(
                    "CREATE TABLE cache_metadata ("
                    "cache_root TEXT PRIMARY KEY, "
                    "thumb_config_signature TEXT, "
                    "thumb_config_changed_at REAL, "
                    "replace_stale_thumbnails INTEGER)"
                )
                conn.execute(
                    "INSERT INTO cache_metadata VALUES (?, ?, ?, ?)",
                    (tempdir, "old-signature", 12.0, 0),
                )
                conn.commit()
            finally:
                conn.close()

            def db_connect():
                opened = sqlite3.connect(db_path)
                opened.row_factory = sqlite3.Row
                return opened

            events = []
            offsets = {"sm": 3, "md": 4, "lg": 5}
            signature = thumbnail_config_metadata.thumb_config_signature(
                "v9",
                {"sm": 400, "md": 1920, "lg": 3840},
                91,
            )
            result = thumbnail_config_metadata.sync_thumb_config_metadata(
                signature,
                cache_root=tempdir,
                current_signature="old-signature",
                current_changed_at=12.0,
                current_replace_stale=False,
                replace_thumbnail_cache=True,
                now=44.0,
                meta_lock=thumbnails._meta_lock,
                db_connect=db_connect,
                clear_memory_tiers=lambda tiers: events.append(("clear", tiers)),
                thumb_tiers=("sm", "md", "lg"),
                pregen_scan_offsets=offsets,
                reset_pregen_bulk_cursor=lambda: events.append("bulk"),
                reset_pregen_full_cursor=lambda: events.append("full"),
            )

            self.assertEqual(result["last_signature"], signature)
            self.assertTrue(result["changed"])
            self.assertEqual(result["changed_at"], 44.0)
            self.assertTrue(result["replace_stale_thumbnails"])
            self.assertEqual(offsets, {"sm": 0, "md": 0, "lg": 0})
            self.assertEqual(events, [("clear", ("sm", "md", "lg")), "bulk", "full"])

            conn = db_connect()
            try:
                row = conn.execute(
                    "SELECT thumb_config_signature, thumb_config_changed_at, replace_stale_thumbnails "
                    "FROM cache_metadata WHERE cache_root = ?",
                    (tempdir,),
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row["thumb_config_signature"], signature)
            self.assertEqual(row["thumb_config_changed_at"], 44.0)
            self.assertEqual(row["replace_stale_thumbnails"], 1)


class ThumbnailBudgetFacadeTests(unittest.TestCase):
    def test_budget_module_owns_archive_estimates_and_facade_math(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = os.path.join(tempdir, "budget.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                conn.executescript(
                    """
                    CREATE TABLE catalog_sources (
                        id INTEGER PRIMARY KEY,
                        included INTEGER,
                        online INTEGER
                    );
                    CREATE TABLE images (
                        id INTEGER PRIMARY KEY,
                        source_id INTEGER,
                        missing_at REAL
                    );
                    CREATE TABLE cache_entries (
                        cache_root TEXT,
                        size TEXT,
                        size_bytes INTEGER,
                        created_at REAL
                    );
                    """
                )
                conn.executemany(
                    "INSERT INTO catalog_sources(id, included, online) VALUES (?, ?, ?)",
                    [(1, 1, 1), (2, 1, 0), (3, 0, 1)],
                )
                conn.executemany(
                    "INSERT INTO images(id, source_id, missing_at) VALUES (?, ?, ?)",
                    [(1, 1, None), (2, 1, None), (3, 2, None), (4, 3, None), (5, 1, 1.0)],
                )
                conn.executemany(
                    "INSERT INTO cache_entries(cache_root, size, size_bytes, created_at) VALUES (?, ?, ?, ?)",
                    [
                        (tempdir, thumbnails.THUMB_TIERS[0], 100, 100.0),
                        (tempdir, thumbnails.THUMB_TIERS[0], 300, 100.0),
                        (tempdir, thumbnails.FULL_TIER, 900, 1.0),
                        (tempdir, "lg", 5000, 1.0),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            def db_connect():
                opened = sqlite3.connect(db_path)
                opened.row_factory = sqlite3.Row
                return opened

            estimates = thumbnail_budget.cache_archive_estimates(
                all_tiers=thumbnails.ALL_TIERS,
                thumb_tiers=thumbnails.THUMB_TIERS,
                full_tier=thumbnails.FULL_TIER,
                ssd_cache_dir=tempdir,
                thumb_config_changed_at=50.0,
                meta_lock=thumbnails._meta_lock,
                db_connect=db_connect,
                estimated_tier_bytes_for_size=lambda _size: 10,
            )

        self.assertEqual(estimates["active_images"], 2)
        self.assertEqual(estimates["total_images"], 3)
        self.assertEqual(estimates["avg_bytes"]["sm"], 200)
        self.assertEqual(estimates["sample_count"]["sm"], 2)
        self.assertEqual(estimates["avg_bytes"][thumbnails.FULL_TIER], 900)
        self.assertEqual(estimates["needed_bytes"]["sm"], 400)
        self.assertEqual(estimates["needed_bytes"]["md"], 20)
        self.assertEqual(estimates["needed_bytes"][thumbnails.FULL_TIER], 2700)
        self.assertEqual(
            thumbnails.estimated_tier_bytes("md"),
            thumbnail_budget.estimated_tier_bytes("md", thumb_quality=thumbnails.THUMB_QUALITY),
        )


class ThumbnailRuntimeFacadeTests(unittest.TestCase):
    def test_runtime_helpers_remain_available_from_facade(self):
        self.assertIs(thumbnails._as_bool, thumbnail_runtime.as_bool)
        self.assertIs(thumbnails._current_time, thumbnail_runtime.current_time)
        self.assertIs(thumbnails._is_sqlite_locked, thumbnail_runtime.is_sqlite_locked)
        self.assertIs(thumbnails._replace_executor, thumbnail_runtime.replace_executor)

        self.assertTrue(thumbnails._as_bool(None, True))
        self.assertFalse(thumbnails._as_bool("off", True))
        self.assertGreater(thumbnails._current_time(), 0.0)
        self.assertTrue(thumbnails._is_sqlite_locked(sqlite3.OperationalError("database is locked")))
        self.assertFalse(thumbnails._is_sqlite_locked(sqlite3.OperationalError("disk I/O error")))


class ThumbnailJobsFacadeTests(unittest.TestCase):
    def test_cache_presence_jobs_own_facade_checks(self):
        calls = []

        self.assertTrue(
            thumbnail_jobs.has_cached(
                "md",
                "source.jpg",
                7,
                thumb_tiers=("sm", "md"),
                build_source_signature=lambda filepath, size, image_id: f"{filepath}:{size}:{image_id}",
                memory_get=lambda size, image_id, signature: calls.append(("memory", size, image_id, signature)) or b"x",
                fast_disk_has=lambda *args: calls.append(("fast", args)) or False,
                get_disk_entry=lambda *args, **kwargs: calls.append(("db", args, kwargs)) or None,
            )
        )
        self.assertEqual(calls, [("memory", "md", 7, "source.jpg:md:7")])

        self.assertTrue(
            thumbnail_jobs.has_cached_fast(
                "md",
                7,
                memory_get_entry_fast=lambda size, image_id: None,
                fast_disk_has=lambda size, image_id: (size, image_id) == ("md", 7),
            )
        )

    def test_thumbnail_jobs_own_ensure_and_get_orchestration(self):
        async def run_case():
            calls = []
            inflight = {}

            async def run_thumbnail_job(
                filepath,
                size,
                image_id,
                executor,
                include_smaller_tiers,
                hot,
                allow_stale_fallback,
            ):
                calls.append(
                    (
                        "run",
                        filepath,
                        size,
                        image_id,
                        executor,
                        include_smaller_tiers,
                        hot,
                        allow_stale_fallback,
                    )
                )
                await asyncio.sleep(0)
                return b"generated"

            generated = await thumbnail_jobs.ensure_thumbnail_with_executor(
                "source.jpg",
                "md",
                7,
                "executor",
                note_activity=True,
                include_smaller_tiers=False,
                allow_stale_fallback=True,
                note_user_activity=lambda: calls.append(("activity",)),
                build_source_signature=lambda filepath, size, image_id: f"{filepath}:{size}:{image_id}",
                memory_get=lambda *_args: None,
                fast_disk_read_entry=lambda *_args: None,
                read_disk_thumbnail=lambda *_args: None,
                source_missing=lambda _filepath: False,
                inflight=inflight,
                run_thumbnail_job=run_thumbnail_job,
            )

            disk = await thumbnail_jobs.ensure_thumbnail_with_executor(
                "source.jpg",
                "md",
                8,
                "executor",
                note_activity=False,
                include_smaller_tiers=True,
                allow_stale_fallback=False,
                note_user_activity=lambda: calls.append(("unexpected-activity",)),
                build_source_signature=lambda filepath, size, image_id: f"{filepath}:{size}:{image_id}",
                memory_get=lambda *_args: None,
                fast_disk_read_entry=lambda *args: ("path", b"disk") if args[2] == "source.jpg:md:8" else None,
                read_disk_thumbnail=lambda *_args: None,
                source_missing=lambda _filepath: False,
                inflight=inflight,
                run_thumbnail_job=run_thumbnail_job,
            )

            missing = await thumbnail_jobs.ensure_thumbnail_with_executor(
                "missing.jpg",
                "md",
                9,
                "executor",
                note_activity=False,
                include_smaller_tiers=False,
                allow_stale_fallback=True,
                note_user_activity=lambda: calls.append(("unexpected-activity",)),
                build_source_signature=lambda filepath, size, image_id: f"{filepath}:{size}:{image_id}",
                memory_get=lambda *_args: None,
                fast_disk_read_entry=lambda *_args: None,
                read_disk_thumbnail=lambda *_args: None,
                source_missing=lambda _filepath: True,
                inflight=inflight,
                run_thumbnail_job=run_thumbnail_job,
            )

            via_get = await thumbnail_jobs.get_thumbnail(
                "source.jpg",
                "sm",
                10,
                executor="default-executor",
                ensure_thumbnail_with_executor=lambda *args, **kwargs: (
                    calls.append(("get", args, kwargs)) or asyncio.sleep(0, result=b"via-get")
                ),
            )

            return generated, disk, missing, via_get, calls, inflight

        generated, disk, missing, via_get, calls, inflight = asyncio.run(run_case())

        self.assertEqual(generated, b"generated")
        self.assertEqual(disk, b"disk")
        self.assertEqual(missing, b"")
        self.assertEqual(via_get, b"via-get")
        self.assertEqual(inflight, {})
        self.assertIn(("activity",), calls)
        self.assertIn(("run", "source.jpg", "md", 7, "executor", False, True, True), calls)
        get_calls = [call for call in calls if call[0] == "get"]
        self.assertEqual(get_calls[0][1], ("source.jpg", "sm", 10, "default-executor"))
        self.assertEqual(
            get_calls[0][2],
            {"note_activity": True, "include_smaller_tiers": False},
        )

    def test_prefetch_jobs_owns_schedule_rules(self):
        async def run_case():
            scheduled = []
            touches = []
            created_tasks = []

            def ensure_thumbnail_with_executor(
                filepath,
                size,
                image_id,
                executor,
                *,
                note_activity,
                include_smaller_tiers,
                allow_stale_fallback,
            ):
                scheduled.append(
                    (
                        filepath,
                        size,
                        image_id,
                        executor,
                        note_activity,
                        include_smaller_tiers,
                        allow_stale_fallback,
                    )
                )
                return f"task:{image_id}"

            result = await thumbnail_jobs.prefetch_images(
                [
                    {"id": 1, "filepath": "memory.jpg"},
                    {"id": 2, "filepath": "disk.jpg"},
                    {"id": 3, "filepath": ""},
                    {"id": 4, "filepath": "needed.jpg"},
                ],
                "md",
                hot=True,
                sizes={"md": 1000},
                replace_stale_thumbnails=lambda: False,
                memory_get_entry_fast=lambda _size, image_id: b"cached" if image_id == 1 else None,
                memory_get=lambda _size, _image_id, _signature: None,
                fast_disk_has=lambda _size, image_id, *_args: image_id == 2,
                touch_cached_signature=lambda *args: touches.append(args) or True,
                touch_cached=lambda *args: touches.append(args) or True,
                build_source_signature=lambda filepath, size, image_id: f"{filepath}:{size}:{image_id}",
                has_cached=lambda _size, _filepath, _image_id: False,
                ensure_thumbnail_with_executor=ensure_thumbnail_with_executor,
                prefetch_executor="prefetch-executor",
                create_task=lambda task: created_tasks.append(asyncio.create_task(task)) or created_tasks[-1],
            )
            await asyncio.gather(*created_tasks)

            stale_scheduled = []
            created_tasks.clear()
            stale_result = await thumbnail_jobs.prefetch_images(
                [{"id": 5, "filepath": "fresh.jpg"}],
                "md",
                hot=False,
                sizes={"md": 1000},
                replace_stale_thumbnails=lambda: True,
                memory_get_entry_fast=lambda _size, _image_id: None,
                memory_get=lambda _size, _image_id, _signature: None,
                fast_disk_has=lambda *_args: False,
                touch_cached_signature=lambda *args: touches.append(args) or True,
                touch_cached=lambda *args: touches.append(args) or True,
                build_source_signature=lambda filepath, size, image_id: f"{filepath}:{size}:{image_id}",
                has_cached=lambda _size, _filepath, _image_id: False,
                ensure_thumbnail_with_executor=lambda *args, **kwargs: (
                    stale_scheduled.append((args, kwargs)) or "stale-task"
                ),
                prefetch_executor="prefetch-executor",
                create_task=lambda task: created_tasks.append(asyncio.create_task(task)) or created_tasks[-1],
            )
            await asyncio.gather(*created_tasks)

            return result, scheduled, touches, created_tasks, stale_result, stale_scheduled

        result, scheduled, touches, created_tasks, stale_result, stale_scheduled = asyncio.run(run_case())

        self.assertEqual(result, 1)
        self.assertEqual(
            scheduled,
            [("needed.jpg", "md", 4, "prefetch-executor", True, True, True)],
        )
        self.assertEqual(touches, [("md", 2, None)])
        self.assertEqual(len(created_tasks), 1)
        self.assertEqual(stale_result, 1)
        self.assertEqual(stale_scheduled[0][0], ("fresh.jpg", "md", 5, "prefetch-executor"))
        self.assertEqual(
            stale_scheduled[0][1],
            {
                "note_activity": False,
                "include_smaller_tiers": True,
                "allow_stale_fallback": False,
            },
        )


class ThumbnailPregenFacadeTests(unittest.TestCase):
    def test_pregen_state_mutation_remains_facaded_from_pregen_module(self):
        base_status = {
            "enabled": True,
            "manual_mode": False,
            "manual_pause": False,
            "state": "idle",
            "message": "",
            "active_phase": None,
            "started_at": None,
            "last_generated_at": None,
            "generated_this_session": 0,
            "last_error": "",
        }
        old_status = thumbnails._pregen_status
        old_enabled = thumbnails.PREGENERATE_ON_IDLE
        old_manual_mode = thumbnails._pregen_manual_mode
        old_manual_pause = thumbnails._pregen_manual_pause
        old_current_time = thumbnails._current_time
        try:
            direct_status = dict(base_status)
            thumbnail_pregen.set_state(
                direct_status,
                "running",
                message="warming",
                phase="bulk",
                enabled=False,
                manual_mode=True,
                manual_pause=False,
                now_provider=lambda: 55.5,
            )

            thumbnails._pregen_status = dict(base_status)
            thumbnails.PREGENERATE_ON_IDLE = False
            thumbnails._pregen_manual_mode = True
            thumbnails._pregen_manual_pause = False
            thumbnails._current_time = lambda: 55.5

            thumbnails._set_pregen_state("running", "warming", "bulk")

            self.assertEqual(thumbnails._pregen_status, direct_status)
            self.assertEqual(thumbnails._pregen_status["started_at"], 55.5)

            thumbnail_pregen.set_state(
                direct_status,
                "paused",
                message="paused",
                error="manual",
                enabled=True,
                manual_mode=False,
                manual_pause=True,
                now_provider=lambda: 99.0,
            )
            thumbnails.PREGENERATE_ON_IDLE = True
            thumbnails._pregen_manual_mode = False
            thumbnails._pregen_manual_pause = True
            thumbnails._current_time = lambda: 99.0

            thumbnails._set_pregen_state("paused", "paused", error="manual")

            self.assertEqual(thumbnails._pregen_status, direct_status)
            self.assertEqual(thumbnails._pregen_status["started_at"], 55.5)
        finally:
            thumbnails._pregen_status = old_status
            thumbnails.PREGENERATE_ON_IDLE = old_enabled
            thumbnails._pregen_manual_mode = old_manual_mode
            thumbnails._pregen_manual_pause = old_manual_pause
            thumbnails._current_time = old_current_time

    def test_decision_helpers_remain_facaded_from_pregen_module(self):
        calls = []

        def decision_provider():
            calls.append("called")
            return {"decision": "manual"}

        decision = type("Decision", (), {"pause": False, "thumbnail_batch_size": 12})()
        old_batch = thumbnails.PREGENERATE_GENERATE_BATCH
        try:
            thumbnails.PREGENERATE_GENERATE_BATCH = 4
            self.assertEqual(
                thumbnails._pregen_generate_batch_for_decision(decision),
                thumbnail_pregen.generate_batch_for_decision(decision, 4),
            )
        finally:
            thumbnails.PREGENERATE_GENERATE_BATCH = old_batch

        self.assertEqual(
            thumbnail_pregen.background_decision(
                12.5,
                decision_provider=decision_provider,
            ),
            {"decision": "manual"},
        )
        self.assertEqual(calls, ["called"])
        self.assertFalse(thumbnail_pregen.should_pause_for_priority())

    def test_session_bookkeeping_remains_facaded_from_pregen_module(self):
        old_history = thumbnails._pregen_history
        old_session_started_at = thumbnails._pregen_session_started_at
        old_session_generated = thumbnails._pregen_session_generated
        old_source_read_failures = thumbnails._pregen_source_read_failures
        old_current_time = thumbnails._current_time
        try:
            thumbnails._pregen_history = thumbnail_pregen.SessionBookkeeping().history
            thumbnails._pregen_session_started_at = None
            thumbnails._pregen_session_generated = 0
            thumbnails._pregen_source_read_failures = 0
            ticks = iter([100.0, 130.0])
            thumbnails._current_time = lambda: next(ticks)

            thumbnails._record_pregen_batch(
                3,
                thumbnails_written=5,
                source_bytes=4 * 1024 * 1024,
                read_seconds=2.0,
                decode_encode_seconds=1.0,
                source_read_failures=1,
            )
            facade_rates = thumbnails._pregen_rates()

            self.assertEqual(thumbnails._pregen_session_started_at, 100.0)
            self.assertEqual(thumbnails._pregen_session_generated, 3)
            self.assertEqual(thumbnails._pregen_source_read_failures, 1)
            self.assertEqual(len(thumbnails._pregen_history), 1)

            direct_bookkeeping = thumbnail_pregen.SessionBookkeeping(
                history=thumbnails._pregen_history,
                session_started_at=thumbnails._pregen_session_started_at,
                session_generated=thumbnails._pregen_session_generated,
                source_read_failures=thumbnails._pregen_source_read_failures,
            )
            self.assertEqual(
                facade_rates,
                thumbnail_pregen.session_rates(direct_bookkeeping, now=130.0),
            )
            self.assertEqual(facade_rates[0], 6.0)
            self.assertEqual(facade_rates[1], 6.0)
            self.assertEqual(facade_rates[2]["recent_thumbnails_written_per_min"], 10.0)
            self.assertEqual(facade_rates[2]["recent_read_mbps"], 2.0)
        finally:
            thumbnails._pregen_history = old_history
            thumbnails._pregen_session_started_at = old_session_started_at
            thumbnails._pregen_session_generated = old_session_generated
            thumbnails._pregen_source_read_failures = old_source_read_failures
            thumbnails._current_time = old_current_time

    def test_pregen_result_accounting_remains_facaded_from_pregen_module(self):
        old_status = thumbnails._pregen_status
        old_record_pregen_batch = thumbnails._record_pregen_batch
        old_current_time = thumbnails._current_time
        base_status = {
            "enabled": True,
            "manual_mode": False,
            "manual_pause": False,
            "state": "idle",
            "message": "",
            "active_phase": None,
            "started_at": None,
            "last_generated_at": None,
            "generated_this_session": 7,
            "last_error": "",
        }
        useful_result = {
            "source_reads": 2,
            "thumbnails_written": 3,
            "originals_written": 1,
            "source_bytes": 1024,
            "read_seconds": 0.5,
            "decode_encode_seconds": 0.25,
            "source_read_failures": 0,
        }
        failure_only_result = {
            "source_reads": 0,
            "thumbnails_written": 0,
            "originals_written": 0,
            "source_bytes": 2048,
            "read_seconds": 0.75,
            "decode_encode_seconds": 0.0,
            "source_read_failures": 1,
        }

        try:
            direct_status = dict(base_status)
            direct_batches = []

            def direct_record_batch(count, **kwargs):
                direct_batches.append((count, kwargs))

            direct_return = thumbnail_pregen.record_result(
                useful_result,
                direct_status,
                record_batch=direct_record_batch,
                now_provider=lambda: 55.5,
            )

            thumbnails._pregen_status = dict(base_status)
            facade_batches = []
            thumbnails._record_pregen_batch = (
                lambda count, **kwargs: facade_batches.append((count, kwargs))
            )
            thumbnails._current_time = lambda: 55.5

            facade_return = thumbnails._record_pregen_result(useful_result)

            self.assertEqual(facade_return, direct_return)
            self.assertEqual(thumbnails._pregen_status, direct_status)
            self.assertEqual(facade_batches, direct_batches)
            self.assertEqual(facade_return, 4)
            self.assertEqual(thumbnails._pregen_status["generated_this_session"], 9)
            self.assertEqual(thumbnails._pregen_status["last_generated_at"], 55.5)
            self.assertEqual(facade_batches[0][0], 2)
            self.assertEqual(facade_batches[0][1]["thumbnails_written"], 3)
            self.assertEqual(facade_batches[0][1]["source_bytes"], 1024)

            direct_status = dict(base_status)
            direct_batches = []
            direct_return = thumbnail_pregen.record_result(
                failure_only_result,
                direct_status,
                record_batch=direct_record_batch,
                now_provider=lambda: self.fail("failure-only result should not update generation time"),
            )

            thumbnails._pregen_status = dict(base_status)
            facade_batches = []
            thumbnails._current_time = lambda: self.fail(
                "failure-only result should not update generation time"
            )

            facade_return = thumbnails._record_pregen_result(failure_only_result)

            self.assertEqual(facade_return, direct_return)
            self.assertEqual(thumbnails._pregen_status, direct_status)
            self.assertEqual(facade_batches, direct_batches)
            self.assertEqual(facade_return, 0)
            self.assertEqual(thumbnails._pregen_status["generated_this_session"], 7)
            self.assertIsNone(thumbnails._pregen_status["last_generated_at"])
            self.assertEqual(facade_batches[0][0], 0)
            self.assertEqual(facade_batches[0][1]["source_read_failures"], 1)
        finally:
            thumbnails._pregen_status = old_status
            thumbnails._record_pregen_batch = old_record_pregen_batch
            thumbnails._current_time = old_current_time


class ThumbnailMaintenanceFacadeTests(unittest.TestCase):
    def setUp(self):
        self.old_cache_dir = thumbnails.SSD_CACHE_DIR
        self.tempdirs = []

    def tearDown(self):
        thumbnails.SSD_CACHE_DIR = self.old_cache_dir
        for tempdir in self.tempdirs:
            tempdir.cleanup()

    def _legacy_cache_root(self) -> str:
        tempdir = tempfile.TemporaryDirectory()
        self.tempdirs.append(tempdir)
        path = os.path.join(tempdir.name, "sm", "1.jpg")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"cache")
        return tempdir.name

    def test_cache_safety_helper_owns_marker_write_and_facade_matches(self):
        direct_root = self._legacy_cache_root()
        direct_result = thumbnail_maintenance.cache_dir_safe_to_clear(
            direct_root,
            thumbnails.ALL_TIERS,
            thumbnails.CACHE_MARKER,
        )
        self.assertEqual(direct_result, (True, ""))
        self.assertTrue(
            os.path.exists(
                thumbnail_maintenance.cache_marker_path(direct_root, thumbnails.CACHE_MARKER)
            )
        )

        facade_root = self._legacy_cache_root()
        thumbnails.SSD_CACHE_DIR = facade_root
        facade_result = thumbnails._cache_dir_safe_to_clear()

        self.assertEqual(facade_result, direct_result)
        self.assertTrue(os.path.exists(thumbnails._cache_marker_path()))

    def test_cache_temp_cleanup_helper_owns_invalidation(self):
        root = self._legacy_cache_root()
        old_tmp = os.path.join(root, thumbnails.FULL_TIER, "old.jpg.123.tmp")
        fresh_tmp = os.path.join(root, thumbnails.FULL_TIER, "fresh.jpg.123.tmp")
        os.makedirs(os.path.dirname(old_tmp), exist_ok=True)
        for path in (old_tmp, fresh_tmp):
            with open(path, "wb") as f:
                f.write(b"x" * 10)
        now = time.time()
        old_time = now - 120
        os.utime(old_tmp, (old_time, old_time))
        invalidations = []

        result = thumbnail_maintenance.cleanup_stale_cache_temps(
            root,
            (thumbnails.FULL_TIER,),
            max_age_seconds=1,
            current_time=lambda: now,
            invalidate_disk_stats_cache=lambda: invalidations.append("invalidated"),
        )

        self.assertEqual(result, {"files_removed": 1, "bytes_removed": 10})
        self.assertEqual(invalidations, ["invalidated"])
        self.assertFalse(os.path.exists(old_tmp))
        self.assertTrue(os.path.exists(fresh_tmp))

    def test_cache_purge_helper_owns_memory_disk_and_source_invalidation(self):
        cache_file = os.path.join(self._legacy_cache_root(), "sm", "42.jpg")
        with open(cache_file, "wb") as f:
            f.write(b"cache")
        events = []

        class FakeConnection:
            def execute(self, sql, params=()):
                events.append(("execute", sql.split()[0], tuple(params)))
                class FakeCursor:
                    def fetchall(self):
                        return [
                            {
                                "cache_root": "/cache",
                                "size": "sm",
                                "image_id": 42,
                                "path": cache_file,
                                "size_bytes": 5,
                            },
                            {
                                "cache_root": "/cache",
                                "size": "md",
                                "image_id": 43,
                                "path": os.path.join(os.path.dirname(cache_file), "missing.jpg"),
                                "size_bytes": 7,
                            },
                        ]

                return FakeCursor()

            def commit(self):
                events.append("commit")

            def close(self):
                events.append("close")

        class FakeLock:
            def __enter__(self):
                events.append("lock")

            def __exit__(self, exc_type, exc, tb):
                events.append("unlock")
                return False

        memory_cache = {(size, 42): (size, b"data") for size in ("sm", "md")}
        tier_byte_totals = {"sm": 5}
        source_stat_cache = {"source": "bits"}

        result = thumbnail_maintenance.purge_image_cache(
            [42, 43],
            memory_cache=memory_cache,
            clear_memory_image_ids=lambda ids: [
                memory_cache.pop(key)
                for key in list(memory_cache)
                if key[1] in ids
            ],
            flush_write_queue=lambda: events.append("flush"),
            meta_lock=FakeLock(),
            db_connect=FakeConnection,
            remove_cache_entry_locked=lambda conn, row: events.append(("remove", row["image_id"])),
            tier_byte_totals=tier_byte_totals,
            invalidate_disk_stats_cache=lambda: events.append("invalidate"),
            source_stat_cache=source_stat_cache,
        )

        self.assertEqual(result, {
            "memory_entries_removed": 2,
            "disk_entries_removed": 2,
            "disk_files_removed": 1,
        })
        self.assertEqual(memory_cache, {})
        self.assertEqual(tier_byte_totals, {})
        self.assertEqual(source_stat_cache, {})
        self.assertIn("flush", events)
        self.assertIn("commit", events)
        self.assertIn("close", events)
        self.assertEqual(
            [event for event in events if isinstance(event, tuple) and event[0] == "remove"],
            [("remove", 42), ("remove", 43)],
        )

    def test_clear_cache_helper_owns_source_safe_delete_and_metadata_reset(self):
        root = self._legacy_cache_root()
        cache_file = os.path.join(root, "sm", "1.jpg")
        events = []

        class FakeConnection:
            def execute(self, sql, params=()):
                events.append(("execute", sql.split()[0], tuple(params)))

            def commit(self):
                events.append("commit")

            def close(self):
                events.append("close")

        class FakeLock:
            def __enter__(self):
                events.append("lock")

            def __exit__(self, exc_type, exc, tb):
                events.append("unlock")
                return False

        tier_byte_totals = {"sm": 5}
        source_stat_cache = {"source": "bits"}
        replaced = []
        invalidated = []

        result = thumbnail_maintenance.clear_cache(
            cache_root=root,
            cache_marker=thumbnails.CACHE_MARKER,
            cache_dir_safe_to_clear=lambda: (True, ""),
            clear_memory_cache=lambda: {
                "entries_cleared": 2,
                "bytes_cleared": 9,
                "counts": {"sm": 1, "md": 1, "lg": 0},
            },
            flush_write_queue=lambda: events.append("flush"),
            ensure_disk_cache_dirs=lambda: thumbnail_maintenance.ensure_disk_cache_dirs(
                root,
                thumbnails.THUMB_TIERS,
                thumbnails.FULL_TIER,
                thumbnails.CACHE_MARKER,
            ),
            clear_disk_index=lambda: events.append("clear-index"),
            tier_byte_totals=tier_byte_totals,
            invalidate_disk_stats_cache=lambda: events.append("invalidate"),
            source_stat_cache=source_stat_cache,
            reset_pregen_bulk_cursor=lambda: events.append("bulk-cursor"),
            reset_pregen_full_cursor=lambda: events.append("full-cursor"),
            meta_lock=FakeLock(),
            db_connect=FakeConnection,
            clear_cache_metadata_lock_backoff=lambda: events.append("clear-backoff"),
            is_sqlite_locked=lambda exc: False,
            note_cache_metadata_lock=lambda: events.append("note-lock"),
            set_replace_stale_thumbnails=lambda value: replaced.append(value),
            invalidate_cached_image_ids_cache=lambda **kwargs: invalidated.append(kwargs),
        )

        self.assertEqual(result["memory_entries_cleared"], 2)
        self.assertEqual(result["memory_bytes_cleared"], 9)
        self.assertEqual(result["disk_files_removed"], 1)
        self.assertEqual(result["ssd_cache_dir"], root)
        self.assertFalse(os.path.exists(cache_file))
        self.assertTrue(os.path.exists(thumbnail_maintenance.cache_marker_path(root, thumbnails.CACHE_MARKER)))
        self.assertEqual(tier_byte_totals, {})
        self.assertEqual(source_stat_cache, {})
        self.assertEqual(replaced, [False])
        self.assertEqual(invalidated, [{"cache_root": root}])
        self.assertIn("clear-backoff", events)
        self.assertIn("bulk-cursor", events)
        self.assertIn("full-cursor", events)


class ThumbnailStatusPayloadTests(unittest.TestCase):
    def test_cache_stats_builder_owns_disk_payload_and_snapshot_cache(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = os.path.join(tempdir, "cache-stats.db")
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    """
                    CREATE TABLE cache_entries (
                        cache_root TEXT,
                        size TEXT,
                        image_id INTEGER,
                        path TEXT,
                        source_signature TEXT,
                        size_bytes INTEGER,
                        last_accessed REAL,
                        created_at REAL
                    );
                    """
                )
                conn.executemany(
                    "INSERT INTO cache_entries "
                    "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (tempdir, "sm", 1, "/tmp/sm-1.jpg", "sig-1", 100, 1.0, 40.0),
                        (tempdir, "sm", 2, "/tmp/sm-2.jpg", "sig-2", 300, 1.0, 60.0),
                        (tempdir, thumbnails.FULL_TIER, 3, "/tmp/full-3.jpg", "sig-3", 900, 1.0, 1.0),
                    ],
                )
                conn.commit()

            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            disk_stats_cache = {"data": None, "expires": 0.0, "stale_until": 0.0}
            try:
                result = thumbnail_status.cache_stats(
                    memory_stats=lambda: {"used_bytes": 12},
                    current_time=lambda: 100.0,
                    disk_stats_cache=disk_stats_cache,
                    disk_stats_cache_ttl_seconds=5.0,
                    disk_stats_cache_max_stale_seconds=7.0,
                    meta_lock=thumbnails.threading.Lock(),
                    db_connect=lambda: opened,
                    cache_root=tempdir,
                    cache_limit_bytes=2048,
                    disk_allocations={"sm": 512, "md": 256, "lg": 128, thumbnails.FULL_TIER: 1024},
                    all_tiers=thumbnails.ALL_TIERS,
                    thumb_tiers=thumbnails.THUMB_TIERS,
                    full_tier=thumbnails.FULL_TIER,
                    replace_stale_thumbnails=True,
                    thumb_config_changed_at=50.0,
                )

                sm_count_before_mutation = result["disk"]["tiers"]["sm"]["count"]
                result["disk"]["tiers"]["sm"]["count"] = 999
                cached_result = thumbnail_status.cache_stats(
                    memory_stats=lambda: {"used_bytes": 34},
                    current_time=lambda: 101.0,
                    disk_stats_cache=disk_stats_cache,
                    disk_stats_cache_ttl_seconds=5.0,
                    disk_stats_cache_max_stale_seconds=7.0,
                    meta_lock=thumbnails.threading.Lock(),
                    db_connect=lambda: (_ for _ in ()).throw(AssertionError("should reuse cached disk stats")),
                    cache_root=tempdir,
                    cache_limit_bytes=2048,
                    disk_allocations={"sm": 512, "md": 256, "lg": 128, thumbnails.FULL_TIER: 1024},
                    all_tiers=thumbnails.ALL_TIERS,
                    thumb_tiers=thumbnails.THUMB_TIERS,
                    full_tier=thumbnails.FULL_TIER,
                    replace_stale_thumbnails=True,
                    thumb_config_changed_at=50.0,
                )
            finally:
                opened.close()

        self.assertEqual(result["memory"], {"used_bytes": 12})
        self.assertEqual(result["disk"]["root"], tempdir)
        self.assertEqual(result["disk"]["limit_bytes"], 2048)
        self.assertEqual(result["disk"]["used_bytes"], 1300)
        self.assertEqual(sm_count_before_mutation, 2)
        self.assertEqual(result["disk"]["tiers"]["sm"]["current_count"], 1)
        self.assertEqual(result["disk"]["tiers"]["sm"]["current_bytes"], 300)
        self.assertEqual(result["disk"]["tiers"]["sm"]["stale_count"], 1)
        self.assertTrue(result["disk"]["tiers"]["sm"]["replacement_mode"])
        self.assertEqual(result["disk"]["tiers"][thumbnails.FULL_TIER]["current_count"], 1)
        self.assertFalse(result["disk"]["tiers"][thumbnails.FULL_TIER]["replacement_mode"])
        self.assertEqual(disk_stats_cache["expires"], 105.0)
        self.assertEqual(disk_stats_cache["stale_until"], 107.0)
        self.assertEqual(cached_result["memory"], {"used_bytes": 34})
        self.assertEqual(cached_result["disk"]["tiers"]["sm"]["count"], 2)
        self.assertEqual(cached_result["thumbnail_config"]["changed_at"], 50.0)
        self.assertTrue(cached_result["thumbnail_config"]["replace_stale_thumbnails"])

    def test_pregen_status_builder_owns_payload_math(self):
        class Decision:
            def to_dict(self):
                return {"pause": False}

        stats = {
            "disk": {
                "tiers": {
                    "sm": {
                        "count": 3,
                        "current_count": 2,
                        "current_bytes": 200,
                        "stale_count": 1,
                        "replacement_mode": True,
                        "bytes": 300,
                        "budget_bytes": 1000,
                    },
                    "md": {
                        "count": 1,
                        "current_count": 1,
                        "current_bytes": 100,
                        "stale_count": 0,
                        "replacement_mode": False,
                        "bytes": 100,
                        "budget_bytes": 800,
                    },
                }
            }
        }
        diagnostics = {
            "recent_source_reads_per_min": 6.0,
            "recent_thumbnails_written_per_min": 12.0,
            "recent_read_mbps": 2.5,
            "avg_source_read_seconds": 0.125,
            "avg_decode_encode_seconds": 0.25,
            "recent_source_read_failures": 1,
            "source_read_failures": 2,
        }

        result = thumbnail_status.pregen_status(
            pregen_state={"enabled": True, "state": "running"},
            stats=stats,
            target_total=5,
            original_total=3,
            archive_estimates=None,
            thumb_tiers=("sm", "md"),
            background_tier_budget=lambda size, _estimates: {"sm": 500, "md": 400}[size],
            estimated_tier_bytes=lambda _size: 100,
            original_status=lambda *_args: {"remaining": 2, "count": 1, "total": 3},
            pregen_rates=lambda: (6.0, 3.0, diagnostics),
            pregen_background_decision=Decision,
            pregen_generate_batch_for_decision=lambda _decision: 7,
            idle_seconds=12.345,
        )

        self.assertEqual(result["state"], "running")
        self.assertNotIn("governor", result)
        self.assertEqual(result["idle_seconds"], 12.35)
        self.assertEqual(result["phases"]["sm"]["count"], 2)
        self.assertTrue(result["phases"]["sm"]["replacement_mode"])
        self.assertEqual(result["phases"]["sm"]["stale_count"], 1)
        self.assertEqual(result["preview"], {
            "count": 3,
            "total": 9,
            "remaining": 6,
            "image_remaining": 3,
            "progress_pct": 33.3,
        })
        self.assertEqual(result["eta_seconds"], 30)
        self.assertIsNone(result["original_eta_seconds"])
        self.assertTrue(result["replacement_mode"])
        self.assertEqual(result["recent_source_read_failures"], 1)
        self.assertEqual(result["source_read_failures"], 2)


class ThumbnailBulkWarmupTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_cache_dir = thumbnails.SSD_CACHE_DIR
        self.old_allocations = dict(thumbnails._disk_allocations)
        self.old_get_source_bits = thumbnails._get_source_bits
        self.old_load_source_image = thumbnails._load_source_image
        self.old_memory_bytes = thumbnails.MEMORY_CACHE_BYTES
        self.old_db_connect = thumbnails._db_connect
        self.old_cache_entry_db_connect = thumbnail_cache_entries._db_connect
        self.old_data_providers = {
            name: getattr(thumbnails.data_providers, name)
            for name in (
                "_db_path",
                "_get_db",
                "_batch_set_orientations",
                "_mark_image_missing_sync",
                "_invalidate_cached_image_ids_cache",
                "_note_cached_image_ids_added",
            )
        }
        self.old_persistent_conn = thumbnail_cache_entries._persistent_conn
        self.old_prefetching = thumbnails._prefetching
        self.old_pregen_manual_mode = thumbnails._pregen_manual_mode
        self.old_pregen_manual_pause = thumbnails._pregen_manual_pause
        self.old_last_user_activity = thumbnails._last_user_activity
        self.old_disk_stats_cache = dict(thumbnails._disk_stats_cache)

        thumbnails.SSD_CACHE_DIR = self.tempdir.name
        self.db_path = os.path.join(self.tempdir.name, "thumbnail-cache-test.db")
        thumbnail_cache_entries._persistent_conn = None
        self.provider_events = []

        async def get_db():
            return await thumbnails.data_connection.open_async(self.db_path)

        async def batch_set_orientations(updates):
            conn = await thumbnails.data_connection.open_async(self.db_path)
            try:
                await conn.executemany(
                    "UPDATE images SET orientation = ?, aspect_ratio = ? WHERE id = ?",
                    updates,
                )
                await conn.commit()
            finally:
                await conn.close()

        def mark_image_missing_sync(image_id):
            with closing(sqlite3.connect(self.db_path)) as conn:
                cursor = conn.execute(
                    "UPDATE images SET missing_at = COALESCE(missing_at, ?) WHERE id = ?",
                    (time.time(), image_id),
                )
                conn.commit()
                return cursor.rowcount > 0

        thumbnails.configure_data_providers(
            db_path=lambda: self.db_path,
            get_db=get_db,
            batch_set_orientations=batch_set_orientations,
            mark_image_missing_sync=mark_image_missing_sync,
            invalidate_cached_image_ids_cache=lambda **kwargs: self.provider_events.append(("invalidate", kwargs)),
            note_cached_image_ids_added=lambda cache_root, size, image_ids: self.provider_events.append(
                ("note", cache_root, size, list(image_ids)),
            ),
        )
        thumbnails._disk_allocations.update({
            "sm": 64 * 1024 * 1024,
            "md": 64 * 1024 * 1024,
            "lg": 64 * 1024 * 1024,
            thumbnails.FULL_TIER: 0,
        })
        thumbnails.MEMORY_CACHE_BYTES = 0
        thumbnails._ensure_disk_cache_dirs()
        thumbnails._clear_memory_cache()
        thumbnails._clear_disk_index()
        thumbnails._tier_byte_totals.clear()
        thumbnails._source_stat_cache.clear()
        thumbnails._thumbnail_retry_after.clear()
        thumbnails._prefetching = True
        thumbnails._pregen_manual_mode = True
        thumbnails._pregen_manual_pause = False
        thumbnails._last_user_activity = thumbnails.time.monotonic() - 30.0
        thumbnails._reset_pregen_bulk_cursor()
        thumbnails._reset_pregen_full_cursor()
        thumbnails._disk_stats_cache.clear()
        thumbnails._disk_stats_cache.update(self.old_disk_stats_cache)
        with thumbnails._write_queue_lock:
            thumbnails._write_queue.clear()
        with thumbnails._disk_index_lock:
            thumbnail_cache_entries._disk_index_built = True

    def tearDown(self):
        thumbnails.SSD_CACHE_DIR = self.old_cache_dir
        thumbnails._disk_allocations.clear()
        thumbnails._disk_allocations.update(self.old_allocations)
        thumbnails._get_source_bits = self.old_get_source_bits
        thumbnails._load_source_image = self.old_load_source_image
        thumbnails._db_connect = self.old_db_connect
        thumbnail_cache_entries._db_connect = self.old_cache_entry_db_connect
        if thumbnail_cache_entries._persistent_conn is not None:
            thumbnail_cache_entries._persistent_conn.close()
        thumbnail_cache_entries._persistent_conn = self.old_persistent_conn
        for name, value in self.old_data_providers.items():
            setattr(thumbnails.data_providers, name, value)
        thumbnails.MEMORY_CACHE_BYTES = self.old_memory_bytes
        thumbnails._prefetching = self.old_prefetching
        thumbnails._pregen_manual_mode = self.old_pregen_manual_mode
        thumbnails._pregen_manual_pause = self.old_pregen_manual_pause
        thumbnails._last_user_activity = self.old_last_user_activity
        thumbnails._clear_memory_cache()
        thumbnails._clear_disk_index()
        thumbnails._tier_byte_totals.clear()
        thumbnails._source_stat_cache.clear()
        thumbnails._thumbnail_retry_after.clear()
        thumbnails._reset_pregen_bulk_cursor()
        thumbnails._reset_pregen_full_cursor()
        with thumbnails._write_queue_lock:
            thumbnails._write_queue.clear()
        thumbnails._clear_cache_metadata_lock_backoff()
        self.tempdir.cleanup()

    def _make_image(self) -> str:
        path = os.path.join(self.tempdir.name, "source.jpg")
        Image.new("RGB", (1200, 800), color=(120, 80, 40)).save(path, "JPEG", quality=90)
        os.utime(path, (time.time(), 1712345678.25))
        return path

    def _make_original_file(self, name: str, size: int) -> str:
        path = os.path.join(self.tempdir.name, name)
        with open(path, "wb") as f:
            f.write(bytes([len(name) % 251]) * size)
        os.utime(path, (time.time(), 1712345678.25 + size))
        return path

    def test_soft_disk_stats_invalidation_reuses_recent_snapshot(self):
        now = time.time()
        thumbnails._disk_stats_cache.update({
            "data": {
                "root": self.tempdir.name,
                "limit_bytes": 1024,
                "used_bytes": 0,
                "tiers": {},
            },
            "expires": 0.0,
            "stale_until": now + 30.0,
        })

        thumbnails._invalidate_disk_stats_cache(soft=True)
        self.assertGreater(thumbnails._disk_stats_cache["expires"], now)

        thumbnails._disk_stats_cache["stale_until"] = now - 1.0
        thumbnails._invalidate_disk_stats_cache(soft=True)
        self.assertEqual(thumbnails._disk_stats_cache["expires"], 0.0)

    def _add_catalog_original(self, image_id: int, path: str):
        stat = os.stat(path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(data_schema.SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO catalog_sources "
                "(id, path, display_name, included, online) VALUES (1, ?, 'catalog', 1, 1)",
                (self.tempdir.name,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO images "
                "(id, source_id, filename, filepath, status, file_size, file_modified_at, missing_at) "
                "VALUES (?, 1, ?, ?, 'kept', ?, ?, NULL)",
                (image_id, os.path.basename(path), path, int(stat.st_size), float(stat.st_mtime)),
            )
            conn.commit()

    def test_pregen_candidate_queries_remain_facaded_from_pregen_module(self):
        first = self._make_original_file("a.jpg", 30)
        second = self._make_original_file("b.jpg", 40)
        self._add_catalog_original(10, second)
        self._add_catalog_original(9, first)

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(data_schema.SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO catalog_sources "
                "(id, path, display_name, included, online) VALUES (2, ?, 'offline', 1, 0)",
                (os.path.join(self.tempdir.name, "offline"),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO catalog_sources "
                "(id, path, display_name, included, online) VALUES (3, ?, 'excluded', 0, 1)",
                (os.path.join(self.tempdir.name, "excluded"),),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO images "
                "(id, source_id, filename, filepath, status, file_size, file_modified_at, missing_at) "
                "VALUES (?, ?, ?, ?, 'kept', ?, ?, ?)",
                [
                    (
                        11,
                        1,
                        "missing.jpg",
                        os.path.join(self.tempdir.name, "missing.jpg"),
                        12,
                        1.0,
                        10.0,
                    ),
                    (
                        12,
                        2,
                        "offline.jpg",
                        os.path.join(self.tempdir.name, "offline.jpg"),
                        12,
                        1.0,
                        None,
                    ),
                    (
                        13,
                        3,
                        "excluded.jpg",
                        os.path.join(self.tempdir.name, "excluded.jpg"),
                        12,
                        1.0,
                        None,
                    ),
                ],
            )
            conn.commit()

        direct_total = asyncio.run(
            thumbnail_pregen.cache_target_total(thumbnails.data_providers.get_db)
        )
        facade_total = asyncio.run(thumbnails._cache_target_total())

        self.assertEqual(facade_total, direct_total)
        self.assertEqual(facade_total, 2)

        direct_cursor = {"source_id": 0, "filepath": "", "id": 0}
        direct_first = asyncio.run(
            thumbnail_pregen.candidate_batch(
                thumbnails.data_providers.get_db,
                direct_cursor,
                1,
            )
        )
        direct_second = asyncio.run(
            thumbnail_pregen.candidate_batch(
                thumbnails.data_providers.get_db,
                direct_cursor,
                10,
            )
        )

        thumbnails._reset_pregen_bulk_cursor()
        facade_first = asyncio.run(thumbnails._pregen_bulk_candidate_batch(1))
        facade_second = asyncio.run(thumbnails._pregen_bulk_candidate_batch(10))

        self.assertEqual(
            [row["id"] for row in facade_first],
            [row["id"] for row in direct_first],
        )
        self.assertEqual(
            [row["id"] for row in facade_second],
            [row["id"] for row in direct_second],
        )
        self.assertEqual([row["id"] for row in facade_first + facade_second], [9, 10])
        self.assertEqual(thumbnails._pregen_bulk_cursor, direct_cursor)

        thumbnails._reset_pregen_full_cursor()
        full_rows = asyncio.run(thumbnails._pregen_full_candidate_batch(10))

        self.assertEqual([row["id"] for row in full_rows], [9, 10])
        self.assertEqual(
            thumbnails._pregen_full_cursor,
            {"source_id": 1, "filepath": second, "id": 10},
        )

    def test_pregen_tier_budget_room_helpers_remain_facaded_from_pregen_module(self):
        class FakeConnection:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class FakeLock:
            def __init__(self):
                self.entered = 0

            def __enter__(self):
                self.entered += 1

            def __exit__(self, exc_type, exc, tb):
                return False

        budgets = {size: (idx + 1) * 100 for idx, size in enumerate(thumbnails.THUMB_TIERS)}
        tier_bytes = {size: (idx + 1) * 25 for idx, size in enumerate(thumbnails.THUMB_TIERS)}
        tier_bytes[thumbnails.FULL_TIER] = 40

        old_background_tier_budget = thumbnails._background_tier_budget
        old_meta_lock = thumbnails._meta_lock
        old_db_connect = thumbnails._db_connect
        old_tier_bytes = thumbnails._tier_bytes
        old_backoff_active = thumbnails._cache_metadata_backoff_active
        old_clear_backoff = thumbnails._clear_cache_metadata_lock_backoff
        old_note_lock = thumbnails._note_cache_metadata_lock
        old_is_locked = thumbnails._is_sqlite_locked
        try:
            thumbnails._background_tier_budget = lambda size: budgets[size]
            self.assertEqual(
                thumbnails._bulk_tier_budgets(),
                thumbnail_pregen.bulk_tier_budgets(
                    thumbnails.THUMB_TIERS,
                    thumbnails._background_tier_budget,
                ),
            )

            direct_events = []
            direct_connection_queue = [FakeConnection(), FakeConnection()]
            direct_connections = list(direct_connection_queue)
            direct_lock = FakeLock()
            direct_full_room = thumbnail_pregen.full_tier_room(
                200,
                full_tier=thumbnails.FULL_TIER,
                meta_lock=direct_lock,
                db_connect=lambda: direct_connection_queue.pop(0),
                tier_bytes=lambda conn, size: tier_bytes[size],
                cache_metadata_backoff_active=lambda: False,
                clear_cache_metadata_lock_backoff=lambda: direct_events.append("clear"),
                note_cache_metadata_lock=lambda: direct_events.append("note"),
                is_sqlite_locked=lambda exc: False,
            )
            direct_bulk_room = thumbnail_pregen.bulk_tier_room(
                budgets,
                thumb_tiers=thumbnails.THUMB_TIERS,
                meta_lock=direct_lock,
                db_connect=lambda: direct_connection_queue.pop(0),
                tier_bytes=lambda conn, size: tier_bytes[size],
                cache_metadata_backoff_active=lambda: False,
                clear_cache_metadata_lock_backoff=lambda: direct_events.append("clear"),
                note_cache_metadata_lock=lambda: direct_events.append("note"),
                is_sqlite_locked=lambda exc: False,
            )

            facade_events = []
            facade_connection_queue = [FakeConnection(), FakeConnection()]
            facade_connections = list(facade_connection_queue)
            thumbnails._meta_lock = FakeLock()
            thumbnails._db_connect = lambda: facade_connection_queue.pop(0)
            thumbnails._tier_bytes = lambda conn, size: tier_bytes[size]
            thumbnails._cache_metadata_backoff_active = lambda: False
            thumbnails._clear_cache_metadata_lock_backoff = lambda: facade_events.append("clear")
            thumbnails._note_cache_metadata_lock = lambda: facade_events.append("note")
            thumbnails._is_sqlite_locked = lambda exc: False

            self.assertEqual(thumbnails._full_tier_room(200), direct_full_room)
            self.assertEqual(thumbnails._bulk_tier_room(budgets), direct_bulk_room)
            self.assertEqual(direct_full_room, 160)
            self.assertEqual(direct_bulk_room, {
                size: budgets[size] - tier_bytes[size] for size in thumbnails.THUMB_TIERS
            })
            self.assertEqual(direct_events, ["clear", "clear"])
            self.assertEqual(facade_events, direct_events)
            self.assertEqual(direct_lock.entered, 2)
            self.assertEqual(thumbnails._meta_lock.entered, 2)
            self.assertTrue(all(conn.closed for conn in direct_connections))
            self.assertTrue(all(conn.closed for conn in facade_connections))

            locked_conn = FakeConnection()
            locked_events = []
            thumbnails._db_connect = lambda: locked_conn
            thumbnails._tier_bytes = (
                lambda conn, size: (_ for _ in ()).throw(
                    sqlite3.OperationalError("database is locked")
                )
            )
            thumbnails._is_sqlite_locked = lambda exc: True
            thumbnails._clear_cache_metadata_lock_backoff = lambda: locked_events.append("clear")
            thumbnails._note_cache_metadata_lock = lambda: locked_events.append("note")

            self.assertEqual(thumbnails._full_tier_room(200), 0)
            self.assertEqual(locked_events, ["note"])
            self.assertTrue(locked_conn.closed)
        finally:
            thumbnails._background_tier_budget = old_background_tier_budget
            thumbnails._meta_lock = old_meta_lock
            thumbnails._db_connect = old_db_connect
            thumbnails._tier_bytes = old_tier_bytes
            thumbnails._cache_metadata_backoff_active = old_backoff_active
            thumbnails._clear_cache_metadata_lock_backoff = old_clear_backoff
            thumbnails._note_cache_metadata_lock = old_note_lock
            thumbnails._is_sqlite_locked = old_is_locked

    def test_full_image_orchestration_remains_facaded_from_full_cache_module(self):
        source_path = self._make_original_file("full-source.bin", 32)
        cached_path = os.path.join(self.tempdir.name, "cached-full.jpg")

        def source_signature(filepath, size, image_id):
            return f"{filepath}:{size}:{image_id}"

        def cached_entry(size, image_id, signature):
            self.assertEqual(size, thumbnails.FULL_TIER)
            self.assertEqual(image_id, 42)
            self.assertEqual(signature, source_signature(source_path, size, image_id))
            return {"path": cached_path}

        old_build_source_signature = thumbnails._build_source_signature
        old_get_disk_entry = thumbnails._get_disk_entry
        old_inflight = thumbnails._inflight
        old_run_full_image_job = thumbnails._run_full_image_job
        old_note_user_activity = thumbnails.note_user_activity
        old_touch_cached_signature = thumbnails.touch_cached_signature
        old_cache_dir = thumbnails.SSD_CACHE_DIR
        old_allocations = dict(thumbnails._disk_allocations)
        try:
            thumbnails._build_source_signature = source_signature
            thumbnails._get_disk_entry = cached_entry
            self.assertEqual(
                thumbnails.get_cached_full_image_path(source_path, 42),
                thumbnail_full_cache.get_cached_full_image_path(
                    source_path,
                    42,
                    full_tier=thumbnails.FULL_TIER,
                    build_source_signature=source_signature,
                    get_disk_entry=cached_entry,
                ),
            )

            async def direct_get_full():
                calls = []

                async def run_job(filepath, image_id, hot):
                    calls.append(("job", filepath, image_id, hot))
                    return cached_path

                inflight = {}
                result = await thumbnail_full_cache.get_full_image_path(
                    source_path,
                    43,
                    note_user_activity=lambda: calls.append(("activity",)),
                    full_tier=thumbnails.FULL_TIER,
                    build_source_signature=source_signature,
                    get_disk_entry=lambda *_args: None,
                    inflight=inflight,
                    run_full_image_job=run_job,
                )
                return result, calls, inflight

            direct_result, direct_calls, direct_inflight = asyncio.run(direct_get_full())

            facade_calls = []

            async def facade_run_job(filepath, image_id, hot):
                facade_calls.append(("job", filepath, image_id, hot))
                return cached_path

            thumbnails._get_disk_entry = lambda *_args: None
            thumbnails._run_full_image_job = facade_run_job
            thumbnails.note_user_activity = lambda: facade_calls.append(("activity",))
            thumbnails._inflight = {}

            facade_result = asyncio.run(thumbnails.get_full_image_path(source_path, 43))

            self.assertEqual(facade_result, direct_result)
            self.assertEqual(facade_calls, direct_calls)
            self.assertEqual(direct_inflight, {})
            self.assertEqual(thumbnails._inflight, {})

            async def run_facade_schedule():
                thumbnails._inflight = {}
                thumbnails.SSD_CACHE_DIR = self.tempdir.name
                thumbnails._disk_allocations[thumbnails.FULL_TIER] = 1000
                thumbnails.touch_cached_signature = lambda *_args: False
                await thumbnails.schedule_full_image_cache(source_path, 44, hot=False)
                await asyncio.sleep(0)
                await asyncio.sleep(0)

            facade_calls.clear()
            asyncio.run(run_facade_schedule())

            self.assertEqual(facade_calls, [("job", source_path, 44, False)])
            self.assertEqual(thumbnails._inflight, {})
        finally:
            thumbnails._build_source_signature = old_build_source_signature
            thumbnails._get_disk_entry = old_get_disk_entry
            thumbnails._inflight = old_inflight
            thumbnails._run_full_image_job = old_run_full_image_job
            thumbnails.note_user_activity = old_note_user_activity
            thumbnails.touch_cached_signature = old_touch_cached_signature
            thumbnails.SSD_CACHE_DIR = old_cache_dir
            thumbnails._disk_allocations.clear()
            thumbnails._disk_allocations.update(old_allocations)

    def test_embedding_image_loader_remains_facaded_from_generation_module(self):
        path = self._make_image()
        image_id = 72
        signature = thumbnails._build_source_signature(path, "md", image_id)
        data = thumbnails._generate_thumbnail_set_sync(
            path,
            image_id,
            {"md": signature},
        )
        self.assertEqual(data["thumbnails_written"], 1)
        cached_bytes = thumbnails.fast_disk_read("md", image_id)
        self.assertIsNotNone(cached_bytes)

        direct_image = thumbnail_generation.load_embedding_image(
            path,
            image_id,
            require_cached=True,
            sizes=thumbnails.SIZES,
            memory_get_fast=lambda *_args: None,
            fast_disk_read=lambda *_args: cached_bytes,
            build_source_signature=thumbnails._build_source_signature,
            memory_get=thumbnails._memory_get,
            read_disk_thumbnail=thumbnails._read_disk_thumbnail,
            load_source_image=thumbnails._load_source_image,
            resize_to_long_side=thumbnails._resize_to_long_side,
        )
        facade_image = thumbnails.load_embedding_image(path, image_id, require_cached=True)
        missing_cached = thumbnails.load_embedding_image(
            os.path.join(self.tempdir.name, "missing.jpg"),
            image_id + 1,
            require_cached=True,
        )
        try:
            self.assertIsNotNone(direct_image)
            self.assertIsNotNone(facade_image)
            self.assertEqual(facade_image.mode, "RGB")
            self.assertEqual(facade_image.size, direct_image.size)
            self.assertIsNone(missing_cached)
        finally:
            if direct_image is not None:
                direct_image.close()
            if facade_image is not None:
                facade_image.close()

    def test_generation_encode_and_orientation_helpers_remain_facaded(self):
        class FakeLock:
            def __init__(self):
                self.entered = 0

            def __enter__(self):
                self.entered += 1

            def __exit__(self, exc_type, exc, tb):
                return False

        direct_queue = {}
        direct_lock = FakeLock()
        direct_image = Image.new("L", (10, 20), color=100)
        thumbnail_generation.queue_orientation(
            5,
            direct_image,
            orientation_lock=direct_lock,
            orientation_queue=direct_queue,
        )

        key = ("md", 5, "signature")
        direct_retry = {key: 1.0}
        direct_calls = []
        direct_variant, direct_data, direct_written = thumbnail_generation.encode_and_cache_thumbnail(
            "md",
            5,
            "signature",
            direct_image,
            hot=True,
            thumb_quality=thumbnails.THUMB_QUALITY,
            memory_put=lambda *args: direct_calls.append(("memory", args)),
            write_thumbnail_to_disk=lambda *args, hot: direct_calls.append(("disk", args, hot)) or True,
            thumbnail_retry_after=direct_retry,
        )

        old_orientation_lock = thumbnails._orientation_lock
        old_orientation_queue = thumbnails._orientation_queue
        old_memory_put = thumbnails._memory_put
        old_write_thumbnail = thumbnails._write_thumbnail_to_disk
        old_retry_after = thumbnails._thumbnail_retry_after
        facade_image = Image.new("L", (10, 20), color=100)
        try:
            facade_lock = FakeLock()
            facade_queue = {}
            facade_retry = {key: 1.0}
            facade_calls = []
            thumbnails._orientation_lock = facade_lock
            thumbnails._orientation_queue = facade_queue
            thumbnails._memory_put = lambda *args: facade_calls.append(("memory", args))
            thumbnails._write_thumbnail_to_disk = (
                lambda *args, hot: facade_calls.append(("disk", args, hot)) or True
            )
            thumbnails._thumbnail_retry_after = facade_retry

            thumbnails._queue_orientation(5, facade_image)
            facade_variant, facade_data, facade_written = thumbnails._encode_and_cache_thumbnail(
                "md",
                5,
                "signature",
                facade_image,
                hot=True,
            )

            self.assertEqual(facade_queue, direct_queue)
            self.assertEqual(facade_lock.entered, direct_lock.entered)
            self.assertEqual(facade_variant.mode, "RGB")
            self.assertEqual(facade_data, direct_data)
            self.assertEqual(facade_written, direct_written)
            self.assertEqual(facade_calls, direct_calls)
            self.assertEqual(facade_retry, direct_retry)
        finally:
            thumbnails._orientation_lock = old_orientation_lock
            thumbnails._orientation_queue = old_orientation_queue
            thumbnails._memory_put = old_memory_put
            thumbnails._write_thumbnail_to_disk = old_write_thumbnail
            thumbnails._thumbnail_retry_after = old_retry_after
            direct_variant.close()
            if 'facade_variant' in locals():
                facade_variant.close()

    def test_generation_flush_orientation_updates_remains_facaded(self):
        class FakeLock:
            def __init__(self):
                self.entered = 0

            def __enter__(self):
                self.entered += 1

            def __exit__(self, exc_type, exc, tb):
                return False

        async def run_case():
            direct_queue = {
                7: ("portrait", 0.75),
                8: ("landscape", 1.5),
            }
            direct_lock = FakeLock()
            direct_calls = []

            await thumbnail_generation.flush_orientation_updates(
                orientation_lock=direct_lock,
                orientation_queue=direct_queue,
                batch_set_orientations=lambda updates: (
                    direct_calls.append(tuple(updates)) or asyncio.sleep(0)
                ),
            )

            old_orientation_lock = thumbnails._orientation_lock
            old_orientation_queue = thumbnails._orientation_queue
            old_batch_set_orientations = thumbnails.data_providers.batch_set_orientations
            try:
                facade_lock = FakeLock()
                facade_queue = {
                    7: ("portrait", 0.75),
                    8: ("landscape", 1.5),
                }
                facade_calls = []
                thumbnails._orientation_lock = facade_lock
                thumbnails._orientation_queue = facade_queue
                thumbnails.data_providers.batch_set_orientations = (
                    lambda updates: facade_calls.append(tuple(updates)) or asyncio.sleep(0)
                )

                await thumbnails.flush_orientation_updates()

                return (
                    direct_queue,
                    direct_lock.entered,
                    direct_calls,
                    facade_queue,
                    facade_lock.entered,
                    facade_calls,
                )
            finally:
                thumbnails._orientation_lock = old_orientation_lock
                thumbnails._orientation_queue = old_orientation_queue
                thumbnails.data_providers.batch_set_orientations = old_batch_set_orientations

        direct_queue, direct_entered, direct_calls, facade_queue, facade_entered, facade_calls = (
            asyncio.run(run_case())
        )

        self.assertEqual(direct_queue, {})
        self.assertEqual(facade_queue, {})
        self.assertEqual(direct_entered, facade_entered)
        self.assertEqual(facade_calls, direct_calls)
        self.assertEqual(direct_calls, [(("portrait", 0.75, 7), ("landscape", 1.5, 8))])

    def test_planned_thumbnail_sizes_remains_facaded_from_generation_module(self):
        path = os.path.join(self.tempdir.name, "planned.jpg")
        image_id = 8

        def source_signature(filepath, size, item_id):
            return f"{filepath}:{size}:{item_id}"

        retry_signature = source_signature(path, "sm", image_id)
        retry_until = time.time() + 3600.0
        retry_after = {("sm", image_id, retry_signature): retry_until}

        def memory_get(size, item_id, signature):
            if size == "md":
                return b"cached"
            return None

        def fast_has(size, item_id, signature=None):
            return size == "lg" and signature is not None

        disk_entry_calls = []

        def disk_entry(size, item_id, signature, *, touch=True):
            disk_entry_calls.append((size, item_id, signature, touch))
            return None

        direct = thumbnail_generation.planned_thumbnail_sizes(
            path,
            image_id,
            "lg",
            include_smaller_tiers=True,
            allow_stale_fallback=True,
            source_missing=lambda _path: False,
            sizes=thumbnails.SIZES,
            thumb_tiers=thumbnails.THUMB_TIERS,
            disk_allocations={"sm": 1, "md": 1, "lg": 1},
            build_source_signature=source_signature,
            thumbnail_retry_after=retry_after,
            memory_get=memory_get,
            fast_disk_has=fast_has,
            get_disk_entry=disk_entry,
            now_provider=lambda: retry_until - 1.0,
        )

        old_source_missing = thumbnails._source_missing
        old_build_source_signature = thumbnails._build_source_signature
        old_retry_after = thumbnails._thumbnail_retry_after
        old_memory_get = thumbnails._memory_get
        old_fast_disk_has = thumbnails.fast_disk_has
        old_get_disk_entry = thumbnails._get_disk_entry
        old_allocations = dict(thumbnails._disk_allocations)
        try:
            thumbnails._source_missing = lambda _path: False
            thumbnails._build_source_signature = source_signature
            thumbnails._thumbnail_retry_after = retry_after
            thumbnails._memory_get = memory_get
            thumbnails.fast_disk_has = fast_has
            thumbnails._get_disk_entry = disk_entry
            thumbnails._disk_allocations.clear()
            thumbnails._disk_allocations.update({"sm": 1, "md": 1, "lg": 1})

            facade = thumbnails._planned_thumbnail_sizes(
                path,
                image_id,
                "lg",
                include_smaller_tiers=True,
                allow_stale_fallback=True,
            )

            self.assertEqual(facade, direct)
            self.assertEqual(facade, [])

            thumbnails._source_missing = lambda _path: True
            self.assertEqual(thumbnails._planned_thumbnail_sizes(path, image_id, "lg"), [])
        finally:
            thumbnails._source_missing = old_source_missing
            thumbnails._build_source_signature = old_build_source_signature
            thumbnails._thumbnail_retry_after = old_retry_after
            thumbnails._memory_get = old_memory_get
            thumbnails.fast_disk_has = old_fast_disk_has
            thumbnails._get_disk_entry = old_get_disk_entry
            thumbnails._disk_allocations.clear()
            thumbnails._disk_allocations.update(old_allocations)

    def test_generate_missing_thumbnails_remains_facaded_from_generation_module(self):
        path = os.path.join(self.tempdir.name, "generate-missing.jpg")
        image_id = 9

        def planned(filepath, item_id, requested_size, **kwargs):
            self.assertEqual((filepath, item_id, requested_size), (path, image_id, "sm"))
            self.assertTrue(kwargs["include_smaller_tiers"])
            self.assertFalse(kwargs["allow_stale_fallback"])
            return ["md", "sm"]

        def source_signature(filepath, size, item_id):
            return f"{filepath}:{size}:{item_id}"

        def make_helpers(calls):
            def load_source(filepath, max_target, *, prefer_draft, image_id=None):
                calls.append(("load", filepath, max_target, prefer_draft))
                return Image.new("RGB", (40, 20), color=(20, 40, 60))

            def queue_orientation(item_id, img):
                calls.append(("orientation", item_id, img.size))

            def resize(img, target):
                calls.append(("resize", target))
                return img.copy()

            def encode(size, item_id, signature, variant, *, hot):
                calls.append(("encode", size, item_id, signature, hot))
                return variant, f"{size}-bytes".encode("ascii"), True

            return load_source, queue_orientation, resize, encode

        direct_calls = []
        direct_load, direct_queue, direct_resize, direct_encode = make_helpers(direct_calls)
        direct_retry = {}
        direct_result = thumbnail_generation.generate_missing_thumbnails(
            path,
            "sm",
            image_id,
            include_smaller_tiers=True,
            hot=True,
            allow_stale_fallback=False,
            planned_thumbnail_sizes=planned,
            sizes=thumbnails.SIZES,
            load_source_image=direct_load,
            queue_orientation=direct_queue,
            resize_to_long_side=direct_resize,
            build_source_signature=source_signature,
            encode_and_cache_thumbnail=direct_encode,
            mark_source_missing_from_error=lambda *_args: False,
            thumbnail_retry_after=direct_retry,
            thumbnail_retry_seconds=thumbnails.THUMBNAIL_RETRY_SECONDS,
            now_provider=lambda: 100.0,
            log=lambda _message: None,
        )

        old_planned = thumbnails._planned_thumbnail_sizes
        old_load_source = thumbnails._load_source_image
        old_queue_orientation = thumbnails._queue_orientation
        old_resize = thumbnails._resize_to_long_side
        old_build_source_signature = thumbnails._build_source_signature
        old_encode = thumbnails._encode_and_cache_thumbnail
        old_mark_missing = thumbnails._mark_source_missing_from_error
        old_retry_after = thumbnails._thumbnail_retry_after
        try:
            facade_calls = []
            facade_load, facade_queue, facade_resize, facade_encode = make_helpers(facade_calls)
            thumbnails._planned_thumbnail_sizes = planned
            thumbnails._load_source_image = facade_load
            thumbnails._queue_orientation = facade_queue
            thumbnails._resize_to_long_side = facade_resize
            thumbnails._build_source_signature = source_signature
            thumbnails._encode_and_cache_thumbnail = facade_encode
            thumbnails._mark_source_missing_from_error = lambda *_args: False
            thumbnails._thumbnail_retry_after = {}

            facade_result = thumbnails._generate_missing_thumbnails_sync(
                path,
                "sm",
                image_id,
                include_smaller_tiers=True,
                hot=True,
                allow_stale_fallback=False,
            )

            self.assertEqual(facade_result, direct_result)
            self.assertEqual(facade_result, b"sm-bytes")
            self.assertEqual(facade_calls, direct_calls)
            self.assertEqual(thumbnails._thumbnail_retry_after, direct_retry)
        finally:
            thumbnails._planned_thumbnail_sizes = old_planned
            thumbnails._load_source_image = old_load_source
            thumbnails._queue_orientation = old_queue_orientation
            thumbnails._resize_to_long_side = old_resize
            thumbnails._build_source_signature = old_build_source_signature
            thumbnails._encode_and_cache_thumbnail = old_encode
            thumbnails._mark_source_missing_from_error = old_mark_missing
            thumbnails._thumbnail_retry_after = old_retry_after

    def _cache_original_now(self, image_id: int, path: str):
        signature = thumbnails._build_source_signature(path, thumbnails.FULL_TIER, image_id)
        return thumbnails._cache_full_image_sync(path, image_id, signature, hot=False)

    def _catalog_signatures(self, path: str, image_id: int = 1) -> tuple[dict[str, str], int, float]:
        stat = os.stat(path)
        file_size = int(stat.st_size)
        file_modified_at = float(stat.st_mtime)
        signatures = {
            size: thumbnails._build_catalog_source_signature(
                path,
                size,
                image_id,
                file_size,
                file_modified_at,
            )[0]
            for size in thumbnails.THUMB_TIERS
        }
        return signatures, file_size, file_modified_at

    def test_catalog_signature_uses_metadata_before_stat_fallback(self):
        calls = []

        def fake_source_bits(filepath):
            calls.append(filepath)
            return f"321|987654321|{filepath}"

        thumbnails._get_source_bits = fake_source_bits
        path = os.path.join(self.tempdir.name, "photo.jpg")

        signature, source_size, missing = thumbnails._build_catalog_source_signature(
            path,
            "md",
            42,
            123,
            456.5,
        )
        self.assertEqual(source_size, 123)
        self.assertFalse(missing)
        self.assertEqual(calls, [])
        self.assertEqual(
            signature,
            thumbnails._build_source_signature_from_bits("catalog|123|456.500000000|" + path, "md", 42),
        )

        fallback_signature, fallback_size, fallback_missing = thumbnails._build_catalog_source_signature(
            path,
            "md",
            42,
            None,
            None,
        )
        self.assertEqual(calls, [path])
        self.assertEqual(fallback_size, 321)
        self.assertFalse(fallback_missing)
        self.assertEqual(
            fallback_signature,
            thumbnails._build_source_signature_from_bits(f"321|987654321|{path}", "md", 42),
        )

    def test_source_identity_facade_uses_current_thumbnail_globals(self):
        calls = []
        old_quality = thumbnails.THUMB_QUALITY
        old_md_size = thumbnails.SIZES["md"]
        old_browser_max_age = thumbnails.BROWSER_CACHE_MAX_AGE
        old_stale = thumbnails.BROWSER_CACHE_STALE_WHILE_REVALIDATE

        def fake_source_bits(filepath):
            calls.append(filepath)
            return f"222|333|{filepath}"

        thumbnails._get_source_bits = fake_source_bits
        thumbnails.THUMB_QUALITY = 80
        thumbnails.SIZES["md"] = 1600
        thumbnails.BROWSER_CACHE_MAX_AGE = 123
        thumbnails.BROWSER_CACHE_STALE_WHILE_REVALIDATE = 456
        path = os.path.join(self.tempdir.name, "facade.jpg")
        try:
            source_bits = f"222|333|{path}"
            expected_etag = (
                f"\"{thumbnails._build_source_signature_from_bits(source_bits, 'md', 9)}\""
            )
            headers = thumbnails.response_headers(path, "md", 9)
        finally:
            thumbnails.THUMB_QUALITY = old_quality
            thumbnails.SIZES["md"] = old_md_size
            thumbnails.BROWSER_CACHE_MAX_AGE = old_browser_max_age
            thumbnails.BROWSER_CACHE_STALE_WHILE_REVALIDATE = old_stale

        self.assertEqual(calls, [path])
        self.assertEqual(headers["ETag"], expected_etag)
        self.assertEqual(
            headers["Cache-Control"],
            "public, max-age=123, stale-while-revalidate=456",
        )

    def test_full_candidate_signature_uses_catalog_metadata_before_stat_fallback(self):
        calls = []
        path = os.path.join(self.tempdir.name, "photo.jpg")
        row = {
            "id": 7,
            "filepath": path,
            "file_size": 1234,
            "file_modified_at": 456.5,
        }

        def fail_source_bits(filepath):
            calls.append(filepath)
            raise AssertionError("full pregen should use catalog metadata first")

        thumbnails._get_source_bits = fail_source_bits

        item = thumbnails._full_candidate_signature(
            row,
            {"bytes": 4096},
            4096,
        )

        self.assertEqual(calls, [])
        self.assertEqual(item["id"], 7)
        self.assertEqual(item["source_size"], 1234)

    def test_bulk_generation_writes_all_missing_tiers_from_one_source_load(self):
        path = self._make_image()
        signatures, file_size, _mtime = self._catalog_signatures(path)
        load_count = 0

        def counted_load(*args, **kwargs):
            nonlocal load_count
            load_count += 1
            return self.old_load_source_image(*args, **kwargs)

        thumbnails._load_source_image = counted_load
        metrics = thumbnails._generate_thumbnail_set_sync(
            path,
            1,
            signatures,
            source_bytes=file_size,
        )

        self.assertEqual(load_count, 1)
        self.assertEqual(metrics["source_reads"], 1)
        self.assertEqual(metrics["thumbnails_written"], 3)
        for size in thumbnails.THUMB_TIERS:
            self.assertTrue(os.path.exists(thumbnails._thumbnail_disk_path(size, 1)))

    def test_bulk_generation_remains_facaded_from_generation_module(self):
        path = self._make_image()
        direct_signatures, file_size, _mtime = self._catalog_signatures(path, image_id=101)
        facade_signatures, _file_size, _mtime = self._catalog_signatures(path, image_id=102)

        direct_metrics = thumbnail_generation.generate_thumbnail_set(
            path,
            101,
            direct_signatures,
            source_bytes=file_size,
            sizes=thumbnails.SIZES,
            thumb_tiers=thumbnails.THUMB_TIERS,
            full_tier=thumbnails.FULL_TIER,
            load_source_image=thumbnails._load_source_image,
            load_source_image_from_bytes=thumbnails._load_source_image_from_bytes,
            queue_orientation=thumbnails._queue_orientation,
            resize_to_long_side=thumbnails._resize_to_long_side,
            encode_and_cache_thumbnail=thumbnails._encode_and_cache_thumbnail,
            cache_full_image_sync=thumbnails._cache_full_image_sync,
            cache_full_image_bytes_sync=thumbnails._cache_full_image_bytes_sync,
            mark_source_missing_from_error=thumbnails._mark_source_missing_from_error,
            fast_disk_has=thumbnails.fast_disk_has,
            is_browser_displayable_original=thumbnails.is_browser_displayable_original,
            thumbnail_retry_after=thumbnails._thumbnail_retry_after,
            thumbnail_retry_seconds=thumbnails.THUMBNAIL_RETRY_SECONDS,
        )
        facade_metrics = thumbnails._generate_thumbnail_set_sync(
            path,
            102,
            facade_signatures,
            source_bytes=file_size,
        )

        self.assertEqual(direct_metrics["source_reads"], facade_metrics["source_reads"])
        self.assertEqual(direct_metrics["thumbnails_written"], facade_metrics["thumbnails_written"])
        self.assertEqual(direct_metrics["source_bytes"], facade_metrics["source_bytes"])
        self.assertEqual(direct_metrics["source_read_failures"], facade_metrics["source_read_failures"])
        self.assertEqual(direct_metrics["originals_written"], facade_metrics["originals_written"])
        for size in thumbnails.THUMB_TIERS:
            self.assertTrue(os.path.exists(thumbnails._thumbnail_disk_path(size, 101)))
            self.assertTrue(os.path.exists(thumbnails._thumbnail_disk_path(size, 102)))

    def test_bulk_generation_reuses_source_bytes_for_original_cache(self):
        path = self._make_image()
        signatures, file_size, _mtime = self._catalog_signatures(path)
        full_signature = thumbnails._build_source_signature(path, thumbnails.FULL_TIER, 1)
        calls = []
        old_cache_full_image_sync = thumbnails._cache_full_image_sync
        old_cache_full_image_bytes_sync = thumbnails._cache_full_image_bytes_sync

        def fail_second_source_read(*_args, **_kwargs):
            raise AssertionError("bulk generation should not copy the source a second time")

        def counted_bytes_cache(filepath, image_id, source_signature, data, *, hot=True, room_prechecked=False):
            calls.append((filepath, image_id, source_signature, len(data), hot))
            cached_path = os.path.join(self.tempdir.name, "full", "1.jpg")
            os.makedirs(os.path.dirname(cached_path), exist_ok=True)
            with open(cached_path, "wb") as f:
                f.write(data)
            thumbnails._index_disk_entry(thumbnails.FULL_TIER, image_id, cached_path, source_signature)
            return cached_path

        thumbnails._cache_full_image_sync = fail_second_source_read
        thumbnails._cache_full_image_bytes_sync = counted_bytes_cache
        try:
            metrics = thumbnails._generate_thumbnail_set_sync(
                path,
                1,
                signatures,
                source_bytes=file_size,
                full_item={
                    "id": 1,
                    "filepath": path,
                    "signature": full_signature,
                    "source_size": file_size,
                },
            )
        finally:
            thumbnails._cache_full_image_sync = old_cache_full_image_sync
            thumbnails._cache_full_image_bytes_sync = old_cache_full_image_bytes_sync

        self.assertEqual(metrics["source_reads"], 1)
        self.assertEqual(metrics["thumbnails_written"], 3)
        self.assertEqual(metrics["originals_written"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], (path, 1, full_signature, file_size, False))

    def test_bulk_generation_records_full_only_copy_metrics(self):
        thumbnails._disk_allocations[thumbnails.FULL_TIER] = 64 * 1024 * 1024
        path = self._make_image()
        stat = os.stat(path)
        full_signature = thumbnails._build_source_signature(path, thumbnails.FULL_TIER, 1)

        metrics = thumbnails._generate_thumbnail_set_sync(
            path,
            1,
            {},
            full_item={
                "id": 1,
                "filepath": path,
                "signature": full_signature,
                "source_size": int(stat.st_size),
            },
        )

        self.assertEqual(metrics["source_reads"], 1)
        self.assertEqual(metrics["source_bytes"], int(stat.st_size))
        self.assertEqual(metrics["originals_written"], 1)
        self.assertGreaterEqual(metrics["read_seconds"], 0.0)

    def test_bulk_generation_marks_missing_source_without_retry(self):
        image_id = 5
        missing_path = os.path.join(self.tempdir.name, "missing.jpg")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, missing_at REAL DEFAULT NULL)")
            conn.execute("INSERT INTO images(id, missing_at) VALUES (?, NULL)", (image_id,))
            conn.commit()

        signatures = {
            "md": thumbnails._build_source_signature_from_bits(
                f"catalog|123|456.000000000|{missing_path}",
                "md",
                image_id,
            )
        }
        metrics = thumbnails._generate_thumbnail_set_sync(
            missing_path,
            image_id,
            signatures,
            source_bytes=123,
        )

        self.assertEqual(metrics["source_read_failures"], 1)
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute("SELECT missing_at FROM images WHERE id = ?", (image_id,)).fetchone()
        self.assertIsNotNone(row[0])
        self.assertEqual(thumbnails._thumbnail_retry_after, {})

    def test_bulk_candidate_skips_already_cached_tiers(self):
        path = self._make_image()
        signatures, file_size, file_modified_at = self._catalog_signatures(path)
        thumbnails._generate_thumbnail_set_sync(path, 1, signatures, source_bytes=file_size)

        needed, _source_size = thumbnails._bulk_candidate_signatures(
            {
                "id": 1,
                "filepath": path,
                "file_size": file_size,
                "file_modified_at": file_modified_at,
            },
            {size: 64 * 1024 * 1024 for size in thumbnails.THUMB_TIERS},
            {size: 64 * 1024 * 1024 for size in thumbnails.THUMB_TIERS},
        )
        self.assertEqual(needed, {})

    def test_bulk_candidate_respects_lg_budget_room(self):
        path = self._make_image()
        stat = os.stat(path)
        row = {
            "id": 2,
            "filepath": path,
            "file_size": int(stat.st_size),
            "file_modified_at": float(stat.st_mtime),
        }
        needed, _source_size = thumbnails._bulk_candidate_signatures(
            row,
            {
                "sm": thumbnails.estimated_tier_bytes("sm"),
                "md": thumbnails.estimated_tier_bytes("md"),
                "lg": thumbnails.estimated_tier_bytes("lg") - 1,
            },
            {size: 64 * 1024 * 1024 for size in thumbnails.THUMB_TIERS},
        )
        self.assertIn("sm", needed)
        self.assertIn("md", needed)
        self.assertNotIn("lg", needed)

    def test_bulk_candidate_skips_tier_inside_retry_window(self):
        path = self._make_image()
        signatures, file_size, file_modified_at = self._catalog_signatures(path, image_id=3)
        thumbnails._thumbnail_retry_after[("sm", 3, signatures["sm"])] = time.time() + 3600
        tier_room = {"sm": thumbnails.estimated_tier_bytes("sm")}

        needed, source_size = thumbnails._bulk_candidate_signatures(
            {
                "id": 3,
                "filepath": path,
                "file_size": file_size,
                "file_modified_at": file_modified_at,
            },
            tier_room,
            {"sm": 64 * 1024 * 1024, "md": 0, "lg": 0},
        )

        self.assertEqual(needed, {})
        self.assertEqual(source_size, file_size)
        self.assertEqual(tier_room["sm"], thumbnails.estimated_tier_bytes("sm"))

    def test_full_warmup_copies_until_budget_room_is_used_and_skips_cached(self):
        thumbnails._disk_allocations[thumbnails.FULL_TIER] = 70
        first = self._make_original_file("first.jpg", 30)
        second = self._make_original_file("second.jpg", 40)
        third = self._make_original_file("third.jpg", 40)
        for image_id, path in ((1, first), (2, second), (3, third)):
            self._add_catalog_original(image_id, path)

        self.assertNotEqual(self._cache_original_now(1, first), first)

        warmed = asyncio.run(thumbnails._run_full_warm_batch(generate_batch=10))

        self.assertEqual(warmed, 1)
        self.assertIsNotNone(thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, 1))
        self.assertIsNotNone(thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, 2))
        self.assertIsNone(thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, 3))

    def test_bulk_warmup_handles_full_only_work_in_same_cursor_pass(self):
        thumbnails._disk_allocations[thumbnails.FULL_TIER] = 64 * 1024 * 1024
        thumbnails._last_user_activity = thumbnails.time.monotonic() - 30.0
        path = self._make_image()
        signatures, file_size, _file_modified_at = self._catalog_signatures(path)
        self._add_catalog_original(1, path)
        thumbnails._generate_thumbnail_set_sync(path, 1, signatures, source_bytes=file_size)

        warmed = asyncio.run(thumbnails._run_pregen_bulk_batch(generate_batch=10))

        self.assertEqual(warmed, 1)
        self.assertIsNotNone(thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, 1))

    def test_bulk_warmup_treats_failure_only_batch_as_no_progress(self):
        path = self._make_original_file("bad.jpg", 128)
        self._add_catalog_original(1, path)
        generated_before = thumbnails._pregen_status["generated_this_session"]
        last_generated_before = thumbnails._pregen_status["last_generated_at"]
        failures_before = thumbnails._pregen_source_read_failures

        warmed = asyncio.run(thumbnails._run_pregen_bulk_batch(generate_batch=1))

        self.assertEqual(warmed, -1)
        self.assertEqual(thumbnails._pregen_status["generated_this_session"], generated_before)
        self.assertEqual(thumbnails._pregen_status["last_generated_at"], last_generated_before)
        self.assertEqual(thumbnails._pregen_source_read_failures, failures_before + 1)

    def test_bulk_candidate_signatures_skips_thumbnail_signature_work_when_previews_exist(self):
        path = self._make_image()
        signatures, file_size, file_modified_at = self._catalog_signatures(path)
        for size, signature in signatures.items():
            cache_path = thumbnails._thumbnail_disk_path(size, 1)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(b"cached")
            thumbnails._index_disk_entry(size, 1, cache_path, signature)

        old_build_catalog_source_signature = thumbnails._build_catalog_source_signature
        calls = []

        def counted_build(*args, **kwargs):
            calls.append(args)
            return old_build_catalog_source_signature(*args, **kwargs)

        thumbnails._build_catalog_source_signature = counted_build
        try:
            needed, source_size = thumbnails._bulk_candidate_signatures(
                {
                    "id": 1,
                    "filepath": path,
                    "file_size": file_size,
                    "file_modified_at": file_modified_at,
                },
                {size: 64 * 1024 * 1024 for size in thumbnails.THUMB_TIERS},
                {size: 64 * 1024 * 1024 for size in thumbnails.THUMB_TIERS},
            )
        finally:
            thumbnails._build_catalog_source_signature = old_build_catalog_source_signature

        self.assertEqual(needed, {})
        self.assertEqual(source_size, file_size)
        self.assertEqual(calls, [])

    def test_manual_pregen_decision_uses_configured_batch_after_activity(self):
        old_batch = thumbnails.PREGENERATE_GENERATE_BATCH
        try:
            thumbnails.PREGENERATE_GENERATE_BATCH = 64
            thumbnails.note_user_activity()

            decision = thumbnails._pregen_background_decision()

            self.assertEqual(decision.reason, "manual background work")
            self.assertFalse(decision.pause)
            self.assertEqual(thumbnails._pregen_generate_batch_for_decision(decision), 64)
        finally:
            thumbnails.PREGENERATE_GENERATE_BATCH = old_batch

    def test_pregeneration_does_not_pause_for_priority_after_activity(self):
        thumbnails.note_user_activity()

        self.assertFalse(thumbnails._pregen_should_pause_for_priority())

    def test_prefetch_worker_caps_manual_batch_to_configured_batch(self):
        old_batch = thumbnails.PREGENERATE_GENERATE_BATCH
        try:
            thumbnails.PREGENERATE_GENERATE_BATCH = 16
            decision = thumbnail_pregen.BackgroundDecision(
                mode="manual",
                intensity=0.75,
                pause=False,
                sleep_seconds=0.0,
                thumbnail_batch_size=64,
                thumbnail_pause_seconds=0.05,
                embedding_pause_seconds=0.25,
                reason="manual background work",
                checked_at=1.0,
            )

            self.assertEqual(thumbnails._pregen_generate_batch_for_decision(decision), 16)
        finally:
            thumbnails.PREGENERATE_GENERATE_BATCH = old_batch

    def test_prefetch_worker_completes_after_repeated_scan_only_passes(self):
        old_sleep = thumbnails.asyncio.sleep
        old_cache_target_total = thumbnails._cache_target_total
        old_run_bulk = thumbnails._run_pregen_bulk_batch
        old_run_full = thumbnails._run_full_warm_batch
        old_flush_write_queue = thumbnails._flush_write_queue
        old_flush_orientation = thumbnails.flush_orientation_updates
        old_should_pause = thumbnails._pregen_should_pause_for_priority
        old_background_decision = thumbnails._pregen_background_decision
        old_no_progress_limit = thumbnails.PREGENERATE_NO_PROGRESS_SCAN_LIMIT
        try:
            thumbnails.PREGENERATE_NO_PROGRESS_SCAN_LIMIT = 3
            thumbnails._prefetching = True
            thumbnails._pregen_manual_pause = False
            thumbnails._pregen_manual_mode = True
            thumbnails._disk_allocations.update({
                "sm": 64 * 1024 * 1024,
                "md": 0,
                "lg": 0,
                thumbnails.FULL_TIER: 0,
            })

            async def fake_cache_target_total():
                return 10

            async def fake_run_bulk(generate_batch=None):
                return -1

            async def fake_run_full(generate_batch=None):
                return 0

            async def fake_flush_orientation():
                return None

            def fake_should_yield():
                return False

            def fake_decision():
                return thumbnail_pregen.BackgroundDecision(
                    mode="manual",
                    intensity=0.45,
                    pause=False,
                    sleep_seconds=0.0,
                    thumbnail_batch_size=8,
                    thumbnail_pause_seconds=0.01,
                    embedding_pause_seconds=0.25,
                    reason="manual background work",
                    checked_at=1.0,
                )

            async def fake_sleep(_seconds):
                if thumbnails._pregen_status["state"] == "complete":
                    thumbnails._prefetching = False

            thumbnails._cache_target_total = fake_cache_target_total
            thumbnails._run_pregen_bulk_batch = fake_run_bulk
            thumbnails._run_full_warm_batch = fake_run_full
            thumbnails._flush_write_queue = lambda: True
            thumbnails.flush_orientation_updates = fake_flush_orientation
            thumbnails._pregen_should_pause_for_priority = fake_should_yield
            thumbnails._pregen_background_decision = fake_decision
            thumbnails.asyncio.sleep = fake_sleep

            asyncio.run(thumbnails.run_prefetch_worker())

            self.assertEqual(thumbnails._pregen_status["state"], "complete")
            self.assertIn("no more warmable images", thumbnails._pregen_status["message"])
        finally:
            thumbnails.asyncio.sleep = old_sleep
            thumbnails._cache_target_total = old_cache_target_total
            thumbnails._run_pregen_bulk_batch = old_run_bulk
            thumbnails._run_full_warm_batch = old_run_full
            thumbnails._flush_write_queue = old_flush_write_queue
            thumbnails.flush_orientation_updates = old_flush_orientation
            thumbnails._pregen_should_pause_for_priority = old_should_pause
            thumbnails._pregen_background_decision = old_background_decision
            thumbnails.PREGENERATE_NO_PROGRESS_SCAN_LIMIT = old_no_progress_limit

    def test_pregen_status_omits_governor_decision(self):
        old_batch = thumbnails.PREGENERATE_GENERATE_BATCH
        try:
            thumbnails.PREGENERATE_GENERATE_BATCH = 16
            thumbnails.note_user_activity()

            status = thumbnails.get_pregen_status(target_total=0, stats=thumbnails.cache_stats())

            self.assertNotIn("governor", status)
            self.assertEqual(status["state"], thumbnails._pregen_status["state"])
        finally:
            thumbnails.PREGENERATE_GENERATE_BATCH = old_batch

    def test_full_warmup_does_not_churn_when_full_tier_is_full(self):
        thumbnails._disk_allocations[thumbnails.FULL_TIER] = 80
        first = self._make_original_file("one.jpg", 40)
        second = self._make_original_file("two.jpg", 40)
        third = self._make_original_file("three.jpg", 10)
        for image_id, path in ((1, first), (2, second), (3, third)):
            self._add_catalog_original(image_id, path)

        self.assertNotEqual(self._cache_original_now(1, first), first)
        self.assertNotEqual(self._cache_original_now(2, second), second)

        warmed = asyncio.run(thumbnails._run_full_warm_batch(generate_batch=10))

        self.assertEqual(warmed, 0)
        self.assertIsNotNone(thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, 1))
        self.assertIsNotNone(thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, 2))
        self.assertIsNone(thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, 3))

    def test_full_warmup_skips_oversized_originals(self):
        thumbnails._disk_allocations[thumbnails.FULL_TIER] = 32
        path = self._make_original_file("oversized.jpg", 64)
        self._add_catalog_original(1, path)

        warmed = asyncio.run(thumbnails._run_full_warm_batch(generate_batch=10))

        self.assertEqual(warmed, 0)
        self.assertFalse(thumbnails.has_cached(thumbnails.FULL_TIER, path, 1))

    def test_touch_cached_signature_ignores_sqlite_lock(self):
        class LockedConn:
            def execute(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("database is locked")

            def rollback(self):
                pass

        thumbnail_cache_entries._db_connect = lambda: LockedConn()

        self.assertFalse(thumbnails.touch_cached_signature("sm", 1, "sig"))

    def test_touch_cached_signature_ignores_sqlite_connect_lock(self):
        def locked_connect():
            raise sqlite3.OperationalError("database is locked")

        thumbnail_cache_entries._db_connect = locked_connect

        self.assertFalse(thumbnails.touch_cached_signature("sm", 1, "sig"))

    def test_flush_write_queue_requeues_after_sqlite_lock(self):
        class LockedConn:
            def execute(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("database is locked")

            def rollback(self):
                pass

        path = os.path.join(self.tempdir.name, "sm", "1.jpg")
        with thumbnails._write_queue_lock:
            thumbnails._write_queue.append(("sm", 1, "sig", path, 123, time.time()))
        thumbnail_cache_entries._db_connect = lambda: LockedConn()

        self.assertFalse(thumbnails._flush_write_queue())
        with thumbnails._write_queue_lock:
            self.assertEqual(len(thumbnails._write_queue), 1)

    def test_flush_write_queue_requeues_after_sqlite_connect_lock(self):
        path = os.path.join(self.tempdir.name, "sm", "1.jpg")
        with thumbnails._write_queue_lock:
            thumbnails._write_queue.append(("sm", 1, "sig", path, 123, time.time()))

        def locked_connect():
            raise sqlite3.OperationalError("database is locked")

        thumbnail_cache_entries._db_connect = locked_connect

        self.assertFalse(thumbnails._flush_write_queue())
        with thumbnails._write_queue_lock:
            self.assertEqual(len(thumbnails._write_queue), 1)

    def test_get_thumbnail_returns_generated_bytes_when_caches_are_disabled(self):
        path = self._make_image()
        thumbnails._disk_allocations.update({tier: 0 for tier in thumbnails.ALL_TIERS})
        thumbnails.MEMORY_CACHE_BYTES = 0

        data = asyncio.run(thumbnails.get_thumbnail(path, "md", 7))

        self.assertGreater(len(data), 100)
        self.assertFalse(os.path.exists(thumbnails._thumbnail_disk_path("md", 7)))
        self.assertIsNone(
            thumbnails._memory_get("md", 7, thumbnails._build_source_signature(path, "md", 7))
        )

    def test_clear_cache_refuses_unmarked_directory_with_non_cache_files(self):
        user_file = os.path.join(self.tempdir.name, "keep.txt")
        with open(user_file, "w", encoding="utf-8") as f:
            f.write("not cache data")
        marker = thumbnails._cache_marker_path()
        if os.path.exists(marker):
            os.remove(marker)

        result = thumbnails.clear_cache()

        self.assertTrue(result["refused"])
        self.assertTrue(os.path.exists(user_file))

    def test_clear_cache_allows_legacy_cache_layout_and_writes_marker(self):
        marker = thumbnails._cache_marker_path()
        if os.path.exists(marker):
            os.remove(marker)
        path = os.path.join(self.tempdir.name, "sm", "1.jpg")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"cache")

        result = thumbnails.clear_cache()

        self.assertNotIn("refused", result)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.exists(marker))

    def test_stale_temp_cleanup_only_removes_old_tmp_files(self):
        old_tmp = os.path.join(self.tempdir.name, "full", "old.jpg.123.tmp")
        fresh_tmp = os.path.join(self.tempdir.name, "full", "fresh.jpg.123.tmp")
        cache_file = os.path.join(self.tempdir.name, "full", "keep.jpg")
        os.makedirs(os.path.dirname(old_tmp), exist_ok=True)
        for path in (old_tmp, fresh_tmp, cache_file):
            with open(path, "wb") as f:
                f.write(b"x" * 10)
        old_time = time.time() - 3600
        os.utime(old_tmp, (old_time, old_time))

        result = thumbnails._cleanup_stale_cache_temps(max_age_seconds=1800)

        self.assertEqual(result["files_removed"], 1)
        self.assertEqual(result["bytes_removed"], 10)
        self.assertFalse(os.path.exists(old_tmp))
        self.assertTrue(os.path.exists(fresh_tmp))
        self.assertTrue(os.path.exists(cache_file))

    def test_stop_prefetch_closes_persistent_metadata_connection(self):
        conn = thumbnails._db_connect()

        thumbnails.stop_prefetch()

        self.assertIsNone(thumbnail_cache_entries._persistent_conn)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
