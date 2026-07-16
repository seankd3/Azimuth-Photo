from test_support import *  # noqa: F401,F403
from unittest import mock

from features.catalog import metadata as catalog_metadata


class SettingsStatusTests(BackendTestCase):
    async def test_orientation_worker_leaves_raws_for_preview_decoder(self):
        source = await self._source("mixed-formats")
        raw_id = await self._image(source["id"], "photo.cr3")
        jpeg_id = await self._image(source["id"], "photo.jpg")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET file_ext = '.cr3' WHERE id = ?", (raw_id,))
            await conn.execute("UPDATE images SET file_ext = '.jpg' WHERE id = ?", (jpeg_id,))
            await conn.commit()
        finally:
            await conn.close()

        rows = await catalog_metadata.get_unclassified_images()

        self.assertEqual([int(row["id"]) for row in rows], [jpeg_id])

    async def test_orientation_worker_marks_unreadable_online_jpeg_missing(self):
        source = await self._source("unreadable-jpeg")
        image_id = await self._image(source["id"], "broken.jpg")
        with open(os.path.join(source["path"], "broken.jpg"), "wb") as handle:
            handle.write(b"not a jpeg")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET file_ext = '.jpg' WHERE id = ?", (image_id,))
            await conn.commit()
        finally:
            await conn.close()

        rows = await catalog_metadata.get_unclassified_images()
        batches = iter((rows,))

        async def one_batch_then_stop(limit=200):
            del limit
            try:
                return next(batches)
            except StopIteration:
                raise asyncio.CancelledError from None

        catalog_metadata.resume_catalog_metadata()
        try:
            with mock.patch.object(
                catalog_metadata,
                "get_unclassified_images",
                one_batch_then_stop,
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await catalog_metadata.classify_orientations_background()
        finally:
            catalog_metadata.pause_catalog_metadata()

        self.assertIsNotNone((await self._image_row(image_id))["missing_at"])

    def test_restored_source_clears_all_orientation_poison_entries(self):
        old_ledger = dict(catalog_metadata._orientation_retry_ledger)
        catalog_metadata._orientation_retry_ledger.clear()
        source_root = os.path.join(self.tempdir.name, "restored-source")
        try:
            for image_id in (1, 2):
                for attempt in range(catalog_metadata.ORIENTATION_POISON_THRESHOLD):
                    catalog_metadata._note_orientation_failure(
                        image_id,
                        "FileNotFoundError",
                        now=float(attempt),
                        source_root=source_root,
                    )

            offline_ready, _cooled, _retry_at = catalog_metadata._ready_orientation_rows(
                [{"id": 1, "source_root": source_root}],
                now=10_000.0,
            )
            self.assertEqual(offline_ready, [])

            os.makedirs(source_root)
            online_ready, _cooled, _retry_at = catalog_metadata._ready_orientation_rows(
                [{"id": 1, "source_root": source_root}],
                now=10_000.0,
            )

            self.assertEqual([row["id"] for row in online_ready], [1])
            self.assertEqual(catalog_metadata._orientation_retry_ledger, {})
        finally:
            catalog_metadata._orientation_retry_ledger.clear()
            catalog_metadata._orientation_retry_ledger.update(old_ledger)

    def test_orientation_retry_ledger_cools_then_poisons_unreadable_images(self):
        old_ledger = dict(catalog_metadata._orientation_retry_ledger)
        catalog_metadata._orientation_retry_ledger.clear()
        try:
            first_retry = catalog_metadata._note_orientation_failure(
                1,
                "FileNotFoundError",
                now=100.0,
            )
            ready, cooled, next_retry_at = catalog_metadata._ready_orientation_rows(
                [{"id": 1}, {"id": 2}],
                now=101.0,
            )

            self.assertEqual([row["id"] for row in ready], [2])
            self.assertEqual(cooled, 1)
            self.assertEqual(next_retry_at, first_retry)

            catalog_metadata._note_orientation_failure(1, "OSError", now=first_retry)
            catalog_metadata._note_orientation_failure(
                1,
                "OSError",
                now=first_retry + catalog_metadata.ORIENTATION_RETRY_SECONDS,
            )
            summary = catalog_metadata._orientation_retry_summary()
            self.assertEqual(summary["poisoned"], 1)
            self.assertEqual(
                catalog_metadata._ready_orientation_rows([{"id": 1}], now=10_000.0)[0],
                [],
            )

            catalog_metadata.resume_catalog_metadata()
            self.assertEqual(catalog_metadata._orientation_retry_ledger, {})
        finally:
            catalog_metadata.pause_catalog_metadata()
            catalog_metadata._orientation_retry_ledger.clear()
            catalog_metadata._orientation_retry_ledger.update(old_ledger)

    async def test_ai_status_counts_only_count_active_embeddings(self):
        active_source = await self._source("active")
        offline_source = await self._source("offline", online=False)
        active = await self._image(active_source["id"], "active.jpg")
        offline = await self._image(offline_source["id"], "offline.jpg")

        await db.store_embeddings_batch([(active, b"active"), (offline, b"offline")])

        ai_counts = await db.get_ai_status_counts()

        self.assertEqual(ai_counts["total_images"], 2)
        self.assertEqual(ai_counts["embedded"], 2)
        self.assertEqual(await db.get_embedding_count(), 2)

    async def test_online_embedding_count_status_path_is_read_only(self):
        source = await self._source("active")
        image_id = await self._image(source["id"], "active.jpg")
        legacy_key = db._legacy_embedding_model_key()

        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO embeddings (image_id, embedding) VALUES (?, ?)",
                (image_id, b"active"),
            )
            await conn.execute("DELETE FROM embedding_models WHERE model_key = ?", (legacy_key,))
            await conn.commit()
        finally:
            await conn.close()

        self.assertEqual(await db.get_embedding_count(), 0)

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT 1 FROM embedding_models WHERE model_key = ?",
                (legacy_key,),
            )
            self.assertIsNone(await cursor.fetchone())
        finally:
            await conn.close()

    async def test_purge_retired_embedding_data_keeps_only_active_model(self):
        source = await self._source("active")
        image_id = await self._image(source["id"], "active.jpg")
        two_b_config = settings.embedding_model_config_for_preset("qwen3-vl-embedding-2b")
        eight_b_config = settings.embedding_model_config_for_preset("qwen3-vl-embedding-8b")

        settings.save_settings({
            "settings_version": settings.SETTINGS_VERSION,
            "embed_model_preset": "qwen3-vl-embedding-8b",
        })
        await db.store_embeddings_batch([(image_id, b"two-b-vector")], embedding_config=two_b_config)
        await db.store_embeddings_batch([(image_id, b"eight-b-vector")], embedding_config=eight_b_config)

        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO embeddings (image_id, embedding) VALUES (?, ?)",
                (image_id, b"legacy-vector"),
            )
            await conn.execute(
                "INSERT OR REPLACE INTO search_query_embeddings "
                "(model_key, query_key, query, embedding, dimension, created_at, last_used_at, updated_at) "
                "VALUES (?, 'old query', 'old query', ?, ?, 1.0, 1.0, 1.0)",
                (two_b_config["model_key"], b"query-vector", int(two_b_config["dimension"])),
            )
            await conn.commit()
        finally:
            await conn.close()

        counts = await db.purge_retired_embedding_data()

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT model_key, embedding, dimension FROM embeddings_by_model ORDER BY model_key"
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            table_counts = {}
            for table in (
                "embeddings",
                "search_query_embeddings",
            ):
                cursor = await conn.execute(f"SELECT COUNT(*) AS c FROM {table}")
                table_counts[table] = int((await cursor.fetchone())["c"])
        finally:
            await conn.close()

        self.assertGreaterEqual(counts["inactive_embeddings"], 1)
        self.assertEqual(
            rows,
            [
                {
                    "model_key": eight_b_config["model_key"],
                    "embedding": b"eight-b-vector",
                    "dimension": int(eight_b_config["dimension"]),
                }
            ],
        )
        self.assertEqual(table_counts["embeddings"], 0)
        self.assertEqual(table_counts["search_query_embeddings"], 0)
        self.assertEqual(await db.get_embedding_count(), 1)

    async def test_qwen8b_preset_is_authoritative(self):
        saved = settings.save_settings({
            "embed_model_preset": "qwen3-vl-embedding-8b",
            "embed_model_dir": "/tmp/stale-2b-path",
            "embed_model_dim": 2048,
        })
        presets = {
            preset["key"]: preset
            for preset in settings.settings_metadata()["embedding_model_presets"]
        }

        self.assertEqual(saved["embed_model_id"], "Qwen/Qwen3-VL-Embedding-8B")
        self.assertEqual(saved["embed_model_dim"], 4096)
        self.assertEqual(saved["embed_model_dir"], presets["qwen3-vl-embedding-8b"]["model_dir"])
        self.assertTrue(settings.embedding_model_key(saved).endswith(":4096"))

    async def test_embedding_model_defaults_to_qwen8b(self):
        normalized = settings.normalize_settings({})

        self.assertEqual(normalized["settings_version"], settings.SETTINGS_VERSION)
        self.assertEqual(normalized["embed_model_preset"], "qwen3-vl-embedding-8b")
        self.assertEqual(normalized["embed_model_id"], "Qwen/Qwen3-VL-Embedding-8B")
        self.assertEqual(normalized["embed_model_dim"], 4096)

    async def test_unversioned_qwen2b_default_migrates_to_qwen8b(self):
        two_b = settings.embedding_model_config_for_preset("qwen3-vl-embedding-2b")
        normalized = settings.normalize_settings({
            "embed_model_preset": "qwen3-vl-embedding-2b",
            "embed_model_id": two_b["model_id"],
            "embed_model_revision": two_b["revision"],
            "embed_model_dir": two_b["model_dir"],
            "embed_model_dim": two_b["dimension"],
        })

        self.assertEqual(normalized["settings_version"], settings.SETTINGS_VERSION)
        self.assertEqual(normalized["embed_model_preset"], "qwen3-vl-embedding-8b")
        self.assertEqual(normalized["embed_model_id"], "Qwen/Qwen3-VL-Embedding-8B")
        self.assertEqual(normalized["embed_model_dim"], 4096)

    async def test_versioned_qwen2b_selection_persists(self):
        normalized = settings.normalize_settings({
            "settings_version": settings.SETTINGS_VERSION,
            "embed_model_preset": "qwen3-vl-embedding-2b",
        })

        self.assertEqual(normalized["settings_version"], settings.SETTINGS_VERSION)
        self.assertEqual(normalized["embed_model_preset"], "qwen3-vl-embedding-2b")
        self.assertEqual(normalized["embed_model_id"], "Qwen/Qwen3-VL-Embedding-2B")
        self.assertEqual(normalized["embed_model_dim"], 2048)

    async def test_embedding_model_metadata_lists_only_supported_presets(self):
        metadata = settings.settings_metadata()
        preset_keys = [
            preset["key"]
            for preset in metadata["embedding_model_presets"]
        ]

        self.assertEqual(
            preset_keys,
            ["qwen3-vl-embedding-8b", "qwen3-vl-embedding-2b"],
        )

    async def test_ui_settings_returns_default_loupe_cache_status(self):
        result = await settings_routes.api_ui_settings()

        self.assertEqual(result, {"settings": {"show_loupe_cache_status": True}})

    async def test_loupe_cache_status_setting_persists(self):
        saved = settings.save_settings({"show_loupe_cache_status": False})
        self.assertFalse(saved["show_loupe_cache_status"])

        reloaded = settings.load_settings(force=True)
        result = await settings_routes.api_ui_settings()

        self.assertFalse(reloaded["show_loupe_cache_status"])
        self.assertEqual(result, {"settings": {"show_loupe_cache_status": False}})

    async def test_retired_background_settings_are_ignored(self):
        normalized = settings.normalize_settings({
            "background_work_mode": "max",
            "pregenerate_on_idle": True,
            "defer_ai_on_startup": False,
        })

        self.assertNotIn("background_work_mode", normalized)
        self.assertNotIn("pregenerate_on_idle", normalized)
        self.assertNotIn("defer_ai_on_startup", normalized)

        saved = settings.save_settings({
            "background_work_mode": "browse",
            "pregenerate_on_idle": True,
            "defer_ai_on_startup": False,
        })
        self.assertNotIn("background_work_mode", saved)
        self.assertNotIn("pregenerate_on_idle", saved)
        self.assertNotIn("defer_ai_on_startup", saved)

        with open(settings.SETTINGS_PATH, encoding="utf-8") as fh:
            persisted = json.load(fh)
        self.assertNotIn("background_work_mode", persisted)
        self.assertNotIn("pregenerate_on_idle", persisted)
        self.assertNotIn("defer_ai_on_startup", persisted)

    async def test_settings_metadata_does_not_list_background_work_modes(self):
        metadata = settings.settings_metadata()

        self.assertNotIn("background_work_modes", metadata)

    async def test_ai_status_reports_active_embedding_index(self):
        source = await self._source()
        image_id = await self._image(source["id"], "active-index.jpg")
        active_config = settings.active_embedding_config()

        await db.store_embeddings_batch([(image_id, b"active-vector")], embedding_config=active_config)
        ai_routes.invalidate_ai_status_response_cache()

        status = await ai_routes.build_ai_status(force=True)
        index = status["embedding_index"]

        self.assertNotIn("embedding_indexes", status)
        self.assertNotIn("deep_search", status)
        self.assertEqual(index["role"], "active")
        self.assertEqual(index["model_key"], active_config["model_key"])
        self.assertEqual(index["dimension"], int(active_config["dimension"]))
        self.assertEqual(index["embedded"], 1)
        self.assertEqual(index["remaining"], 0)

    async def test_ai_status_reports_active_model_install_progress_on_active_index(self):
        active_config = settings.active_embedding_config()
        install_state = {
            "running": True,
            "status": "downloading",
            "message": "Downloading Qwen/Qwen3-VL-Embedding-8B",
            "model_id": active_config["model_id"],
            "revision": active_config["revision"],
            "model_dir": active_config["model_dir"],
            "started_at": 1.0,
            "finished_at": None,
            "last_error": "",
        }
        old_get_model_status = ai_models.get_model_status

        def fake_get_model_status(config=None):
            cfg = config or active_config
            return {
                "model_id": cfg["model_id"],
                "revision": cfg["revision"],
                "model_dir": cfg["model_dir"],
                "dimension": int(cfg["dimension"]),
                "model_key": cfg["model_key"],
                "installed": False,
                "install": dict(install_state),
            }

        ai_models.get_model_status = fake_get_model_status
        ai_routes.invalidate_ai_status_response_cache()
        try:
            status = await ai_routes.build_ai_status(force=True)
        finally:
            ai_models.get_model_status = old_get_model_status
            ai_routes.invalidate_ai_status_response_cache()

        index = status["embedding_index"]
        self.assertTrue(index["installing"])
        self.assertEqual(index["install_status"], "downloading")
        self.assertIn("8B", index["install_message"])

    async def test_install_model_noops_when_requested_model_is_already_installed(self):
        from core import capabilities as _capabilities
        if not _capabilities.capability_status("search")["available"]:
            self.skipTest("AI search capability unavailable â€” matches AI-optional installs")
        active_config = settings.active_embedding_config()
        old_get_model_status = ai_models.get_model_status
        old_start_model_install = ai_models.start_model_install

        def fake_get_model_status(config=None):
            cfg = config or active_config
            return {
                "model_id": cfg["model_id"],
                "revision": cfg["revision"],
                "model_dir": cfg["model_dir"],
                "dimension": int(cfg["dimension"]),
                "model_key": cfg["model_key"],
                "installed": True,
                "install": {
                    "running": False,
                    "status": "idle",
                    "message": "",
                    "model_id": "",
                    "revision": "",
                    "model_dir": "",
                    "started_at": None,
                    "finished_at": None,
                    "last_error": "",
                },
            }

        def fail_start_model_install(_config=None):
            raise AssertionError("installed model should not start a download")

        ai_models.get_model_status = fake_get_model_status
        ai_models.start_model_install = fail_start_model_install
        ai_routes.invalidate_ai_status_response_cache()
        try:
            response = await ai_routes.api_install_ai_model(role="deep")
        finally:
            ai_models.get_model_status = old_get_model_status
            ai_models.start_model_install = old_start_model_install
            ai_routes.invalidate_ai_status_response_cache()

        self.assertTrue(response["ok"])
        self.assertTrue(response["already_installed"])
        self.assertEqual(response["role"], "active")
        self.assertEqual(response["model_status"]["model_key"], active_config["model_key"])

    async def test_install_model_reports_conflict_when_other_model_is_downloading(self):
        from core import capabilities as _capabilities
        if not _capabilities.capability_status("search")["available"]:
            self.skipTest("AI search capability unavailable â€” matches AI-optional installs")
        active_config = settings.active_embedding_config()
        other_config = settings.embedding_model_config_for_preset("qwen3-vl-embedding-2b")
        old_get_model_status = ai_models.get_model_status
        old_start_model_install = ai_models.start_model_install
        install_state = {
            "running": True,
            "status": "downloading",
            "message": "Downloading another model",
            "model_id": other_config["model_id"],
            "revision": other_config["revision"],
            "model_dir": other_config["model_dir"],
            "started_at": 1.0,
            "finished_at": None,
            "last_error": "",
        }

        def fake_get_model_status(config=None):
            cfg = config or active_config
            return {
                "model_id": cfg["model_id"],
                "revision": cfg["revision"],
                "model_dir": cfg["model_dir"],
                "dimension": int(cfg["dimension"]),
                "model_key": cfg["model_key"],
                "installed": False,
                "install": dict(install_state),
            }

        ai_models.get_model_status = fake_get_model_status
        ai_models.start_model_install = lambda _config: dict(install_state)
        try:
            response = await ai_routes.api_install_ai_model(role="deep")
        finally:
            ai_models.get_model_status = old_get_model_status
            ai_models.start_model_install = old_start_model_install

        body = json.loads(response.body)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(body["ok"])
        self.assertIn("already running", body["error"])

    async def test_save_settings_reports_manual_metadata_status(self):
        response = await settings_routes.api_save_settings(JsonRequest({"thumb_quality": 83}))

        self.assertTrue(response["ok"])
        self.assertEqual(response["settings"]["thumb_quality"], 83)
        self.assertIn("metadata_status", response)
        self.assertTrue(response["metadata_status"]["manual_pause"])

    async def test_settings_reads_do_not_expose_cached_state(self):
        first = settings.get_settings()
        first["thumb_quality"] = 40

        second = settings.get_settings()

        self.assertNotEqual(second["thumb_quality"], 40)

    async def test_api_settings_cache_returns_independent_responses_and_invalidates(self):
        first = await settings_routes.api_settings()
        self.assertIsNotNone(settings_status._settings_response_cache["data"])

        second = await settings_routes.api_settings()
        second["settings"] = {"thumb_quality": 40}
        third = await settings_routes.api_settings()
        self.assertNotEqual(third["settings"], {"thumb_quality": 40})

        target_quality = 80 if third["settings"]["thumb_quality"] != 80 else 79
        await settings_routes.api_save_settings(JsonRequest({"thumb_quality": target_quality}))
        refreshed = await settings_routes.api_settings()

        self.assertEqual(refreshed["settings"]["thumb_quality"], target_quality)

    async def test_api_settings_cache_protects_nested_responses(self):
        first = await settings_routes.api_settings()
        self.assertIsNotNone(settings_status._settings_response_cache["data"])

        first["settings"]["thumb_quality"] = 40
        first["cache_stats"]["disk"]["tiers"]["sm"]["count"] = 999999
        first["catalog"]["sources"].append({"id": 999999})

        second = await settings_routes.api_settings()

        self.assertNotEqual(second["settings"]["thumb_quality"], 40)
        self.assertNotEqual(second["cache_stats"]["disk"]["tiers"]["sm"]["count"], 999999)
        self.assertNotIn({"id": 999999}, second["catalog"]["sources"])

    async def test_api_settings_returns_stale_cache_while_refreshing(self):
        stale = {
            "settings": {"thumb_quality": 40},
            "cache_stats": {},
            "model_status": {},
            "ai_status": {},
            "catalog": {},
            **settings.settings_metadata(),
        }
        fresh = {
            "settings": {"thumb_quality": 80},
            "cache_stats": {},
            "model_status": {},
            "ai_status": {},
            "catalog": {},
            **settings.settings_metadata(),
        }
        settings_status._settings_response_cache["data"] = stale
        settings_status._settings_response_cache["expires"] = 0
        old_build_settings_response = settings_routes._build_settings_response

        async def fake_build_settings_response():
            await asyncio.sleep(0)
            return fresh

        settings_routes._build_settings_response = fake_build_settings_response
        try:
            response = await settings_routes.api_settings()
            self.assertEqual(response["settings"]["thumb_quality"], 40)
            await asyncio.sleep(0.01)
            self.assertEqual(
                settings_status._settings_response_cache["data"]["settings"]["thumb_quality"],
                80,
            )
        finally:
            settings_routes._build_settings_response = old_build_settings_response

    async def test_ai_status_response_cache_protects_nested_responses(self):
        first = await ai_routes.build_ai_status()
        self.assertIsNotNone(ai_routes._ai_status_response_cache["data"])

        first["last_batch_stage_seconds"]["db"] = 999999
        first["embedding_index"]["worker_message"] = "mutated"

        second = await ai_routes.build_ai_status()

        self.assertNotEqual(second["last_batch_stage_seconds"].get("db"), 999999)
        self.assertNotEqual(second["embedding_index"].get("worker_message"), "mutated")

    async def test_ai_status_response_cache_returns_stale_while_refreshing(self):
        model_status = ai_models.get_model_status()
        ai_routes._ai_status_response_cache.update({
            "data": {
                "embedded": 1,
                "last_batch_stage_seconds": {"db": 1},
                "embedding_index": {"worker_message": "cached"},
            },
            "key": ai_routes._ai_model_status_cache_key(model_status),
            "expires": ai_routes.time.monotonic() - 1,
        })
        old_create_task = ai_routes.asyncio.create_task
        scheduled = []

        def fake_create_task(coro):
            scheduled.append(coro)
            coro.close()
            return object()

        try:
            ai_routes.asyncio.create_task = fake_create_task
            ai_routes._ai_status_response_refreshing = False

            first = await ai_routes.build_ai_status(model_status)
            second = await ai_routes.build_ai_status(model_status)

            self.assertEqual(first["embedded"], 1)
            self.assertEqual(second["embedding_index"]["worker_message"], "cached")
            self.assertEqual(len(scheduled), 1)
            self.assertTrue(ai_routes._ai_status_response_refreshing)
        finally:
            ai_routes.asyncio.create_task = old_create_task
            ai_routes._ai_status_response_refreshing = False
            ai_routes.invalidate_ai_status_response_cache()

    async def test_ai_status_skips_per_model_embedding_count_when_no_active_images(self):
        config_names = (
            "_invalidate_settings_response_cache",
            "_get_ai_status_counts",
            "_count_embeddings_for_model",
        )
        old_config = {name: getattr(ai_routes, name) for name in config_names}
        calls = []

        async def fake_get_ai_status_counts():
            return {
                "embedded": 0,
                "total_images": 0,
                "rated_images": 0,
                "direct_comparison_rows": 0,
                "ranking_signal_count": 0,
                "imported_ranking_without_history": 0,
            }

        async def fake_count_embeddings_for_model(config, **kwargs):
            calls.append((config["model_key"], dict(kwargs)))
            return 99

        try:
            ai_routes.configure(
                invalidate_settings_response_cache=lambda: None,
                get_ai_status_counts=fake_get_ai_status_counts,
                count_embeddings_for_model=fake_count_embeddings_for_model,
            )
            ai_routes.invalidate_ai_status_response_cache()

            status = await ai_routes.build_ai_status(force=True)

            self.assertEqual(status["total_images"], 0)
            self.assertEqual(status["embedding_index"]["embedded"], 0)
            self.assertEqual(calls, [])
        finally:
            for name, value in old_config.items():
                setattr(ai_routes, name, value)
            ai_routes.invalidate_ai_status_response_cache()

    async def test_ai_status_counts_reuse_warm_stats_cache(self):
        db._stats_cache["data"] = {
            "active_images": 42,
            "rated_images": 7,
            "direct_comparison_rows": 5,
            "ranking_signal_count": 9,
            "imported_ranking_without_history": 2,
        }
        db._stats_cache["expires"] = db._time.time() + db.STATS_CACHE_TTL_SECONDS
        db._ai_status_counts_cache["data"] = None
        db._ai_status_counts_cache["expires"] = 0
        self.assertIs(db._ai_status_counts_cache, stats_repository._ai_status_counts_cache)

        counts = await db.get_ai_status_counts()

        self.assertEqual(counts["total_images"], 42)
        self.assertEqual(counts["rated_images"], 7)
        self.assertEqual(counts["direct_comparison_rows"], 5)
        self.assertEqual(counts["ranking_signal_count"], 9)
        self.assertEqual(counts["imported_ranking_without_history"], 2)

    async def test_ai_status_counts_reuse_stale_stats_cache_while_refreshing(self):
        db._stats_cache["data"] = {
            "active_images": 42,
            "rated_images": 7,
            "direct_comparison_rows": 5,
            "ranking_signal_count": 9,
            "imported_ranking_without_history": 2,
        }
        db._stats_cache["expires"] = db._time.time() - 1
        db._ai_status_counts_cache["data"] = None
        db._ai_status_counts_cache["expires"] = 0
        db._embedding_count_cache["value"] = 11
        db._embedding_count_cache["expires"] = db._time.time() + db.EMBEDDING_COUNT_CACHE_TTL_SECONDS
        stats_repository._stats_inflight_task = None
        db._stats_inflight_task = None
        started = asyncio.Event()
        release = asyncio.Event()
        old_get_stats_uncached = db._get_stats_uncached

        async def fake_get_stats_uncached():
            started.set()
            await release.wait()
            return db._stats_cache["data"]

        db._get_stats_uncached = fake_get_stats_uncached
        try:
            counts = await db.get_ai_status_counts()
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertIs(db._stats_inflight_task, stats_repository._stats_inflight_task)

            self.assertEqual(counts["embedded"], 11)
            self.assertEqual(counts["total_images"], 42)
            self.assertEqual(counts["rated_images"], 7)
            self.assertEqual(counts["direct_comparison_rows"], 5)
            self.assertEqual(counts["ranking_signal_count"], 9)
            self.assertEqual(counts["imported_ranking_without_history"], 2)
        finally:
            release.set()
            task = stats_repository._stats_inflight_task
            if task is not None and not task.done():
                await task
            stats_repository._stats_inflight_task = None
            db._stats_inflight_task = None
            db._get_stats_uncached = old_get_stats_uncached
            db.invalidate_stats_cache()


if __name__ == "__main__":
    unittest.main()
