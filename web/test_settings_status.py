from test_support import *  # noqa: F401,F403
import os
from unittest import mock

from data.repositories import embeddings as embedding_repository


class SettingsStatusTests(BackendTestCase):
    async def test_bounded_status_does_not_cancel_slow_sqlite_style_work(self):
        started = asyncio.Event()
        release = asyncio.Event()
        completed = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_status():
            started.set()
            try:
                await release.wait()
                return {"fresh": True}
            except asyncio.CancelledError:
                cancelled.set()
                raise
            finally:
                completed.set()

        result = await settings_status._bounded_status(
            slow_status(),
            lambda latency_ms: {"status_stale": True, "latency_ms": latency_ms},
            timeout_seconds=0.01,
        )

        self.assertTrue(started.is_set())
        self.assertTrue(result["status_stale"])
        self.assertFalse(cancelled.is_set())
        self.assertFalse(completed.is_set())

        release.set()
        await asyncio.wait_for(completed.wait(), timeout=1)
        self.assertFalse(cancelled.is_set())

    async def test_background_work_status_composes_one_bounded_snapshot(self):
        ai = {"worker_state": "paused"}
        cache = {"pregen": {"state": "running"}}
        people = {"worker": {"state": "idle"}}
        captions = {"worker": {"state": "paused"}}

        # The route calls these modules directly now, so the stubs go on them
        # rather than on injected copies held by settings_routes.
        with mock.patch.object(
            settings_routes.ai_routes,
            "build_ai_status",
            new=mock.AsyncMock(return_value=ai),
        ), mock.patch.object(
            settings_routes.core_api,
            "cache_status",
            new=mock.AsyncMock(return_value=cache),
        ), mock.patch.object(
            settings_routes.core_api,
            "people_status",
            new=mock.AsyncMock(return_value=people),
        ), mock.patch.object(
            settings_routes.core_api,
            "captions_status",
            new=mock.AsyncMock(return_value=captions),
        ):
            result = await settings_routes.api_background_work_status()

        self.assertEqual(result["ai"], ai)
        self.assertEqual(result["cache"], cache)
        self.assertEqual(result["people"], people)
        self.assertEqual(result["captions"], captions)

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

    async def test_satellite_memory_cache_defaults_to_fraction_of_ram(self):
        with mock.patch.dict(os.environ, {"AZIMUTH_MODE": "satellite"}, clear=False), mock.patch.object(
            settings, "_system_memory_gb", return_value=64.0
        ):
            normalized = settings.normalize_settings({})
        # min(25% of 64GB, 8GB) = 8GB
        self.assertEqual(normalized["memory_cache_gb"], 8.0)

    async def test_hub_memory_cache_keeps_half_gb_default(self):
        with mock.patch.dict(os.environ, {"AZIMUTH_MODE": "hub"}, clear=False), mock.patch.object(
            settings, "_system_memory_gb", return_value=64.0
        ):
            normalized = settings.normalize_settings({})
        self.assertEqual(normalized["memory_cache_gb"], 0.5)


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

        status = await ai_routes.build_ai_status()
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
        try:
            status = await ai_routes.build_ai_status()
        finally:
            ai_models.get_model_status = old_get_model_status

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
        try:
            response = await ai_routes.api_install_ai_model(role="deep")
        finally:
            ai_models.get_model_status = old_get_model_status
            ai_models.start_model_install = old_start_model_install

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

    async def test_save_settings_returns_saved_quality(self):
        response = await settings_routes.api_save_settings(JsonRequest({"thumb_quality": 83}))

        self.assertTrue(response["ok"])
        self.assertEqual(response["settings"]["thumb_quality"], 83)

    async def test_settings_reads_do_not_expose_cached_state(self):
        first = settings.get_settings()
        first["thumb_quality"] = 40

        second = settings.get_settings()

        self.assertNotEqual(second["thumb_quality"], 40)

    async def test_api_settings_cache_returns_independent_responses_and_invalidates(self):
        await settings_routes.api_settings()
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



