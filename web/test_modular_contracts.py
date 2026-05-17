import asyncio
from collections import Counter
import importlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from fastapi.routing import APIRoute

import app as app_module
import db
from core import cache_events
from core import query_constraints


PUBLIC_ROUTE_CONTRACT = {
    ("GET", "/"),
    ("GET", "/compare"),
    ("GET", "/rankings"),
    ("GET", "/library"),
    ("GET", "/people"),
    ("GET", "/settings"),
    ("GET", "/catalog"),
    ("GET", "/api/dev/status"),
    ("GET", "/api/people/status"),
    ("GET", "/api/people"),
    ("GET", "/api/people/faces/{face_id}/thumb"),
    ("POST", "/api/people/scan/pause"),
    ("POST", "/api/people/scan/resume"),
    ("POST", "/api/people/{person_id}/label"),
    ("POST", "/api/people/merge"),
    ("POST", "/api/people/merge-suggestions/{suggestion_id}/reject"),
    ("POST", "/api/people/faces/{face_id}/assign"),
    ("POST", "/api/people/faces/{face_id}/ignore"),
    ("POST", "/api/people/{person_id}/ignore"),
    ("POST", "/api/scan"),
    ("GET", "/api/scan/status"),
    ("GET", "/api/scan/folder"),
    ("GET", "/api/catalog/folder-picker"),
    ("POST", "/api/catalog/select-folder"),
    ("GET", "/api/catalog/browse"),
    ("GET", "/api/catalog"),
    ("POST", "/api/catalog/sources"),
    ("POST", "/api/catalog/sources/{source_id}/rescan"),
    ("POST", "/api/catalog/sources/{source_id}/remove"),
    ("GET", "/api/thumb/{size}/{image_id}"),
    ("GET", "/api/full/{image_id}"),
    ("GET", "/api/image/{image_id}/media-status"),
    ("POST", "/api/images/media-status"),
    ("POST", "/api/images/warm"),
    ("GET", "/api/cache/status"),
    ("POST", "/api/cache/pregen/start"),
    ("POST", "/api/cache/pregen/stop"),
    ("GET", "/api/cache/pregen/status"),
    ("POST", "/api/ai/embeddings/pause"),
    ("POST", "/api/ai/embeddings/resume"),
    ("GET", "/api/settings"),
    ("GET", "/api/ui/settings"),
    ("POST", "/api/image/{image_id}/flag"),
    ("POST", "/api/images/flag"),
    ("POST", "/api/settings"),
    ("POST", "/api/settings/reset"),
    ("POST", "/api/cache/clear"),
    ("POST", "/api/ai/model/install"),
    ("GET", "/api/mosaic/next"),
    ("POST", "/api/mosaic/pick"),
    ("GET", "/api/propagation/last"),
    ("POST", "/api/propagation/predict"),
    ("GET", "/api/compare/next"),
    ("POST", "/api/compare"),
    ("POST", "/api/compare/undo"),
    ("GET", "/api/rankings"),
    ("GET", "/api/date-groups"),
    ("GET", "/api/map/markers"),
    ("GET", "/api/export"),
    ("GET", "/api/search"),
    ("GET", "/api/similar/{image_id}"),
    ("GET", "/api/duplicates"),
    ("GET", "/api/image/{image_id}/exif"),
    ("GET", "/api/collections"),
    ("GET", "/api/folders"),
    ("GET", "/api/filter-options"),
    ("GET", "/api/stats"),
    ("GET", "/api/ai/status"),
}


class ModularContractTests(unittest.TestCase):
    def test_compatibility_imports_still_resolve(self):
        for module_name in ("app", "db", "thumbnails"):
            module = importlib.import_module(module_name)
            self.assertIsNotNone(module)

        self.assertIs(app_module.app, importlib.import_module("app").app)
        self.assertTrue(hasattr(importlib.import_module("thumbnails"), "__path__"))

    def test_shared_helpers_are_feature_owned_without_app_facades(self):
        responses = importlib.import_module("core.responses")
        request_helpers = importlib.import_module("core.requests")
        helpers = importlib.import_module("helpers")

        self.assertTrue(callable(responses.interaction_pool_stats))
        self.assertTrue(callable(responses.compare_response_rows))
        self.assertTrue(callable(responses.copy_interaction_response))
        self.assertTrue(callable(responses.visibility_counts))
        self.assertTrue(callable(request_helpers.positive_int))
        self.assertTrue(callable(request_helpers.clamp_int))
        self.assertTrue(callable(request_helpers.json_object))
        self.assertTrue(callable(helpers.ranking_signal_count))
        self.assertTrue(callable(helpers.has_ranking_signal))
        self.assertTrue(callable(helpers.camera_label))
        self.assertTrue(callable(responses.metadata_payload))
        self.assertIs(helpers.metadata_payload, responses.metadata_payload)
        self.assertIs(helpers.image_card, responses.image_card)
        self.assertIs(helpers.METADATA_FIELDS, responses.METADATA_FIELDS)
        self.assertTrue(callable(helpers._chunks))
        self.assertTrue(callable(helpers.filter_by_metadata))
        self.assertFalse(hasattr(app_module, "_interaction_pool_stats"))
        self.assertFalse(hasattr(app_module, "_compare_response_rows"))
        self.assertFalse(hasattr(app_module, "_copy_interaction_response"))
        self.assertFalse(hasattr(app_module, "_visibility_counts"))
        self.assertFalse(hasattr(app_module, "_positive_int"))
        self.assertFalse(hasattr(app_module, "_clamp_int"))
        self.assertFalse(hasattr(app_module, "_json_object"))
        self.assertFalse(hasattr(app_module, "_ranking_signal_count"))
        self.assertFalse(hasattr(app_module, "_has_ranking_signal"))
        self.assertFalse(hasattr(app_module, "_camera_label"))
        self.assertFalse(hasattr(app_module, "_metadata_payload"))
        self.assertFalse(hasattr(app_module, "_chunks"))
        self.assertFalse(hasattr(app_module, "_filter_by_metadata"))
        self.assertFalse(hasattr(app_module, "_top_indices_desc"))
        self.assertFalse(hasattr(app_module, "_visible_ranked_images"))
        self.assertFalse(hasattr(app_module, "_count_visible_ranked_ids"))
        self.assertTrue(callable(helpers.configure))
        self.assertTrue(callable(helpers._cached_image_ids_provider))
        self.assertTrue(callable(helpers._get_active_images_by_ids_provider))
        with open(os.path.join(os.path.dirname(__file__), "helpers.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)
            self.assertNotIn("def metadata_payload", contents)
            self.assertNotIn("def image_card", contents)
        self.assertEqual(
            responses.visibility_counts(5, 3),
            responses.visibility_counts(5, 3),
        )
        self.assertEqual(
            helpers.image_card({"id": 7, "filename": "seven.jpg"}, "md"),
            responses.image_card({"id": 7, "filename": "seven.jpg"}, "md"),
        )

    def test_embed_cache_uses_app_injected_db_providers(self):
        embed_cache = importlib.import_module("embed_cache")

        with open(os.path.join(os.path.dirname(__file__), "embed_cache.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        self.assertFalse(hasattr(app_module, "embed_cache"))
        self.assertTrue(callable(embed_cache.configure))
        self.assertTrue(callable(embed_cache._active_embedding_model_key))
        self.assertTrue(callable(embed_cache._db_path))

        old_active_key = app_module.db.active_embedding_model_key
        old_db_path = app_module.db.DB_PATH
        try:
            app_module.db.active_embedding_model_key = lambda: "late-bound-model"
            app_module.db.DB_PATH = "/tmp/photoarchive-late-bound.db"

            self.assertEqual(embed_cache._target_model_key(), "late-bound-model")
            self.assertEqual(
                [entry[0] for entry in embed_cache._db_file_signature()],
                [
                    "photoarchive-late-bound.db",
                    "photoarchive-late-bound.db-wal",
                    "photoarchive-late-bound.db-shm",
                ],
            )
        finally:
            app_module.db.active_embedding_model_key = old_active_key
            app_module.db.DB_PATH = old_db_path

    def test_elo_propagation_uses_app_injected_db_providers(self):
        elo_propagation = importlib.import_module("elo_propagation")

        with open(os.path.join(os.path.dirname(__file__), "elo_propagation.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        self.assertFalse(hasattr(app_module, "elo_propagation"))
        self.assertTrue(callable(elo_propagation.configure))
        self.assertTrue(callable(elo_propagation._active_embedding_model_key))
        self.assertTrue(callable(elo_propagation._get_active_images_by_ids))
        self.assertTrue(callable(elo_propagation._get_db))
        self.assertTrue(callable(elo_propagation._invalidate_rating_stats_cache))

        old_active_key = app_module.db.active_embedding_model_key
        old_get_images = app_module.db.get_active_images_by_ids
        old_get_db = app_module.db.get_db
        old_invalidate = app_module.db.invalidate_rating_stats_cache
        calls = []

        class FakeConnection:
            pass

        async def fake_get_images(image_ids):
            calls.append(("images", list(image_ids)))
            return {image_id: {"id": image_id} for image_id in image_ids}

        async def fake_get_db():
            calls.append(("db",))
            return FakeConnection()

        try:
            app_module.db.active_embedding_model_key = lambda: "late-bound-fast"
            app_module.db.get_active_images_by_ids = fake_get_images
            app_module.db.get_db = fake_get_db
            app_module.db.invalidate_rating_stats_cache = lambda: calls.append(("invalidate",))

            self.assertEqual(elo_propagation._active_embedding_model_key(), "late-bound-fast")
            self.assertEqual(
                asyncio.run(elo_propagation._get_active_images_by_ids([3, 4])),
                {3: {"id": 3}, 4: {"id": 4}},
            )
            self.assertIsInstance(asyncio.run(elo_propagation._get_db()), FakeConnection)
            elo_propagation._invalidate_rating_stats_cache()
        finally:
            app_module.db.active_embedding_model_key = old_active_key
            app_module.db.get_active_images_by_ids = old_get_images
            app_module.db.get_db = old_get_db
            app_module.db.invalidate_rating_stats_cache = old_invalidate

        self.assertEqual(calls, [("images", [3, 4]), ("db",), ("invalidate",)])

    def test_embedding_worker_uses_app_injected_db_providers(self):
        embedding_worker = importlib.import_module("embedding_worker")

        with open(os.path.join(os.path.dirname(__file__), "embedding_worker.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        self.assertFalse(hasattr(app_module, "embedding_worker"))
        self.assertTrue(callable(embedding_worker.configure))
        provider_names = (
            "_get_deep_search_cache_status",
            "_get_catalog_image_counts",
            "_count_embeddings_for_model",
            "_get_unembedded_images",
            "_get_pending_deep_search_queries",
            "_store_deep_search_query_embedding",
            "_store_embeddings_batch",
            "_get_embedding_count",
        )
        for name in provider_names:
            self.assertTrue(callable(getattr(embedding_worker, name)))

        old_providers = {
            "get_deep_search_cache_status": app_module.db.get_deep_search_cache_status,
            "get_catalog_image_counts": app_module.db.get_catalog_image_counts,
            "count_embeddings_for_model": app_module.db.count_embeddings_for_model,
            "get_unembedded_images": app_module.db.get_unembedded_images,
            "get_pending_deep_search_queries": app_module.db.get_pending_deep_search_queries,
            "store_deep_search_query_embedding": app_module.db.store_deep_search_query_embedding,
            "store_embeddings_batch": app_module.db.store_embeddings_batch,
            "get_embedding_count": app_module.db.get_embedding_count,
        }
        calls = []
        config = {"model_key": "model"}

        async def fake_deep_status(config_arg, terms=None):
            calls.append(("deep_status", config_arg, terms))
            return {"pending_queries": 1, "embedded_queries": 2}

        async def fake_catalog_counts():
            calls.append(("catalog_counts",))
            return {"active_images": 3}

        async def fake_count_embeddings(config_arg, **kwargs):
            calls.append(("count_model", config_arg, kwargs))
            return 4

        async def fake_unembedded(**kwargs):
            calls.append(("unembedded", kwargs))
            return [{"id": 5}]

        async def fake_pending(config_arg, terms, **kwargs):
            calls.append(("pending", config_arg, terms, kwargs))
            return [{"query": "crane"}]

        async def fake_store_query(config_arg, query, blob):
            calls.append(("store_query", config_arg, query, blob))

        async def fake_store_batch(rows, **kwargs):
            calls.append(("store_batch", rows, kwargs))

        async def fake_embedding_count():
            calls.append(("embedding_count",))
            return 6

        try:
            app_module.db.get_deep_search_cache_status = fake_deep_status
            app_module.db.get_catalog_image_counts = fake_catalog_counts
            app_module.db.count_embeddings_for_model = fake_count_embeddings
            app_module.db.get_unembedded_images = fake_unembedded
            app_module.db.get_pending_deep_search_queries = fake_pending
            app_module.db.store_deep_search_query_embedding = fake_store_query
            app_module.db.store_embeddings_batch = fake_store_batch
            app_module.db.get_embedding_count = fake_embedding_count

            self.assertEqual(
                asyncio.run(embedding_worker._get_deep_search_cache_status(config, ["crane"])),
                {"pending_queries": 1, "embedded_queries": 2},
            )
            self.assertEqual(asyncio.run(embedding_worker._get_catalog_image_counts()), {"active_images": 3})
            self.assertEqual(asyncio.run(embedding_worker._count_embeddings_for_model(config, include_all=True)), 4)
            self.assertEqual(asyncio.run(embedding_worker._get_unembedded_images(limit=1)), [{"id": 5}])
            self.assertEqual(
                asyncio.run(embedding_worker._get_pending_deep_search_queries(config, ["crane"], limit=2)),
                [{"query": "crane"}],
            )
            asyncio.run(embedding_worker._store_deep_search_query_embedding(config, "crane", b"vec"))
            asyncio.run(embedding_worker._store_embeddings_batch([(5, b"vec")], embedding_config=config))
            self.assertEqual(asyncio.run(embedding_worker._get_embedding_count()), 6)
        finally:
            for name, provider in old_providers.items():
                setattr(app_module.db, name, provider)

        self.assertEqual(
            calls,
            [
                ("deep_status", config, ["crane"]),
                ("catalog_counts",),
                ("count_model", config, {"include_all": True}),
                ("unembedded", {"limit": 1}),
                ("pending", config, ["crane"], {"limit": 2}),
                ("store_query", config, "crane", b"vec"),
                ("store_batch", [(5, b"vec")], {"embedding_config": config}),
                ("embedding_count",),
            ],
        )

    def test_compare_service_owns_cache_and_helpers_without_app_facades(self):
        app_factory = importlib.import_module("core.app_factory")
        service = importlib.import_module("features.compare.service")

        self.assertTrue(callable(app_factory.configure_compare_service))
        with open(os.path.join(os.path.dirname(__file__), "core", "app_factory.py"), encoding="utf-8") as fh:
            factory_entry = fh.read()
        with open(os.path.join(os.path.dirname(__file__), "app.py"), encoding="utf-8") as fh:
            app_entry = fh.read()
        self.assertIn("configure_compare_service(", factory_entry)
        self.assertNotIn("configure_compare_service(", app_entry)
        self.assertNotIn("compare_service.configure(", app_entry)
        self.assertIsInstance(service._pairing_cache, dict)
        self.assertIsInstance(service._matchups_cache, dict)
        self.assertIsInstance(service._visible_matchups_cache, dict)
        self.assertIsInstance(service._visible_pairing_candidates_cache, dict)
        self.assertIsInstance(service._visible_pairing_candidates_refreshing, set)
        self.assertIsInstance(service._interaction_response_cache, dict)
        for name in (
            "get_pairing_images",
            "get_past_matchups",
            "get_visible_past_matchups",
            "get_past_matchups_for_candidate_ids",
            "add_past_matchups",
            "patch_pairing_cache",
            "filter_visible_candidates",
            "hydrate_active_rows",
            "default_visible_pairing_candidates",
            "filtered_visible_ranked_candidates",
            "load_filtered_visible_ranked_candidates",
            "search_visible_ranked_candidates",
            "warm_filtered_visible_ranked_candidates",
            "metadata_text_match",
            "apply_text_search_constraint",
            "has_candidate_filters",
            "diverse_sample",
            "mosaic_next_impl",
            "compare_next_impl",
        ):
            self.assertTrue(callable(getattr(service, name)))
        for name in (
            "_pairing_cache",
            "_matchups_cache",
            "_visible_matchups_cache",
            "_visible_pairing_candidates_cache",
            "_visible_pairing_candidates_refreshing",
            "_interaction_response_cache",
            "_visible_pairing_candidates_cache_ttl_seconds",
            "_SWISS_PAIR_WINDOW",
            "_FILTERED_SWISS_PAIR_WINDOW",
            "_FILTERED_MOSAIC_WINDOW",
            "_MOSAIC_EXPLORE_WINDOW",
            "_get_pairing_images",
            "_get_past_matchups",
            "_get_visible_past_matchups",
            "_get_past_matchups_for_candidate_ids",
            "_add_past_matchups",
            "_patch_pairing_cache",
            "_filter_visible_candidates",
            "_hydrate_active_rows",
            "_default_visible_pairing_candidates",
            "_filtered_visible_ranked_candidates",
            "_load_filtered_visible_ranked_candidates",
            "_search_visible_ranked_candidates",
            "_warm_filtered_visible_ranked_candidates",
            "_metadata_text_match",
            "_apply_text_search_constraint",
            "_has_candidate_filters",
            "_diverse_sample",
            "_mosaic_next_impl",
            "_compare_next_impl",
        ):
            self.assertFalse(hasattr(app_module, name))
        self.assertFalse(hasattr(app_module, "mosaic_next"))
        self.assertFalse(hasattr(app_module, "mosaic_pick"))
        self.assertFalse(hasattr(app_module, "propagation_last"))
        self.assertFalse(hasattr(app_module, "propagation_predict"))
        self.assertFalse(hasattr(app_module, "compare_next"))
        self.assertFalse(hasattr(app_module, "submit_comparison"))
        self.assertFalse(hasattr(app_module, "compare_undo"))
        self.assertTrue(callable(service._db_signature))
        self.assertTrue(callable(service._get_visible_images_for_pairing))
        self.assertTrue(callable(service._get_visible_pairing_pool_counts))
        self.assertTrue(callable(service._count_rankings))
        self.assertTrue(callable(service._get_rankings))

    def test_compare_service_uses_injected_db_backed_providers(self):
        app_factory = importlib.import_module("core.app_factory")
        base_dir = os.path.dirname(__file__)
        service = importlib.import_module("features.compare.service")

        with open(os.path.join(base_dir, "features", "compare", "service.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        config_names = (
            "_invalidate_rankings_cache",
            "_invalidate_interaction_response_cache",
            "_cache_root",
            "_resolve_library_constraints",
            "_schedule_thumbnail_prefetch",
            "_schedule_cached_thumbnail_memory_warm",
            "_db_signature",
            "_get_active_images_for_pairing",
            "_get_past_matchups",
            "_get_visible_past_matchups",
            "_get_past_matchups_for_image_ids",
            "_get_active_images_by_ids",
            "_get_visible_images_for_pairing",
            "_get_visible_orientation_pairing_pool_counts",
            "_count_rankings",
            "_get_rankings",
            "_get_visible_pairing_pool_counts",
            "_get_top_images",
        )
        old_config = {name: getattr(service, name) for name in config_names}
        old_pairing_cache = dict(service._pairing_cache)
        old_matchups_cache = dict(service._matchups_cache)
        old_visible_matchups_cache = dict(service._visible_matchups_cache)
        old_visible_candidates_cache = dict(service._visible_pairing_candidates_cache)
        old_visible_refreshing = set(service._visible_pairing_candidates_refreshing)
        old_interaction_cache = dict(service._interaction_response_cache)
        old_generation = service._visible_pairing_candidates_generation
        old_filter_visible = service.app_helpers.filter_visible_candidates
        calls = []

        def row(image_id, *, created=True):
            result = {
                "id": image_id,
                "filename": f"{image_id}.jpg",
                "filepath": f"/tmp/{image_id}.jpg",
                "elo": 1200.0 + image_id,
                "comparisons": image_id,
                "status": "kept",
                "flag": "unflagged",
                "aspect_ratio": 1.5,
            }
            if created:
                result["created_at"] = "2024-01-01"
            return result

        async def fake_resolve(q, *, people="", deep=False):
            calls.append(("resolve", q, people, deep))
            return {
                "id_filter": None,
                "scores": {},
                "search_mode": "",
                "text_query": q,
                "active": bool(q),
                "ai_unavailable": False,
                "deep_requested": deep,
                "deep_search_cached": False,
                "fallback_reason": "",
            }

        async def fake_filter_visible(candidates, size, cache_root):
            calls.append(("filter_visible", [item["id"] for item in candidates], size, cache_root))
            return candidates

        async def fake_active_images_for_pairing():
            calls.append(("active_pairing",))
            return [row(1), row(2)]

        async def fake_past_matchups():
            calls.append(("past_matchups",))
            return {(1, 2)}

        async def fake_visible_past_matchups(size, cache_root):
            calls.append(("visible_past", size, cache_root))
            return {(2, 3)}

        async def fake_past_for_ids(image_ids):
            calls.append(("past_for_ids", tuple(image_ids)))
            return {(1, 3)}

        async def fake_active_images_by_ids(image_ids):
            calls.append(("active_by_ids", tuple(image_ids)))
            return {int(image_id): row(int(image_id)) for image_id in image_ids}

        async def fake_visible_images_for_pairing(size, cache_root, **kwargs):
            calls.append(("visible_pairing", size, cache_root, kwargs))
            return [row(1), row(2), row(3)]

        async def fake_visible_orientation_counts(size, cache_root, orientation):
            calls.append(("orientation_counts", size, cache_root, orientation))
            return {"active_images": 3, "visible_images": 2}

        async def fake_count_rankings(**kwargs):
            calls.append(("count_rankings", kwargs))
            return 2 if kwargs.get("visible_thumb_size") else 3

        async def fake_get_rankings(**kwargs):
            calls.append(("get_rankings", kwargs))
            return [row(1), row(2), row(3)]

        async def fake_visible_pool(size, cache_root):
            calls.append(("visible_pool", size, cache_root))
            return {"active_images": 3, "visible_images": 3}

        async def fake_top_images(**kwargs):
            calls.append(("top_images", kwargs))
            return [row(1), row(2)]

        try:
            service._pairing_cache.clear()
            service._pairing_cache.update({"data": None, "valid": False})
            service._matchups_cache.clear()
            service._matchups_cache.update({"data": None, "valid": False})
            service._visible_matchups_cache.clear()
            service._visible_pairing_candidates_cache.clear()
            service._visible_pairing_candidates_refreshing.clear()
            service._interaction_response_cache.clear()
            service._visible_pairing_candidates_generation = 0
            service.app_helpers.filter_visible_candidates = fake_filter_visible
            app_factory.configure_compare_service(
                invalidate_rankings_cache=lambda: calls.append(("invalidate_rankings",)),
                invalidate_interaction_response_cache=lambda: calls.append(("invalidate_interaction",)),
                cache_root=lambda: "/tmp/compare-cache",
                resolve_library_constraints=fake_resolve,
                schedule_thumbnail_prefetch=lambda rows, size, *, limit: calls.append(
                    ("prefetch", [item["id"] for item in rows], size, limit)
                ),
                schedule_cached_thumbnail_memory_warm=lambda rows, size, *, limit: calls.append(
                    ("memory_warm", [item["id"] for item in rows], size, limit)
                ),
                db_signature=lambda: "/tmp/compare.db",
                get_active_images_for_pairing=fake_active_images_for_pairing,
                get_past_matchups=fake_past_matchups,
                get_visible_past_matchups=fake_visible_past_matchups,
                get_past_matchups_for_image_ids=fake_past_for_ids,
                get_active_images_by_ids=fake_active_images_by_ids,
                get_visible_images_for_pairing=fake_visible_images_for_pairing,
                get_visible_orientation_pairing_pool_counts=fake_visible_orientation_counts,
                count_rankings=fake_count_rankings,
                get_rankings=fake_get_rankings,
                get_visible_pairing_pool_counts=fake_visible_pool,
                get_top_images=fake_top_images,
            )

            pairing_rows = asyncio.run(service.get_pairing_images())
            past = asyncio.run(service.get_past_matchups())
            visible_past = asyncio.run(service.get_visible_past_matchups("md"))
            candidate_past = asyncio.run(service.get_past_matchups_for_candidate_ids("md", [1, 3, 3]))
            hydrated = asyncio.run(service.hydrate_active_rows([row(1, created=False)]))
            default_rows = asyncio.run(service.default_visible_pairing_candidates("sm", limit=2))
            filtered_rows, filtered_total, filtered_visible = asyncio.run(
                service.load_filtered_visible_ranked_candidates("md", limit=2, orientation="landscape")
            )
            search_rows, search_total, search_visible = asyncio.run(
                service.search_visible_ranked_candidates(
                    "sm",
                    limit=2,
                    search={"id_filter": {1, 2}, "text_query": ""},
                )
            )
            mosaic_default = asyncio.run(service.mosaic_next_impl(n=2, strategy="explore"))
            mosaic = asyncio.run(service.mosaic_next_impl(n=2, strategy="top"))
            cache_keys = list(service._visible_matchups_cache) + list(service._visible_pairing_candidates_cache)
        finally:
            service.app_helpers.filter_visible_candidates = old_filter_visible
            service._pairing_cache.clear()
            service._pairing_cache.update(old_pairing_cache)
            service._matchups_cache.clear()
            service._matchups_cache.update(old_matchups_cache)
            service._visible_matchups_cache.clear()
            service._visible_matchups_cache.update(old_visible_matchups_cache)
            service._visible_pairing_candidates_cache.clear()
            service._visible_pairing_candidates_cache.update(old_visible_candidates_cache)
            service._visible_pairing_candidates_refreshing.clear()
            service._visible_pairing_candidates_refreshing.update(old_visible_refreshing)
            service._interaction_response_cache.clear()
            service._interaction_response_cache.update(old_interaction_cache)
            service._visible_pairing_candidates_generation = old_generation
            for name, value in old_config.items():
                setattr(service, name, value)

        self.assertEqual([item["id"] for item in pairing_rows], [1, 2])
        self.assertEqual(past, {(1, 2)})
        self.assertEqual(visible_past, {(2, 3)})
        self.assertEqual(candidate_past, {(1, 3)})
        self.assertEqual(hydrated[0]["created_at"], "2024-01-01")
        self.assertEqual([item["id"] for item in default_rows], [1, 2, 3])
        self.assertEqual([item["id"] for item in filtered_rows], [1, 2, 3])
        self.assertEqual((filtered_total, filtered_visible), (3, 2))
        self.assertEqual([item["id"] for item in search_rows], [1, 2])
        self.assertEqual((search_total, search_visible), (3, 2))
        self.assertEqual(len(mosaic_default["images"]), 2)
        self.assertEqual(len(mosaic["images"]), 2)
        self.assertTrue(any(key.startswith("/tmp/compare.db:") for key in cache_keys))
        self.assertIn(("visible_pool", "sm", "/tmp/compare-cache"), calls)
        self.assertIn(("top_images", {"limit": 50}), calls)
        self.assertTrue(any(call[0] == "prefetch" for call in calls))

    def test_compare_service_helper_defaults_to_fresh_db_providers(self):
        app_factory = importlib.import_module("core.app_factory")
        service = importlib.import_module("features.compare.service")

        with self.assertRaises(TypeError):
            app_factory.configure_compare_service(
                invalidate_rankings_cache=lambda: None,
                invalidate_interaction_response_cache=lambda: None,
                cache_root=lambda: "/tmp/cache",
            )

        config_names = (
            "_invalidate_rankings_cache",
            "_invalidate_interaction_response_cache",
            "_cache_root",
            "_resolve_library_constraints",
            "_schedule_thumbnail_prefetch",
            "_schedule_cached_thumbnail_memory_warm",
            "_db_signature",
            "_get_active_images_for_pairing",
            "_get_past_matchups",
            "_get_visible_past_matchups",
            "_get_past_matchups_for_image_ids",
            "_get_active_images_by_ids",
            "_get_visible_images_for_pairing",
            "_get_visible_orientation_pairing_pool_counts",
            "_count_rankings",
            "_get_rankings",
            "_get_visible_pairing_pool_counts",
            "_get_top_images",
        )
        db_provider_names = (
            "get_active_images_for_pairing",
            "get_past_matchups",
            "get_visible_past_matchups",
            "get_past_matchups_for_image_ids",
            "get_active_images_by_ids",
            "get_visible_images_for_pairing",
            "get_visible_orientation_pairing_pool_counts",
            "count_rankings",
            "get_rankings",
            "get_visible_pairing_pool_counts",
            "get_top_images",
        )
        old_config = {name: getattr(service, name) for name in config_names}
        old_db_path = db.DB_PATH
        old_db_providers = {name: getattr(db, name) for name in db_provider_names}
        old_pairing_cache = dict(service._pairing_cache)
        old_matchups_cache = dict(service._matchups_cache)
        old_visible_matchups_cache = dict(service._visible_matchups_cache)
        old_visible_candidates_cache = dict(service._visible_pairing_candidates_cache)
        old_visible_refreshing = set(service._visible_pairing_candidates_refreshing)
        old_interaction_cache = dict(service._interaction_response_cache)
        old_generation = service._visible_pairing_candidates_generation
        old_filter_visible = service.app_helpers.filter_visible_candidates
        calls = []

        def row(image_id, *, created=True):
            result = {
                "id": image_id,
                "filename": f"{image_id}.jpg",
                "filepath": f"/tmp/{image_id}.jpg",
                "elo": 1200.0 + image_id,
                "comparisons": image_id,
                "status": "kept",
                "flag": "unflagged",
                "aspect_ratio": 1.5,
            }
            if created:
                result["created_at"] = "2024-01-01"
            return result

        async def fake_resolve(q, *, people="", deep=False):
            calls.append(("resolve", q, people, deep))
            return {
                "id_filter": None,
                "scores": {},
                "search_mode": "",
                "text_query": q,
                "active": bool(q),
                "ai_unavailable": False,
                "deep_requested": deep,
                "deep_search_cached": False,
                "fallback_reason": "",
            }

        async def fake_filter_visible(candidates, size, cache_root):
            calls.append(("filter_visible", [item["id"] for item in candidates], size, cache_root))
            return candidates

        async def fake_active_images_for_pairing():
            calls.append(("db_active_pairing",))
            return [row(1), row(2), row(3)]

        async def fake_past_matchups():
            calls.append(("db_past_matchups",))
            return {(1, 2)}

        async def fake_visible_past_matchups(size, cache_root):
            calls.append(("db_visible_past", size, cache_root))
            return {(2, 3)}

        async def fake_past_matchups_for_image_ids(image_ids):
            calls.append(("db_past_for_ids", tuple(image_ids)))
            return {(1, 3)}

        async def fake_active_images_by_ids(image_ids):
            calls.append(("db_active_by_ids", tuple(image_ids)))
            return {int(image_id): row(int(image_id)) for image_id in image_ids}

        async def fake_visible_images_for_pairing(size, cache_root, **kwargs):
            calls.append(("db_visible_pairing", size, cache_root, kwargs))
            return [row(1), row(2), row(3)]

        async def fake_visible_orientation_counts(size, cache_root, orientation):
            calls.append(("db_orientation_counts", size, cache_root, orientation))
            return {"active_images": 3, "visible_images": 2}

        async def fake_count_rankings(**kwargs):
            calls.append(("db_count_rankings", kwargs))
            return 2 if kwargs.get("visible_thumb_size") else 3

        async def fake_get_rankings(**kwargs):
            calls.append(("db_get_rankings", kwargs))
            return [row(1), row(2), row(3)]

        async def fake_visible_pairing_pool_counts(size, cache_root):
            calls.append(("db_visible_pool", size, cache_root))
            return {"active_images": 3, "visible_images": 3}

        async def fake_top_images(**kwargs):
            calls.append(("db_top_images", kwargs))
            return [row(1), row(2), row(3)]

        try:
            db.DB_PATH = "/tmp/default-compare.db"
            db.get_active_images_for_pairing = fake_active_images_for_pairing
            db.get_past_matchups = fake_past_matchups
            db.get_visible_past_matchups = fake_visible_past_matchups
            db.get_past_matchups_for_image_ids = fake_past_matchups_for_image_ids
            db.get_active_images_by_ids = fake_active_images_by_ids
            db.get_visible_images_for_pairing = fake_visible_images_for_pairing
            db.get_visible_orientation_pairing_pool_counts = fake_visible_orientation_counts
            db.count_rankings = fake_count_rankings
            db.get_rankings = fake_get_rankings
            db.get_visible_pairing_pool_counts = fake_visible_pairing_pool_counts
            db.get_top_images = fake_top_images

            service._pairing_cache.clear()
            service._pairing_cache.update({"data": None, "valid": False})
            service._matchups_cache.clear()
            service._matchups_cache.update({"data": None, "valid": False})
            service._visible_matchups_cache.clear()
            service._visible_pairing_candidates_cache.clear()
            service._visible_pairing_candidates_refreshing.clear()
            service._interaction_response_cache.clear()
            service._visible_pairing_candidates_generation = 0
            service.app_helpers.filter_visible_candidates = fake_filter_visible

            app_factory.configure_compare_service(
                invalidate_rankings_cache=lambda: calls.append(("invalidate_rankings",)),
                invalidate_interaction_response_cache=lambda: calls.append(("invalidate_interaction",)),
                cache_root=lambda: "/tmp/default-cache",
                resolve_library_constraints=fake_resolve,
                schedule_thumbnail_prefetch=lambda rows, size, *, limit: calls.append(
                    ("prefetch", [item["id"] for item in rows], size, limit)
                ),
                schedule_cached_thumbnail_memory_warm=lambda rows, size, *, limit: calls.append(
                    ("memory_warm", [item["id"] for item in rows], size, limit)
                ),
            )

            pairing_rows = asyncio.run(service.get_pairing_images())
            past = asyncio.run(service.get_past_matchups())
            visible_past = asyncio.run(service.get_visible_past_matchups("md"))
            candidate_past = asyncio.run(service.get_past_matchups_for_candidate_ids("md", [1, 3, 3]))
            hydrated = asyncio.run(service.hydrate_active_rows([row(1, created=False)]))
            filtered_rows, filtered_total, filtered_visible = asyncio.run(
                service.load_filtered_visible_ranked_candidates("md", limit=2, orientation="landscape")
            )
            search_rows, search_total, search_visible = asyncio.run(
                service.search_visible_ranked_candidates(
                    "sm",
                    limit=2,
                    search={"id_filter": {1, 2}, "text_query": ""},
                )
            )
            mosaic_default = asyncio.run(service.mosaic_next_impl(n=2, strategy="explore"))
            mosaic_top = asyncio.run(service.mosaic_next_impl(n=2, strategy="top"))
            cache_keys = list(service._visible_matchups_cache) + list(service._visible_pairing_candidates_cache)
        finally:
            db.DB_PATH = old_db_path
            for name, provider in old_db_providers.items():
                setattr(db, name, provider)
            service.app_helpers.filter_visible_candidates = old_filter_visible
            service._pairing_cache.clear()
            service._pairing_cache.update(old_pairing_cache)
            service._matchups_cache.clear()
            service._matchups_cache.update(old_matchups_cache)
            service._visible_matchups_cache.clear()
            service._visible_matchups_cache.update(old_visible_matchups_cache)
            service._visible_pairing_candidates_cache.clear()
            service._visible_pairing_candidates_cache.update(old_visible_candidates_cache)
            service._visible_pairing_candidates_refreshing.clear()
            service._visible_pairing_candidates_refreshing.update(old_visible_refreshing)
            service._interaction_response_cache.clear()
            service._interaction_response_cache.update(old_interaction_cache)
            service._visible_pairing_candidates_generation = old_generation
            for name, value in old_config.items():
                setattr(service, name, value)

        self.assertEqual([item["id"] for item in pairing_rows], [1, 2, 3])
        self.assertEqual(past, {(1, 2)})
        self.assertEqual(visible_past, {(2, 3)})
        self.assertEqual(candidate_past, {(1, 3)})
        self.assertEqual(hydrated[0]["created_at"], "2024-01-01")
        self.assertEqual([item["id"] for item in filtered_rows], [1, 2, 3])
        self.assertEqual((filtered_total, filtered_visible), (3, 2))
        self.assertEqual([item["id"] for item in search_rows], [1, 2])
        self.assertEqual((search_total, search_visible), (3, 2))
        self.assertEqual(len(mosaic_default["images"]), 2)
        self.assertEqual(len(mosaic_top["images"]), 2)
        self.assertTrue(any(key.startswith("/tmp/default-compare.db:") for key in cache_keys))
        self.assertIn(("db_active_pairing",), calls)
        self.assertIn(("db_past_matchups",), calls)
        self.assertIn(("db_visible_past", "md", "/tmp/default-cache"), calls)
        self.assertIn(("db_past_for_ids", (1, 3)), calls)
        self.assertIn(("db_active_by_ids", (1,)), calls)
        self.assertTrue(any(call[0] == "db_visible_pairing" for call in calls))
        self.assertIn(("db_orientation_counts", "md", "/tmp/default-cache", "landscape"), calls)
        self.assertTrue(any(call[0] == "db_count_rankings" for call in calls))
        self.assertTrue(any(call[0] == "db_get_rankings" for call in calls))
        self.assertIn(("db_visible_pool", "sm", "/tmp/default-cache"), calls)
        self.assertIn(("db_top_images", {"limit": 50}), calls)

    def test_library_service_owns_rankings_cache_without_app_facades(self):
        app_factory = importlib.import_module("core.app_factory")
        service = importlib.import_module("features.library.service")
        routes = importlib.import_module("features.library.routes")

        self.assertTrue(callable(app_factory.configure_library_service))
        self.assertIsInstance(service._rankings_response_cache, dict)
        self.assertGreater(service._rankings_response_cache_ttl_seconds, 0)
        self.assertTrue(callable(service.api_rankings_impl))
        self.assertTrue(callable(service.date_groups_payload))
        self.assertTrue(callable(service.map_markers_payload))
        self.assertTrue(callable(service.filter_options_payload))
        self.assertTrue(callable(service.stats_payload))
        self.assertTrue(callable(service.copy_rankings_response))
        self.assertTrue(callable(service.cache_rankings_response))
        self.assertFalse(hasattr(app_module, "_rankings_response_cache"))
        self.assertFalse(hasattr(app_module, "_rankings_response_cache_ttl_seconds"))
        self.assertFalse(hasattr(app_module, "_api_rankings_impl"))
        self.assertFalse(hasattr(app_module, "_copy_rankings_response"))
        self.assertFalse(hasattr(app_module, "_cache_rankings_response"))
        self.assertFalse(hasattr(app_module, "api_rankings"))
        self.assertFalse(hasattr(app_module, "api_date_groups"))
        self.assertFalse(hasattr(app_module, "api_map_markers"))
        self.assertFalse(hasattr(app_module, "api_filter_options"))
        self.assertFalse(hasattr(app_module, "api_stats"))

        service._rankings_response_cache[("probe",)] = {"data": {}, "expires": 1}
        cache_events.invalidate_rankings_cache()
        self.assertFalse(service._rankings_response_cache)

        old_ttl = service._rankings_response_cache_ttl_seconds
        try:
            service._rankings_response_cache_ttl_seconds = 0.0
            service.cache_rankings_response(("probe",), {"images": []})
            self.assertLessEqual(service._rankings_response_cache[("probe",)]["expires"], service.time.monotonic())
        finally:
            service._rankings_response_cache_ttl_seconds = old_ttl
            cache_events.invalidate_rankings_cache()

        ranking_route = next(
            route
            for route in app_module.app.routes
            if isinstance(route, APIRoute) and route.path == "/api/rankings"
        )
        self.assertIs(ranking_route.endpoint, routes.api_rankings)
        self.assertEqual(ranking_route.dependant.request_param_name, "request")
        self.assertNotIn("request", [param.name for param in ranking_route.dependant.query_params])

    def test_library_service_uses_injected_db_backed_providers(self):
        base_dir = os.path.dirname(__file__)
        service = importlib.import_module("features.library.service")

        with open(os.path.join(base_dir, "features", "library", "service.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        config_names = (
            "_resolve_library_constraints",
            "_cache_root",
            "_clamp_int",
            "_normalize_search_query",
            "_schedule_thumbnail_prefetch",
            "_schedule_result_thumbnail_memory_warm",
            "_rankings_response_cache_ttl_seconds_provider",
            "_extension_search_terms",
            "_db_signature",
            "_get_date_groups",
            "_get_map_markers",
            "_get_filter_options",
            "_get_stats",
            "_count_rankings",
            "_get_rankings",
            "_get_visible_pairing_pool_counts",
        )
        old_config = {name: getattr(service, name) for name in config_names}
        old_cache = dict(service._rankings_response_cache)
        cache_keys = []
        calls = []

        async def fake_resolve(q, *, people="", deep=False):
            calls.append(("resolve", q, people, deep))
            active = bool(q)
            return {
                "id_filter": None,
                "scores": {},
                "search_mode": "metadata" if active else "",
                "text_query": q,
                "active": active,
                "ai_unavailable": False,
                "deep_requested": deep,
                "deep_search_cached": False,
                "fallback_reason": "",
                "people_ids": [int(people)] if people else [],
                "people_active": bool(people),
            }

        async def fake_date_groups(**kwargs):
            calls.append(("date_groups", kwargs))
            return [{"date": "2024-01", "count": 1}]

        async def fake_map_markers(**kwargs):
            calls.append(("map_markers", kwargs))
            return {"markers": [{"id": 1}]}

        async def fake_filter_options():
            calls.append(("filter_options",))
            return {"file_types": [{"ext": "jpg"}]}

        async def fake_stats():
            calls.append(("stats",))
            return {"total_images": 2}

        async def fake_count_rankings(**kwargs):
            calls.append(("count_rankings", kwargs))
            return 1 if kwargs.get("visible_thumb_size") else 2

        async def fake_get_rankings(**kwargs):
            calls.append(("get_rankings", kwargs))
            return [
                {
                    "id": 11,
                    "filename": "eleven.jpg",
                    "elo": 1225.0,
                    "comparisons": 3,
                    "status": "kept",
                    "flag": "unflagged",
                    "aspect_ratio": 1.5,
                }
            ]

        async def fake_visible_pairing_pool_counts(size, cache_root):
            calls.append(("visible_pool", size, cache_root))
            return {"active_images": 2, "visible_images": 1}

        try:
            service._rankings_response_cache.clear()
            service.configure(
                resolve_library_constraints=fake_resolve,
                cache_root=lambda: "/tmp/cache-root",
                clamp_int=lambda value, default, minimum, maximum: max(
                    minimum,
                    min(int(value if value is not None else default), maximum),
                ),
                normalize_search_query=lambda query: str(query or "").strip().lower(),
                schedule_thumbnail_prefetch=lambda rows, size, *, limit: calls.append(
                    ("prefetch", [row["id"] for row in rows], size, limit)
                ),
                schedule_result_thumbnail_memory_warm=lambda rows: calls.append(
                    ("memory_warm", [row["id"] for row in rows])
                ),
                extension_search_terms=lambda: {"jpg"},
                db_signature=lambda: "/tmp/library.db",
                get_date_groups=fake_date_groups,
                get_map_markers=fake_map_markers,
                get_filter_options=fake_filter_options,
                get_stats=fake_stats,
                count_rankings=fake_count_rankings,
                get_rankings=fake_get_rankings,
                get_visible_pairing_pool_counts=fake_visible_pairing_pool_counts,
                rankings_response_cache_ttl_seconds=lambda: 30.0,
            )

            date_groups = asyncio.run(service.date_groups_payload(q="sun", people="7", deep=True))
            markers = asyncio.run(service.map_markers_payload(q="sun"))
            filters = asyncio.run(service.filter_options_payload())
            stats = asyncio.run(service.stats_payload())
            unfiltered = asyncio.run(service.api_rankings_impl(limit=5, offset=0))
            extension_search = asyncio.run(service.api_rankings_impl(limit=5, offset=0, q="jpg"))
            cache_keys = list(service._rankings_response_cache)
        finally:
            service._rankings_response_cache.clear()
            service._rankings_response_cache.update(old_cache)
            for name, value in old_config.items():
                setattr(service, name, value)

        self.assertEqual(date_groups, {"groups": [{"date": "2024-01", "count": 1}]})
        self.assertEqual(markers, {"markers": [{"id": 1}]})
        self.assertEqual(filters, {"file_types": [{"ext": "jpg"}]})
        self.assertEqual(stats, {"total_images": 2})
        self.assertEqual(unfiltered["total_images"], 2)
        self.assertEqual(unfiltered["visible_images"], 1)
        self.assertEqual(extension_search["total_images"], 2)
        self.assertEqual(extension_search["visible_images"], 1)
        self.assertIn(("date_groups", {
            "orientation": "",
            "compared": "",
            "min_stars": 0,
            "folder": "",
            "flag": "",
            "date_taken": "",
            "file_type": "",
            "camera": "",
            "lens": "",
            "visible_thumb_size": "sm",
            "cache_root": "/tmp/cache-root",
            "id_filter": None,
            "text_query": "sun",
        }), calls)
        self.assertIn(("map_markers", {
            "orientation": "",
            "compared": "",
            "min_stars": 0,
            "folder": "",
            "flag": "",
            "date_taken": "",
            "file_type": "",
            "camera": "",
            "lens": "",
            "visible_thumb_size": "sm",
            "cache_root": "/tmp/cache-root",
            "id_filter": None,
            "text_query": "sun",
        }), calls)
        self.assertIn(("visible_pool", "sm", "/tmp/cache-root"), calls)
        self.assertTrue(
            any(
                call[0] == "get_rankings"
                and call[1]["sort"] == "elo"
                and call[1]["file_type"] == "jpg"
                and call[1]["text_query"] == ""
                for call in calls
            )
        )
        self.assertIn("/tmp/library.db", [key[0] for key in cache_keys])

    def test_core_query_constraints_own_text_resolution_with_runtime_facades(self):
        app_factory = importlib.import_module("core.app_factory")
        constraints = importlib.import_module("core.query_constraints")
        search_service = importlib.import_module("search_service")

        with open(os.path.join(os.path.dirname(__file__), "core", "query_constraints.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        self.assertIsInstance(constraints._text_search_resolution_cache, dict)
        self.assertIsInstance(constraints._deep_search_query_record_cache, dict)
        self.assertFalse(hasattr(app_module, "_text_search_resolution_cache"))
        self.assertFalse(hasattr(app_module, "_deep_search_query_record_cache"))
        self.assertFalse(hasattr(app_module, "_deep_search_query_record_cache_ttl_seconds"))
        for name in (
            "_normalize_search_query",
            "_resolve_cached_deep_search",
            "_encode_text_with_config",
            "_start_search_model_load",
            "_search_constraint_active",
            "_intersect_image_id_filters",
            "_sync_query_constraint_compat_globals",
            "_record_deep_search_query",
            "_apply_metadata_search_ids",
        ):
            self.assertFalse(hasattr(app_module, name))
        self.assertTrue(callable(constraints.normalize_search_query))
        self.assertTrue(callable(constraints.resolve_cached_deep_search))
        self.assertTrue(callable(constraints.encode_text_with_config))
        self.assertTrue(callable(constraints.start_search_model_load))
        self.assertTrue(callable(constraints.search_constraint_active))
        self.assertTrue(callable(constraints.intersect_image_id_filters))
        self.assertTrue(callable(constraints.configure))
        self.assertTrue(callable(constraints.sync_configured_ttls))
        self.assertTrue(callable(constraints.record_configured_deep_search_query))
        self.assertTrue(callable(constraints.apply_configured_metadata_search_ids))
        self.assertTrue(callable(constraints.resolve_configured_text_search))
        self.assertTrue(callable(constraints.resolve_configured_library_constraints))
        self.assertTrue(callable(app_factory.configure_query_constraints))
        self.assertFalse(hasattr(app_module, "_resolve_text_search"))
        self.assertFalse(hasattr(app_module, "_resolve_library_constraints"))
        self.assertTrue(callable(app_module._runtime_services.resolve_text_search))
        self.assertTrue(callable(app_module._runtime_services.resolve_library_constraints))
        self.assertIs(search_service.normalize_search_query, constraints.normalize_search_query)
        self.assertIs(search_service.resolve_cached_deep_search, constraints.resolve_cached_deep_search)
        self.assertIs(search_service.encode_text_with_config, constraints.encode_text_with_config)
        self.assertTrue(callable(constraints._CONFIG.get("get_deep_search_query_embedding")))

        constraints._text_search_resolution_cache[("probe", False)] = {"data": {}, "expires": 1}
        constraints._deep_search_query_record_cache["probe"] = 1
        cache_events.invalidate_rankings_cache()
        self.assertFalse(constraints._text_search_resolution_cache)
        self.assertFalse(constraints._deep_search_query_record_cache)

        sentinel = object()
        old_embedding_provider = constraints._CONFIG.get("get_deep_search_query_embedding", sentinel)
        calls = []

        async def fake_get_deep_search_query_embedding(query, model_key):
            calls.append((query, model_key))
            return None

        try:
            constraints.configure(get_deep_search_query_embedding=fake_get_deep_search_query_embedding)
            result = asyncio.run(constraints.resolve_cached_deep_search("deep provider probe"))
        finally:
            if old_embedding_provider is sentinel:
                constraints._CONFIG.pop("get_deep_search_query_embedding", None)
            else:
                constraints._CONFIG["get_deep_search_query_embedding"] = old_embedding_provider

        self.assertIsNone(result)
        self.assertEqual(calls, [("deep provider probe", app_module.settings.deep_search_embedding_config()["model_key"])])

        old_config = dict(constraints._CONFIG)
        try:
            app_factory.configure_query_constraints(
                text_search_resolution_cache_ttl_seconds=lambda: 12.0,
                deep_search_query_record_cache_ttl_seconds=lambda: 34.0,
            )
            constraints.sync_configured_ttls()
            self.assertEqual(constraints._text_search_resolution_cache_ttl_seconds, 12.0)
            self.assertEqual(constraints._deep_search_query_record_cache_ttl_seconds, 34.0)
            self.assertTrue(callable(constraints._CONFIG.get("record_deep_search_query")))
            self.assertTrue(callable(constraints._CONFIG.get("metadata_search_image_ids")))
        finally:
            constraints._CONFIG.clear()
            constraints._CONFIG.update(old_config)
            constraints.sync_configured_ttls()

        old_db_deep_embedding = app_module.db.get_deep_search_query_embedding
        calls = []

        async def fake_db_get_deep_search_query_embedding(query, model_key):
            calls.append((query, model_key))
            return None

        try:
            app_module.db.get_deep_search_query_embedding = fake_db_get_deep_search_query_embedding
            result = asyncio.run(constraints.resolve_cached_deep_search("late bound probe"))
        finally:
            app_module.db.get_deep_search_query_embedding = old_db_deep_embedding

        self.assertIsNone(result)
        self.assertEqual(calls, [("late bound probe", app_module.settings.deep_search_embedding_config()["model_key"])])

    def test_core_text_search_ttl_provider_controls_resolution_cache(self):
        old_ttl = query_constraints._text_search_resolution_cache_ttl_seconds
        query_constraints.clear_text_search_caches()
        calls = {"record": 0, "deep": 0}

        async def fake_record(_query):
            calls["record"] += 1

        async def fake_resolve_deep(_query, *, allow_cold_load=True):
            self.assertTrue(allow_cold_load)
            calls["deep"] += 1
            return None

        async def run_probe():
            kwargs = {
                "record_query": fake_record,
                "resolve_deep_search": fake_resolve_deep,
                "extension_search_terms": set(),
                "fast_search_embedding_config": app_module.settings.fast_search_embedding_config,
                "get_settings": app_module.settings.get_settings,
            }
            await query_constraints.resolve_text_search("ttl probe", deep=True, **kwargs)
            await query_constraints.resolve_text_search("ttl probe", deep=True, **kwargs)

        try:
            query_constraints._text_search_resolution_cache_ttl_seconds = 0.0
            asyncio.run(run_probe())
            self.assertEqual(calls, {"record": 2, "deep": 2})
        finally:
            query_constraints._text_search_resolution_cache_ttl_seconds = old_ttl
            query_constraints.clear_text_search_caches()

    def test_core_deep_query_record_ttl_provider_controls_record_cache(self):
        old_ttl = query_constraints._deep_search_query_record_cache_ttl_seconds
        old_config = dict(query_constraints._CONFIG)
        query_constraints.clear_text_search_caches()
        calls = []

        async def fake_record(query):
            calls.append(query)

        async def run_probe():
            await query_constraints.record_configured_deep_search_query("ttl probe")
            await query_constraints.record_configured_deep_search_query("ttl probe")

        try:
            query_constraints.configure(
                record_deep_search_query=fake_record,
                extension_search_terms=set(),
                invalidate_ai_status_response_cache=lambda: None,
                invalidate_settings_response_cache=lambda: None,
            )
            query_constraints._deep_search_query_record_cache_ttl_seconds = 0.0
            asyncio.run(run_probe())
            self.assertEqual(calls, ["ttl probe", "ttl probe"])
        finally:
            query_constraints._deep_search_query_record_cache_ttl_seconds = old_ttl
            query_constraints._CONFIG.clear()
            query_constraints._CONFIG.update(old_config)
            query_constraints.clear_text_search_caches()

    def test_data_layer_modules_back_legacy_facades(self):
        connection = importlib.import_module("data.connection")
        schema = importlib.import_module("data.schema")
        catalog = importlib.import_module("data.repositories.catalog")
        cache_entries = importlib.import_module("data.repositories.cache_entries")
        embeddings = importlib.import_module("data.repositories.embeddings")
        filter_options = importlib.import_module("data.repositories.filter_options")
        images = importlib.import_module("data.repositories.images")
        metadata_search = importlib.import_module("data.repositories.metadata_search")
        people = importlib.import_module("data.repositories.people")
        ratings = importlib.import_module("data.repositories.ratings")
        rankings = importlib.import_module("data.repositories.rankings")
        stats = importlib.import_module("data.repositories.stats")

        self.assertTrue(callable(connection.open_async))
        self.assertTrue(callable(connection.open_sync))
        self.assertTrue(callable(cache_entries.cached_image_id_set))
        self.assertTrue(callable(cache_entries.cached_image_ids))
        self.assertTrue(callable(cache_entries.cached_image_id_set_cached))
        self.assertTrue(callable(cache_entries.cache_entry_count_cached))
        self.assertTrue(callable(cache_entries.invalidate_cached_image_ids_cache))
        self.assertTrue(callable(cache_entries.note_cached_image_ids_added))
        self.assertIs(db._cached_image_ids_cache, cache_entries._cached_image_ids_cache)
        self.assertIs(db._cache_entry_count_cache, cache_entries._cache_entry_count_cache)
        self.assertTrue(callable(embeddings.store_embeddings_batch))
        self.assertTrue(callable(embeddings.store_deep_search_query_embedding))
        self.assertTrue(callable(filter_options.filter_options))
        self.assertTrue(callable(filter_options.empty_filter_options))
        self.assertTrue(callable(filter_options.filter_options_cached))
        self.assertTrue(callable(filter_options.load_filter_options_uncached))
        self.assertTrue(callable(filter_options.invalidate_filter_options_cache))
        self.assertTrue(callable(filter_options.clear_filter_options_cache))
        self.assertIs(db._filter_options_cache, filter_options._filter_options_cache)
        self.assertEqual(
            db.FILTER_OPTIONS_CACHE_TTL_SECONDS,
            filter_options.FILTER_OPTIONS_CACHE_TTL_SECONDS,
        )
        self.assertTrue(callable(images.get_active_images_by_ids))
        self.assertTrue(callable(metadata_search.metadata_fts_query))
        self.assertTrue(callable(metadata_search.metadata_search_image_ids))
        self.assertTrue(callable(people.get_people_image_id_filter))
        self.assertTrue(callable(people._face_embedding_blob))
        self.assertTrue(callable(people._face_embedding_vector))
        self.assertTrue(callable(people.store_face_scan_result))
        self.assertTrue(callable(people.cluster_unassigned_faces))
        self.assertTrue(callable(people.get_people_review))
        self.assertIs(db._face_embedding_blob, people._face_embedding_blob)
        self.assertIs(db._face_embedding_vector, people._face_embedding_vector)
        self.assertTrue(callable(db.store_face_scan_result))
        self.assertTrue(callable(db.cluster_unassigned_faces))
        self.assertTrue(callable(db.get_people_review))
        self.assertTrue(callable(people.assign_face))
        self.assertTrue(callable(ratings.active_images_for_pairing))
        self.assertTrue(callable(ratings.visible_images_for_pairing))
        self.assertTrue(callable(ratings.visible_pairing_pool_counts))
        self.assertTrue(callable(ratings.visible_orientation_pairing_pool_counts))
        self.assertTrue(callable(ratings.load_past_matchups_for_image_ids))
        self.assertTrue(callable(ratings.record_active_comparison))
        self.assertTrue(callable(rankings.ranking_filter_parts))
        self.assertTrue(callable(rankings.rankings))
        self.assertTrue(callable(rankings.rankings_cached))
        self.assertTrue(callable(rankings.has_ranking_count_filters))
        self.assertTrue(callable(rankings.count_rankings_uncached))
        self.assertTrue(callable(rankings.count_rankings_uncached_with_visible_cache))
        self.assertTrue(callable(rankings.count_rankings_cached))
        self.assertTrue(callable(rankings.rankable_image_id_set))
        self.assertTrue(callable(rankings.rankable_image_id_set_cached))
        self.assertTrue(callable(rankings.invalidate_rankable_image_ids_cache))
        self.assertTrue(callable(rankings.ranking_count_cache_key))
        self.assertTrue(callable(rankings.facet_cache_key))
        self.assertTrue(callable(rankings.invalidate_ranking_count_cache))
        self.assertTrue(callable(rankings.invalidate_facet_caches))
        self.assertTrue(callable(rankings.invalidate_visible_facet_caches))
        self.assertTrue(callable(rankings.invalidate_rating_facet_caches))
        self.assertTrue(callable(rankings.invalidate_visible_cache_dependent_counts))
        self.assertTrue(callable(rankings.invalidate_rating_ranking_count_cache))
        self.assertIs(db._ranking_filter_parts, rankings.ranking_filter_parts)
        self.assertIs(db._ranking_index_for_query, rankings.ranking_index_for_query)
        self.assertIs(db._ranking_image_source, rankings.ranking_image_source)
        self.assertIs(db._ranking_count_image_source, rankings.ranking_count_image_source)
        self.assertIs(db._ranking_count_cache_key, rankings.ranking_count_cache_key)
        self.assertIs(db._facet_cache_key, rankings.facet_cache_key)
        self.assertIs(db._cache_scope_matches, rankings.cache_scope_matches)
        self.assertIs(db._ranking_count_cache, rankings._ranking_count_cache)
        self.assertIs(db._date_groups_cache, rankings._date_groups_cache)
        self.assertIs(db._date_groups_refreshing, rankings._date_groups_refreshing)
        self.assertIs(db._map_markers_cache, rankings._map_markers_cache)
        self.assertIs(db._rankable_image_ids_cache, rankings._rankable_image_ids_cache)
        self.assertEqual(db.RANKING_COUNT_CACHE_TTL_SECONDS, rankings.RANKING_COUNT_CACHE_TTL_SECONDS)
        self.assertEqual(db.FACET_CACHE_TTL_SECONDS, rankings.FACET_CACHE_TTL_SECONDS)
        self.assertTrue(callable(rankings.count_rankings_with_id_filter_on_conn))
        self.assertTrue(callable(rankings.date_groups))
        self.assertTrue(callable(rankings.date_groups_cached))
        self.assertTrue(callable(rankings.map_markers))
        self.assertTrue(callable(rankings.map_markers_cached))
        self.assertTrue(callable(rankings.empty_map_markers))
        self.assertTrue(callable(stats.catalog_image_counts))
        self.assertTrue(callable(stats.catalog_image_counts_cached))
        self.assertTrue(callable(stats.invalidate_catalog_image_counts_cache))
        self.assertIs(db._catalog_image_counts_cache, stats._catalog_image_counts_cache)
        self.assertTrue(callable(stats.full_stats))
        self.assertTrue(callable(stats.full_stats_cached))
        self.assertTrue(callable(stats.refresh_full_stats_cache))
        self.assertTrue(callable(stats.schedule_full_stats_refresh))
        self.assertTrue(callable(stats.invalidate_full_stats_cache))
        self.assertIs(db._stats_cache, stats._stats_cache)
        self.assertEqual(db.STATS_CACHE_TTL_SECONDS, stats.FULL_STATS_CACHE_TTL_SECONDS)
        self.assertTrue(callable(stats.browser_original_summary))
        self.assertTrue(callable(stats.cache_ahead_counts))
        self.assertEqual(db.SCHEMA_VERSION, schema.SCHEMA_VERSION)
        self.assertEqual(db.EXPECTED_EMBEDDING_DIM, schema.EXPECTED_EMBEDDING_DIM)
        self.assertIs(db.SCHEMA, schema.SCHEMA)
        self.assertTrue(callable(schema.prepare_existing_database_for_schema))
        self.assertTrue(callable(schema.ensure_compatibility_columns))
        self.assertTrue(callable(schema.ensure_compatibility_indexes))
        self.assertTrue(callable(schema.backfill_legacy_aspect_ratios))
        self.assertTrue(callable(schema.apply_schema_and_migrations))
        self.assertTrue(callable(schema.normalize_legacy_image_state))
        self.assertTrue(callable(schema.migrate_catalog_sources))
        self.assertTrue(callable(db._apply_schema_and_migrations))
        self.assertTrue(callable(db._normalize_legacy_image_state))
        self.assertTrue(callable(db._migrate_catalog_sources))
        self.assertIs(db._table_columns, schema.table_columns)
        self.assertIs(db._schema_is_current, schema.schema_is_current)
        self.assertIs(db._ensure_metadata_fts, schema.ensure_metadata_fts)
        self.assertIs(db._apply_schema_and_migrations, schema.apply_schema_and_migrations)
        self.assertIn(("aspect_ratio", "REAL DEFAULT NULL"), schema.IMAGE_COMPAT_COLUMNS)
        self.assertTrue(
            any("idx_comparisons_action_id" in sql for sql in schema.COMPAT_INDEX_SQL)
        )
        self.assertIs(db.normalize_source_path, catalog.normalize_source_path)
        self.assertIs(db.source_display_name, catalog.source_display_name)
        self.assertIs(db.active_source_join, catalog.active_source_join)
        self.assertIs(db.active_source_condition, catalog.active_source_condition)
        self.assertIs(db.active_image_condition, catalog.active_image_condition)
        self.assertIs(db._ensure_catalog_source, catalog.ensure_catalog_source_on_conn)
        self.assertIs(db._update_source_counts, catalog.update_source_counts_on_conn)
        self.assertIs(
            db._refresh_source_online_states_on_conn,
            catalog.refresh_source_online_states_on_conn,
        )
        self.assertEqual(db.normalize_source_path("."), catalog.normalize_source_path("."))
        self.assertEqual(db.active_source_condition("src"), catalog.active_source_condition("src"))
        self.assertTrue(callable(catalog.active_source_id_set_cached))
        self.assertTrue(callable(catalog.invalidate_active_source_ids_cache))
        self.assertIs(db._active_source_ids_cache, catalog._active_source_ids_cache)
        self.assertTrue(callable(catalog.catalog_sources_cached))
        self.assertTrue(callable(catalog.catalog_summary_cached))
        self.assertTrue(callable(catalog.catalog_light_summary_cached))
        self.assertTrue(callable(catalog.invalidate_catalog_cache))
        self.assertTrue(callable(catalog.invalidate_catalog_summary_cache))
        self.assertIs(db._catalog_sources_cache, catalog._catalog_sources_cache)
        self.assertIs(db._catalog_summary_cache, catalog._catalog_summary_cache)
        self.assertIs(db._catalog_light_summary_cache, catalog._catalog_light_summary_cache)
        self.assertEqual(db.CATALOG_CACHE_TTL_SECONDS, catalog.CATALOG_CACHE_TTL_SECONDS)
        self.assertTrue(callable(catalog.folder_source_rows))
        self.assertTrue(callable(catalog.folder_image_filepaths_by_source))
        self.assertIs(db._metadata_fts_query, metadata_search.metadata_fts_query)
        self.assertIs(db.parse_people_ids, people.parse_people_ids)
        self.assertIs(db._ensure_embedding_model_row, embeddings.ensure_embedding_model_row)
        self.assertEqual(db._metadata_fts_query('sun"set'), metadata_search.metadata_fts_query('sun"set'))

    def test_filter_options_facade_delegates_with_mutable_db_path(self):
        filter_options = importlib.import_module("data.repositories.filter_options")
        old_db_path = db.DB_PATH
        old_cached = filter_options.filter_options_cached
        old_uncached = filter_options.load_filter_options_uncached
        calls = []

        async def fake_cached(db_path, **kwargs):
            calls.append((
                "cached",
                db_path,
                kwargs["get_catalog_image_counts"],
                kwargs["get_active_source_id_set"],
                kwargs["ttl_seconds"],
            ))
            return {"source": "cached"}

        async def fake_uncached(db_path, **kwargs):
            calls.append((
                "uncached",
                db_path,
                kwargs["get_catalog_image_counts"],
                kwargs["get_active_source_id_set"],
                kwargs["ttl_seconds"],
            ))
            return {"source": "uncached"}

        try:
            filter_options.filter_options_cached = fake_cached
            filter_options.load_filter_options_uncached = fake_uncached

            db.DB_PATH = "/tmp/photoarchive-filter-options-cached.db"
            self.assertEqual(asyncio.run(db.get_filter_options()), {"source": "cached"})

            db.DB_PATH = "/tmp/photoarchive-filter-options-uncached.db"
            self.assertEqual(asyncio.run(db._load_filter_options_uncached()), {"source": "uncached"})
        finally:
            db.DB_PATH = old_db_path
            filter_options.filter_options_cached = old_cached
            filter_options.load_filter_options_uncached = old_uncached

        self.assertEqual(
            calls,
            [
                (
                    "cached",
                    "/tmp/photoarchive-filter-options-cached.db",
                    db.get_catalog_image_counts,
                    db.get_active_source_id_set,
                    db.FILTER_OPTIONS_CACHE_TTL_SECONDS,
                ),
                (
                    "uncached",
                    "/tmp/photoarchive-filter-options-uncached.db",
                    db.get_catalog_image_counts,
                    db.get_active_source_id_set,
                    db.FILTER_OPTIONS_CACHE_TTL_SECONDS,
                ),
            ],
        )

    def test_face_worker_uses_app_injected_people_scan_providers(self):
        face_worker = importlib.import_module("face_worker")

        with open(os.path.join(os.path.dirname(__file__), "face_worker.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        self.assertFalse(hasattr(app_module, "face_worker"))
        self.assertTrue(callable(face_worker.configure))
        self.assertTrue(callable(face_worker._count_images_needing_faces))
        self.assertTrue(callable(face_worker._get_images_needing_faces))
        self.assertTrue(callable(face_worker._store_face_scan_result))
        self.assertTrue(callable(face_worker._cluster_unassigned_faces))

        old_count = app_module.db.count_images_needing_faces
        old_get = app_module.db.get_images_needing_faces
        old_store = app_module.db.store_face_scan_result
        old_cluster = app_module.db.cluster_unassigned_faces
        calls = []

        async def fake_count(**kwargs):
            calls.append(("count", kwargs))
            return 7

        async def fake_get(**kwargs):
            calls.append(("get", kwargs))
            return [{"id": 3, "cache_path": "/tmp/cache.jpg"}]

        async def fake_store(**kwargs):
            calls.append(("store", kwargs))
            return {"ok": True}

        async def fake_cluster(**kwargs):
            calls.append(("cluster", kwargs))
            return {"assigned": 1}

        try:
            app_module.db.count_images_needing_faces = fake_count
            app_module.db.get_images_needing_faces = fake_get
            app_module.db.store_face_scan_result = fake_store
            app_module.db.cluster_unassigned_faces = fake_cluster

            self.assertEqual(
                asyncio.run(face_worker._count_images_needing_faces(model_id="m", cache_root="/cache")),
                7,
            )
            self.assertEqual(
                asyncio.run(face_worker._get_images_needing_faces(model_id="m", cache_root="/cache", limit=1)),
                [{"id": 3, "cache_path": "/tmp/cache.jpg"}],
            )
            self.assertEqual(
                asyncio.run(face_worker._store_face_scan_result(image_id=3, model_id="m", cache_path="/tmp/cache.jpg")),
                {"ok": True},
            )
            self.assertEqual(
                asyncio.run(face_worker._cluster_unassigned_faces(model_id="m")),
                {"assigned": 1},
            )
        finally:
            app_module.db.count_images_needing_faces = old_count
            app_module.db.get_images_needing_faces = old_get
            app_module.db.store_face_scan_result = old_store
            app_module.db.cluster_unassigned_faces = old_cluster

        self.assertEqual(
            calls,
            [
                ("count", {"model_id": "m", "cache_root": "/cache"}),
                ("get", {"model_id": "m", "cache_root": "/cache", "limit": 1}),
                ("store", {"image_id": 3, "model_id": "m", "cache_path": "/tmp/cache.jpg"}),
                ("cluster", {"model_id": "m"}),
            ],
        )

    def test_catalog_routes_use_injected_db_backed_providers(self):
        base_dir = os.path.dirname(__file__)
        catalog_routes = importlib.import_module("features.catalog.routes")
        catalog_repository = importlib.import_module("data.repositories.catalog")

        with open(os.path.join(base_dir, "features", "catalog", "routes.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        drained_names = (
            "start_scan",
            "scan_status",
            "scan_folder",
            "_scan_prefetch_on_batch",
            "_quick_browse_roots",
            "_folder_picker_start",
            "_folder_picker_commands",
            "_native_folder_picker_available",
            "_run_native_folder_picker",
            "api_catalog_folder_picker_status",
            "api_catalog_select_folder",
            "api_catalog_browse",
            "api_catalog_summary",
            "api_add_catalog_source",
            "api_rescan_catalog_source",
            "api_remove_catalog_source",
            "_invalidate_folders_cache",
            "_clear_folders_cache",
            "_add_folder_counts",
            "_parent_directory",
            "_build_source_level_folders_payload",
            "_build_folders_payload",
            "api_folders",
        )
        for name in drained_names:
            self.assertFalse(hasattr(app_module, name), name)

        config_names = (
            "_invalidate_pairing_cache",
            "_invalidate_cache_status_cache",
            "_db_path",
            "_get_recent_active_images",
            "_add_or_restore_source",
            "_get_scan_folder",
            "_get_catalog_summary",
            "_get_source",
            "_remove_source_keep_data",
            "_get_source_image_ids",
            "_purge_source_catalog_data",
            "_get_catalog_image_counts",
        )
        old_config = {name: getattr(catalog_routes, name) for name in config_names}
        old_folder_source_rows = catalog_repository.folder_source_rows
        old_folder_filepaths = catalog_repository.folder_image_filepaths_by_source
        old_prefetch = catalog_routes.thumbnails.prefetch_images
        old_purge_cache = catalog_routes.thumbnails.purge_image_cache
        old_folders_cache = dict(catalog_routes._folders_cache)
        old_folders_refreshing = set(catalog_routes._folders_refreshing)
        calls = []

        class JsonRequest:
            def __init__(self, payload):
                self._payload = payload

            async def json(self):
                return self._payload

        async def fake_recent_active_images(**kwargs):
            calls.append(("recent", kwargs))
            return [{"id": 9, "filepath": "/tmp/source/nine.jpg"}]

        async def fake_add_or_restore_source(path):
            calls.append(("add_source", path))
            return {"id": 5, "path": catalog_repository.normalize_source_path(path)}

        async def fake_get_scan_folder():
            calls.append(("scan_folder",))
            return "/tmp/scan-folder"

        async def fake_catalog_summary():
            calls.append(("summary",))
            return {"sources": [{"id": 5}], "stats": {"active_images": 2}}

        async def fake_get_source(source_id):
            calls.append(("get_source", source_id))
            return {"id": source_id, "path": source_root}

        async def fake_remove_source_keep_data(source_id):
            calls.append(("remove_keep", source_id))

        async def fake_get_source_image_ids(source_id):
            calls.append(("source_image_ids", source_id))
            return [10, 11]

        async def fake_purge_source_catalog_data(source_id):
            calls.append(("purge_source", source_id))
            return {"images_deleted": 2, "comparisons_deleted": 1}

        async def fake_catalog_image_counts():
            calls.append(("counts",))
            return {"active_images": 2}

        async def fake_prefetch_images(rows, tier, **kwargs):
            calls.append(("prefetch", [row["id"] for row in rows], tier, kwargs))
            return len(rows)

        def fake_purge_image_cache(image_ids):
            calls.append(("purge_cache", tuple(image_ids)))
            return {"purged": len(image_ids)}

        def fake_folder_source_rows(db_path):
            calls.append(("folder_sources", db_path))
            return [(5, source_root, 2)]

        def fake_folder_filepaths(db_path, source_ids):
            calls.append(("folder_paths", db_path, tuple(source_ids)))
            return {5: [os.path.join(source_root, "one.jpg"), os.path.join(source_root, "nested", "two.jpg")]}

        with tempfile.TemporaryDirectory() as source_root:
            try:
                catalog_routes.thumbnails.prefetch_images = fake_prefetch_images
                catalog_routes.thumbnails.purge_image_cache = fake_purge_image_cache
                catalog_repository.folder_source_rows = fake_folder_source_rows
                catalog_repository.folder_image_filepaths_by_source = fake_folder_filepaths
                catalog_routes.clear_folders_cache()
                catalog_routes.configure(
                    invalidate_pairing_cache=lambda **kwargs: calls.append(("invalidate_pairing", kwargs)),
                    invalidate_cache_status_cache=lambda: calls.append(("invalidate_cache_status",)),
                    db_path=lambda: "/tmp/catalog-routes.db",
                    get_recent_active_images=fake_recent_active_images,
                    add_or_restore_source=fake_add_or_restore_source,
                    get_scan_folder=fake_get_scan_folder,
                    get_catalog_summary=fake_catalog_summary,
                    get_source=fake_get_source,
                    remove_source_keep_data=fake_remove_source_keep_data,
                    get_source_image_ids=fake_get_source_image_ids,
                    purge_source_catalog_data=fake_purge_source_catalog_data,
                    get_catalog_image_counts=fake_catalog_image_counts,
                )

                asyncio.run(catalog_routes.scan_prefetch_on_batch(1))
                scan_folder = asyncio.run(catalog_routes.scan_folder())
                summary = asyncio.run(catalog_routes.api_catalog_summary())
                added = asyncio.run(
                    catalog_routes.api_add_catalog_source(JsonRequest({"path": source_root, "scan": False}))
                )
                kept = asyncio.run(
                    catalog_routes.api_remove_catalog_source(5, JsonRequest({"mode": "keep"}))
                )
                purged = asyncio.run(
                    catalog_routes.api_remove_catalog_source(5, JsonRequest({"mode": "purge"}))
                )
                folders = asyncio.run(catalog_routes.api_folders())
            finally:
                catalog_routes.thumbnails.prefetch_images = old_prefetch
                catalog_routes.thumbnails.purge_image_cache = old_purge_cache
                catalog_repository.folder_source_rows = old_folder_source_rows
                catalog_repository.folder_image_filepaths_by_source = old_folder_filepaths
                catalog_routes._folders_cache.clear()
                catalog_routes._folders_cache.update(old_folders_cache)
                catalog_routes._folders_refreshing.clear()
                catalog_routes._folders_refreshing.update(old_folders_refreshing)
                for name, value in old_config.items():
                    setattr(catalog_routes, name, value)

        self.assertEqual(scan_folder, {"folder": "/tmp/scan-folder"})
        self.assertEqual(summary, {"sources": [{"id": 5}], "stats": {"active_images": 2}})
        self.assertTrue(added["ok"])
        self.assertFalse(added["scan_started"])
        self.assertEqual(kept["source_id"], 5)
        self.assertTrue(kept["kept_data"])
        self.assertEqual(purged["cache"], {"purged": 2})
        self.assertEqual(folders["root"], source_root)
        self.assertIn({"path": ".", "count": 1, "depth": 0}, folders["folders"])
        self.assertIn({"path": "nested", "count": 1, "depth": 0}, folders["folders"])
        self.assertIn(("recent", {"limit": 50}), calls)
        self.assertIn(("prefetch", [9], "lg", {"limit": 1}), calls)
        self.assertIn(("add_source", source_root), calls)
        self.assertIn(("remove_keep", 5), calls)
        self.assertIn(("source_image_ids", 5), calls)
        self.assertIn(("purge_source", 5), calls)
        self.assertIn(("folder_sources", "/tmp/catalog-routes.db"), calls)
        self.assertIn(("folder_paths", "/tmp/catalog-routes.db", (5,)), calls)
        self.assertGreaterEqual(calls.count(("summary",)), 4)
        self.assertEqual(calls.count(("invalidate_cache_status",)), 2)

    def test_scanner_uses_injected_catalog_write_providers(self):
        base_dir = os.path.dirname(__file__)
        scanner = importlib.import_module("scanner")

        with open(os.path.join(base_dir, "scanner.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        old_started = scanner._mark_source_scan_started
        old_insert = scanner._insert_images_batch
        old_finished = scanner._mark_source_scan_finished
        old_scan_state = dict(scanner.scan_state)
        calls = []
        self.assertTrue(callable(scanner._mark_source_scan_started))
        self.assertTrue(callable(scanner._insert_images_batch))
        self.assertTrue(callable(scanner._mark_source_scan_finished))

        async def fake_started(source_id):
            calls.append(("started", source_id))

        async def fake_insert(rows, **kwargs):
            calls.append(("insert", [row[0] for row in rows], kwargs))

        async def fake_finished(source_id, **kwargs):
            calls.append(("finished", source_id, kwargs))

        async def fake_on_batch(count):
            calls.append(("batch", count))

        try:
            scanner.configure(
                mark_source_scan_started=fake_started,
                insert_images_batch=fake_insert,
                mark_source_scan_finished=fake_finished,
            )
            with tempfile.TemporaryDirectory() as folder:
                source_bytes = {}
                for index in range(101):
                    subdir = folder if index % 2 == 0 else os.path.join(folder, "nested")
                    os.makedirs(subdir, exist_ok=True)
                    path = os.path.join(subdir, f"image-{index:03d}.jpg")
                    data = f"source-{index}".encode("ascii")
                    source_bytes[path] = data
                    with open(path, "wb") as fh:
                        fh.write(data)
                with open(os.path.join(folder, "ignored.txt"), "wb") as fh:
                    fh.write(b"ignored")

                asyncio.run(scanner.scan_folder(folder, source_id=7, on_batch=fake_on_batch))
                final_scan_state = dict(scanner.scan_state)
                final_source_bytes = {
                    path: self._read_bytes(path)
                    for path in source_bytes
                }
        finally:
            scanner._mark_source_scan_started = old_started
            scanner._insert_images_batch = old_insert
            scanner._mark_source_scan_finished = old_finished
            scanner.scan_state.clear()
            scanner.scan_state.update(old_scan_state)

        self.assertEqual(calls[0], ("started", 7))
        self.assertEqual(calls[1][0], "insert")
        self.assertEqual(len(calls[1][1]), 100)
        self.assertEqual(calls[1][2], {"source_id": 7})
        self.assertEqual(calls[2], ("batch", 100))
        self.assertEqual(calls[3][0], "insert")
        self.assertEqual(len(calls[3][1]), 1)
        self.assertEqual(calls[3][2], {"source_id": 7})
        self.assertEqual(calls[4], ("batch", 101))
        self.assertEqual(calls[5][0:2], ("finished", 7))
        self.assertEqual(len(calls[5][2]["seen_filepaths"]), 101)
        self.assertEqual(final_scan_state["scanning"], False)
        self.assertEqual(final_scan_state["done"], True)
        self.assertEqual(final_scan_state["error"], "")
        self.assertEqual(final_scan_state["folder"], folder)
        self.assertEqual(final_scan_state["source_id"], 7)
        self.assertEqual(final_scan_state["total_found"], 101)
        self.assertEqual(final_scan_state["total_inserted"], 101)
        self.assertEqual(final_source_bytes, source_bytes)

    @staticmethod
    def _read_bytes(path):
        with open(path, "rb") as fh:
            return fh.read()

    def test_ai_status_feature_owns_builder_without_app_facades(self):
        base_dir = os.path.dirname(__file__)
        ai_routes = importlib.import_module("features.ai.routes")

        with open(os.path.join(base_dir, "features", "ai", "routes.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        drained_names = (
            "ai_status",
            "api_pause_embeddings",
            "api_resume_embeddings",
            "api_install_ai_model",
            "build_ai_status",
            "_invalidate_ai_status_response_cache",
            "_ai_status_response_cache",
            "_copy_ai_status_response",
            "_ai_model_status_cache_key",
            "_ai_status_response_cache_ttl_seconds",
        )
        for name in drained_names:
            self.assertFalse(hasattr(app_module, name), name)
        self.assertTrue(callable(ai_routes._refresh_ai_status_response_cache))

        config_names = (
            "_invalidate_settings_response_cache",
            "_get_ai_status_counts",
            "_count_embeddings_for_model",
            "_get_deep_search_cache_status",
            "_list_deep_search_queries",
        )
        old_config = {name: getattr(ai_routes, name) for name in config_names}
        calls = []

        async def fake_get_ai_status_counts():
            calls.append(("counts",))
            return {
                "embedded": 4,
                "total_images": 10,
                "rated_images": 3,
                "direct_comparison_rows": 2,
                "ranking_signal_count": 5,
                "imported_ranking_without_history": 1,
            }

        async def fake_count_embeddings_for_model(config, **kwargs):
            calls.append(("count_embeddings", config["model_key"], kwargs))
            return 6

        async def fake_get_deep_search_cache_status(config, terms=None):
            calls.append(("deep_cache", config["model_key"], terms))
            return {"pending_queries": 7, "embedded_queries": 8}

        async def fake_list_deep_search_queries(config):
            calls.append(("deep_queries", config["model_key"]))
            return [{"query": "golden hour", "cached": True}]

        try:
            ai_routes.configure(
                invalidate_settings_response_cache=lambda: calls.append(("invalidate_settings",)),
                get_ai_status_counts=fake_get_ai_status_counts,
                count_embeddings_for_model=fake_count_embeddings_for_model,
                get_deep_search_cache_status=fake_get_deep_search_cache_status,
                list_deep_search_queries=fake_list_deep_search_queries,
            )
            ai_routes.invalidate_ai_status_response_cache()
            model_status = app_module.ai_models.get_model_status()
            response = asyncio.run(ai_routes.build_ai_status(model_status, force=True))
            for name in config_names:
                setattr(ai_routes, name, None)
            ai_routes.invalidate_ai_status_response_cache()
            with self.assertRaisesRegex(RuntimeError, "AI routes are not configured"):
                asyncio.run(ai_routes.build_ai_status(model_status, force=True))
        finally:
            for name, value in old_config.items():
                setattr(ai_routes, name, value)
            ai_routes.invalidate_ai_status_response_cache()

        deep_key = app_module.settings.deep_search_embedding_config()["model_key"]
        self.assertEqual(response["embedded"], 4)
        self.assertEqual(response["total_images"], 10)
        self.assertEqual(response["embedding_indexes"]["deep"]["embedded"], 6)
        self.assertEqual(response["embedding_indexes"]["deep"]["pending_queries"], 7)
        self.assertEqual(response["embedding_indexes"]["deep"]["embedded_queries"], 8)
        self.assertEqual(response["embedding_indexes"]["deep"]["queries"], [{"query": "golden hour", "cached": True}])
        self.assertIn(("counts",), calls)
        self.assertIn(("count_embeddings", deep_key, {"online_only": True}), calls)
        self.assertIn(("deep_cache", deep_key, None), calls)
        self.assertIn(("deep_queries", deep_key), calls)

    def test_people_routes_use_injected_people_facades(self):
        base_dir = os.path.dirname(__file__)
        people_routes = importlib.import_module("features.people.routes")

        with open(os.path.join(base_dir, "features", "people", "routes.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        drained_names = (
            "_people_status_payload",
            "api_people_status",
            "api_people",
            "api_people_face_thumb",
            "api_people_scan_pause",
            "api_people_scan_resume",
            "api_label_person",
            "api_merge_people",
            "api_reject_people_merge",
            "api_assign_face",
            "api_ignore_face",
            "api_ignore_person",
        )
        for name in drained_names:
            self.assertFalse(hasattr(app_module, name), name)

        config_names = (
            "_get_people_review",
            "_get_face_thumbnail_context",
            "_label_person",
            "_merge_people",
            "_reject_merge_suggestion",
            "_assign_face",
            "_ignore_face",
            "_ignore_person",
        )
        old_config = {name: getattr(people_routes, name) for name in config_names}
        calls = []

        class JsonRequest:
            def __init__(self, payload):
                self._payload = payload

            async def json(self):
                return self._payload

        class HeaderRequest:
            headers = {}

        async def fake_get_people_review(limit=24, **kwargs):
            calls.append(("review", limit, kwargs))
            return {"people": [], "counts": {"clusters": 2}}

        async def fake_get_face_thumbnail_context(face_id):
            calls.append(("face_thumb", face_id))
            return None

        async def fake_label_person(person_id, name):
            calls.append(("label", person_id, name))
            return {"ok": True, "person_id": person_id, "name": name}

        async def fake_merge_people(source_person_id, target_person_id):
            calls.append(("merge", source_person_id, target_person_id))
            return {"ok": True, "source_person_id": source_person_id, "target_person_id": target_person_id}

        async def fake_reject_merge_suggestion(suggestion_id):
            calls.append(("reject", suggestion_id))
            return {"ok": True, "suggestion_id": suggestion_id}

        async def fake_assign_face(face_id, person_id=None, name=""):
            calls.append(("assign", face_id, person_id, name))
            return {"ok": True, "face_id": face_id, "person_id": person_id, "name": name}

        async def fake_ignore_face(face_id):
            calls.append(("ignore_face", face_id))
            return {"ok": True, "face_id": face_id}

        async def fake_ignore_person(person_id):
            calls.append(("ignore_person", person_id))
            return {"ok": True, "person_id": person_id}

        try:
            people_routes.configure(
                get_people_review=fake_get_people_review,
                get_face_thumbnail_context=fake_get_face_thumbnail_context,
                label_person=fake_label_person,
                merge_people=fake_merge_people,
                reject_merge_suggestion=fake_reject_merge_suggestion,
                assign_face=fake_assign_face,
                ignore_face=fake_ignore_face,
                ignore_person=fake_ignore_person,
            )

            status = asyncio.run(people_routes.people_status_payload())
            people = asyncio.run(people_routes.api_people(limit=5))
            label = asyncio.run(people_routes.api_label_person(7, JsonRequest({"name": "Sean"})))
            merge = asyncio.run(
                people_routes.api_merge_people(
                    JsonRequest({"source_person_id": 3, "target_person_id": "4"})
                )
            )
            reject = asyncio.run(people_routes.api_reject_people_merge(9))
            assign = asyncio.run(
                people_routes.api_assign_face(11, JsonRequest({"person_id": "12", "name": "Ada"}))
            )
            ignored_face = asyncio.run(people_routes.api_ignore_face(13))
            ignored_person = asyncio.run(people_routes.api_ignore_person(14))
            missing_thumb = asyncio.run(people_routes.api_people_face_thumb(HeaderRequest(), 15))
        finally:
            for name, value in old_config.items():
                setattr(people_routes, name, value)

        self.assertEqual(status["counts"]["clusters"], 2)
        self.assertEqual(people["status"]["counts"]["clusters"], 2)
        self.assertEqual(label["name"], "Sean")
        self.assertEqual(merge["target_person_id"], 4)
        self.assertEqual(reject["suggestion_id"], 9)
        self.assertEqual(assign["person_id"], 12)
        self.assertEqual(ignored_face["face_id"], 13)
        self.assertEqual(ignored_person["person_id"], 14)
        self.assertEqual(missing_thumb.status_code, 404)
        self.assertIn(("review", 12, {}), calls)
        self.assertIn(("review", 5, {}), calls)
        self.assertIn(("label", 7, "Sean"), calls)
        self.assertIn(("merge", 3, 4), calls)
        self.assertIn(("reject", 9), calls)
        self.assertIn(("assign", 11, 12, "Ada"), calls)
        self.assertIn(("ignore_face", 13), calls)
        self.assertIn(("ignore_person", 14), calls)
        self.assertIn(("face_thumb", 15), calls)

    def test_cache_status_feature_owns_builder_without_app_facades(self):
        base_dir = os.path.dirname(__file__)
        cache_status = importlib.import_module("features.cache.status")
        stats_repository = importlib.import_module("data.repositories.stats")

        with open(os.path.join(base_dir, "features", "cache", "status.py"), encoding="utf-8") as fh:
            self.assertNotIn("import db", fh.read())

        drained_names = (
            "cache_status",
            "cache_pregen_start",
            "cache_pregen_stop",
            "cache_pregen_status",
            "api_clear_thumbnail_cache",
            "build_cache_status",
            "_invalidate_cache_status_cache",
            "_cache_status_cache",
            "_cache_status_refreshing",
            "_cache_status_cache_ttl_seconds",
            "_cache_status_ahead_limit",
            "_browser_original_count_cache",
            "_browser_original_count_cache_ttl_seconds",
            "_browser_original_summary",
            "_browser_original_count",
            "_cache_recommendations",
            "_cache_archive_estimates_from_status",
            "_copy_dict_of_dicts",
            "_system_resource_status",
            "_copy_cache_status_response",
            "_cache_status_ttl",
        )
        for name in drained_names:
            self.assertFalse(hasattr(app_module, name), name)

        config_names = (
            "_cache_root_provider",
            "_db_path",
            "_get_catalog_image_counts",
            "_expire_settings_response_cache",
        )
        old_config = {name: getattr(cache_status, name) for name in config_names}
        old_browser_original_summary = stats_repository.browser_original_summary
        old_cache_ahead_counts = stats_repository.cache_ahead_counts
        old_cache_stats = cache_status.thumbnails.cache_stats
        old_pregen_status = cache_status.thumbnails.get_pregen_status
        calls = []

        def fake_cache_stats():
            tiers = {
                tier: {
                    "count": 1,
                    "current_count": 1,
                    "bytes": 100,
                    "current_bytes": 100,
                    "budget_bytes": 1000,
                }
                for tier in cache_status.thumbnails.ALL_TIERS
            }
            return {
                "memory": {"used_bytes": 100, "limit_bytes": 1000},
                "disk": {"used_bytes": 200, "limit_bytes": 2000, "tiers": tiers},
            }

        def fake_pregen_status(active_total, cache, browser_total, archive_estimates):
            calls.append(("pregen", active_total, browser_total))
            return {
                "state": "idle",
                "enabled": False,
                "manual_pause": False,
                "preview": {"remaining": 0},
                "originals": {"remaining": 0},
            }

        async def fake_catalog_counts():
            calls.append(("counts",))
            return {
                "active_images": 3,
                "total_catalog_images": 5,
                "removed_images": 0,
                "offline_images": 0,
            }

        async def fake_browser_original_summary(
            db_path,
            *,
            catalog_counts,
            browser_extensions,
            is_browser_displayable_original,
        ):
            calls.append((
                "browser_originals",
                db_path,
                catalog_counts,
                tuple(browser_extensions),
                is_browser_displayable_original(".jpg"),
            ))
            return {"count": 2, "bytes": 2000}

        async def fake_cache_ahead_counts(db_path, *, ahead, cache_root, size):
            calls.append(("ahead", db_path, ahead, cache_root, size))
            return {"total": ahead, "cached": 1}

        try:
            stats_repository.browser_original_summary = fake_browser_original_summary
            stats_repository.cache_ahead_counts = fake_cache_ahead_counts
            cache_status.thumbnails.cache_stats = fake_cache_stats
            cache_status.thumbnails.get_pregen_status = fake_pregen_status
            cache_status.configure(
                cache_root=lambda: "/tmp/cache-root",
                db_path=lambda: "/tmp/cache-status.db",
                get_catalog_image_counts=fake_catalog_counts,
                expire_settings_response_cache=lambda: calls.append(("expire_settings",)),
            )
            cache_status.invalidate_cache_status_cache()
            result = asyncio.run(
                cache_status.build_cache_status(
                    ahead=3,
                    force=True,
                    cache_recommendations=lambda _cache, eligible, total, browser, estimates=None: {
                        "eligible_images": eligible,
                        "total_images": total,
                        "browser_original_images": browser,
                        "tiers": {},
                    },
                )
            )
        finally:
            stats_repository.browser_original_summary = old_browser_original_summary
            stats_repository.cache_ahead_counts = old_cache_ahead_counts
            cache_status.thumbnails.cache_stats = old_cache_stats
            cache_status.thumbnails.get_pregen_status = old_pregen_status
            for name, value in old_config.items():
                setattr(cache_status, name, value)
            cache_status.invalidate_cache_status_cache()

        self.assertEqual(result["eligible_images"], 3)
        self.assertEqual(result["browser_original_images"], 2)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["cached"], 1)
        self.assertIn(("pregen", 3, 2), calls)
        self.assertIn(("ahead", "/tmp/cache-status.db", 3, "/tmp/cache-root", "lg"), calls)
        self.assertTrue(
            any(call[0] == "browser_originals" and call[1] == "/tmp/cache-status.db" for call in calls)
        )

    def test_media_warm_feature_owns_scheduler_without_app_facades(self):
        media_warm = importlib.import_module("features.media.warm")

        self.assertIsInstance(media_warm._thumbnail_prefetch_inflight, set)
        self.assertIsInstance(media_warm._thumbnail_memory_warm_inflight, set)
        self.assertTrue(callable(media_warm.schedule_thumbnail_prefetch))
        self.assertTrue(callable(media_warm.schedule_cached_thumbnail_memory_warm))
        self.assertTrue(callable(media_warm.schedule_result_thumbnail_memory_warm))
        self.assertTrue(callable(media_warm.cached_image_ids))
        self.assertFalse(hasattr(app_module, "_thumbnail_prefetch_inflight"))
        self.assertFalse(hasattr(app_module, "_thumbnail_memory_warm_inflight"))
        self.assertFalse(hasattr(app_module, "_schedule_thumbnail_prefetch"))
        self.assertFalse(hasattr(app_module, "_schedule_cached_thumbnail_memory_warm"))
        self.assertFalse(hasattr(app_module, "_schedule_result_thumbnail_memory_warm"))
        self.assertFalse(hasattr(app_module, "_cached_image_ids"))

    def test_catalog_metadata_feature_owns_background_helpers_without_app_facades(self):
        base_dir = os.path.dirname(__file__)
        catalog_metadata = importlib.import_module("features.catalog.metadata")
        image_repository = importlib.import_module("data.repositories.images")

        with open(os.path.join(base_dir, "features", "catalog", "metadata.py"), encoding="utf-8") as fh:
            self.assertNotIn("import db", fh.read())

        self.assertTrue(callable(catalog_metadata.classify_orientations_background))
        self.assertTrue(callable(catalog_metadata.metadata_update_tuple))
        self.assertTrue(callable(catalog_metadata.scan_metadata_background))
        self.assertFalse(hasattr(app_module, "classify_orientations_background"))
        self.assertFalse(hasattr(app_module, "_metadata_update_tuple"))
        self.assertFalse(hasattr(app_module, "scan_metadata_background"))

        old_time = catalog_metadata.time.time
        try:
            catalog_metadata.time.time = lambda: 123.0
            row = catalog_metadata.metadata_update_tuple(
                42,
                {
                    "date_taken": "2024-01-02 03:04:05",
                    "camera_make": "Fuji",
                    "camera_model": "X-T5",
                    "lens": "35mm",
                    "file_ext": "jpg",
                    "file_size": 1000,
                    "file_modified_at": 99.0,
                    "width": "4000",
                    "height": "2000",
                    "latitude": 40.0,
                    "longitude": -90.0,
                },
            )
        finally:
            catalog_metadata.time.time = old_time

        self.assertEqual(row[0:9], (
            "2024-01-02 03:04:05",
            "Fuji",
            "X-T5",
            "35mm",
            "jpg",
            1000,
            99.0,
            "4000",
            "2000",
        ))
        self.assertEqual(row[9], 123.0)
        self.assertEqual(row[11:16], ("landscape", 2.0, 40.0, -90.0, 42))

        config_names = ("_db_path", "_invalidate_filter_options_cache")
        old_config = {name: getattr(catalog_metadata, name) for name in config_names}
        old_get_unclassified = image_repository.get_unclassified_images
        old_batch_orientations = image_repository.batch_set_orientations
        old_get_metadata = image_repository.get_images_needing_metadata
        old_batch_metadata = image_repository.batch_update_metadata
        calls = []

        async def fake_get_unclassified_images(db_path, limit=200):
            calls.append(("unclassified", db_path, limit))
            return [{"id": 1, "filepath": "/tmp/one.jpg"}]

        async def fake_batch_set_orientations(db_path, updates):
            calls.append(("orientations", db_path, tuple(updates)))

        async def fake_get_images_needing_metadata(db_path, limit=100, metadata_version=1):
            calls.append(("metadata_rows", db_path, limit, metadata_version))
            return [{"id": 2, "filepath": "/tmp/two.jpg"}]

        async def fake_batch_update_metadata(db_path, updates):
            calls.append(("metadata_updates", db_path, tuple(updates)))

        try:
            image_repository.get_unclassified_images = fake_get_unclassified_images
            image_repository.batch_set_orientations = fake_batch_set_orientations
            image_repository.get_images_needing_metadata = fake_get_images_needing_metadata
            image_repository.batch_update_metadata = fake_batch_update_metadata
            catalog_metadata.configure(
                db_path=lambda: "/tmp/catalog-metadata.db",
                invalidate_filter_options_cache=lambda: calls.append(("invalidate_filters",)),
            )

            unclassified = asyncio.run(catalog_metadata.get_unclassified_images(limit=12))
            asyncio.run(catalog_metadata.batch_set_orientations([("landscape", 1.5, 1)]))
            metadata_rows = asyncio.run(
                catalog_metadata.get_images_needing_metadata(limit=7, metadata_version=4)
            )
            asyncio.run(catalog_metadata.batch_update_metadata([("metadata", 2)]))
            asyncio.run(catalog_metadata.batch_update_metadata([]))
        finally:
            image_repository.get_unclassified_images = old_get_unclassified
            image_repository.batch_set_orientations = old_batch_orientations
            image_repository.get_images_needing_metadata = old_get_metadata
            image_repository.batch_update_metadata = old_batch_metadata
            for name, value in old_config.items():
                setattr(catalog_metadata, name, value)

        self.assertEqual(unclassified, [{"id": 1, "filepath": "/tmp/one.jpg"}])
        self.assertEqual(metadata_rows, [{"id": 2, "filepath": "/tmp/two.jpg"}])
        self.assertIn(("unclassified", "/tmp/catalog-metadata.db", 12), calls)
        self.assertIn(("orientations", "/tmp/catalog-metadata.db", (("landscape", 1.5, 1),)), calls)
        self.assertIn(("metadata_rows", "/tmp/catalog-metadata.db", 7, 4), calls)
        self.assertIn(("metadata_updates", "/tmp/catalog-metadata.db", (("metadata", 2),)), calls)
        self.assertEqual(calls.count(("invalidate_filters",)), 2)

    def test_search_service_owns_vector_helpers_without_app_facades(self):
        search_service = importlib.import_module("features.search.service")
        cache_events = importlib.import_module("core.cache_events")
        cache_entry_repository = importlib.import_module("data.repositories.cache_entries")
        image_repository = importlib.import_module("data.repositories.images")

        self.assertTrue(callable(search_service.visible_embedding_page))
        self.assertIsInstance(search_service._duplicates_cache, dict)
        self.assertIsInstance(search_service._collections_cache, dict)
        self.assertFalse(hasattr(app_module, "_visible_embedding_page"))
        self.assertFalse(hasattr(app_module, "_duplicates_cache"))
        self.assertFalse(hasattr(app_module, "_collections_cache"))
        self.assertIs(cache_events._duplicates_cache, search_service._duplicates_cache)
        self.assertIs(cache_events._collections_cache, search_service._collections_cache)

        search_service._duplicates_cache.update({"key": ("probe",), "data": {"pairs": []}})
        search_service._collections_cache.update({"key": ("probe",), "data": {"collections": []}})
        cache_events.invalidate_vector_derived_caches()
        self.assertIsNone(search_service._duplicates_cache["key"])
        self.assertIsNone(search_service._duplicates_cache["data"])
        self.assertIsNone(search_service._collections_cache["key"])
        self.assertIsNone(search_service._collections_cache["data"])

        old_cache_root = search_service._cache_root
        old_db_path = search_service._db_path
        old_cached_set = cache_entry_repository.cached_image_id_set_cached
        old_active_images = image_repository.get_active_images_by_ids
        calls = []

        async def fake_cached_image_id_set_cached(db_path, *, size, cache_root, ttl_seconds=None):
            calls.append(("cached", db_path, size, cache_root, ttl_seconds))
            return frozenset({2, 3})

        async def fake_get_active_images_by_ids(db_path, image_ids):
            calls.append(("images", db_path, tuple(image_ids)))
            return {
                2: {"id": 2, "filename": "two.jpg"},
                3: {"id": 3, "filename": "three.jpg"},
            }

        try:
            cache_entry_repository.cached_image_id_set_cached = fake_cached_image_id_set_cached
            image_repository.get_active_images_by_ids = fake_get_active_images_by_ids
            search_service.configure(cache_root=lambda: "/tmp/cache-root", db_path=lambda: "/tmp/photo.db")
            rows, visible_count, total = asyncio.run(
                search_service.visible_embedding_page([1, 2, 3], [0.1, 0.9, 0.8], 2)
            )
        finally:
            cache_entry_repository.cached_image_id_set_cached = old_cached_set
            image_repository.get_active_images_by_ids = old_active_images
            search_service._cache_root = old_cache_root
            search_service._db_path = old_db_path

        self.assertEqual([row["id"] for row in rows], [2, 3])
        self.assertEqual(visible_count, 2)
        self.assertEqual(total, 3)
        self.assertEqual(calls[0][:4], ("cached", "/tmp/photo.db", "sm", "/tmp/cache-root"))
        self.assertEqual(calls[1], ("images", "/tmp/photo.db", (2, 3)))

        search_service._duplicates_cache.update({"key": ("probe",), "data": {"pairs": []}})
        search_service._collections_cache.update({"key": ("probe",), "data": {"collections": []}})
        cache_events.invalidate_vector_derived_caches()
        self.assertIsNone(search_service._duplicates_cache["key"])
        self.assertIsNone(search_service._duplicates_cache["data"])
        self.assertIsNone(search_service._collections_cache["key"])
        self.assertIsNone(search_service._collections_cache["data"])

    def test_app_factory_owns_cache_event_wiring(self):
        app_factory = importlib.import_module("core.app_factory")
        cache_events = importlib.import_module("core.cache_events")
        compare_service = importlib.import_module("features.compare.service")
        elo_propagation = importlib.import_module("elo_propagation")
        library_service = importlib.import_module("features.library.service")
        search_service = importlib.import_module("features.search.service")

        config_names = (
            "_compare_service",
            "_library_service",
            "_query_constraints",
            "_invalidate_ai_status_response_cache",
            "_duplicates_cache",
            "_collections_cache",
            "_elo_propagation",
        )
        old_config = {name: getattr(cache_events, name) for name in config_names}
        old_embedding_listeners = list(app_module.db._embedding_batch_listeners)
        old_deep_query_listeners = list(app_module.db._deep_search_query_embedding_listeners)
        try:
            for name in config_names:
                setattr(cache_events, name, None)
            app_module.db._embedding_batch_listeners[:] = []
            app_module.db._deep_search_query_embedding_listeners[:] = []

            app_factory.create_base_app(base_dir=os.path.dirname(__file__))
            self.assertTrue(all(getattr(cache_events, name) is None for name in config_names))
            self.assertNotIn(cache_events.embedding_batch_stored, app_module.db._embedding_batch_listeners)
            self.assertNotIn(
                cache_events.deep_search_query_embedding_stored,
                app_module.db._deep_search_query_embedding_listeners,
            )

            app_factory.create_app(base_dir=os.path.dirname(__file__))
            self.assertFalse(hasattr(app_module, "_invalidate_rankings_cache"))
            self.assertFalse(hasattr(app_module, "_invalidate_vector_derived_caches"))
            self.assertFalse(hasattr(app_module, "_embedding_batch_stored"))
            self.assertIs(cache_events._compare_service, compare_service)
            self.assertIs(cache_events._library_service, library_service)
            self.assertIs(cache_events._query_constraints, query_constraints)
            self.assertIs(cache_events._duplicates_cache, search_service._duplicates_cache)
            self.assertIs(cache_events._collections_cache, search_service._collections_cache)
            self.assertIs(cache_events._elo_propagation, elo_propagation)
            self.assertIn(cache_events.embedding_batch_stored, app_module.db._embedding_batch_listeners)
            self.assertIn(
                cache_events.deep_search_query_embedding_stored,
                app_module.db._deep_search_query_embedding_listeners,
            )
        finally:
            for name, value in old_config.items():
                setattr(cache_events, name, value)
            app_module.db._embedding_batch_listeners[:] = old_embedding_listeners
            app_module.db._deep_search_query_embedding_listeners[:] = old_deep_query_listeners

    def test_search_routes_use_injected_db_backed_providers(self):
        base_dir = os.path.dirname(__file__)
        search_routes = importlib.import_module("features.search.routes")

        with open(os.path.join(base_dir, "features", "search", "routes.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        drained_names = (
            "api_search",
            "api_similar",
            "api_duplicates",
            "api_exif",
            "api_collections",
            "_exif_cache",
        )
        for name in drained_names:
            self.assertFalse(hasattr(app_module, name), name)

        config_names = (
            "_api_rankings",
            "_visible_embedding_page",
            "_cached_image_ids",
            "_metadata_update_tuple",
            "_invalidate_pairing_cache",
            "_normalize_search_query",
            "_clamp_int",
            "_visibility_counts",
            "_active_embedding_model_key",
            "_db_signature",
            "_get_active_source_id_set",
            "_get_active_images_by_ids",
            "_get_image_by_id",
            "_batch_update_metadata",
            "_duplicates_cache",
            "_collections_cache",
        )
        old_config = {name: getattr(search_routes, name) for name in config_names}
        old_extract = search_routes.photo_metadata.extract_image_metadata
        old_exif_cache = dict(search_routes._exif_cache)
        calls = []

        async def fake_api_rankings(**kwargs):
            calls.append(("rankings", kwargs))
            return {"images": [], "visible_images": 0, "total_images": 0}

        async def fake_visible_embedding_page(*args, **kwargs):
            calls.append(("visible_embedding", args, kwargs))
            return [], 0, 0

        async def fake_cached_image_ids(image_ids, tier):
            calls.append(("cached", tuple(image_ids), tier))
            return set()

        async def fake_get_active_source_id_set():
            calls.append(("active_sources",))
            return frozenset()

        async def fake_get_active_images_by_ids(image_ids):
            calls.append(("active_images", tuple(image_ids)))
            return {}

        async def fake_get_image_by_id(image_id):
            calls.append(("image", image_id))
            return {
                "id": image_id,
                "filepath": "/tmp/source.jpg",
                "date_taken": "2024-01-02",
                "camera_make": "Fuji",
                "camera_model": "X-T5",
                "lens": "35mm",
                "file_ext": "jpg",
                "file_size": 123,
                "file_modified_at": 99.0,
                "latitude": 40.0,
                "longitude": -90.0,
                "width": 4000,
                "height": 2000,
            }

        async def fake_batch_update_metadata(updates):
            calls.append(("metadata_updates", tuple(updates)))

        try:
            search_routes._exif_cache.clear()
            search_routes.photo_metadata.extract_image_metadata = lambda _path: {"filepath": "/tmp/source.jpg"}
            search_routes.configure(
                api_rankings=fake_api_rankings,
                visible_embedding_page=fake_visible_embedding_page,
                cached_image_ids=fake_cached_image_ids,
                metadata_update_tuple=lambda image_id, exif: ("metadata", image_id, exif.get("camera_make")),
                invalidate_pairing_cache=lambda **kwargs: calls.append(("invalidate", kwargs)),
                normalize_search_query=lambda value: " ".join(str(value or "").split()),
                clamp_int=lambda value, default, minimum, maximum: max(minimum, min(int(value or default), maximum)),
                visibility_counts=lambda total, visible: {"total_images": total, "visible_images": visible},
                active_embedding_model_key=lambda: "model-key",
                db_signature=lambda: "/tmp/search.db",
                get_active_source_id_set=fake_get_active_source_id_set,
                get_active_images_by_ids=fake_get_active_images_by_ids,
                get_image_by_id=fake_get_image_by_id,
                batch_update_metadata=fake_batch_update_metadata,
                duplicates_cache={"key": None, "data": None},
                collections_cache={"key": None, "data": None},
            )

            search = asyncio.run(search_routes.api_search(q="  sunset   sky  ", limit=7))
            exif = asyncio.run(search_routes.api_exif(42))
            duplicates = asyncio.run(search_routes.api_duplicates())
            collections = asyncio.run(search_routes.api_collections())
        finally:
            search_routes.photo_metadata.extract_image_metadata = old_extract
            search_routes._exif_cache.clear()
            search_routes._exif_cache.update(old_exif_cache)
            for name, value in old_config.items():
                setattr(search_routes, name, value)

        self.assertEqual(search["query"], "sunset sky")
        self.assertEqual(exif["exif"]["camera_make"], "Fuji")
        self.assertEqual(duplicates, {"pairs": [], "visible_pairs": 0, "total_pairs": 0, "hidden_pending_thumbnails": 0})
        self.assertEqual(collections, {"collections": []})
        self.assertIn(("rankings", {
            "limit": 7,
            "offset": 0,
            "sort": "similarity",
            "orientation": "",
            "compared": "",
            "min_stars": 0,
            "folder": "",
            "flag": "",
            "date_taken": "",
            "file_type": "",
            "camera": "",
            "lens": "",
            "q": "sunset sky",
            "deep": False,
            "people": "",
        }), calls)
        self.assertIn(("image", 42), calls)
        self.assertIn(("metadata_updates", (("metadata", 42, "Fuji"),)), calls)
        self.assertIn(("invalidate", {}), calls)
        self.assertEqual(calls.count(("active_sources",)), 2)

    def test_export_routes_use_repositories_with_injected_db_path(self):
        app_factory = importlib.import_module("core.app_factory")
        export_routes = importlib.import_module("features.export.routes")
        image_repository = importlib.import_module("data.repositories.images")
        ranking_repository = importlib.import_module("data.repositories.rankings")
        stats_repository = importlib.import_module("data.repositories.stats")

        old_resolve = export_routes._resolve_library_constraints
        old_db_path = export_routes._db_path
        old_get_images = image_repository.get_images_by_ids
        old_rankings = ranking_repository.rankings
        old_catalog_counts = stats_repository.catalog_image_counts_cached
        calls = []

        async def fake_resolve(q, *, people="", deep=False):
            calls.append(("resolve", q, people, deep))
            return {"id_filter": {1, 2}, "text_query": q}

        async def fake_get_images_by_ids(db_path, image_ids):
            calls.append(("images", db_path, tuple(image_ids)))
            return {
                1: {"id": 1, "filename": "one.jpg"},
                2: {"id": 2, "filename": "two.jpg"},
            }

        async def fake_catalog_image_counts_cached(db_path):
            calls.append(("counts", db_path))
            return {"active_images": 2, "total_catalog_images": 2, "removed_images": 0}

        async def fake_rankings(db_path, *, catalog_counts, limit, offset, sort, id_filter, text_query, **filters):
            calls.append((
                "rankings",
                db_path,
                catalog_counts,
                limit,
                offset,
                sort,
                frozenset(id_filter),
                text_query,
                filters,
            ))
            return [{"id": 2, "filename": "two.jpg"}]

        try:
            image_repository.get_images_by_ids = fake_get_images_by_ids
            ranking_repository.rankings = fake_rankings
            stats_repository.catalog_image_counts_cached = fake_catalog_image_counts_cached
            app_factory.configure_export_routes(
                resolve_library_constraints=fake_resolve,
                db_path=lambda: "/tmp/export.db",
            )

            requested = asyncio.run(
                export_routes._get_export_images(
                    ids="2,1",
                    limit=10,
                    sort="elo",
                    orientation="",
                    compared="",
                    min_stars=0,
                    folder="",
                    flag="",
                    date_taken="",
                    file_type="",
                    camera="",
                    lens="",
                    q="",
                    deep=False,
                    people="",
                )
            )
            ranked = asyncio.run(
                export_routes._get_export_images(
                    ids="",
                    limit=5,
                    sort="similarity",
                    orientation="landscape",
                    compared="",
                    min_stars=0,
                    folder="",
                    flag="picked",
                    date_taken="",
                    file_type="jpg",
                    camera="",
                    lens="",
                    q="sunset",
                    deep=True,
                    people="7",
                )
            )
        finally:
            image_repository.get_images_by_ids = old_get_images
            ranking_repository.rankings = old_rankings
            stats_repository.catalog_image_counts_cached = old_catalog_counts
            export_routes._resolve_library_constraints = old_resolve
            export_routes._db_path = old_db_path

        self.assertEqual([row["id"] for row in requested], [2, 1])
        self.assertEqual([row["id"] for row in ranked], [2])
        self.assertEqual(calls[0], ("images", "/tmp/export.db", (2, 1)))
        self.assertEqual(calls[1], ("resolve", "sunset", "7", True))
        self.assertEqual(calls[2], ("counts", "/tmp/export.db"))
        self.assertEqual(calls[3][0:8], (
            "rankings",
            "/tmp/export.db",
            {"active_images": 2, "total_catalog_images": 2, "removed_images": 0},
            5,
            0,
            "elo",
            frozenset({1, 2}),
            "sunset",
        ))
        self.assertEqual(calls[3][8]["orientation"], "landscape")
        self.assertEqual(calls[3][8]["flag"], "picked")
        self.assertEqual(calls[3][8]["file_type"], "jpg")

    def test_media_routes_use_image_repository_with_injected_db_path(self):
        base_dir = os.path.dirname(__file__)
        media_routes = importlib.import_module("features.media.routes")
        image_repository = importlib.import_module("data.repositories.images")

        with open(os.path.join(base_dir, "features", "media", "routes.py"), encoding="utf-8") as fh:
            self.assertNotIn("import db", fh.read())

        drained_names = (
            "serve_thumbnail",
            "serve_full_image",
            "_image_media_status_payload",
            "image_media_status",
            "images_media_status",
            "warm_images",
        )
        for name in drained_names:
            self.assertFalse(hasattr(app_module, name), name)

        old_db_path = media_routes._db_path
        old_cached_image_ids = media_routes._cached_image_ids
        old_memory_warm = media_routes._schedule_cached_thumbnail_memory_warm
        old_get_image = image_repository.get_image_by_id
        old_active_images = image_repository.get_active_images_by_ids
        old_memory_get = media_routes.thumbnails._memory_get_entry_fast
        old_path_entry = media_routes.thumbnails.fast_disk_path_entry
        old_read_entry = media_routes.thumbnails.fast_disk_read_entry
        old_get_thumbnail = media_routes.thumbnails.get_thumbnail
        old_response_headers = media_routes.thumbnails.response_headers
        old_prefetch_images = media_routes.thumbnails.prefetch_images
        calls = []

        class HeaderRequest:
            headers = {}

        class JsonRequest:
            async def json(self):
                return {"tiers": {"md": [11]}}

        async def fake_cached_image_ids(_ids, _tier):
            return set()

        def fake_memory_warm(rows, tier, *, limit, active_min_warm=0):
            calls.append(("memory_warm", [row["id"] for row in rows], tier, limit, active_min_warm))

        async def fake_get_image_by_id(db_path, image_id):
            calls.append(("image", db_path, image_id))
            return {"id": image_id, "filepath": f"/tmp/source-{image_id}.dng"}

        async def fake_get_active_images_by_ids(db_path, image_ids):
            calls.append(("active", db_path, tuple(image_ids)))
            return {11: {"id": 11, "filepath": "/tmp/source-11.jpg"}}

        async def fake_get_thumbnail(_filepath, _size, _image_id):
            return b"jpeg"

        async def fake_prefetch_images(rows, tier, **_kwargs):
            calls.append(("prefetch", [row["id"] for row in rows], tier))
            return len(rows)

        try:
            image_repository.get_image_by_id = fake_get_image_by_id
            image_repository.get_active_images_by_ids = fake_get_active_images_by_ids
            media_routes.thumbnails._memory_get_entry_fast = lambda *_args, **_kwargs: None
            media_routes.thumbnails.fast_disk_path_entry = lambda *_args, **_kwargs: None
            media_routes.thumbnails.fast_disk_read_entry = lambda *_args, **_kwargs: None
            media_routes.thumbnails.get_thumbnail = fake_get_thumbnail
            media_routes.thumbnails.response_headers = lambda *_args, **_kwargs: {"ETag": '"repo"'}
            media_routes.thumbnails.prefetch_images = fake_prefetch_images
            media_routes.configure(
                cached_image_ids=fake_cached_image_ids,
                schedule_cached_thumbnail_memory_warm=fake_memory_warm,
                db_path=lambda: "/tmp/media.db",
            )

            thumb_response = asyncio.run(media_routes.serve_thumbnail(HeaderRequest(), "sm", 7))
            full_response = asyncio.run(
                media_routes.serve_full_image(HeaderRequest(), 8, app_module.BackgroundTasks())
            )
            warm_response = asyncio.run(media_routes.warm_images(JsonRequest()))
        finally:
            image_repository.get_image_by_id = old_get_image
            image_repository.get_active_images_by_ids = old_active_images
            media_routes.thumbnails._memory_get_entry_fast = old_memory_get
            media_routes.thumbnails.fast_disk_path_entry = old_path_entry
            media_routes.thumbnails.fast_disk_read_entry = old_read_entry
            media_routes.thumbnails.get_thumbnail = old_get_thumbnail
            media_routes.thumbnails.response_headers = old_response_headers
            media_routes.thumbnails.prefetch_images = old_prefetch_images
            media_routes._db_path = old_db_path
            media_routes._cached_image_ids = old_cached_image_ids
            media_routes._schedule_cached_thumbnail_memory_warm = old_memory_warm

        self.assertEqual(thumb_response.status_code, 200)
        self.assertEqual(full_response.status_code, 200)
        self.assertEqual(warm_response, {"scheduled": {"md": 1}, "images": 1})
        self.assertIn(("image", "/tmp/media.db", 7), calls)
        self.assertIn(("image", "/tmp/media.db", 8), calls)
        self.assertIn(("active", "/tmp/media.db", (11,)), calls)

    def test_settings_status_feature_owns_cache_without_app_facades(self):
        base_dir = os.path.dirname(__file__)
        settings_status = importlib.import_module("features.settings.status")
        catalog_repository = importlib.import_module("data.repositories.catalog")

        with open(os.path.join(base_dir, "features", "settings", "status.py"), encoding="utf-8") as fh:
            self.assertNotIn("import db", fh.read())

        drained_names = (
            "_build_settings_response",
            "_settings_response_cache",
            "_settings_response_cache_ttl_seconds",
            "_invalidate_settings_response_cache",
            "_expire_settings_response_cache",
            "_copy_settings_response",
            "_set_settings_response_refreshing",
            "_settings_response_refreshing",
        )
        for name in drained_names:
            self.assertFalse(hasattr(app_module, name), name)

        settings_status.set_settings_response_refreshing(True)
        try:
            self.assertTrue(settings_status.get_settings_response_refreshing())
        finally:
            settings_status.set_settings_response_refreshing(False)

        config_names = (
            "_build_cache_status",
            "_build_ai_status",
            "_people_status_payload",
            "_db_path",
            "_get_catalog_image_counts",
            "_refresh_source_online_states",
        )
        old_config = {name: getattr(settings_status, name) for name in config_names}
        old_catalog_light_summary = catalog_repository.catalog_light_summary_cached
        calls = []

        async def fake_cache_status(**kwargs):
            calls.append(("cache_status", kwargs))
            return {"ok": True}

        async def fake_ai_status(**kwargs):
            calls.append(("ai_status", sorted(kwargs)))
            return {"ready": True}

        async def fake_people_status():
            calls.append(("people_status",))
            return {"clusters": 0}

        async def fake_catalog_image_counts():
            calls.append(("counts",))
            return {"active_images": 2, "total_catalog_images": 3}

        async def fake_refresh_source_online_states():
            calls.append(("refresh_sources",))
            return False

        async def fake_catalog_light_summary_cached(
            db_path,
            *,
            get_catalog_image_counts,
            refresh_source_online_states,
            ttl_seconds=None,
        ):
            calls.append(("catalog", db_path, ttl_seconds))
            counts = await get_catalog_image_counts()
            refreshed = await refresh_source_online_states()
            return {"sources": [], "stats": counts, "refreshed": refreshed}

        try:
            catalog_repository.catalog_light_summary_cached = fake_catalog_light_summary_cached
            settings_status.configure(
                build_cache_status=fake_cache_status,
                build_ai_status=fake_ai_status,
                people_status_payload=fake_people_status,
                db_path=lambda: "/tmp/settings-status.db",
                get_catalog_image_counts=fake_catalog_image_counts,
                refresh_source_online_states=fake_refresh_source_online_states,
            )
            response = asyncio.run(settings_status.build_settings_response())
        finally:
            catalog_repository.catalog_light_summary_cached = old_catalog_light_summary
            for name, value in old_config.items():
                setattr(settings_status, name, value)

        self.assertEqual(
            response["catalog"],
            {
                "sources": [],
                "stats": {"active_images": 2, "total_catalog_images": 3},
                "refreshed": False,
            },
        )
        self.assertIn(("catalog", "/tmp/settings-status.db", None), calls)
        self.assertIn(("counts",), calls)
        self.assertIn(("refresh_sources",), calls)

    def test_settings_routes_use_repositories_with_injected_db_path(self):
        app_factory = importlib.import_module("core.app_factory")
        base_dir = os.path.dirname(__file__)
        settings_routes = importlib.import_module("features.settings.routes")
        catalog_repository = importlib.import_module("data.repositories.catalog")
        embedding_repository = importlib.import_module("data.repositories.embeddings")
        image_repository = importlib.import_module("data.repositories.images")

        with open(os.path.join(base_dir, "features", "settings", "routes.py"), encoding="utf-8") as fh:
            self.assertNotIn("import db", fh.read())

        drained_names = (
            "api_settings",
            "api_ui_settings",
            "api_set_image_flag",
            "api_batch_set_flag",
            "api_save_settings",
            "api_reset_settings",
        )
        for name in drained_names:
            self.assertFalse(hasattr(app_module, name), name)

        config_names = (
            "_settings_response_cache",
            "_settings_response_cache_ttl_seconds",
            "_build_settings_response",
            "_copy_settings_response",
            "_track_background_task",
            "_get_refreshing",
            "_set_refreshing",
            "_db_path",
            "_get_stats",
            "_refresh_source_online_states",
            "_build_cache_status",
            "_build_ai_status",
            "_people_status_payload",
            "_invalidate_image_flag_caches",
            "_invalidate_pairing_cache",
            "_invalidate_cache_status_cache",
            "_invalidate_ai_status_response_cache",
            "_invalidate_settings_response_cache",
            "_invalidate_rankings_cache",
            "_invalidate_vector_derived_caches",
        )
        old_config = {name: getattr(settings_routes, name) for name in config_names}
        old_get_image = image_repository.get_image_by_id
        old_set_flag = image_repository.set_image_flag
        old_batch_flags = image_repository.batch_set_image_flags
        old_sync_terms = embedding_repository.sync_deep_search_terms
        old_catalog_summary = catalog_repository.catalog_summary_cached
        calls = []

        class JsonRequest:
            def __init__(self, payload):
                self._payload = payload

            async def json(self):
                return self._payload

        async def fake_build():
            return {}

        async def fake_cache_status(**_kwargs):
            return {}

        async def fake_get_stats():
            calls.append(("stats",))
            return {"active_images": 2}

        async def fake_refresh_source_online_states():
            calls.append(("refresh_sources",))
            return False

        async def fake_get_image_by_id(db_path, image_id):
            calls.append(("get_image", db_path, image_id))
            return {"id": image_id}

        async def fake_set_image_flag(db_path, image_id, flag):
            calls.append(("set_flag", db_path, image_id, flag))

        async def fake_batch_set_image_flags(db_path, image_ids, flag, chunk_size=500):
            calls.append(("batch_flags", db_path, tuple(image_ids), flag, chunk_size))
            return len(image_ids)

        async def fake_sync_deep_search_terms(db_path, normalized_terms):
            calls.append(("sync_terms", db_path, tuple(normalized_terms)))

        async def fake_catalog_summary_cached(
            db_path,
            *,
            get_stats,
            refresh_source_online_states,
            ttl_seconds=None,
        ):
            calls.append(("catalog", db_path, ttl_seconds))
            return {
                "sources": [],
                "stats": await get_stats(),
                "refreshed": await refresh_source_online_states(),
            }

        def fake_invalidate_image_flag_caches():
            calls.append(("flag_caches",))

        def fake_invalidate_pairing_cache(*, matchups=False):
            calls.append(("pairing", matchups))

        try:
            image_repository.get_image_by_id = fake_get_image_by_id
            image_repository.set_image_flag = fake_set_image_flag
            image_repository.batch_set_image_flags = fake_batch_set_image_flags
            embedding_repository.sync_deep_search_terms = fake_sync_deep_search_terms
            catalog_repository.catalog_summary_cached = fake_catalog_summary_cached
            app_factory.configure_settings_routes(
                settings_response_cache={},
                settings_response_cache_ttl_seconds=lambda: 1.0,
                build_settings_response=fake_build,
                copy_settings_response=lambda payload: dict(payload),
                track_background_task=lambda task: calls.append(("track", task)),
                get_refreshing=lambda: False,
                set_refreshing=lambda value: calls.append(("refreshing", value)),
                db_path=lambda: "/tmp/settings.db",
                get_stats=fake_get_stats,
                refresh_source_online_states=fake_refresh_source_online_states,
                build_cache_status=fake_cache_status,
                build_ai_status=fake_build,
                people_status_payload=fake_build,
                invalidate_image_flag_caches=fake_invalidate_image_flag_caches,
                invalidate_pairing_cache=fake_invalidate_pairing_cache,
                invalidate_cache_status_cache=lambda: calls.append(("cache_status",)),
                invalidate_ai_status_response_cache=lambda: calls.append(("ai_status",)),
                invalidate_settings_response_cache=lambda: calls.append(("settings_status",)),
                invalidate_rankings_cache=lambda: calls.append(("rankings",)),
                invalidate_vector_derived_caches=lambda: calls.append(("vectors",)),
            )

            single = asyncio.run(
                settings_routes.api_set_image_flag(7, JsonRequest({"flag": "picked"}))
            )
            batch = asyncio.run(
                settings_routes.api_batch_set_flag(
                    JsonRequest({"flag": "rejected", "image_ids": [8, "9", 8, "bad", 0]})
                )
            )
            asyncio.run(settings_routes._sync_deep_search_terms([
                "  Golden hour ",
                "golden hour",
                "Moon  rise",
            ]))
            catalog = asyncio.run(settings_routes._catalog_summary_payload())
        finally:
            image_repository.get_image_by_id = old_get_image
            image_repository.set_image_flag = old_set_flag
            image_repository.batch_set_image_flags = old_batch_flags
            embedding_repository.sync_deep_search_terms = old_sync_terms
            catalog_repository.catalog_summary_cached = old_catalog_summary
            for name, value in old_config.items():
                setattr(settings_routes, name, value)

        self.assertEqual(single, {"ok": True, "id": 7, "flag": "picked"})
        self.assertEqual(batch, {"ok": True, "count": 2, "flag": "rejected"})
        self.assertEqual(catalog, {
            "sources": [],
            "stats": {"active_images": 2},
            "refreshed": False,
        })
        self.assertIn(("get_image", "/tmp/settings.db", 7), calls)
        self.assertIn(("set_flag", "/tmp/settings.db", 7, "picked"), calls)
        self.assertIn(("batch_flags", "/tmp/settings.db", (8, 9), "rejected", 500), calls)
        self.assertIn(("sync_terms", "/tmp/settings.db", ("Golden hour", "Moon rise")), calls)
        self.assertIn(("catalog", "/tmp/settings.db", None), calls)
        self.assertEqual(calls.count(("flag_caches",)), 2)
        self.assertEqual(calls.count(("pairing", False)), 2)

    def test_smoke_mode_flag_is_opt_in(self):
        background = importlib.import_module("core.background")
        old_value = os.environ.pop("PHOTOARCHIVE_SMOKE_MODE", None)
        try:
            self.assertFalse(app_module._smoke_mode_enabled())
            self.assertFalse(background.smoke_mode_enabled())
            os.environ["PHOTOARCHIVE_SMOKE_MODE"] = "1"
            self.assertTrue(app_module._smoke_mode_enabled())
            self.assertTrue(background.smoke_mode_enabled())
            os.environ["PHOTOARCHIVE_SMOKE_MODE"] = "0"
            self.assertFalse(app_module._smoke_mode_enabled())
            self.assertFalse(background.smoke_mode_enabled())
        finally:
            if old_value is None:
                os.environ.pop("PHOTOARCHIVE_SMOKE_MODE", None)
            else:
                os.environ["PHOTOARCHIVE_SMOKE_MODE"] = old_value

    def test_compare_routes_use_injected_rating_facades(self):
        base_dir = os.path.dirname(__file__)
        app_factory = importlib.import_module("core.app_factory")
        compare_routes = importlib.import_module("features.compare.routes")

        with open(os.path.join(base_dir, "features", "compare", "routes.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        config_names = (
            "_patch_pairing_cache",
            "_add_past_matchups",
            "_schedule_pairing_propagation",
            "_invalidate_pairing_cache",
            "_mosaic_next_handler",
            "_compare_next_handler",
            "_record_active_mosaic_pick",
            "_record_active_comparison",
            "_undo_last_comparison",
        )
        old_config = {name: getattr(compare_routes, name) for name in config_names}
        calls = []
        failure_calls = []

        class JsonRequest:
            def __init__(self, payload):
                self._payload = payload

            async def json(self):
                return self._payload

        async def fake_record_mosaic_pick(picked_id, other_ids, action_id):
            calls.append(("mosaic", picked_id, tuple(other_ids), bool(action_id)))
            return {
                "ok": True,
                "new_elo": 1234.56,
                "pairs_recorded": len(other_ids),
                "loser_updates": [(image_id, 1190.0 + image_id) for image_id in other_ids],
            }

        async def fake_record_comparison(winner_id, loser_id, mode, **kwargs):
            calls.append(("compare", winner_id, loser_id, mode, bool(kwargs.get("action_id"))))
            return {"winner_elo": 1301.1, "loser_elo": 1198.9, "k": 24.0}

        async def fake_undo_last_comparison():
            calls.append(("undo",))
            return {"restored": 2}

        def fake_schedule(coro):
            calls.append(("schedule",))
            close = getattr(coro, "close", None)
            if close:
                close()

        try:
            app_factory.configure_compare_routes(
                patch_pairing_cache=lambda updates: calls.append(("patch", tuple(updates))),
                add_past_matchups=lambda matchups: calls.append(("matchups", tuple(matchups))),
                schedule_pairing_propagation=fake_schedule,
                invalidate_pairing_cache=lambda **kwargs: calls.append(("invalidate", kwargs)),
                record_active_mosaic_pick=fake_record_mosaic_pick,
                record_active_comparison=fake_record_comparison,
                undo_last_comparison=fake_undo_last_comparison,
            )

            mosaic = asyncio.run(
                compare_routes.mosaic_pick(JsonRequest({"winner_id": 7, "loser_ids": [8, "9"]}))
            )
            comparison = asyncio.run(
                compare_routes.submit_comparison(
                    JsonRequest({"winner_id": 10, "loser_id": "11", "mode": "topn"})
                )
            )
            undo = asyncio.run(compare_routes.compare_undo())

            async def fake_failed_mosaic_pick(picked_id, other_ids, action_id):
                failure_calls.append(("mosaic", picked_id, tuple(other_ids), bool(action_id)))
                return {"ok": False, "missing_ids": [99]}

            async def fake_failed_comparison(winner_id, loser_id, mode, **kwargs):
                failure_calls.append(("compare", winner_id, loser_id, mode, bool(kwargs.get("action_id"))))
                return None

            async def fake_empty_undo():
                failure_calls.append(("undo",))
                return None

            app_factory.configure_compare_routes(
                patch_pairing_cache=lambda updates: failure_calls.append(("patch", tuple(updates))),
                add_past_matchups=lambda matchups: failure_calls.append(("matchups", tuple(matchups))),
                schedule_pairing_propagation=lambda coro: failure_calls.append(("schedule", coro)),
                invalidate_pairing_cache=lambda **kwargs: failure_calls.append(("invalidate", kwargs)),
                record_active_mosaic_pick=fake_failed_mosaic_pick,
                record_active_comparison=fake_failed_comparison,
                undo_last_comparison=fake_empty_undo,
            )
            mosaic_failure = asyncio.run(
                compare_routes.mosaic_pick(JsonRequest({"winner_id": 7, "loser_ids": [99]}))
            )
            comparison_failure = asyncio.run(
                compare_routes.submit_comparison(JsonRequest({"winner_id": 10, "loser_id": 99}))
            )
            undo_failure = asyncio.run(compare_routes.compare_undo())
        finally:
            for name, value in old_config.items():
                setattr(compare_routes, name, value)

        self.assertEqual(mosaic["ok"], True)
        self.assertEqual(mosaic["pairs_recorded"], 2)
        self.assertEqual(comparison["winner_elo"], 1301.1)
        self.assertEqual(undo, {"ok": True, "restored": 2})
        self.assertIn(("mosaic", 7, (8, 9), True), calls)
        self.assertIn(("compare", 10, 11, "topn", True), calls)
        self.assertIn(("undo",), calls)
        self.assertIn(("matchups", ((7, 8), (7, 9))), calls)
        self.assertIn(("matchups", ((10, 11),)), calls)
        self.assertIn(("invalidate", {"matchups": True}), calls)
        self.assertEqual(calls.count(("schedule",)), 2)
        self.assertEqual(mosaic_failure.status_code, 400)
        self.assertIn(b"Images must exist", mosaic_failure.body)
        self.assertEqual(comparison_failure.status_code, 400)
        self.assertIn(b"Images must exist", comparison_failure.body)
        self.assertEqual(undo_failure.status_code, 400)
        self.assertIn(b"Nothing to undo", undo_failure.body)
        self.assertIn(("mosaic", 7, (99,), True), failure_calls)
        self.assertIn(("compare", 10, 99, "swiss", True), failure_calls)
        self.assertIn(("undo",), failure_calls)
        self.assertNotIn("patch", [call[0] for call in failure_calls])
        self.assertNotIn("matchups", [call[0] for call in failure_calls])
        self.assertNotIn("schedule", [call[0] for call in failure_calls])
        self.assertNotIn("invalidate", [call[0] for call in failure_calls])

    def test_core_background_owns_lifecycle_with_app_facades(self):
        app_factory = importlib.import_module("core.app_factory")
        background = importlib.import_module("core.background")

        with open(os.path.join(os.path.dirname(__file__), "core", "background.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        self.assertIs(app_module.background_runtime, background)
        self.assertIsInstance(app_module._background_task_tracker, background.BackgroundTaskTracker)
        self.assertIs(app_module._background_task_tracker, app_module._app_shell.background_task_tracker)
        self.assertIs(app_module._BACKGROUND_TASKS, app_module._app_shell.background_tasks)
        self.assertIs(app_module._IDLE_ACTIVITY_EXCLUDED_PATHS, background.IDLE_ACTIVITY_EXCLUDED_PATHS)
        self.assertTrue(callable(background.install_idle_activity_middleware))
        self.assertTrue(callable(app_module.track_idle_activity))
        self.assertIs(app_module.track_idle_activity, app_module._app_shell.idle_activity_middleware)
        self.assertTrue(callable(background.run_startup))
        self.assertTrue(callable(background.run_shutdown))
        self.assertTrue(callable(background.track_idle_activity))
        self.assertIsInstance(app_module._lifecycle, app_factory.AppLifecycleHandlers)
        self.assertIs(app_module._lifecycle, app_module._app_shell.lifecycle)
        self.assertIs(app_module.startup, app_module._lifecycle.startup)
        self.assertIs(app_module.shutdown, app_module._lifecycle.shutdown)
        self.assertIs(app_module.startup, app_module.app.router.on_startup[-1])
        self.assertIs(app_module.shutdown, app_module.app.router.on_shutdown[-1])
        factory_app = app_factory.create_app(base_dir=os.path.dirname(__file__))
        self.assertGreater(len(factory_app.router.on_startup), 0)
        self.assertGreater(len(factory_app.router.on_shutdown), 0)

    def test_core_app_shell_owns_page_and_dev_wiring(self):
        app_factory = importlib.import_module("core.app_factory")
        app_wiring = importlib.import_module("core.wiring")
        ai_routes = importlib.import_module("features.ai.routes")
        cache_routes = importlib.import_module("features.cache.routes")
        cache_status_service = importlib.import_module("features.cache.status")
        catalog_routes = importlib.import_module("features.catalog.routes")
        compare_routes = importlib.import_module("features.compare.routes")
        compare_service = importlib.import_module("features.compare.service")
        page_routes = importlib.import_module("features.pages.routes")
        people_routes = importlib.import_module("features.people.routes")
        dev_routes = importlib.import_module("features.dev.routes")
        export_routes = importlib.import_module("features.export.routes")
        library_routes = importlib.import_module("features.library.routes")
        library_service = importlib.import_module("features.library.service")
        media_routes = importlib.import_module("features.media.routes")
        search_service = importlib.import_module("features.search.service")
        search_routes = importlib.import_module("features.search.routes")
        settings_routes = importlib.import_module("features.settings.routes")
        settings_status = importlib.import_module("features.settings.status")
        catalog_metadata = importlib.import_module("features.catalog.metadata")
        data_providers = importlib.import_module("thumbnails.data_providers")
        embed_cache = importlib.import_module("embed_cache")
        embedding_worker = importlib.import_module("embedding_worker")
        elo_propagation = importlib.import_module("elo_propagation")
        face_worker = importlib.import_module("face_worker")
        helpers = importlib.import_module("helpers")
        scanner = importlib.import_module("scanner")

        self.assertIsInstance(app_module._app_shell, app_factory.AppShell)
        self.assertIs(app_module.app, app_module._app_shell.app)
        self.assertIs(app_module.templates, app_module._app_shell.templates)
        self.assertIs(app_module._static_assets, app_module._app_shell.static_assets)
        self.assertEqual(app_module._GIT_COMMIT, app_module._app_shell.git_commit)
        self.assertIs(app_module._static_version.__self__, app_module._app_shell)
        self.assertEqual(app_module._static_version.__func__, app_factory.AppShell.static_version)
        self.assertIs(app_module._template_context.__self__, app_module._app_shell)
        self.assertEqual(app_module._template_context.__func__, app_factory.AppShell.template_context)
        self.assertIs(app_module._warm_templates.__self__, app_module._app_shell)
        self.assertEqual(app_module._warm_templates.__func__, app_factory.AppShell.warm_templates)
        self.assertTrue(callable(app_module._app_shell.install_idle_activity_middleware))
        self.assertTrue(callable(app_module._app_shell.idle_activity_middleware))
        self.assertIs(
            app_module._IDLE_ACTIVITY_EXCLUDED_PATHS,
            app_module._app_shell.idle_activity_excluded_paths,
        )
        self.assertIs(app_module._track_background_task.__self__, app_module._app_shell)
        self.assertEqual(
            app_module._track_background_task.__func__,
            app_factory.AppShell.track_background_task,
        )
        self.assertTrue(callable(app_factory.create_base_app))
        self.assertTrue(callable(app_factory.create_app))
        self.assertTrue(callable(app_factory.create_app_shell))
        self.assertTrue(callable(app_factory.configure_compare_service))
        self.assertTrue(callable(app_factory.configure_compare_routes))
        self.assertTrue(callable(app_factory.configure_export_routes))
        self.assertTrue(callable(app_factory.configure_library_service))
        self.assertTrue(callable(app_factory.configure_query_constraints))
        self.assertTrue(callable(app_factory.configure_settings_routes))
        self.assertTrue(callable(app_wiring.configure_database_backed_providers))
        self.assertTrue(callable(app_wiring.configure_people_routes))
        self.assertTrue(callable(app_wiring.configure_status_media_search_providers))
        self.assertTrue(callable(app_wiring.configure_cache_events))
        self.assertTrue(callable(app_wiring.configure_catalog_routes))
        self.assertTrue(callable(app_wiring.configure_library_routes))
        self.assertTrue(callable(app_wiring.configure_search_routes))
        self.assertTrue(callable(app_wiring.configure_compare_service))
        self.assertTrue(callable(app_wiring.configure_compare_routes))
        self.assertTrue(callable(app_wiring.configure_export_routes))
        self.assertTrue(callable(app_wiring.configure_library_service))
        self.assertTrue(callable(app_wiring.configure_query_constraints))
        self.assertTrue(callable(app_wiring.configure_settings_routes))
        with open(os.path.join(os.path.dirname(__file__), "core", "app_factory.py"), encoding="utf-8") as fh:
            app_factory_source = fh.read()
        with open(os.path.join(os.path.dirname(__file__), "core", "wiring.py"), encoding="utf-8") as fh:
            app_wiring_source = fh.read()
        self.assertIn("wiring.configure_database_backed_providers()", app_factory_source)
        self.assertIn("wiring.configure_compare_service(", app_factory_source)
        self.assertNotIn("def _configure_database_backed_providers", app_factory_source)
        self.assertIn("def configure_database_backed_providers", app_wiring_source)
        self.assertIn("def configure_compare_service", app_wiring_source)

        people_config_names = (
            "_get_people_review",
            "_get_face_thumbnail_context",
            "_label_person",
            "_merge_people",
            "_reject_merge_suggestion",
            "_assign_face",
            "_ignore_face",
            "_ignore_person",
        )
        provider_groups = (
            (embed_cache, ("_active_embedding_model_key", "_db_path")),
            (
                embedding_worker,
                (
                    "_get_deep_search_cache_status",
                    "_get_catalog_image_counts",
                    "_count_embeddings_for_model",
                    "_get_unembedded_images",
                    "_get_pending_deep_search_queries",
                    "_store_deep_search_query_embedding",
                    "_store_embeddings_batch",
                    "_get_embedding_count",
                ),
            ),
            (
                elo_propagation,
                (
                    "_active_embedding_model_key",
                    "_get_active_images_by_ids",
                    "_get_db",
                    "_invalidate_rating_stats_cache",
                ),
            ),
            (
                face_worker,
                (
                    "_count_images_needing_faces",
                    "_get_images_needing_faces",
                    "_store_face_scan_result",
                    "_cluster_unassigned_faces",
                ),
            ),
            (
                data_providers,
                (
                    "_db_path",
                    "_get_db",
                    "_batch_set_orientations",
                    "_mark_image_missing_sync",
                    "_invalidate_cached_image_ids_cache",
                    "_note_cached_image_ids_added",
                ),
            ),
            (helpers, ("_cached_image_ids_provider", "_get_active_images_by_ids_provider")),
            (catalog_metadata, ("_db_path", "_invalidate_filter_options_cache")),
            (scanner, ("_mark_source_scan_started", "_insert_images_batch", "_mark_source_scan_finished")),
            (people_routes, people_config_names),
            (
                cache_status_service,
                (
                    "_cache_root_provider",
                    "_db_path",
                    "_get_catalog_image_counts",
                    "_expire_settings_response_cache",
                ),
            ),
            (search_service, ("_cache_root", "_db_path")),
            (media_routes, ("_cached_image_ids", "_schedule_cached_thumbnail_memory_warm", "_db_path")),
            (
                search_routes,
                (
                    "_api_rankings",
                    "_visible_embedding_page",
                    "_cached_image_ids",
                    "_metadata_update_tuple",
                    "_invalidate_pairing_cache",
                    "_normalize_search_query",
                    "_clamp_int",
                    "_visibility_counts",
                    "_active_embedding_model_key",
                    "_db_signature",
                    "_get_active_source_id_set",
                    "_get_active_images_by_ids",
                    "_get_image_by_id",
                    "_batch_update_metadata",
                ),
            ),
            (
                settings_status,
                (
                    "_build_cache_status",
                    "_build_ai_status",
                    "_people_status_payload",
                    "_db_path",
                    "_get_catalog_image_counts",
                    "_refresh_source_online_states",
                ),
            ),
            (cache_routes, ("_build_ai_status",)),
            (library_routes, ("_rankings_handler",)),
            (
                ai_routes,
                (
                    "_invalidate_settings_response_cache",
                    "_get_ai_status_counts",
                    "_count_embeddings_for_model",
                    "_get_deep_search_cache_status",
                    "_list_deep_search_queries",
                ),
            ),
            (
                compare_routes,
                (
                    "_patch_pairing_cache",
                    "_add_past_matchups",
                    "_schedule_pairing_propagation",
                    "_invalidate_pairing_cache",
                    "_mosaic_next_handler",
                    "_compare_next_handler",
                    "_record_active_mosaic_pick",
                    "_record_active_comparison",
                    "_undo_last_comparison",
                ),
            ),
            (
                compare_service,
                (
                    "_invalidate_rankings_cache",
                    "_invalidate_interaction_response_cache",
                    "_cache_root",
                    "_resolve_library_constraints",
                    "_schedule_thumbnail_prefetch",
                    "_schedule_cached_thumbnail_memory_warm",
                    "_db_signature",
                    "_get_active_images_for_pairing",
                    "_get_past_matchups",
                    "_get_visible_past_matchups",
                    "_get_past_matchups_for_image_ids",
                    "_get_active_images_by_ids",
                    "_get_visible_images_for_pairing",
                    "_get_visible_orientation_pairing_pool_counts",
                    "_count_rankings",
                    "_get_rankings",
                    "_get_visible_pairing_pool_counts",
                    "_get_top_images",
                ),
            ),
            (
                library_service,
                (
                    "_resolve_library_constraints",
                    "_cache_root",
                    "_clamp_int",
                    "_normalize_search_query",
                    "_schedule_thumbnail_prefetch",
                    "_schedule_result_thumbnail_memory_warm",
                    "_rankings_response_cache_ttl_seconds_provider",
                    "_extension_search_terms",
                    "_db_signature",
                    "_get_date_groups",
                    "_get_map_markers",
                    "_get_filter_options",
                    "_get_stats",
                    "_count_rankings",
                    "_get_rankings",
                    "_get_visible_pairing_pool_counts",
                ),
            ),
            (export_routes, ("_resolve_library_constraints", "_db_path")),
            (
                settings_routes,
                (
                    "_settings_response_cache_ttl_seconds",
                    "_build_settings_response",
                    "_copy_settings_response",
                    "_track_background_task",
                    "_get_refreshing",
                    "_set_refreshing",
                    "_db_path",
                    "_get_stats",
                    "_refresh_source_online_states",
                    "_build_cache_status",
                    "_build_ai_status",
                    "_people_status_payload",
                    "_invalidate_image_flag_caches",
                    "_invalidate_pairing_cache",
                    "_invalidate_cache_status_cache",
                    "_invalidate_ai_status_response_cache",
                    "_invalidate_settings_response_cache",
                    "_invalidate_rankings_cache",
                    "_invalidate_vector_derived_caches",
                ),
            ),
        )
        provider_value_groups = (
            (search_routes, ("_duplicates_cache", "_collections_cache")),
            (settings_routes, ("_settings_response_cache",)),
        )
        old_provider_config = {
            (module, name): getattr(module, name)
            for module, names in provider_groups + provider_value_groups
            for name in names
        }
        try:
            for module, names in provider_groups + provider_value_groups:
                for name in names:
                    setattr(module, name, None)
            base_app = app_factory.create_base_app(base_dir=os.path.dirname(__file__))
            self.assertTrue(
                all(
                    getattr(module, name) is None
                    for module, names in provider_groups + provider_value_groups
                    for name in names
                )
            )
            factory_app = app_factory.create_app(base_dir=os.path.dirname(__file__))
            shell_app = app_factory.create_app_shell(base_dir=os.path.dirname(__file__)).app
            self.assertTrue(
                all(callable(getattr(module, name)) for module, names in provider_groups for name in names)
            )
            self.assertTrue(
                all(getattr(module, name) is not None for module, names in provider_value_groups for name in names)
            )
        finally:
            for (module, name), value in old_provider_config.items():
                setattr(module, name, value)

        def route_contract(fastapi_app):
            return {
                (method, route.path)
                for route in fastapi_app.routes
                if isinstance(route, APIRoute)
                for method in route.methods
                if method not in {"HEAD", "OPTIONS"}
            }

        self.assertEqual(route_contract(base_app), set())
        self.assertEqual(route_contract(factory_app), route_contract(shell_app))
        self.assertEqual(route_contract(shell_app), route_contract(app_module.app))
        self.assertEqual(len(base_app.router.on_startup), 0)
        self.assertGreater(len(factory_app.router.on_startup), 0)
        self.assertGreater(len(factory_app.router.on_shutdown), 0)
        self.assertGreater(len(shell_app.router.on_startup), 0)
        self.assertGreater(len(shell_app.router.on_shutdown), 0)
        self.assertGreater(len(app_module.app.router.on_startup), 0)
        self.assertGreater(len(app_module.app.router.on_shutdown), 0)

        endpoints = {
            (method, route.path): route.endpoint
            for route in app_module.app.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        }
        self.assertIs(endpoints[("GET", "/")], page_routes.index)
        self.assertIs(endpoints[("GET", "/library")], page_routes.library_page)
        self.assertIs(endpoints[("GET", "/settings")], page_routes.settings_page)
        self.assertIs(endpoints[("GET", "/api/people/status")], people_routes.api_people_status)
        self.assertIs(endpoints[("GET", "/api/people")], people_routes.api_people)
        self.assertIs(endpoints[("POST", "/api/people/{person_id}/label")], people_routes.api_label_person)
        self.assertIs(endpoints[("GET", "/api/dev/status")], dev_routes.dev_status)
        self.assertIs(endpoints[("POST", "/api/scan")], catalog_routes.start_scan)
        self.assertIs(endpoints[("GET", "/api/mosaic/next")], compare_routes.mosaic_next)
        self.assertIs(endpoints[("GET", "/api/thumb/{size}/{image_id}")], media_routes.serve_thumbnail)
        self.assertIs(endpoints[("GET", "/api/rankings")], library_routes.api_rankings)
        self.assertIs(endpoints[("GET", "/api/export")], export_routes.export_rankings)
        self.assertIs(endpoints[("GET", "/api/search")], search_routes.api_search)
        self.assertIs(endpoints[("GET", "/api/settings")], settings_routes.api_settings)
        self.assertIs(endpoints[("GET", "/api/cache/status")], cache_routes.cache_status)
        self.assertIs(endpoints[("GET", "/api/ai/status")], ai_routes.ai_status)

    def test_smoke_mode_startup_skips_archive_initialization(self):
        old_value = os.environ.get("PHOTOARCHIVE_SMOKE_MODE")
        old_init_db = app_module.db.init_db
        old_configure = app_module.thumbnails.configure
        old_warm_templates = app_module._warm_templates
        calls = []

        async def fail_init_db():
            raise AssertionError("smoke startup should not initialize the archive database")

        def fail_configure(_settings):
            raise AssertionError("smoke startup should not configure thumbnail workers")

        try:
            os.environ["PHOTOARCHIVE_SMOKE_MODE"] = "1"
            app_module.db.init_db = fail_init_db
            app_module.thumbnails.configure = fail_configure
            app_module._warm_templates = lambda: calls.append("warm")

            asyncio.run(app_module.startup())

            self.assertEqual(calls, ["warm"])
        finally:
            if old_value is None:
                os.environ.pop("PHOTOARCHIVE_SMOKE_MODE", None)
            else:
                os.environ["PHOTOARCHIVE_SMOKE_MODE"] = old_value
            app_module.db.init_db = old_init_db
            app_module.thumbnails.configure = old_configure
            app_module._warm_templates = old_warm_templates

    def test_frontend_module_bootstrap_preserves_global_contract(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "templates", "base.html"), encoding="utf-8") as fh:
            base_template = fh.read()
        with open(os.path.join(base_dir, "static", "app.js"), encoding="utf-8") as fh:
            app_entry = fh.read()
        with open(os.path.join(base_dir, "static", "js", "bootstrap.js"), encoding="utf-8") as fh:
            bootstrap = fh.read()
        with open(os.path.join(base_dir, "static", "js", "legacy", "app.js"), encoding="utf-8") as fh:
            legacy = fh.read()
        with open(os.path.join(base_dir, "static", "js", "api.js"), encoding="utf-8") as fh:
            api_module = fh.read()
        with open(os.path.join(base_dir, "static", "js", "ai", "status.js"), encoding="utf-8") as fh:
            ai_status = fh.read()
        with open(os.path.join(base_dir, "static", "js", "ai", "poller.js"), encoding="utf-8") as fh:
            ai_poller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "catalog", "status.js"), encoding="utf-8") as fh:
            catalog_status = fh.read()
        with open(os.path.join(base_dir, "static", "js", "query_state.js"), encoding="utf-8") as fh:
            query_state = fh.read()
        with open(os.path.join(base_dir, "static", "js", "filters.js"), encoding="utf-8") as fh:
            filters_module = fh.read()
        with open(os.path.join(base_dir, "static", "js", "media_status.js"), encoding="utf-8") as fh:
            media_status = fh.read()
        with open(os.path.join(base_dir, "static", "js", "media_metadata.js"), encoding="utf-8") as fh:
            media_metadata = fh.read()
        with open(os.path.join(base_dir, "static", "js", "catalog", "sources.js"), encoding="utf-8") as fh:
            catalog_sources = fh.read()
        with open(os.path.join(base_dir, "static", "js", "catalog", "directory.js"), encoding="utf-8") as fh:
            catalog_directory = fh.read()
        with open(os.path.join(base_dir, "static", "js", "catalog", "browser.js"), encoding="utf-8") as fh:
            catalog_browser = fh.read()
        with open(os.path.join(base_dir, "static", "js", "catalog", "actions.js"), encoding="utf-8") as fh:
            catalog_actions = fh.read()
        with open(os.path.join(base_dir, "static", "js", "catalog", "controller.js"), encoding="utf-8") as fh:
            catalog_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "loupe", "tiers.js"), encoding="utf-8") as fh:
            loupe_tiers = fh.read()
        with open(os.path.join(base_dir, "static", "js", "loupe", "loading.js"), encoding="utf-8") as fh:
            loupe_loading = fh.read()
        with open(os.path.join(base_dir, "static", "js", "loupe", "status.js"), encoding="utf-8") as fh:
            loupe_status = fh.read()
        with open(os.path.join(base_dir, "static", "js", "loupe", "metadata.js"), encoding="utf-8") as fh:
            loupe_metadata = fh.read()
        with open(os.path.join(base_dir, "static", "js", "loupe", "navigation.js"), encoding="utf-8") as fh:
            loupe_navigation = fh.read()
        with open(os.path.join(base_dir, "static", "js", "loupe", "filmstrip.js"), encoding="utf-8") as fh:
            loupe_filmstrip = fh.read()
        with open(os.path.join(base_dir, "static", "js", "loupe", "focus.js"), encoding="utf-8") as fh:
            loupe_focus = fh.read()
        with open(os.path.join(base_dir, "static", "js", "loupe", "zoom.js"), encoding="utf-8") as fh:
            loupe_zoom = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "query.js"), encoding="utf-8") as fh:
            compare_query = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "navigation.js"), encoding="utf-8") as fh:
            compare_navigation = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "keyboard.js"), encoding="utf-8") as fh:
            compare_keyboard = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "images.js"), encoding="utf-8") as fh:
            compare_images = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "view.js"), encoding="utf-8") as fh:
            compare_view = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "mode_controller.js"), encoding="utf-8") as fh:
            compare_mode_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "action_controller.js"), encoding="utf-8") as fh:
            compare_action_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "mosaic_action_controller.js"), encoding="utf-8") as fh:
            compare_mosaic_action_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "mosaic_replacements.js"), encoding="utf-8") as fh:
            compare_mosaic_replacements = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "actions.js"), encoding="utf-8") as fh:
            compare_actions = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "propagation.js"), encoding="utf-8") as fh:
            compare_propagation = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "status_controller.js"), encoding="utf-8") as fh:
            compare_status_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "status.js"), encoding="utf-8") as fh:
            compare_status = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "mosaic.js"), encoding="utf-8") as fh:
            compare_mosaic = fh.read()
        with open(os.path.join(base_dir, "static", "js", "cache", "status.js"), encoding="utf-8") as fh:
            cache_status = fh.read()
        with open(os.path.join(base_dir, "static", "js", "cache", "guide.js"), encoding="utf-8") as fh:
            cache_guide = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "query.js"), encoding="utf-8") as fh:
            library_query = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "sort.js"), encoding="utf-8") as fh:
            library_sort = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "sort_controller.js"), encoding="utf-8") as fh:
            library_sort_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "search_state.js"), encoding="utf-8") as fh:
            library_search_state = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "search_controls.js"), encoding="utf-8") as fh:
            library_search_controls = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "search_controller.js"), encoding="utf-8") as fh:
            library_search_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "flags.js"), encoding="utf-8") as fh:
            library_flags = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "batch.js"), encoding="utf-8") as fh:
            library_batch = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "batch_controller.js"), encoding="utf-8") as fh:
            library_batch_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "filter_controller.js"), encoding="utf-8") as fh:
            library_filter_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "export", "actions.js"), encoding="utf-8") as fh:
            export_actions = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "similar.js"), encoding="utf-8") as fh:
            library_similar = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "display.js"), encoding="utf-8") as fh:
            library_display = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "shell.js"), encoding="utf-8") as fh:
            library_shell = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "date_scrubber.js"), encoding="utf-8") as fh:
            library_date_scrubber = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "navigation.js"), encoding="utf-8") as fh:
            library_navigation = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "map.js"), encoding="utf-8") as fh:
            library_map = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "map_controller.js"), encoding="utf-8") as fh:
            library_map_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "filters.js"), encoding="utf-8") as fh:
            library_filters = fh.read()
        with open(os.path.join(base_dir, "static", "js", "search", "query.js"), encoding="utf-8") as fh:
            search_query = fh.read()
        with open(os.path.join(base_dir, "static", "js", "people", "labels.js"), encoding="utf-8") as fh:
            people_labels = fh.read()
        with open(os.path.join(base_dir, "static", "js", "people", "cards.js"), encoding="utf-8") as fh:
            people_cards = fh.read()
        with open(os.path.join(base_dir, "static", "js", "people", "page.js"), encoding="utf-8") as fh:
            people_page = fh.read()
        with open(os.path.join(base_dir, "static", "js", "people", "actions.js"), encoding="utf-8") as fh:
            people_actions = fh.read()
        with open(os.path.join(base_dir, "static", "js", "people", "controller.js"), encoding="utf-8") as fh:
            people_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "status.js"), encoding="utf-8") as fh:
            settings_status = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "cache_status.js"), encoding="utf-8") as fh:
            settings_cache_status = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "ai_status.js"), encoding="utf-8") as fh:
            settings_ai_status = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "people_status.js"), encoding="utf-8") as fh:
            settings_people_status = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "work_banner.js"), encoding="utf-8") as fh:
            settings_work_banner = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "deep_search.js"), encoding="utf-8") as fh:
            settings_deep_search = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "display.js"), encoding="utf-8") as fh:
            settings_display = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "form.js"), encoding="utf-8") as fh:
            settings_form = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "actions.js"), encoding="utf-8") as fh:
            settings_actions = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "controller.js"), encoding="utf-8") as fh:
            settings_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "page.js"), encoding="utf-8") as fh:
            settings_page = fh.read()
        with open(os.path.join(base_dir, "static", "js", "ui.js"), encoding="utf-8") as fh:
            ui_module = fh.read()
        with open(os.path.join(base_dir, "static", "js", "warmup.js"), encoding="utf-8") as fh:
            warmup = fh.read()
        with open(os.path.join(base_dir, "static", "js", "thumbnail_size.js"), encoding="utf-8") as fh:
            thumbnail_size = fh.read()

        self.assertIn('type="module" src="/static/app.js?v={{ static_version }}"', base_template)
        self.assertIn("window.PhotoArchiveReady = import(`./js/bootstrap.js${suffix}`)", app_entry)
        self.assertIn("Object.assign(compatibilityTarget, PhotoArchive);", bootstrap)
        self.assertIn("from '../api.js';", legacy)
        self.assertIn("from '../ai/poller.js';", legacy)
        self.assertIn("from '../query_state.js';", legacy)
        self.assertIn("from '../filters.js';", legacy)
        self.assertIn("from '../media_status.js';", legacy)
        self.assertIn("from '../media_metadata.js';", legacy)
        self.assertIn("from '../thumbnail_size.js';", legacy)
        self.assertIn("from '../compare/query.js';", legacy)
        self.assertIn("from '../compare/navigation.js';", legacy)
        self.assertIn("from '../compare/images.js';", legacy)
        self.assertIn("from '../compare/view.js';", legacy)
        self.assertIn("from '../compare/mode_controller.js';", legacy)
        self.assertIn("from '../compare/action_controller.js';", legacy)
        self.assertIn("from '../compare/mosaic_action_controller.js';", legacy)
        self.assertIn("from '../compare/mosaic_replacements.js';", legacy)
        self.assertIn("from '../compare/propagation.js';", legacy)
        self.assertIn("from '../compare/status_controller.js';", legacy)
        self.assertIn("from '../compare/mosaic.js';", legacy)
        self.assertIn("from '../compare/keyboard.js';", legacy)
        self.assertIn("from '../loupe/tiers.js';", legacy)
        self.assertIn("from '../loupe/loading.js';", legacy)
        self.assertIn("from '../loupe/status.js';", legacy)
        self.assertIn("from '../loupe/metadata.js';", legacy)
        self.assertIn("from '../loupe/navigation.js';", legacy)
        self.assertIn("from '../loupe/filmstrip.js';", legacy)
        self.assertIn("from '../loupe/focus.js';", legacy)
        self.assertIn("from '../loupe/zoom.js';", legacy)
        self.assertIn("from '../library/query.js';", legacy)
        self.assertIn("from '../library/sort.js';", legacy)
        self.assertIn("from '../library/sort_controller.js';", legacy)
        self.assertIn("from '../library/search_state.js';", legacy)
        self.assertIn("from '../library/search_controls.js';", legacy)
        self.assertIn("from '../library/search_controller.js';", legacy)
        self.assertIn("from '../library/flags.js';", legacy)
        self.assertIn("from '../library/batch_controller.js';", legacy)
        self.assertIn("from '../library/filter_controller.js';", legacy)
        self.assertIn("from '../export/actions.js';", legacy)
        self.assertIn("from '../library/similar.js';", legacy)
        self.assertIn("from '../library/display.js';", legacy)
        self.assertIn("from '../library/shell.js';", legacy)
        self.assertIn("from '../library/date_scrubber.js';", legacy)
        self.assertIn("from '../library/navigation.js';", legacy)
        self.assertIn("from '../library/map_controller.js';", legacy)
        self.assertIn("from '../library/filters.js';", legacy)
        self.assertIn("createLibraryFilterController", legacy)
        self.assertIn("from '../search/query.js';", legacy)
        self.assertIn("from '../people/controller.js';", legacy)
        self.assertIn("from '../settings/page.js';", legacy)
        self.assertIn("from '../ui.js';", legacy)
        self.assertIn("from '../warmup.js';", legacy)
        self.assertIn("export async function fetchJson", api_module)
        self.assertIn("export function aiStatusPollDelay", ai_status)
        self.assertIn("export function renderAIBottomBarStatus", ai_status)
        self.assertIn("export function renderAIStatusWidgets", ai_status)
        self.assertIn("export function renderAIPanelStatus", ai_status)
        self.assertIn("export function deepSearchQueryListHtml", ai_status)
        self.assertIn("export function modelInstallDisplay", ai_status)
        self.assertIn("export function toggleAIPanel", ai_status)
        self.assertIn("export function deepSearchWorkerText", ai_status)
        self.assertIn("export function deepSearchImageStatusText", ai_status)
        self.assertIn("export function deepSearchQueryStatusText", ai_status)
        self.assertIn("export function createAIStatusPoller", ai_poller)
        self.assertIn("from './status.js';", ai_poller)
        self.assertIn("initVisibilityRefresh?.();", ai_poller)
        self.assertIn("handleVisible", ai_poller)
        self.assertIn("export function sourceState", catalog_status)
        self.assertIn("export function setScanBusy", catalog_status)
        self.assertIn("export function catalogSourcesHtml", catalog_sources)
        self.assertIn("export function renderCatalogSources", catalog_sources)
        self.assertIn("export function openRemoveSourceDialog", catalog_sources)
        self.assertIn("export function closeRemoveSourceDialog", catalog_sources)
        self.assertIn("export function directoryBrowserListHtml", catalog_directory)
        self.assertIn("export function directoryCrumbsHtml", catalog_directory)
        self.assertIn("export function renderDirectoryBrowser", catalog_directory)
        self.assertIn("export async function chooseCatalogFolder", catalog_browser)
        self.assertIn("export async function browseDirectory", catalog_browser)
        self.assertIn("export function toggleDirectoryBrowser", catalog_browser)
        self.assertIn("export function browseDirectoryParent", catalog_browser)
        self.assertIn("export function selectBrowsedDirectory", catalog_browser)
        self.assertIn("export async function loadCatalogSources", catalog_actions)
        self.assertIn("export async function pollScanUntilDone", catalog_actions)
        self.assertIn("export async function addCatalogSource", catalog_actions)
        self.assertIn("export async function rescanCatalogSource", catalog_actions)
        self.assertIn("export async function removeCatalogSource", catalog_actions)
        self.assertIn("export function createCatalogApi", catalog_controller)
        self.assertIn("from './status.js';", catalog_controller)
        self.assertIn("from './sources.js';", catalog_controller)
        self.assertIn("from './browser.js';", catalog_controller)
        self.assertIn("from './actions.js';", catalog_controller)
        self.assertIn("getFallbackStats = () => ({})", catalog_controller)
        self.assertIn("catalogSources.find", catalog_controller)
        self.assertIn("export function normalizeFilterState", query_state)
        self.assertIn("export function filterParams", query_state)
        self.assertIn("export function filterQueryString", query_state)
        self.assertIn("export function buildFilterNeighborStates", query_state)
        self.assertIn("export function syncLibraryUrlState", query_state)
        self.assertIn("export function activeMetadataFilterCount", filters_module)
        self.assertIn("export function updateMetadataFilterButton", filters_module)
        self.assertIn("export function toggleMetadataFilters", filters_module)
        self.assertIn("export function loadFolderList", filters_module)
        self.assertIn("export function loadFilterOptions", filters_module)
        self.assertIn("export function scheduleFilterOptionsLoad", filters_module)
        self.assertIn("export function initStarHover", filters_module)
        self.assertIn("export function createMediaStatusClient", media_status)
        self.assertIn("export function imageMetadataTitle", media_metadata)
        self.assertIn("export function formatDateTime", media_metadata)
        self.assertIn("export function formatCacheTier", cache_status)
        self.assertIn("export function cacheMemoryUsageText", cache_status)
        self.assertIn("export function resourceFreeText", cache_status)
        self.assertIn("export function pregenSummaryText", cache_status)
        self.assertIn("export function pregenDiagnosticsText", cache_status)
        self.assertIn("export function renderCacheTierGuide", cache_guide)
        self.assertIn("export function buildCompareUrl", compare_query)
        self.assertIn("export function buildMosaicUrl", compare_query)
        self.assertIn("export function selectMosaicCell", compare_navigation)
        self.assertIn("export function deselectMosaicCell", compare_navigation)
        self.assertIn("export function findMosaicCellInDirection", compare_navigation)
        self.assertIn("export function createCompareKeyboardHandler", compare_keyboard)
        self.assertIn("getSelectedMosaicIndex", compare_keyboard)
        self.assertIn("submitComparison('left')", compare_keyboard)
        self.assertIn("export function renderCompareImage", compare_images)
        self.assertIn("export async function upgradeCompareImage", compare_images)
        self.assertIn("export async function adoptCompareTier", compare_images)
        self.assertIn("export function setCompareModeView", compare_view)
        self.assertIn("export function showCompareEmpty", compare_view)
        self.assertIn("export function createCompareModeController", compare_mode_controller)
        self.assertIn("from './view.js';", compare_mode_controller)
        self.assertIn("setCompareModeViewImpl", compare_mode_controller)
        self.assertIn("export function createCompareActionController", compare_action_controller)
        self.assertIn("from './actions.js';", compare_action_controller)
        self.assertIn("postComparisonImpl", compare_action_controller)
        self.assertIn("export function createMosaicActionController", compare_mosaic_action_controller)
        self.assertIn("from './mosaic.js';", compare_mosaic_action_controller)
        self.assertIn("postMosaicPickImpl", compare_mosaic_action_controller)
        self.assertIn("export function createMosaicReplacementBuffer", compare_mosaic_replacements)
        self.assertIn("export const MOSAIC_REPLACEMENT_LOW_WATER", compare_mosaic_replacements)
        self.assertIn("replacementPreloadTimeoutMs", compare_mosaic_replacements)
        self.assertIn("export function buildComparisonPayload", compare_actions)
        self.assertIn("export async function postComparison", compare_actions)
        self.assertIn("export function applyComparisonElos", compare_actions)
        self.assertIn("export async function postUndoComparison", compare_actions)
        self.assertIn("export function undoComparisonToastText", compare_actions)
        self.assertIn("export function precomputePropagationCounts", compare_propagation)
        self.assertIn("export function fetchPropagationCount", compare_propagation)
        self.assertIn("export function createCompareStatusController", compare_status_controller)
        self.assertIn("from './propagation.js';", compare_status_controller)
        self.assertIn("coverageStatsFetchPromise", compare_status_controller)
        self.assertIn("export function visibleTotalLabel", compare_status)
        self.assertIn("export function poolVisibleCount", compare_status)
        self.assertIn("export function coveragePercent", compare_status)
        self.assertIn("export function bumpRankingSignals", compare_status)
        self.assertIn("export function mergeCoverageStats", compare_status)
        self.assertIn("export function renderCompareProgress", compare_status)
        self.assertIn("export function renderCoverageBar", compare_status)
        self.assertIn("export function propagationBadgeText", compare_status)
        self.assertIn("export function showPropagationBadge", compare_status)
        self.assertIn("export function rollUpCounter", compare_status)
        self.assertIn("export function mosaicGridElo", compare_mosaic)
        self.assertIn("export function mosaicThumbHeightForSize", compare_mosaic)
        self.assertIn("export function mosaicSizeFromThumbHeight", compare_mosaic)
        self.assertIn("export function renderMosaicGrid", compare_mosaic)
        self.assertIn("export async function upgradeMosaicCellImage", compare_mosaic)
        self.assertIn("export async function adoptMosaicTier", compare_mosaic)
        self.assertIn("export function mosaicLoserIds", compare_mosaic)
        self.assertIn("export function mosaicReplacementIndices", compare_mosaic)
        self.assertIn("export async function parseMosaicPickResponse", compare_mosaic)
        self.assertIn("export function postMosaicPick", compare_mosaic)
        self.assertIn("export function loupeTierUrl", loupe_tiers)
        self.assertIn("export function cancelLoupeProbes", loupe_loading)
        self.assertIn("export function loadLoupeTier", loupe_loading)
        self.assertIn("export async function runLoupeProgressiveLoad", loupe_loading)
        self.assertIn("export function loupeCacheStatusText", loupe_status)
        self.assertIn("export function loupeTierLoadingText", loupe_status)
        self.assertIn("export function setLoupeTierLoading", loupe_status)
        self.assertIn("export function clearLoupeTierLoading", loupe_status)
        self.assertIn("export function renderLoupeStatusLine", loupe_status)
        self.assertIn("export function loupeMetadataParts", loupe_metadata)
        self.assertIn("export function renderLoupeMetadataOverlay", loupe_metadata)
        self.assertIn("export function loupeNeighborOffsets", loupe_navigation)
        self.assertIn("export function loupeHotSetTierIds", loupe_navigation)
        self.assertIn("export function clearFilmstrip", loupe_filmstrip)
        self.assertIn("export function updateFilmstripCounter", loupe_filmstrip)
        self.assertIn("export function buildFilmstrip", loupe_filmstrip)
        self.assertIn("export function updateFilmstripActive", loupe_filmstrip)
        self.assertIn("export function centerFilmstripActive", loupe_filmstrip)
        self.assertIn("export function focusLoupe", loupe_focus)
        self.assertIn("export function loupeFocusableElements", loupe_focus)
        self.assertIn("export function trapLoupeFocus", loupe_focus)
        self.assertIn("export function loupeComputeFitScale", loupe_zoom)
        self.assertIn("export function updateLoupeZoomIndicator", loupe_zoom)
        self.assertIn("export function clampLoupePan", loupe_zoom)
        self.assertIn("export function rankingQueryString", library_query)
        self.assertIn("export function sortValueForState", library_sort)
        self.assertIn("export function createLibrarySortController", library_sort_controller)
        self.assertIn("export function saveSortState", library_search_state)
        self.assertIn("export function saveSearchState", library_search_state)
        self.assertIn("export function saveSearchSortState", library_search_state)
        self.assertIn("export function clearPersistedSearchState", library_search_state)
        self.assertIn("export function restoreSortState", library_search_state)
        self.assertIn("export function restoreSearchSortState", library_search_state)
        self.assertIn("export function restoreSearchState", library_search_state)
        self.assertIn("export function updateSortDirIcon", library_search_controls)
        self.assertIn("export function updateSimilaritySortOption", library_search_controls)
        self.assertIn("export function syncSortControls", library_search_controls)
        self.assertIn("export function updateCompareSearchIndicator", library_search_controls)
        self.assertIn("export function updateSearchControls", library_search_controls)
        self.assertIn("export function initSearchInputControls", library_search_controller)
        self.assertIn("export function applySearchQueryChange", library_search_controller)
        self.assertIn("export function clearSearch", library_search_controller)
        self.assertIn("export function runDeepSearch", library_search_controller)
        self.assertIn("export function updateImageFlagLocal", library_flags)
        self.assertIn("export async function setImageFlag", library_flags)
        self.assertIn("export function setCurrentLibraryFlag", library_flags)
        self.assertIn("export function batchBarHtml", library_batch)
        self.assertIn("export function updateBatchBar", library_batch)
        self.assertIn("export function markRankCardsSelectable", library_batch)
        self.assertIn("export function clearBatchSelection", library_batch)
        self.assertIn("export function toggleBatchMode", library_batch)
        self.assertIn("export function handleCardClick", library_batch)
        self.assertIn("export async function batchFlag", library_batch)
        self.assertIn("export function batchExport", library_batch)
        self.assertIn("from '../export/actions.js';", library_batch)
        self.assertIn("export function createBatchSelectionController", library_batch_controller)
        self.assertIn("from './batch.js';", library_batch_controller)
        self.assertIn("selectedImageIds", library_batch_controller)
        self.assertIn("export function createLibraryFilterController", library_filter_controller)
        self.assertIn("from '../filters.js';", library_filter_controller)
        self.assertIn("from './filters.js';", library_filter_controller)
        self.assertIn("reloadForFilters", library_filter_controller)
        self.assertIn("export function fullRankingsExportUrl", export_actions)
        self.assertIn("export function selectedImagesExportUrl", export_actions)
        self.assertIn("export function exportRankings", export_actions)
        self.assertIn("export function batchExport", export_actions)
        self.assertIn("from '../library/query.js';", export_actions)
        self.assertIn("export function setSimilarSearchControls", library_similar)
        self.assertIn("export function renderSimilarCards", library_similar)
        self.assertIn("export function createFindSimilarAction", library_similar)
        self.assertIn("from './display.js';", library_similar)
        self.assertIn("export function flagBadge", library_display)
        self.assertIn("export function libraryCardInfoLine", library_display)
        self.assertIn("export function dateScrubberLabelsHtml", library_display)
        self.assertIn("export function similarLibraryCardHtml", library_display)
        self.assertIn("export function updateLibraryEmptyState", library_shell)
        self.assertIn("export function saveScrollPosition", library_shell)
        self.assertIn("export function restoreScrollPosition", library_shell)
        self.assertIn("export function scrollLibraryContainerToElement", library_shell)
        self.assertIn("export function isDateSortValue", library_date_scrubber)
        self.assertIn("export function findDateGroupHeader", library_date_scrubber)
        self.assertIn("export function dateGroupOffset", library_date_scrubber)
        self.assertIn("export async function jumpToDateGroup", library_date_scrubber)
        self.assertIn("export function setActiveDateScrubberGroup", library_date_scrubber)
        self.assertIn("export function setupDateScrubberScrollTracking", library_date_scrubber)
        self.assertIn("export function renderDateScrubber", library_date_scrubber)
        self.assertIn("export function selectLibraryCard", library_navigation)
        self.assertIn("export function scrollCardFullyVisible", library_navigation)
        self.assertIn("export function deselectLibraryCard", library_navigation)
        self.assertIn("export function findCardInDirection", library_navigation)
        self.assertIn("export async function loadLeafletLibraries", library_map)
        self.assertIn("export function mapInfoText", library_map)
        self.assertIn("export function renderMapInfo", library_map)
        self.assertIn("export function showMapError", library_map)
        self.assertIn("export function clearMapError", library_map)
        self.assertIn("export function buildMapPopup", library_map)
        self.assertIn("export function createLibraryMapController", library_map_controller)
        self.assertIn("from './map.js';", library_map_controller)
        self.assertIn("export const FILTER_STORAGE_KEY = 'pa_filters';", library_filters)
        self.assertIn("export function saveFilters", library_filters)
        self.assertIn("export function restoreFilters", library_filters)
        self.assertIn("export function applyFilterUiState", library_filters)
        self.assertIn("export function setFilter", library_filters)
        self.assertIn("export function clearLibraryFilters", library_filters)
        self.assertIn("export function toggleFilter", library_filters)
        self.assertIn("export function toggleStar", library_filters)
        self.assertIn("export function appendSearchParams", search_query)
        self.assertIn("export function visiblePersonLabel", people_labels)
        self.assertIn("export function peopleSettingsModelText", people_labels)
        self.assertIn("export function peopleGridHtml", people_cards)
        self.assertIn("export function mergeSuggestionsHtml", people_cards)
        self.assertIn("export function renderPeople", people_page)
        self.assertIn("export async function loadPeople", people_page)
        self.assertIn("export function initPeople", people_page)
        self.assertIn("export function rememberPeopleLabelDraft", people_page)
        self.assertIn("export function forgetPeopleLabelDraft", people_page)
        self.assertIn("export function useFallbackThumb", people_page)
        self.assertIn("export async function labelPerson", people_actions)
        self.assertIn("export async function mergePeople", people_actions)
        self.assertIn("export async function rejectPeopleMerge", people_actions)
        self.assertIn("export async function ignorePerson", people_actions)
        self.assertIn("export function filterLibraryByPerson", people_actions)
        self.assertIn("export function createPeopleApi", people_controller)
        self.assertIn("from './page.js';", people_controller)
        self.assertIn("from './actions.js';", people_controller)
        self.assertIn("export function setSettingsStatus", settings_status)
        self.assertIn("export function backgroundWorkStatusText", settings_status)
        self.assertIn("export function renderCacheSettingsStatus", settings_cache_status)
        self.assertIn("export function renderAutoTuningStatus", settings_cache_status)
        self.assertIn("export function renderModelStatus", settings_ai_status)
        self.assertIn("export function renderAISettingsStatus", settings_ai_status)
        self.assertIn("export function renderEmbeddingModelPresets", settings_ai_status)
        self.assertIn("export function applySelectedEmbeddingPreset", settings_ai_status)
        self.assertIn("export function renderPeopleSettingsStatus", settings_people_status)
        self.assertIn("export function workBannerHtml", settings_work_banner)
        self.assertIn("export function normalizeDeepSearchTerms", settings_deep_search)
        self.assertIn("export function deepSearchTermListHtml", settings_deep_search)
        self.assertIn("export function normalizeDeepSearchDays", settings_deep_search)
        self.assertIn("export function formatDeepSearchTime", settings_deep_search)
        self.assertIn("export function deepSearchScheduleSummaryText", settings_deep_search)
        self.assertIn("export function normalizeBackgroundWorkMode", settings_display)
        self.assertIn("export function formatEta", settings_display)
        self.assertIn("export function badgeStateForIndex", settings_display)
        self.assertIn("export function embeddingIndexDisplay", settings_display)
        self.assertIn("export function effectiveFastModelSettings", settings_display)
        self.assertIn("export function autoWorkersText", settings_display)
        self.assertIn("export function autoPrefetchText", settings_display)
        self.assertIn("export function browserCachePolicyText", settings_display)
        self.assertIn("export function thumbnailOutputChanges", settings_display)
        self.assertIn("export function thumbnailChangeNoticeText", settings_display)
        self.assertIn("export function populateSettingsForm", settings_form)
        self.assertIn("export function collectSettingsForm", settings_form)
        self.assertIn("export function renderDeepSearchSchedule", settings_form)
        self.assertIn("export function updateThumbnailChangeNotice", settings_form)
        self.assertIn("export function cacheProfileHintText", settings_form)
        self.assertIn("from './deep_search.js';", settings_form)
        self.assertIn("from './display.js';", settings_form)
        self.assertIn("export async function saveSettings", settings_actions)
        self.assertIn("export function resetSettings", settings_actions)
        self.assertIn("export function clearThumbnailCache", settings_actions)
        self.assertIn("export async function startCachePregeneration", settings_actions)
        self.assertIn("export async function stopCachePregeneration", settings_actions)
        self.assertIn("export async function pauseEmbeddings", settings_actions)
        self.assertIn("export async function resumeEmbeddings", settings_actions)
        self.assertIn("export async function installAIModel", settings_actions)
        self.assertIn("export function createSettingsApi", settings_controller)
        self.assertIn("from './display.js';", settings_controller)
        self.assertIn("from './actions.js';", settings_controller)
        self.assertIn("getSettingsPageData = () => ({})", settings_controller)
        self.assertIn("api.saveSettings();", settings_controller)
        self.assertIn("export function createSettingsPageController", settings_page)
        self.assertIn("from '../catalog/controller.js';", settings_page)
        self.assertIn("from '../cache/guide.js';", settings_page)
        self.assertIn("from './status.js';", settings_page)
        self.assertIn("from './cache_status.js';", settings_page)
        self.assertIn("from './ai_status.js';", settings_page)
        self.assertIn("from './people_status.js';", settings_page)
        self.assertIn("from './work_banner.js';", settings_page)
        self.assertIn("from './display.js';", settings_page)
        self.assertIn("from './form.js';", settings_page)
        self.assertIn("from './controller.js';", settings_page)
        self.assertIn("refreshSettingsMetaIfActive", settings_page)
        self.assertIn("export function formatBytes", ui_module)
        self.assertIn("export function escapeHtml", ui_module)
        self.assertIn("export function jsString", ui_module)
        self.assertIn("export function showToast", ui_module)
        self.assertIn("export function showShortcuts", ui_module)
        self.assertIn("export function hideShortcuts", ui_module)
        self.assertIn("export function initShortcutOverlay", ui_module)
        self.assertIn("export function updateBottomBarHeightVar", ui_module)
        self.assertIn("export function initBottomBarMeasurement", ui_module)
        self.assertIn("export function initVisibilityRefresh", ui_module)
        self.assertIn("export function findElementInAdjacentVisualRow", ui_module)
        self.assertIn("export function createImagePreloader", warmup)
        self.assertIn("export function loadImageProbe", warmup)
        self.assertIn("export function withTimeout", warmup)
        self.assertIn("export function createWarmCacheStore", warmup)
        self.assertIn("export function imageThumbUrls", warmup)
        self.assertIn("export function compareThumbUrls", warmup)
        self.assertIn("export function applyLibraryThumbSize", thumbnail_size)
        self.assertIn("export function createThumbnailSizeHandler", thumbnail_size)
        self.assertIn("export default legacyPhotoArchive;", legacy)

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_export_actions_node_probe_preserves_url_contracts(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import {
    batchExport as batchExportAction,
    exportRankings,
    fullRankingsExportUrl,
    selectedImagesExportUrl,
} from './static/js/export/actions.js';
import { batchExport as batchExportFacade } from './static/js/library/batch.js';

const queryState = {
    filters: {
        orientation: 'portrait',
        rating: '4',
        folder: 'Trips 2026',
        flag: 'picked',
        taken: '2026-05',
        fileType: 'image',
        camera: 'Fuji X-T5',
        lens: '35mm',
        people: '12',
    },
    sort: 'date_taken_asc',
    searchMode: 'search',
    searchQuery: 'family crane',
    deepSearch: true,
};

const opened = [];
exportRankings('csv', {
    queryState,
    sort: 'date_taken_asc',
    windowImpl: {
        open: (url, target) => opened.push({ url, target }),
    },
});

assert.equal(opened.length, 1);
assert.equal(opened[0].target, '_blank');
assert.equal(opened[0].url, fullRankingsExportUrl('csv', {
    queryState,
    sort: 'date_taken_asc',
}));
assert.match(opened[0].url, /^\/api\/export\?/);
assert.match(opened[0].url, /&format=csv$/);

const params = new URL(`http://photoarchive.test${opened[0].url}`).searchParams;
assert.equal(params.get('limit'), '10000');
assert.equal(params.get('offset'), '0');
assert.equal(params.get('sort'), 'date_taken_asc');
assert.equal(params.get('format'), 'csv');
assert.equal(params.get('orientation'), 'portrait');
assert.equal(params.get('min_stars'), '4');
assert.equal(params.get('folder'), 'Trips 2026');
assert.equal(params.get('flag'), 'picked');
assert.equal(params.get('date_taken'), '2026-05');
assert.equal(params.get('file_type'), 'image');
assert.equal(params.get('camera'), 'Fuji X-T5');
assert.equal(params.get('lens'), '35mm');
assert.equal(params.get('people'), '12');
assert.equal(params.get('q'), 'family crane');
assert.equal(params.get('deep'), '1');

assert.equal(
    selectedImagesExportUrl('json', { imageIds: [7, 5] }),
    '/api/export?format=json&ids=7,5',
);

const selectedOpened = [];
batchExportAction('json', {
    imageIds: [7, 5],
    windowImpl: {
        open: (url, target) => selectedOpened.push({ url, target }),
    },
});
assert.deepEqual(selectedOpened, [{
    url: '/api/export?format=json&ids=7,5',
    target: '_blank',
}]);

const facadeOpened = [];
batchExportFacade('json', {
    imageIds: [7, 5],
    windowImpl: {
        open: (url, target) => facadeOpened.push({ url, target }),
    },
});
assert.deepEqual(facadeOpened, selectedOpened);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_library_batch_controller_node_probe_preserves_selection_flow(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createBatchSelectionController } from './static/js/library/batch_controller.js';

function classList() {
    const values = new Set();
    return {
        add: (...names) => names.forEach((name) => values.add(name)),
        remove: (...names) => names.forEach((name) => values.delete(name)),
        contains: (name) => values.has(name),
    };
}

const cards = [
    { classList: classList() },
    { classList: classList() },
];
let batchBar = null;
const documentImpl = {
    body: {
        appendChild(el) {
            if (el.id === 'batch-bar') batchBar = el;
        },
    },
    createElement() {
        return {
            id: '',
            className: '',
            innerHTML: '',
            remove() {
                if (this.id === 'batch-bar') batchBar = null;
            },
        };
    },
    getElementById(id) {
        return id === 'batch-bar' ? batchBar : null;
    },
    querySelectorAll(selector) {
        return selector === '.rank-card' ? cards : [];
    },
};
const images = [
    { id: 7, flag: 'unflagged' },
    { id: 8, flag: 'picked' },
];
const opened = [];
const controller = createBatchSelectionController({
    documentImpl,
    getImages: () => images,
    openImage: (img) => opened.push(img.id),
});

controller.handleCardClick({}, images[0], cards[0], 0);
assert.deepEqual(opened, [7]);
assert.equal(controller.isBatchMode(), false);
assert.deepEqual(controller.selectedImageIds(), []);

controller.toggleBatchMode();
assert.equal(controller.isBatchMode(), true);
assert.equal(cards[0].classList.contains('selectable'), true);
assert.equal(cards[1].classList.contains('selectable'), true);

controller.handleCardClick({}, images[0], cards[0], 0);
assert.deepEqual(controller.selectedImageIds(), [7]);
assert.equal(controller.isSelected(7), true);
assert.equal(controller.hasSelection(), true);
assert.equal(cards[0].classList.contains('selected'), true);
assert.match(batchBar.innerHTML, /1 selected/);

controller.handleCardClick({ shiftKey: true, preventDefault() {} }, images[1], cards[1], 1);
assert.deepEqual(controller.selectedImageIds(), [7, 8]);
assert.equal(cards[1].classList.contains('selected'), true);
assert.match(batchBar.innerHTML, /2 selected/);

controller.clearBatchSelection();
assert.equal(controller.isBatchMode(), false);
assert.equal(controller.hasSelection(), false);
assert.deepEqual(controller.selectedImageIds(), []);
assert.equal(cards[0].classList.contains('selected'), false);
assert.equal(cards[0].classList.contains('selectable'), false);
assert.equal(batchBar, null);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_compare_mode_controller_node_probe_preserves_mode_flow(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createCompareModeController } from './static/js/compare/mode_controller.js';

const siblingButtons = [
    { classList: { removed: [], remove(cls) { this.removed.push(cls); } } },
];
const strategyButton = {
    classList: {
        added: [],
        add(cls) { this.added.push(cls); },
    },
    parentElement: {
        querySelectorAll(selector) {
            assert.equal(selector, 'button');
            return siblingButtons;
        },
    },
};
const documentImpl = {
    getElementById(id) {
        return id === 'strategy-explore' ? strategyButton : null;
    },
};
let mosaicStrategy = 'diverse';
let compareMode = 'swiss';
let transition = 0;
let imageToken = 0;
const events = [];
const controller = createCompareModeController({
    documentImpl,
    clearWarmups: () => events.push(['clear']),
    setMosaicStrategyValue: (strategy) => {
        mosaicStrategy = strategy;
        events.push(['strategy', strategy]);
    },
    setCompareModeValue: (mode) => {
        compareMode = mode;
        events.push(['mode', mode]);
    },
    incrementTransitionToken: () => {
        transition += 1;
        return transition;
    },
    isCurrentTransition: (token) => token === transition,
    incrementCompareImageToken: () => {
        imageToken += 1;
        events.push(['imageToken', imageToken]);
    },
    loadMosaicBatch: () => events.push(['mosaic']),
    resetComparePairs: () => events.push(['resetPairs']),
    fetchComparePairs: () => {
        events.push(['fetchPairs']);
        return Promise.resolve();
    },
    showComparePair: () => events.push(['showPair']),
    setCompareModeViewImpl: (mode, options) => {
        events.push(['view', mode, options.transitionToken, options.isCurrentTransition(options.transitionToken)]);
        if (mode === 'mosaic') options.onMosaic();
        else options.onPair();
    },
});

controller.setMosaicStrategy('explore');
assert.equal(mosaicStrategy, 'explore');
assert.deepEqual(siblingButtons[0].classList.removed, ['active']);
assert.deepEqual(strategyButton.classList.added, ['active']);
assert.deepEqual(events, [['clear'], ['strategy', 'explore'], ['mosaic']]);

events.length = 0;
controller.mosaicShuffle();
assert.deepEqual(events, [['mosaic']]);

events.length = 0;
controller.setCompareMode('mosaic');
assert.equal(compareMode, 'mosaic');
assert.equal(transition, 1);
assert.equal(imageToken, 1);
assert.deepEqual(events, [
    ['clear'],
    ['mode', 'mosaic'],
    ['view', 'mosaic', 1, true],
    ['imageToken', 1],
    ['mosaic'],
]);

events.length = 0;
controller.setCompareMode('topn');
await Promise.resolve();
assert.equal(compareMode, 'topn');
assert.deepEqual(events, [
    ['clear'],
    ['mode', 'topn'],
    ['view', 'topn', 2, true],
    ['resetPairs'],
    ['fetchPairs'],
    ['showPair'],
]);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_compare_keyboard_node_probe_preserves_navigation_flow(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createCompareKeyboardHandler } from './static/js/compare/keyboard.js';

const cells = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
const images = [{ id: 7 }, { id: 8 }, { id: 9 }];
let selected = -1;
let mode = 'mosaic';
const events = [];
const timers = [];
const windowImpl = { location: { href: '' } };
const documentImpl = {
    querySelectorAll(selector) {
        return selector === '.mosaic-cell' ? cells : [];
    },
};
const handler = createCompareKeyboardHandler({
    documentImpl,
    windowImpl,
    setTimeoutImpl: (fn, ms) => timers.push({ fn, ms }),
    getCompareMode: () => mode,
    getSelectedMosaicIndex: () => selected,
    getMosaicImages: () => images,
    selectMosaicCell: (index, nextCells) => {
        assert.equal(nextCells, cells);
        selected = index;
        events.push(['select', index]);
    },
    deselectMosaicCell: (nextCells) => {
        assert.equal(nextCells, cells);
        selected = -1;
        events.push(['deselect']);
    },
    findMosaicCellInDirection: (_cells, current, direction) => current + direction,
    mosaicClick: (id) => events.push(['pick', id]),
    undoComparison: () => events.push(['undo']),
    submitComparison: (side) => events.push(['submit', side]),
});

function key(name, tagName = 'DIV') {
    let prevented = false;
    return {
        key: name,
        target: { tagName },
        preventDefault: () => { prevented = true; },
        get prevented() { return prevented; },
    };
}

const inputEvent = key('ArrowRight', 'INPUT');
handler(inputEvent);
assert.equal(inputEvent.prevented, false);
assert.deepEqual(events, []);

const tabEvent = key('Tab');
handler(tabEvent);
assert.equal(tabEvent.prevented, true);
assert.equal(windowImpl.location.href, '/library');

const firstArrow = key('ArrowRight');
handler(firstArrow);
assert.equal(firstArrow.prevented, true);
assert.deepEqual(events.at(-1), ['select', 0]);

const downArrow = key('ArrowDown');
handler(downArrow);
assert.equal(downArrow.prevented, true);
assert.deepEqual(events.at(-1), ['select', 1]);

const enterEvent = key('Enter');
handler(enterEvent);
assert.equal(enterEvent.prevented, true);
assert.deepEqual(events.at(-1), ['pick', 8]);
assert.equal(timers[0].ms, 200);
timers[0].fn();
assert.deepEqual(events.at(-1), ['select', 1]);

const escapeEvent = key('Escape');
handler(escapeEvent);
assert.equal(escapeEvent.prevented, true);
assert.deepEqual(events.at(-1), ['deselect']);

mode = 'swiss';
const leftEvent = key('ArrowLeft');
handler(leftEvent);
assert.equal(leftEvent.prevented, false);
assert.deepEqual(events.at(-1), ['submit', 'left']);

const undoEvent = key('ArrowUp');
handler(undoEvent);
assert.deepEqual(events.at(-1), ['undo']);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_thumbnail_size_node_probe_preserves_library_and_mosaic_flow(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import {
    applyLibraryThumbSize,
    createThumbnailSizeHandler,
} from './static/js/thumbnail_size.js';

const rootStyle = {};
const cards = [
    { dataset: { ar: '1.25' }, style: {} },
    { dataset: {}, style: {} },
];
let mosaicGrid = null;
const documentImpl = {
    documentElement: {
        style: {
            setProperty: (name, value) => { rootStyle[name] = value; },
        },
    },
    querySelectorAll(selector) {
        return selector === '.rank-card' ? cards : [];
    },
    getElementById(id) {
        return id === 'mosaic-grid' ? mosaicGrid : null;
    },
};

assert.equal(applyLibraryThumbSize('200', { documentImpl }), 200);
assert.equal(rootStyle['--thumb-height'], '200px');
assert.equal(cards[0].style.height, '200px');
assert.equal(cards[0].style.flexBasis, '250px');
assert.equal(cards[1].style.flexBasis, '300px');

let mosaicSize = 12;
let thumbHeight = 0;
const events = [];
const setThumbSize = createThumbnailSizeHandler({
    documentImpl,
    getMosaicSize: () => mosaicSize,
    setMosaicSize: (value) => {
        mosaicSize = value;
        events.push(['size', value]);
    },
    mosaicSizeFromThumbHeight: (value) => Number(value) > 300 ? 6 : 12,
    clearWarmups: () => events.push(['clear']),
    loadMosaicBatch: () => events.push(['load']),
    setThumbHeight: (value) => { thumbHeight = value; },
});

setThumbSize('180');
assert.equal(thumbHeight, 180);
assert.equal(rootStyle['--thumb-height'], '180px');
assert.deepEqual(events, []);

mosaicGrid = {};
setThumbSize('320');
assert.equal(thumbHeight, 320);
assert.equal(mosaicSize, 6);
assert.deepEqual(events, [['clear'], ['size', 6], ['load']]);

setThumbSize('340');
assert.deepEqual(events, [['clear'], ['size', 6], ['load']]);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_library_sort_controller_node_probe_preserves_reload_flow(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createLibrarySortController } from './static/js/library/sort_controller.js';

let rankingsSort = 'elo';
let sortField = 'elo';
let sortDesc = true;
let dateGroups = ['2026-05'];
const events = [];
const controller = createLibrarySortController({
    getSortField: () => sortField,
    getSortDesc: () => sortDesc,
    setRankingsSortValue: (value) => {
        rankingsSort = value;
        events.push(['rankingsSort', value]);
    },
    applySortState: (field, desc, options = {}) => {
        sortField = field;
        sortDesc = desc;
        events.push(['apply', field, desc, options.persist]);
    },
    resetLibraryResults: (options) => events.push(['reset', options.clearBatch]),
    clearDateGroups: () => {
        dateGroups = [];
        events.push(['dateGroups']);
    },
    loadRankings: (clearFirst) => events.push(['load', clearFirst]),
    updateDateScrubber: () => events.push(['scrubber']),
});

controller.setRankingsSort('date_taken_asc', { persist: false });
assert.equal(rankingsSort, 'date_taken_asc');
assert.equal(sortField, 'date_taken');
assert.equal(sortDesc, false);
assert.deepEqual(dateGroups, []);
assert.deepEqual(events, [
    ['rankingsSort', 'date_taken_asc'],
    ['apply', 'date_taken', false, false],
    ['reset', true],
    ['dateGroups'],
    ['load', true],
    ['scrubber'],
]);

events.length = 0;
controller.setSortField('filename');
assert.equal(sortField, 'filename');
assert.equal(sortDesc, false);
assert.deepEqual(events, [
    ['apply', 'filename', false, true],
    ['reset', true],
    ['dateGroups'],
    ['load', true],
    ['scrubber'],
]);

events.length = 0;
assert.equal(controller.setSortField('not-real'), false);
assert.deepEqual(events, []);

events.length = 0;
sortField = 'elo';
sortDesc = true;
assert.equal(controller.toggleSortDir(), true);
assert.equal(sortField, 'elo');
assert.equal(sortDesc, false);
assert.deepEqual(events, [
    ['apply', 'elo', false, undefined],
    ['reset', true],
    ['dateGroups'],
    ['load', true],
    ['scrubber'],
]);

events.length = 0;
sortField = 'similarity';
assert.equal(controller.toggleSortDir(), false);
assert.deepEqual(events, []);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_library_filter_controller_node_probe_preserves_reload_flow(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createLibraryFilterController } from './static/js/library/filter_controller.js';

let gridPresent = true;
let filters = { folder: '', flag: '', rating: '' };
let compareMode = 'mosaic';
let libraryView = 'grid';
let dateSort = false;
const elements = {
    folder: { value: '' },
    star: { dataset: { star: '3' }, classList: { toggle: () => {}, remove: () => {} }, textContent: '' },
};
const documentImpl = {
    getElementById(id) {
        if (id === 'rankings-grid') return gridPresent ? {} : null;
        return elements[id] || { value: '' };
    },
    querySelectorAll(selector) {
        if (selector === '.filter-star') return [elements.star];
        return [];
    },
};
const events = [];
const controller = createLibraryFilterController({
    documentImpl,
    emptyFilters: { folder: '', flag: '', rating: '' },
    getFilters: () => filters,
    setFilters: (nextFilters) => {
        filters = nextFilters;
        events.push(['setFilters', { ...nextFilters }]);
    },
    clearWarmups: () => events.push(['clearWarmups']),
    resetLibraryResults: (options) => events.push(['reset', options.clearBatch]),
    loadRankings: (clearFirst) => events.push(['rankings', clearFirst]),
    isDateSortActive: () => dateSort,
    updateDateScrubber: () => events.push(['scrubber']),
    currentLibraryView: () => libraryView,
    loadMap: () => events.push(['map']),
    getCompareMode: () => compareMode,
    loadMosaicBatch: () => events.push(['mosaic']),
    resetComparePairs: () => events.push(['resetCompare']),
    fetchComparePairs: () => {
        events.push(['fetchPairs']);
        return Promise.resolve();
    },
    showComparePair: () => events.push(['showPair']),
    updateMetadataFilterButton: () => events.push(['metadata']),
    saveFilters: () => events.push(['save']),
    activeMetadataFilterCount: () => 1,
    loadFolderListImpl: ({ filters }) => events.push(['folders', filters.folder]),
    loadFilterOptionsImpl: ({ filters }) => events.push(['options', filters.folder]),
    scheduleFilterOptionsLoadImpl: ({ filters, activeMetadataFilterCount, loadFilterOptions }) => {
        events.push(['schedule', filters.folder, activeMetadataFilterCount()]);
        loadFilterOptions();
    },
    toggleMetadataFiltersImpl: ({ loadFilterOptions }) => {
        events.push(['toggleMetadata']);
        loadFilterOptions();
    },
    initStarHoverImpl: () => events.push(['stars']),
});

controller.setFilter('folder', '/photos');
assert.equal(filters.folder, '/photos');
assert.deepEqual(events, [
    ['metadata'],
    ['save'],
    ['clearWarmups'],
    ['reset', true],
    ['rankings', true],
    ['setFilters', { folder: '/photos', flag: '', rating: '' }],
]);

events.length = 0;
libraryView = 'map';
dateSort = true;
assert.equal(controller.reloadForFilters(), 'library');
assert.deepEqual(events, [['clearWarmups'], ['reset', true], ['rankings', true], ['scrubber'], ['map']]);

events.length = 0;
gridPresent = false;
compareMode = 'mosaic';
assert.equal(controller.reloadForFilters(), 'mosaic');
assert.deepEqual(events, [['clearWarmups'], ['mosaic']]);

events.length = 0;
compareMode = 'swiss';
assert.equal(controller.reloadForFilters(), 'compare');
await Promise.resolve();
assert.deepEqual(events, [['clearWarmups'], ['resetCompare'], ['fetchPairs'], ['showPair']]);

events.length = 0;
controller.loadFolderList();
controller.loadFilterOptions();
controller.scheduleFilterOptionsLoad();
controller.toggleMetadataFilters();
controller.initStarHover();
assert.deepEqual(events, [
    ['folders', '/photos'],
    ['options', '/photos'],
    ['schedule', '/photos', 1],
    ['options', '/photos'],
    ['toggleMetadata'],
    ['options', '/photos'],
    ['stars'],
]);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_ai_status_panel_toggle_node_probe_preserves_class_contract(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { toggleAIPanel } from './static/js/ai/status.js';

const panel = {
    hidden: false,
    classList: {
        toggle(name) {
            assert.equal(name, 'hidden');
            panel.hidden = !panel.hidden;
        },
    },
};
const document = {
    getElementById(id) {
        return id === 'ai-panel' ? panel : null;
    },
};

assert.equal(toggleAIPanel({ documentImpl: document }), true);
assert.equal(panel.hidden, true);
assert.equal(toggleAIPanel({ documentImpl: document }), true);
assert.equal(panel.hidden, false);
assert.equal(toggleAIPanel({ documentImpl: { getElementById: () => null } }), false);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_ai_status_poller_node_probe_preserves_lifecycle(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createAIStatusPoller } from './static/js/ai/poller.js';

const barAI = { style: { display: '' } };
const document = {
    hidden: false,
    getElementById(id) {
        return id === 'bar-ai' ? barAI : null;
    },
};
const timers = [];
const clearedTimers = [];
let initVisibilityCalls = 0;
let fetchCalls = 0;
const rendered = [];

const poller = createAIStatusPoller({
    documentImpl: document,
    initVisibilityRefresh: () => {
        initVisibilityCalls += 1;
    },
    setTimeoutImpl: (callback, delayMs) => {
        const timer = { callback, delayMs };
        timers.push(timer);
        return timer;
    },
    clearTimeoutImpl: (timer) => {
        clearedTimers.push(timer);
        timer.cleared = true;
    },
    fetchImpl: async (url) => {
        assert.equal(url, '/api/ai/status');
        fetchCalls += 1;
        return {
            async json() {
                return {
                    model_installed: true,
                    worker_state: fetchCalls === 1 ? 'embedding' : 'idle',
                    embedded: fetchCalls,
                    total_images: 10,
                };
            },
        };
    },
    renderStatus: (data, options) => {
        assert.equal(options.documentImpl, document);
        rendered.push(data);
    },
    activePollMs: 11,
    idlePollMs: 33,
});

assert.equal(poller.started(), false);
poller.start(7);
assert.equal(poller.started(), true);
assert.equal(initVisibilityCalls, 1);
assert.equal(timers.length, 1);
assert.equal(timers[0].delayMs, 7);

poller.start(3);
assert.equal(initVisibilityCalls, 2);
assert.equal(timers.length, 1);

await timers.shift().callback();
assert.equal(fetchCalls, 1);
assert.equal(rendered.length, 1);
assert.equal(rendered[0].worker_state, 'embedding');
assert.equal(timers.length, 1);
assert.equal(timers[0].delayMs, 11);

const scheduledAfterPoll = timers[0];
poller.handleVisible();
assert.equal(clearedTimers.includes(scheduledAfterPoll), true);
assert.equal(timers.length, 2);
assert.equal(timers[1].delayMs, 0);

document.hidden = true;
poller.schedule(5);
assert.equal(timers.length, 2);

const failingPoller = createAIStatusPoller({
    documentImpl: document,
    fetchImpl: async () => {
        throw new Error('offline');
    },
    setTimeoutImpl: (callback, delayMs) => {
        const timer = { callback, delayMs };
        timers.push(timer);
        return timer;
    },
});
document.hidden = false;
failingPoller.start(0);
await timers.pop().callback();
assert.equal(barAI.style.display, 'none');
assert.equal(timers.at(-1).delayMs, 30000);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_library_filter_module_preserves_ui_storage_and_url_contracts(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import {
    FILTER_STORAGE_KEY,
    applyFilterUiState,
    clearLibraryFilters,
    restoreFilters,
    saveFilters,
    setFilter,
    toggleFilter,
    toggleStar,
} from './static/js/library/filters.js';

class FakeClassList {
    constructor() {
        this.names = new Set();
    }
    add(...names) {
        names.forEach(name => this.names.add(name));
    }
    remove(...names) {
        names.forEach(name => this.names.delete(name));
    }
    contains(name) {
        return this.names.has(name);
    }
    toggle(name, force) {
        const shouldAdd = arguments.length > 1 ? Boolean(force) : !this.names.has(name);
        if (shouldAdd) this.add(name);
        else this.remove(name);
        return shouldAdd;
    }
}

class FakeElement {
    constructor({ title = '', dataset = {} } = {}) {
        this.title = title;
        this.dataset = dataset;
        this.classList = new FakeClassList();
        this.value = '';
        this.textContent = '';
    }
}

function createFakeDocument() {
    const icons = [
        new FakeElement({ title: 'Landscape' }),
        new FakeElement({ title: 'Portrait' }),
        new FakeElement({ title: 'Ranked' }),
        new FakeElement({ title: 'Unranked' }),
        new FakeElement({ title: 'High confidence (10+)' }),
    ];
    const flags = [
        new FakeElement({ dataset: { flag: 'picked' } }),
        new FakeElement({ dataset: { flag: 'unflagged' } }),
        new FakeElement({ dataset: { flag: 'rejected' } }),
    ];
    const allFilterButtons = [...icons, ...flags];
    const filterGroup = {
        querySelectorAll(selector) {
            if (selector === '.filter-icon') return allFilterButtons;
            return [];
        },
    };
    allFilterButtons.forEach(btn => {
        btn.parentElement = filterGroup;
    });
    const stars = [1, 2, 3, 4, 5].map(star => new FakeElement({ dataset: { star: String(star) } }));
    const selects = Object.fromEntries([
        'filter-folder',
        'filter-taken',
        'filter-type',
        'filter-camera',
        'filter-lens',
        'filter-people',
    ].map(id => [id, new FakeElement()]));
    return {
        icons,
        flags,
        stars,
        selects,
        querySelectorAll(selector) {
            if (selector === '.bar-filters .filter-icon' || selector === '.filter-icon') return [...icons, ...flags];
            if (selector === '.filter-flag') return flags;
            if (selector === '.filter-star') return stars;
            return [];
        },
        getElementById(id) {
            return selects[id] || null;
        },
    };
}

const storage = {
    items: new Map(),
    getItem(key) {
        return this.items.has(key) ? this.items.get(key) : null;
    },
    setItem(key, value) {
        this.items.set(key, String(value));
    },
};

let state = {};
let syncCalls = 0;
let applyCalls = 0;
let metadataCalls = 0;
const document = createFakeDocument();
const setFilters = filters => {
    state = filters;
};
const applyCurrentFilters = () => {
    applyCalls += 1;
    applyFilterUiState({
        filters: state,
        document,
        updateMetadataFilterButton: () => {
            metadataCalls += 1;
        },
    });
};

const urlRestored = restoreFilters({
    storage,
    location: {
        search: '?orientation=portrait&compared=confident&min_stars=3&folder=%2FTrips&flag=rejected&date_taken=2024&file_type=.jpg&camera=Fuji&lens=XF%2035mm&people=42',
    },
    setFilters,
    applyFilterUiState: applyCurrentFilters,
});

assert.equal(urlRestored.orientation, 'portrait');
assert.equal(urlRestored.rating, 3);
assert.equal(state.folder, '/Trips');
assert.equal(storage.getItem(FILTER_STORAGE_KEY), null);
assert.equal(applyCalls, 1);
assert.equal(metadataCalls, 1);
assert.equal(document.icons[1].classList.contains('active'), true);
assert.equal(document.icons[4].classList.contains('active'), true);
assert.equal(document.flags[2].classList.contains('active'), true);
assert.equal(document.stars[0].classList.contains('lit'), true);
assert.equal(document.stars[2].textContent, '★');
assert.equal(document.stars[3].classList.contains('lit'), false);
assert.equal(document.stars[3].textContent, '☆');
assert.equal(document.selects['filter-folder'].value, '/Trips');
assert.equal(document.selects['filter-people'].value, '42');

saveFilters({
    getFilters: () => state,
    storage,
    syncLibraryUrlState: () => {
        syncCalls += 1;
    },
});
assert.equal(syncCalls, 1);
assert.deepEqual(JSON.parse(storage.getItem(FILTER_STORAGE_KEY)), state);

storage.setItem(FILTER_STORAGE_KEY, JSON.stringify({
    orientation: 'landscape',
    rating: '2',
    flag: 'picked',
    people: '77',
}));
const savedRestored = restoreFilters({
    storage,
    location: { search: '' },
    setFilters,
    applyFilterUiState: applyCurrentFilters,
});

assert.equal(savedRestored.orientation, 'landscape');
assert.equal(savedRestored.rating, '2');
assert.equal(document.icons[0].classList.contains('active'), true);
assert.equal(document.icons[1].classList.contains('active'), false);
assert.equal(document.flags[0].classList.contains('active'), true);
assert.equal(document.flags[2].classList.contains('active'), false);
assert.equal(document.stars[1].classList.contains('lit'), true);
assert.equal(document.stars[2].classList.contains('lit'), false);
assert.equal(document.selects['filter-people'].value, '77');

let actionSaves = 0;
let actionReloads = 0;
let actionMetadataUpdates = 0;
const actionContext = () => ({
    filters: state,
    document,
    updateMetadataFilterButton: () => {
        actionMetadataUpdates += 1;
    },
    saveFilters: () => {
        actionSaves += 1;
    },
    reloadForFilters: () => {
        actionReloads += 1;
    },
});

state = setFilter('people', '88', actionContext());
assert.equal(state.people, '88');
assert.equal(actionMetadataUpdates, 1);
assert.equal(actionSaves, 1);
assert.equal(actionReloads, 1);

state = toggleFilter('flag', 'picked', document.flags[0], actionContext());
assert.equal(state.flag, '');
assert.equal(document.flags[0].classList.contains('active'), false);
assert.equal(actionSaves, 2);
assert.equal(actionReloads, 2);

state = toggleFilter('orientation', 'portrait', document.icons[1], actionContext());
assert.equal(state.orientation, 'portrait');
assert.equal(document.icons[0].classList.contains('active'), false);
assert.equal(document.icons[1].classList.contains('active'), true);
assert.equal(actionSaves, 3);
assert.equal(actionReloads, 3);

state = toggleStar(4, actionContext());
assert.equal(state.rating, 4);
assert.equal(document.stars[3].classList.contains('lit'), true);
assert.equal(document.stars[4].classList.contains('lit'), false);
assert.equal(actionSaves, 4);
assert.equal(actionReloads, 4);

let clearSavedState = null;
state = clearLibraryFilters({
    emptyFilters: {
        orientation: '',
        compared: '',
        rating: '',
        folder: '',
        flag: '',
        taken: '',
        fileType: '',
        camera: '',
        lens: '',
        people: '',
    },
    document,
    setFilters: nextFilters => {
        state = nextFilters;
    },
    updateMetadataFilterButton: () => {
        actionMetadataUpdates += 1;
    },
    saveFilters: () => {
        actionSaves += 1;
        clearSavedState = { ...state };
    },
    reloadForFilters: () => {
        actionReloads += 1;
    },
});
assert.equal(state.orientation, '');
assert.equal(state.rating, '');
assert.equal(state.people, '');
assert.deepEqual(clearSavedState, state);
assert.equal(document.icons[1].classList.contains('active'), false);
assert.equal(document.stars[3].classList.contains('lit'), false);
assert.equal(document.stars[3].textContent, '☆');
assert.equal(document.selects['filter-people'].value, '');
assert.equal(actionMetadataUpdates, 2);
assert.equal(actionSaves, 5);
assert.equal(actionReloads, 5);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_compare_actions_node_probe_preserves_request_contracts(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import {
    applyComparisonElos,
    buildComparisonPayload,
    postComparison,
    postUndoComparison,
    undoComparisonToastText,
} from './static/js/compare/actions.js';

const pair = {
    left: { id: 11, elo: 1500 },
    right: { id: 22, elo: 1400 },
};
assert.deepEqual(buildComparisonPayload(pair, 'left', 'swiss'), {
    winner_id: 11,
    loser_id: 22,
    mode: 'swiss',
});
assert.deepEqual(buildComparisonPayload(pair, 'right', 'topn'), {
    winner_id: 22,
    loser_id: 11,
    mode: 'topn',
});

const calls = [];
const saveResult = await postComparison(
    buildComparisonPayload(pair, 'right', 'topn'),
    {
        fetchImpl: async (url, init) => {
            calls.push({ url, init });
            return {
                ok: true,
                json: async () => ({ winner_elo: 1512, loser_elo: 1398 }),
            };
        },
    },
);
assert.equal(calls[0].url, '/api/compare');
assert.equal(calls[0].init.method, 'POST');
assert.deepEqual(JSON.parse(calls[0].init.body), {
    winner_id: 22,
    loser_id: 11,
    mode: 'topn',
});
applyComparisonElos(pair, 'right', saveResult);
assert.equal(pair.right.elo, 1512);
assert.equal(pair.left.elo, 1398);

const undoResult = await postUndoComparison({
    fetchImpl: async (url, init) => {
        assert.equal(url, '/api/compare/undo');
        assert.equal(init.method, 'POST');
        return {
            ok: true,
            json: async () => ({ comparisons_undone: 2 }),
        };
    },
});
assert.equal(undoResult.ok, true);
assert.equal(undoResult.comparisonsUndone, 2);
assert.equal(undoComparisonToastText(2), 'Undid 2 comparisons');
assert.equal(undoComparisonToastText(1), 'Undid 1 comparison');
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_compare_status_controller_node_probe_preserves_progress_and_propagation_flow(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createCompareStatusController } from './static/js/compare/status_controller.js';

const stats = {
    ranking_signal_count: 10,
    total_comparisons: 10,
    direct_comparison_rows: 4,
    filtered_pool_visible: 2,
    total_images: 10,
    rated_images: 1,
};
const events = [];
const controller = createCompareStatusController({
    getCompareStats: () => stats,
    nowImpl: () => 100,
    coverageStatsThrottleMs: 20,
    fetchImpl: async (url) => {
        events.push(['fetch', url]);
        assert.equal(url, '/api/stats');
        return {
            async json() {
                return {
                    total_images: 20,
                    rated_images: 5,
                    total_comparisons: 17,
                    ranking_signal_count: 19,
                };
            },
        };
    },
    renderCoverageBarImpl: (nextStats) => {
        events.push(['coverage', nextStats.total_images, nextStats.rated_images]);
        return true;
    },
    renderCompareProgressImpl: ({
        stats: progressStats,
        displayedComparisons,
        updateCoverageBarImpl,
        rollUpCounterImpl,
    }) => {
        events.push(['progress', progressStats.ranking_signal_count, displayedComparisons]);
        updateCoverageBarImpl();
        rollUpCounterImpl('counter', displayedComparisons, progressStats.ranking_signal_count);
        return progressStats.ranking_signal_count;
    },
    rollUpCounterImpl: (el, from, to) => events.push(['roll', el, from, to]),
    fetchPropagationCountImpl: (directCount, { onApply, onBadge }) => {
        events.push(['propagation-request', directCount]);
        onApply(Number(directCount) + 3, Number(directCount));
        onBadge(3);
        return 'propagation-ok';
    },
    showPropagationBadgeImpl: (count) => {
        events.push(['badge', count]);
        return true;
    },
});

controller.bumpRankingSignals(2, 1);
assert.equal(stats.ranking_signal_count, 12);
assert.equal(stats.total_comparisons, 12);
assert.equal(stats.direct_comparison_rows, 5);

assert.equal(controller.updateCompareProgress(), 12);
await controller.coverageStatsFetchPromise();
assert.equal(stats.total_images, 20);
assert.equal(stats.rated_images, 5);
assert.equal(stats.ranking_signal_count, 19);
assert.deepEqual(events, [
    ['progress', 12, -1],
    ['coverage', 10, 1],
    ['fetch', '/api/stats'],
    ['roll', 'counter', -1, 12],
    ['coverage', 20, 5],
    ['progress', 19, 12],
    ['coverage', 20, 5],
    ['roll', 'counter', 12, 19],
]);

events.length = 0;
assert.equal(controller.updateCoverageBar(), false);
assert.deepEqual(events, [['coverage', 20, 5]]);

events.length = 0;
assert.equal(controller.fetchPropagationCount(1), 'propagation-ok');
assert.equal(stats.ranking_signal_count, 23);
assert.equal(stats.total_comparisons, 23);
assert.equal(stats.direct_comparison_rows, 6);
assert.deepEqual(events, [
    ['propagation-request', 1],
    ['progress', 23, 19],
    ['coverage', 20, 5],
    ['roll', 'counter', 19, 23],
    ['badge', 3],
]);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_compare_action_controller_node_probe_preserves_submit_and_undo_flow(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createCompareActionController } from './static/js/compare/action_controller.js';

let compareBusy = false;
let undoCount = 0;
let compareActionSeq = 0;
let compareIndex = 0;
let compareMode = 'swiss';
let comparePairs = [
    { left: { id: 7, elo: 1200 }, right: { id: 8, elo: 1100 } },
];
const events = [];
const controller = createCompareActionController({
    getCompareBusy: () => compareBusy,
    setCompareBusy: (busy) => {
        compareBusy = busy;
        events.push(['busy', busy]);
    },
    setUndoCount: (count) => {
        undoCount = count;
        events.push(['undoCount', count]);
    },
    incrementUndoCount: () => {
        undoCount += 1;
        events.push(['undoCount', undoCount]);
        return undoCount;
    },
    incrementCompareActionSeq: () => {
        compareActionSeq += 1;
        events.push(['seq', compareActionSeq]);
        return compareActionSeq;
    },
    getCompareActionSeq: () => compareActionSeq,
    getCompareIndex: () => compareIndex,
    setCompareIndex: (index) => {
        compareIndex = index;
        events.push(['index', index]);
    },
    getComparePairs: () => comparePairs,
    getCompareMode: () => compareMode,
    showComparePair: () => events.push(['show']),
    showToast: (message) => events.push(['toast', message]),
    fetchPropagationCount: (count) => events.push(['propagation', count]),
    bumpRankingSignals: (signalDelta, directDelta) => events.push(['bump', signalDelta, directDelta]),
    updateCompareProgress: () => events.push(['progress']),
    buildComparisonPayloadImpl: (pair, side, mode) => {
        events.push(['payload', pair.left.id, side, mode]);
        return { pairId: pair.left.id, side, mode };
    },
    postComparisonImpl: async (payload) => {
        events.push(['post', payload.side, payload.mode]);
        return { winner_elo: 1300, loser_elo: 1050 };
    },
    applyComparisonElosImpl: (pair, side, result) => {
        events.push(['apply', side, result.winner_elo, result.loser_elo]);
        pair.left.elo = result.winner_elo;
    },
    postUndoComparisonImpl: async () => {
        events.push(['undoPost']);
        return { ok: true, comparisonsUndone: 2 };
    },
    undoComparisonToastTextImpl: (count) => `undo ${count}`,
});

assert.equal(controller.submitComparison('left'), true);
await Promise.resolve();
assert.equal(compareIndex, 1);
assert.equal(compareBusy, false);
assert.equal(comparePairs[0].left.elo, 1300);
assert.deepEqual(events, [
    ['busy', true],
    ['undoCount', 0],
    ['seq', 1],
    ['payload', 7, 'left', 'swiss'],
    ['index', 1],
    ['show'],
    ['busy', false],
    ['post', 'left', 'swiss'],
    ['apply', 'left', 1300, 1050],
    ['propagation', 1],
]);

events.length = 0;
compareIndex = 0;
const failingController = createCompareActionController({
    getCompareBusy: () => false,
    setCompareBusy: (busy) => events.push(['busy', busy]),
    setUndoCount: (count) => events.push(['undoCount', count]),
    incrementCompareActionSeq: () => {
        compareActionSeq += 1;
        return compareActionSeq;
    },
    getCompareActionSeq: () => compareActionSeq,
    getCompareIndex: () => compareIndex,
    setCompareIndex: (index) => {
        compareIndex = index;
        events.push(['index', index]);
    },
    getComparePairs: () => comparePairs,
    getCompareMode: () => 'topn',
    showComparePair: () => events.push(['show']),
    showToast: (message) => events.push(['toast', message]),
    fetchPropagationCount: () => {},
    bumpRankingSignals: () => {},
    updateCompareProgress: () => {},
    postComparisonImpl: async () => {
        throw new Error('boom');
    },
});
assert.equal(failingController.submitComparison('right'), true);
await Promise.resolve();
await Promise.resolve();
assert.equal(compareIndex, 0);
assert.deepEqual(events, [
    ['busy', true],
    ['undoCount', 0],
    ['index', 1],
    ['show'],
    ['busy', false],
    ['index', 0],
    ['show'],
    ['toast', 'Failed to save comparison; restored the previous pair'],
]);

events.length = 0;
compareBusy = false;
undoCount = 0;
compareIndex = 1;
compareMode = 'swiss';
assert.equal(await controller.undoComparison(), true);
assert.equal(compareBusy, false);
assert.equal(compareIndex, 0);
assert.deepEqual(events, [
    ['undoCount', 1],
    ['busy', true],
    ['undoPost'],
    ['bump', -2, -2],
    ['progress'],
    ['index', 0],
    ['show'],
    ['busy', false],
]);

events.length = 0;
undoCount = 3;
assert.equal(await controller.undoComparison(), false);
assert.deepEqual(events, [
    ['undoCount', 4],
    ['toast', 'Maximum undo reached'],
]);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_mosaic_action_controller_node_probe_preserves_pick_and_rollback_flow(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createMosaicActionController } from './static/js/compare/mosaic_action_controller.js';

function makeCell() {
    const imgEl = {
        dataset: {},
        src: '',
        alt: '',
        classList: {
            added: [],
            add(value) {
                this.added.push(value);
            },
        },
    };
    return {
        dataset: {},
        clientHeight: 260,
        onclick: null,
        imgEl,
        classList: {
            added: [],
            removed: [],
            add(value) {
                this.added.push(value);
            },
            remove(value) {
                this.removed.push(value);
            },
        },
        querySelector(selector) {
            return selector === 'img' ? imgEl : null;
        },
    };
}

let mosaicBusy = false;
let undoCount = 1;
let mosaicActionSeq = 0;
let mosaicImages = [
    { id: 1, filename: 'one.jpg', thumb_url: '/one.jpg' },
    { id: 2, filename: 'two.jpg', thumb_url: '/two.jpg' },
    { id: 3, filename: 'three.jpg', thumb_url: '/three.jpg' },
];
let mosaicAge = [0, 11, 4];
let mosaicReplacements = [
    { id: 4, filename: 'four.jpg', thumb_url: '/four.jpg' },
    { id: 5, filename: 'five.jpg', thumb_url: '/five.jpg' },
];
let mosaicRenderToken = 17;
let mosaicPropagationCounts = { 1: 2 };
let compareStats = { visible: 3 };
let cells = [makeCell(), makeCell(), makeCell()];
const events = [];

const controller = createMosaicActionController({
    getMosaicBusy: () => mosaicBusy,
    setMosaicBusy: (busy) => {
        mosaicBusy = busy;
        events.push(['busy', busy]);
    },
    setUndoCount: (count) => {
        undoCount = count;
        events.push(['undoCount', count]);
    },
    incrementMosaicActionSeq: () => {
        mosaicActionSeq += 1;
        events.push(['seq', mosaicActionSeq]);
        return mosaicActionSeq;
    },
    getMosaicActionSeq: () => mosaicActionSeq,
    getMosaicImages: () => mosaicImages,
    setMosaicImages: (images) => {
        mosaicImages = images;
        events.push(['setImages', images.map((img) => img.id)]);
    },
    getMosaicAge: () => mosaicAge,
    setMosaicAge: (age) => {
        mosaicAge = age;
        events.push(['setAge', [...age]]);
    },
    getMosaicReplacements: () => mosaicReplacements,
    setMosaicReplacements: (replacements) => {
        mosaicReplacements = replacements;
        events.push(['setReplacements', replacements.map((img) => img.id)]);
    },
    getMosaicRenderToken: () => mosaicRenderToken,
    getMosaicPropagationCounts: () => mosaicPropagationCounts,
    setMosaicPropagationCounts: (counts) => {
        mosaicPropagationCounts = counts;
        events.push(['setPropagationCounts', { ...counts }]);
    },
    getCompareStats: () => compareStats,
    setCompareStats: (stats) => {
        compareStats = stats;
        events.push(['setStats', { ...stats }]);
    },
    queryMosaicCells: () => cells,
    scheduleMosaicImageUpgrade: (_cell, img, height, token, index) => {
        events.push(['upgrade', img.id, height, token, index]);
    },
    mosaicFillReplacements: () => events.push(['fill']),
    precomputePropagation: () => events.push(['precompute']),
    bumpRankingSignals: (signalDelta, directDelta) => events.push(['bump', signalDelta, directDelta]),
    updateCompareProgress: () => events.push(['progress']),
    fetchPropagationCount: (count) => events.push(['fetchPropagation', count]),
    showPropagationBadge: (count) => events.push(['badge', count]),
    renderMosaic: () => events.push(['render']),
    showToast: (message) => events.push(['toast', message]),
    showCompareEmpty: () => events.push(['empty']),
    replacementLowWater: 2,
    postMosaicPickImpl: async (winnerId, loserIds) => {
        events.push(['post', winnerId, loserIds]);
        return { ok: true };
    },
});

assert.equal(controller.mosaicClick(1), true);
await Promise.resolve();
assert.equal(mosaicBusy, false);
assert.equal(undoCount, 0);
assert.deepEqual(mosaicImages.map((img) => img.id), [4, 5, 3]);
assert.deepEqual(mosaicAge, [0, 0, 5]);
assert.deepEqual(mosaicReplacements, []);
assert.equal(cells[0].dataset.id, 4);
assert.equal(cells[0].imgEl.src, '/four.jpg');
assert.equal(cells[0].imgEl.alt, 'four.jpg');
assert.deepEqual(events, [
    ['busy', true],
    ['undoCount', 0],
    ['seq', 1],
    ['post', 1, [2, 3]],
    ['bump', 4, 2],
    ['progress'],
    ['badge', 2],
    ['fill'],
    ['upgrade', 4, 260, 17, 0],
    ['fill'],
    ['upgrade', 5, 260, 17, 1],
    ['fill'],
    ['busy', false],
    ['fill'],
    ['precompute'],
]);

events.length = 0;
mosaicBusy = false;
undoCount = 1;
mosaicActionSeq = 0;
mosaicImages = [
    { id: 10, filename: 'ten.jpg', thumb_url: '/ten.jpg' },
    { id: 11, filename: 'eleven.jpg', thumb_url: '/eleven.jpg' },
];
mosaicAge = [0, 0];
mosaicReplacements = [
    { id: 12, filename: 'twelve.jpg', thumb_url: '/twelve.jpg' },
];
mosaicRenderToken = 22;
mosaicPropagationCounts = {};
compareStats = { visible: 2 };
cells = [makeCell(), makeCell()];

const failingController = createMosaicActionController({
    getMosaicBusy: () => mosaicBusy,
    setMosaicBusy: (busy) => {
        mosaicBusy = busy;
        events.push(['busy', busy]);
    },
    setUndoCount: (count) => {
        undoCount = count;
        events.push(['undoCount', count]);
    },
    incrementMosaicActionSeq: () => {
        mosaicActionSeq += 1;
        events.push(['seq', mosaicActionSeq]);
        return mosaicActionSeq;
    },
    getMosaicActionSeq: () => mosaicActionSeq,
    getMosaicImages: () => mosaicImages,
    setMosaicImages: (images) => {
        mosaicImages = images;
        events.push(['setImages', images.map((img) => img.id)]);
    },
    getMosaicAge: () => mosaicAge,
    setMosaicAge: (age) => {
        mosaicAge = age;
        events.push(['setAge', [...age]]);
    },
    getMosaicReplacements: () => mosaicReplacements,
    setMosaicReplacements: (replacements) => {
        mosaicReplacements = replacements;
        events.push(['setReplacements', replacements.map((img) => img.id)]);
    },
    getMosaicRenderToken: () => mosaicRenderToken,
    getMosaicPropagationCounts: () => mosaicPropagationCounts,
    setMosaicPropagationCounts: (counts) => {
        mosaicPropagationCounts = counts;
        events.push(['setPropagationCounts', { ...counts }]);
    },
    getCompareStats: () => compareStats,
    setCompareStats: (stats) => {
        compareStats = stats;
        events.push(['setStats', { ...stats }]);
    },
    queryMosaicCells: () => cells,
    scheduleMosaicImageUpgrade: (_cell, img, height, token, index) => {
        events.push(['upgrade', img.id, height, token, index]);
    },
    mosaicFillReplacements: () => events.push(['fill']),
    precomputePropagation: () => events.push(['precompute']),
    bumpRankingSignals: (signalDelta, directDelta) => events.push(['bump', signalDelta, directDelta]),
    updateCompareProgress: () => events.push(['progress']),
    fetchPropagationCount: (count) => events.push(['fetchPropagation', count]),
    showPropagationBadge: (count) => events.push(['badge', count]),
    renderMosaic: () => events.push(['render']),
    showToast: (message) => events.push(['toast', message]),
    showCompareEmpty: () => events.push(['empty']),
    postMosaicPickImpl: async () => ({ ok: false }),
});

assert.equal(failingController.mosaicClick(10), true);
await Promise.resolve();
assert.deepEqual(mosaicImages.map((img) => img.id), [10, 11]);
assert.deepEqual(mosaicAge, [0, 0]);
assert.deepEqual(mosaicReplacements.map((img) => img.id), [12]);
assert.deepEqual(events, [
    ['busy', true],
    ['undoCount', 0],
    ['seq', 1],
    ['bump', 1, 1],
    ['progress'],
    ['fill'],
    ['upgrade', 12, 260, 22, 0],
    ['fill'],
    ['busy', false],
    ['setImages', [10, 11]],
    ['setAge', [0, 0]],
    ['setReplacements', [12]],
    ['setStats', { visible: 2 }],
    ['setPropagationCounts', {}],
    ['render'],
    ['progress'],
    ['toast', 'Failed to save pick; restored the previous grid'],
]);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_mosaic_replacement_buffer_node_probe_preserves_fetch_dedupe_and_retry_flow(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createMosaicReplacementBuffer } from './static/js/compare/mosaic_replacements.js';

let mosaicFilling = false;
let mosaicImages = [
    { id: 1 },
    { id: 2 },
];
let mosaicReplacements = [
    { id: 3, thumb_url: '/three.jpg' },
];
let mosaicRenderToken = 9;
let warmupGeneration = 4;
let compareStats = {};
const events = [];
const timers = [];
const enqueued = [];

const buffer = createMosaicReplacementBuffer({
    getMosaicFilling: () => mosaicFilling,
    setMosaicFilling: (filling) => {
        mosaicFilling = filling;
        events.push(['filling', filling]);
    },
    getMosaicImages: () => mosaicImages,
    getMosaicReplacements: () => mosaicReplacements,
    getMosaicRenderToken: () => mosaicRenderToken,
    getWarmupGeneration: () => warmupGeneration,
    enqueueWarmup: (task, options) => {
        enqueued.push({ task, options });
        events.push(['enqueue', options.generation]);
    },
    buildMosaicUrl: ({ n, exclude }) => {
        events.push(['url', n, exclude]);
        return `/api/mosaic/next?n=${n}&exclude=${exclude}`;
    },
    fetchImpl: async (url) => {
        events.push(['fetch', url]);
        return {
            async json() {
                return {
                    stats: { visible: 5 },
                    images: [
                        { id: 2, thumb_url: '/two.jpg' },
                        { id: 3, thumb_url: '/three.jpg' },
                        { id: 4, thumb_url: '/four.jpg' },
                        { id: 5, thumb_url: '/five.jpg' },
                        { id: 4, thumb_url: '/four-duplicate.jpg' },
                    ],
                };
            },
        };
    },
    loadImageProbe: async (url, options) => {
        events.push(['probe', url, options.priority, options.timeoutMs]);
        return { ok: url !== '/five.jpg' };
    },
    setCompareStats: (stats) => {
        compareStats = stats;
        events.push(['stats', { ...stats }]);
    },
    updateCompareProgress: () => events.push(['progress']),
    setTimeoutImpl: (callback, delayMs) => {
        timers.push({ callback, delayMs });
        events.push(['retry', delayMs]);
    },
    replacementTarget: 3,
    replacementFetchMin: 2,
    replacementProbeConcurrency: 1,
    replacementPreloadTimeoutMs: 123,
});

assert.equal(buffer.fillReplacements(), true);
assert.equal(mosaicFilling, true);
assert.equal(enqueued.length, 1);
assert.equal(enqueued[0].options.generation, 4);
assert.deepEqual(events, [
    ['filling', true],
    ['enqueue', 4],
]);

await enqueued[0].task();
assert.equal(mosaicFilling, false);
assert.deepEqual(mosaicReplacements.map((img) => img.id), [3, 4]);
assert.deepEqual(compareStats, { visible: 5 });
assert.equal(timers.length, 0);
assert.deepEqual(events, [
    ['filling', true],
    ['enqueue', 4],
    ['url', 2, '1,2,3'],
    ['fetch', '/api/mosaic/next?n=2&exclude=1,2,3'],
    ['stats', { visible: 5 }],
    ['progress'],
    ['probe', '/four.jpg', 'auto', 123],
    ['probe', '/five.jpg', 'auto', 123],
    ['filling', false],
]);

events.length = 0;
mosaicFilling = true;
assert.equal(buffer.fillReplacements(), false);
assert.deepEqual(events, []);

mosaicFilling = false;
mosaicReplacements = [];
const retryBuffer = createMosaicReplacementBuffer({
    getMosaicFilling: () => mosaicFilling,
    setMosaicFilling: (filling) => {
        mosaicFilling = filling;
        events.push(['retry-filling', filling]);
    },
    getMosaicImages: () => [],
    getMosaicReplacements: () => mosaicReplacements,
    getMosaicRenderToken: () => 1,
    getWarmupGeneration: () => 1,
    enqueueWarmup: (task, options) => enqueued.push({ task, options }),
    buildMosaicUrl: () => '/retry',
    fetchImpl: async () => ({
        async json() {
            return { images: [{ id: 9, thumb_url: '/nine.jpg' }] };
        },
    }),
    loadImageProbe: async () => ({ ok: false }),
    setCompareStats: () => {},
    updateCompareProgress: () => {},
    setTimeoutImpl: (callback, delayMs) => {
        timers.push({ callback, delayMs });
        events.push(['retry-scheduled', delayMs]);
    },
    replacementTarget: 4,
    replacementFetchMin: 2,
});
assert.equal(retryBuffer.fillReplacements(), true);
await enqueued.at(-1).task();
assert.deepEqual(events, [
    ['retry-filling', true],
    ['retry-scheduled', 300],
    ['retry-filling', false],
]);
assert.equal(typeof timers.at(-1).callback, 'function');

events.length = 0;
mosaicFilling = true;
enqueued.at(-1).options.onDrop();
assert.deepEqual(events, [['retry-filling', false]]);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_compare_mosaic_node_probe_preserves_grid_and_pick_contracts(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import {
    mosaicGridElo,
    mosaicLoserIds,
    mosaicReplacementIndices,
    mosaicSizeFromThumbHeight,
    mosaicThumbHeightForSize,
    postMosaicPick,
    renderMosaicGrid,
} from './static/js/compare/mosaic.js';

const images = [
    { id: 1, filename: 'first.jpg', elo: 1200, aspect_ratio: 2, thumb_url: '/api/thumb/sm/1' },
    { id: 2, filename: 'second.jpg', elo: 1400, aspect_ratio: 1, thumb_url: '/api/thumb/sm/2' },
];
assert.equal(mosaicGridElo(images), 1300);
assert.equal(mosaicGridElo([]), 0);
assert.equal(mosaicSizeFromThumbHeight(mosaicThumbHeightForSize(12)), 12);
assert.deepEqual(mosaicLoserIds(images, 1), [2]);
assert.deepEqual(mosaicReplacementIndices([0, 12, 4], 0), [0, 1]);
assert.deepEqual(mosaicReplacementIndices([0, 9, 4], 0), [0]);

function makeElement(tag) {
    return {
        tag,
        className: '',
        dataset: {},
        style: {},
        children: [],
        isConnected: true,
        onclick: null,
        parentElement: null,
        appendChild(child) {
            child.parentElement = this;
            this.children.push(child);
        },
        querySelector(selector) {
            if (selector === 'img') return this.children.find((child) => child.tag === 'img') || null;
            return null;
        },
        classList: {
            added: [],
            removed: [],
            add(value) { this.added.push(value); },
            remove(value) { this.removed.push(value); },
        },
    };
}

const grid = makeElement('div');
grid.clientWidth = 500;
grid.clientHeight = 240;
const picked = [];
const preloaded = [];
const upgradeJobs = [];
const documentImpl = {
    createElement: makeElement,
    createDocumentFragment: () => makeElement('fragment'),
    querySelector: () => ({ offsetHeight: 60 }),
};
const result = renderMosaicGrid({
    images,
    grid,
    token: 9,
    documentImpl,
    windowImpl: { innerWidth: 800, innerHeight: 600 },
    onPick: (id) => picked.push(id),
    preloadImage: (url) => preloaded.push(url),
    scheduleImageUpgrade: (...args) => upgradeJobs.push(args),
});
assert.equal(result.rendered, true);
assert.equal(grid.children.length, 1);
const frag = grid.children[0];
assert.equal(frag.children.length, 2);
const firstCell = frag.children[0];
assert.equal(firstCell.className, 'mosaic-cell skeleton-cell');
assert.equal(firstCell.dataset.id, 1);
assert.match(firstCell.style.height, /^\d+px$/);
assert.equal(firstCell.style.flexGrow, 2);
assert.equal(firstCell.children[0].src, '/api/thumb/sm/1');
assert.equal(firstCell.children[0].alt, 'first.jpg');
firstCell.onclick();
assert.deepEqual(picked, [1]);
assert.deepEqual(preloaded, ['/api/thumb/sm/1', '/api/thumb/sm/2']);
assert.equal(upgradeJobs.length, 2);

const fetches = [];
const success = await postMosaicPick(1, [2, 3], {
    setTimeoutImpl: (fn) => fn(),
    fetchImpl: async (url, options) => {
        fetches.push({ url, options });
        return { ok: true, async json() { return { ok: true, replacement: { id: 4 } }; } };
    },
});
assert.equal(success.ok, true);
assert.equal(fetches[0].url, '/api/mosaic/pick');
assert.equal(fetches[0].options.method, 'POST');
assert.equal(fetches[0].options.headers['Content-Type'], 'application/json');
assert.deepEqual(JSON.parse(fetches[0].options.body), { winner_id: 1, loser_ids: [2, 3] });

const failure = await postMosaicPick(1, [2], {
    setTimeoutImpl: (fn) => fn(),
    fetchImpl: async () => ({ ok: false, async json() { return { error: 'nope' }; } }),
});
assert.equal(failure.ok, false);
assert.equal(failure.error.message, 'nope');
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_people_controller_node_probe_preserves_public_adapter(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createPeopleApi } from './static/js/people/controller.js';

const calls = [];
const showToast = (message) => calls.push(['toast', message]);
const api = createPeopleApi({
    initPeople: (...args) => calls.push(['initPeople', args]),
    loadPeople: (...args) => {
        calls.push(['loadPeople', args]);
        return 'loaded';
    },
    rememberPeopleLabelDraft: (...args) => calls.push(['rememberPeopleLabelDraft', args]),
    useFallbackThumb: (...args) => {
        calls.push(['useFallbackThumb', args]);
        return true;
    },
    labelPerson: (personId, options) => {
        calls.push(['labelPerson', personId, typeof options.loadPeople, options.showToast === showToast]);
        return options.loadPeople('after-label');
    },
    mergePeople: (sourcePersonId, targetPersonId, options) => {
        calls.push(['mergePeople', sourcePersonId, targetPersonId, typeof options.loadPeople, options.showToast === showToast]);
    },
    rejectPeopleMerge: (suggestionId, options) => {
        calls.push(['rejectPeopleMerge', suggestionId, typeof options.loadPeople, options.showToast === showToast]);
    },
    ignorePerson: (personId, options) => {
        calls.push(['ignorePerson', personId, typeof options.loadPeople, options.showToast === showToast]);
    },
    filterLibraryByPerson: (...args) => calls.push(['filterLibraryByPerson', args]),
    showToast,
});

api.initPeople('now');
api.rememberPeopleLabelDraft(7, 'Ada');
assert.equal(api.useFallbackThumb({ dataset: { fallbackSrc: '/fallback.jpg' } }), true);
assert.equal(await api.labelPerson(7), 'loaded');
await api.mergePeople(7, 8);
await api.rejectPeopleMerge(12);
await api.ignorePerson(9);
api.filterLibraryByPerson(7);

assert.deepEqual(calls, [
    ['initPeople', ['now']],
    ['rememberPeopleLabelDraft', [7, 'Ada']],
    ['useFallbackThumb', [{ dataset: { fallbackSrc: '/fallback.jpg' } }]],
    ['labelPerson', 7, 'function', true],
    ['loadPeople', ['after-label']],
    ['mergePeople', 7, 8, 'function', true],
    ['rejectPeopleMerge', 12, 'function', true],
    ['ignorePerson', 9, 'function', true],
    ['filterLibraryByPerson', [7]],
]);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_settings_controller_node_probe_preserves_public_adapter(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createSettingsApi } from './static/js/settings/controller.js';

const calls = [];
const collectSettingsForm = () => ({ saved: true });
const populateSettingsForm = () => {};
const rememberThumbnailOutput = () => {};
const renderSettingsMeta = () => {};
const setSettingsStatus = () => {};
const showToast = () => {};
const updateThumbnailChangeNotice = () => {};
const showConfirmModal = () => {};
const formatBytes = () => '1 MB';
const renderCacheSettingsStatus = () => {};
const renderAISettingsStatus = () => {};
const renderModelStatus = () => {};
const updateCacheProfileHint = () => calls.push(['updateCacheProfileHint']);
const elements = {
    cache_profile: { value: '' },
    memory_cache_gb: { value: '' },
    pregenerate_on_idle: { checked: false },
};

const api = createSettingsApi({
    collectSettingsForm,
    populateSettingsForm,
    rememberThumbnailOutput,
    renderSettingsMeta,
    setSettingsStatus,
    showToast,
    updateThumbnailChangeNotice,
    showConfirmModal,
    formatBytes,
    renderCacheSettingsStatus,
    renderAISettingsStatus,
    renderModelStatus,
    updateCacheProfileHint,
    getSettingsPageData: () => ({ settings: { seed: 42 } }),
    documentImpl: {
        getElementById: (id) => elements[id] || null,
    },
    recommendedMemoryGb: (settings) => {
        calls.push(['recommendedMemoryGb', settings.seed]);
        return 12;
    },
    saveSettings: (options) => {
        calls.push([
            'saveSettings',
            options.collectSettingsForm === collectSettingsForm,
            options.populateSettingsForm === populateSettingsForm,
            options.rememberThumbnailOutput === rememberThumbnailOutput,
            options.renderSettingsMeta === renderSettingsMeta,
            options.setSettingsStatus === setSettingsStatus,
            options.showToast === showToast,
            options.updateThumbnailChangeNotice === updateThumbnailChangeNotice,
        ]);
        return 'save-result';
    },
    resetSettings: (options) => {
        calls.push([
            'resetSettings',
            options.populateSettingsForm === populateSettingsForm,
            options.rememberThumbnailOutput === rememberThumbnailOutput,
            options.renderSettingsMeta === renderSettingsMeta,
            options.setSettingsStatus === setSettingsStatus,
            options.showConfirmModal === showConfirmModal,
            options.showToast === showToast,
            options.updateThumbnailChangeNotice === updateThumbnailChangeNotice,
        ]);
        return 'reset-result';
    },
    clearThumbnailCache: (options) => {
        calls.push([
            'clearThumbnailCache',
            options.formatBytes === formatBytes,
            options.renderSettingsMeta === renderSettingsMeta,
            options.setSettingsStatus === setSettingsStatus,
            options.showConfirmModal === showConfirmModal,
            options.showToast === showToast,
        ]);
        return 'clear-result';
    },
    startCachePregeneration: (options) => {
        calls.push([
            'startCachePregeneration',
            options.renderCacheSettingsStatus === renderCacheSettingsStatus,
            options.setSettingsStatus === setSettingsStatus,
            options.showToast === showToast,
        ]);
        return 'start-result';
    },
    stopCachePregeneration: (options) => {
        calls.push([
            'stopCachePregeneration',
            options.renderCacheSettingsStatus === renderCacheSettingsStatus,
            options.setSettingsStatus === setSettingsStatus,
            options.showToast === showToast,
        ]);
        return 'stop-result';
    },
    pauseEmbeddings: (options) => {
        calls.push([
            'pauseEmbeddings',
            options.renderAISettingsStatus === renderAISettingsStatus,
            options.setSettingsStatus === setSettingsStatus,
            options.showToast === showToast,
        ]);
        return 'pause-result';
    },
    resumeEmbeddings: (options) => {
        calls.push([
            'resumeEmbeddings',
            options.renderAISettingsStatus === renderAISettingsStatus,
            options.setSettingsStatus === setSettingsStatus,
            options.showToast === showToast,
        ]);
        return 'resume-result';
    },
    installAIModel: (role, options) => {
        calls.push([
            'installAIModel',
            role,
            options.collectSettingsForm === collectSettingsForm,
            options.populateSettingsForm === populateSettingsForm,
            options.rememberThumbnailOutput === rememberThumbnailOutput,
            options.renderSettingsMeta === renderSettingsMeta,
            options.renderAISettingsStatus === renderAISettingsStatus,
            options.renderModelStatus === renderModelStatus,
            options.setSettingsStatus === setSettingsStatus,
            options.showToast === showToast,
            options.updateThumbnailChangeNotice === updateThumbnailChangeNotice,
        ]);
        return 'install-result';
    },
});

assert.equal(await api.saveSettings(), undefined);
assert.equal(api.resetSettings(), 'reset-result');
assert.equal(api.clearThumbnailCache(), 'clear-result');
assert.equal(await api.startCachePregeneration(), undefined);
assert.equal(await api.stopCachePregeneration(), undefined);
assert.equal(await api.pauseEmbeddings(), undefined);
assert.equal(await api.resumeEmbeddings(), undefined);
assert.equal(await api.installAIModel(), undefined);
assert.equal(await api.installAIModel('deep'), undefined);
assert.equal(api.applyRecommendedCache(), undefined);
assert.equal(elements.cache_profile.value, 'original_heavy');
assert.equal(elements.memory_cache_gb.value, 12);
assert.equal(elements.pregenerate_on_idle.checked, true);
assert.equal(api.pauseAllWork(), undefined);
assert.equal(api.resumeAllWork(), undefined);

assert.deepEqual(calls, [
    ['saveSettings', true, true, true, true, true, true, true],
    ['resetSettings', true, true, true, true, true, true, true],
    ['clearThumbnailCache', true, true, true, true, true],
    ['startCachePregeneration', true, true, true],
    ['stopCachePregeneration', true, true, true],
    ['pauseEmbeddings', true, true, true],
    ['resumeEmbeddings', true, true, true],
    ['installAIModel', 'fast', true, true, true, true, true, true, true, true, true],
    ['installAIModel', 'deep', true, true, true, true, true, true, true, true, true],
    ['recommendedMemoryGb', 42],
    ['updateCacheProfileHint'],
    ['saveSettings', true, true, true, true, true, true, true],
    ['pauseEmbeddings', true, true, true],
    ['stopCachePregeneration', true, true, true],
    ['resumeEmbeddings', true, true, true],
    ['startCachePregeneration', true, true, true],
]);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_catalog_controller_node_probe_preserves_public_adapter(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createCatalogApi } from './static/js/catalog/controller.js';

const calls = [];
const setSettingsStatus = (message, tone) => calls.push(['status', message, tone]);
const showToast = (message) => calls.push(['toast', message]);
let fallbackStats = { active_images: 3 };

const api = createCatalogApi({
    getFallbackStats: () => fallbackStats,
    setSettingsStatus,
    showToast,
    sourceState: (...args) => {
        calls.push(['sourceState', args]);
        return { label: 'Online', cls: 'online' };
    },
    renderCatalogSources: (catalog) => {
        calls.push(['renderCatalogSources', catalog]);
        return catalog.sources || [];
    },
    loadCatalogSources: (options) => {
        calls.push([
            'loadCatalogSources',
            typeof options.renderCatalogSources,
            options.setSettingsStatus === setSettingsStatus,
        ]);
        options.renderCatalogSources({ sources: [{ id: 7, path: '/seven' }] });
        return 'loaded';
    },
    setScanBusy: (busy, label) => calls.push(['setScanBusy', busy, label]),
    pollScanUntilDone: (options) => {
        calls.push([
            'pollScanUntilDone',
            typeof options.loadCatalogSources,
            typeof options.setScanBusy,
            options.setSettingsStatus === setSettingsStatus,
        ]);
        return options.loadCatalogSources();
    },
    addCatalogSource: (options) => {
        calls.push([
            'addCatalogSource',
            options.fallbackStats,
            typeof options.pollScanUntilDone,
            typeof options.renderCatalogSources,
            typeof options.setScanBusy,
            options.setSettingsStatus === setSettingsStatus,
            options.showToast === showToast,
        ]);
        return options.pollScanUntilDone();
    },
    rescanCatalogSource: (sourceId, options) => {
        calls.push([
            'rescanCatalogSource',
            sourceId,
            typeof options.pollScanUntilDone,
            options.setSettingsStatus === setSettingsStatus,
            options.showToast === showToast,
        ]);
        return options.pollScanUntilDone();
    },
    openRemoveSourceDialog: (source) => calls.push(['openRemoveSourceDialog', source]),
    closeRemoveSourceDialog: () => calls.push(['closeRemoveSourceDialog']),
    removeCatalogSource: (sourceId, mode, options) => {
        calls.push([
            'removeCatalogSource',
            sourceId,
            mode,
            typeof options.closeRemoveSourceDialog,
            typeof options.renderCatalogSources,
            options.setSettingsStatus === setSettingsStatus,
            options.showToast === showToast,
        ]);
        options.closeRemoveSourceDialog();
        options.renderCatalogSources({ sources: [{ id: 11, path: '/eleven' }] });
        return 'removed';
    },
    chooseCatalogFolder: (options) => {
        calls.push([
            'chooseCatalogFolder',
            options.setSettingsStatus === setSettingsStatus,
            options.showToast === showToast,
        ]);
        return 'chosen';
    },
    browseDirectory: (path, options) => {
        calls.push(['browseDirectory', path, options.showToast === showToast]);
        return path;
    },
    toggleDirectoryBrowser: (options) => {
        calls.push(['toggleDirectoryBrowser', typeof options.browseDirectoryImpl]);
        return options.browseDirectoryImpl('/toggle');
    },
    browseDirectoryParent: (options) => {
        calls.push(['browseDirectoryParent', typeof options.browseDirectoryImpl]);
        return options.browseDirectoryImpl('/parent');
    },
    selectBrowsedDirectory: (...args) => calls.push(['selectBrowsedDirectory', args]),
    useBrowsedDirectory: (...args) => calls.push(['useBrowsedDirectory', args]),
});

assert.deepEqual(api.sourceState({ included: true }), { label: 'Online', cls: 'online' });
api.renderCatalogSources({ sources: [{ id: 7, path: '/seven' }] });
api.openRemoveSourceDialog('7');
assert.deepEqual(calls.at(-1), ['openRemoveSourceDialog', { id: 7, path: '/seven' }]);

assert.equal(await api.loadCatalogSources(), 'loaded');
assert.equal(await api.addCatalogSource(), 'loaded');
fallbackStats = { total_images: 8 };
assert.equal(await api.addCatalogSource(), 'loaded');
assert.equal(await api.rescanCatalogSource(7), 'loaded');
api.setScanBusy(false);
assert.equal(await api.removeCatalogSource(7, 'keep'), 'removed');
api.openRemoveSourceDialog(11);
assert.deepEqual(calls.at(-1), ['openRemoveSourceDialog', { id: 11, path: '/eleven' }]);
assert.equal(await api.chooseCatalogFolder(), 'chosen');
assert.equal(await api.browseDirectory('/root'), '/root');
assert.equal(await api.toggleDirectoryBrowser(), '/toggle');
assert.equal(api.browseDirectoryParent(), '/parent');
api.selectBrowsedDirectory('/picked');
api.useBrowsedDirectory();

assert(calls.some((call) => (
    call[0] === 'addCatalogSource'
    && call[1].active_images === 3
    && call[2] === 'function'
    && call[3] === 'function'
    && call[4] === 'function'
    && call[5] === true
    && call[6] === true
)));
assert(calls.some((call) => call[0] === 'addCatalogSource' && call[1].total_images === 8));
assert(calls.some((call) => (
    call[0] === 'rescanCatalogSource'
    && call[1] === 7
    && call[2] === 'function'
    && call[3] === true
    && call[4] === true
)));
assert(calls.some((call) => (
    call[0] === 'removeCatalogSource'
    && call[1] === 7
    && call[2] === 'keep'
    && call[3] === 'function'
    && call[4] === 'function'
    && call[5] === true
    && call[6] === true
)));
assert(calls.some((call) => call[0] === 'toggleDirectoryBrowser' && call[1] === 'function'));
assert(calls.some((call) => call[0] === 'browseDirectoryParent' && call[1] === 'function'));
assert(calls.some((call) => call[0] === 'selectBrowsedDirectory' && call[1][0] === '/picked'));
assert(calls.some((call) => call[0] === 'useBrowsedDirectory'));
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_library_search_controller_node_probe_preserves_search_flow(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import {
    applySearchQueryChange,
    clearSearch,
    runDeepSearch,
} from './static/js/library/search_controller.js';

let searchQuery = '';
let deepSearchRequested = false;
let sortField = 'elo';
const calls = [];
const ctx = () => ({
    getSearchQuery: () => searchQuery,
    setSearchQuery: (value) => { searchQuery = value; },
    setDeepSearchRequested: (value) => { deepSearchRequested = value; },
    getSortField: () => sortField,
    hasActiveTextSearch: (value = searchQuery) => Boolean(value && value !== '__similar__'),
    saveSearchState: () => calls.push('saveSearchState'),
    updateSimilaritySortOption: () => calls.push('updateSimilaritySortOption'),
    applySortState: (field, desc, opts) => {
        sortField = field;
        calls.push(['applySortState', field, desc, opts]);
    },
    saveSearchSortState: () => calls.push('saveSearchSortState'),
    clearPersistedSearchState: () => calls.push('clearPersistedSearchState'),
    restoreSortState: () => {
        sortField = 'date_taken';
        calls.push('restoreSortState');
    },
    updateSearchControls: () => calls.push('updateSearchControls'),
    reloadForFilters: () => calls.push('reloadForFilters'),
    updateDateScrubber: () => calls.push('updateDateScrubber'),
});

applySearchQueryChange(' sunset ', ctx());
assert.equal(searchQuery, 'sunset');
assert.equal(deepSearchRequested, false);
assert.equal(sortField, 'similarity');
assert.deepEqual(calls.slice(0, 3), [
    'saveSearchState',
    'updateSimilaritySortOption',
    ['applySortState', 'similarity', true, { persist: false }],
]);

calls.length = 0;
applySearchQueryChange('', ctx());
assert.equal(searchQuery, '');
assert.equal(sortField, 'date_taken');
assert.deepEqual(calls.slice(0, 2), ['clearPersistedSearchState', 'restoreSortState']);

function inputEl(value = '') {
    return {
        value,
        focused: false,
        style: {},
        focus() { this.focused = true; },
        classList: { remove() {} },
    };
}
const input = inputEl('mountain');
const sortToggles = { style: { opacity: '0.5' } };
const documentImpl = {
    getElementById(id) {
        if (id === 'search-input') return input;
        if (id === 'sort-toggles') return sortToggles;
        return null;
    },
};

sortField = 'similarity';
searchQuery = 'abc';
deepSearchRequested = true;
clearSearch({ ...ctx(), documentImpl, clearSearchDebounce: () => calls.push('clearTimer') });
assert.equal(searchQuery, '');
assert.equal(deepSearchRequested, false);
assert.equal(input.value, '');
assert.equal(sortToggles.style.opacity, '');

input.value = 'mountain';
sortField = 'elo';
assert.equal(runDeepSearch({ ...ctx(), documentImpl }), true);
assert.equal(searchQuery, 'mountain');
assert.equal(deepSearchRequested, true);
assert.equal(sortField, 'similarity');

input.value = '';
searchQuery = '__similar__';
assert.equal(runDeepSearch({ ...ctx(), documentImpl }), false);
assert.equal(input.focused, true);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_library_similar_node_probe_preserves_request_and_render_contract(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createFindSimilarAction } from './static/js/library/similar.js';

const created = [];
function makeElement(tag) {
    const element = {
        tag,
        className: '',
        dataset: {},
        style: {},
        onclick: null,
        innerHTML: '',
        children: [],
        appendChild(child) {
            this.children.push(child);
        },
        classList: {
            removed: [],
            remove(value) {
                this.removed.push(value);
            },
        },
    };
    created.push(element);
    return element;
}

const controls = {
    'search-input': makeElement('input'),
    'search-clear': makeElement('button'),
    'sort-toggles': makeElement('div'),
    'rankings-grid': makeElement('div'),
};
const documentImpl = {
    getElementById(id) {
        return controls[id] || null;
    },
    createElement: makeElement,
};

let lightboxIndex = 0;
let libraryImages = [{ id: 42, filename: 'anchor.jpg' }];
let rankingsOffset = -1;
let rankingsExhausted = false;
let compareStats = { filtered_pool: 1 };
let searchQuery = '';
let deepSearchRequested = true;
let generation = 0;
const calls = [];
const fetches = [];

const findSimilar = createFindSimilarAction({
    documentImpl,
    fetchImpl: async (url) => {
        fetches.push(url);
        return {
            async json() {
                return {
                    visible_images: 2,
                    total_images: 5,
                    images: [
                        { id: 7, filename: 'near.jpg', aspect_ratio: 2, flag: 'picked', elo: 1234, comparisons: 3 },
                        { id: 8, filename: 'far.jpg', aspect_ratio: 1, flag: '', elo: 1200, comparisons: 0 },
                    ],
                };
            },
        };
    },
    getLightboxIndex: () => lightboxIndex,
    getLibraryImages: () => libraryImages,
    setLibraryImages: (images) => { libraryImages = images; calls.push(['setLibraryImages', images.map((image) => image.id)]); },
    setRankingsOffset: (offset) => { rankingsOffset = offset; calls.push(['setRankingsOffset', offset]); },
    setRankingsExhausted: (exhausted) => { rankingsExhausted = exhausted; calls.push(['setRankingsExhausted', exhausted]); },
    getThumbHeight: () => 180,
    getCompareStats: () => compareStats,
    setCompareStats: (stats) => { compareStats = stats; calls.push(['setCompareStats', stats]); },
    setSearchQuery: (query) => { searchQuery = query; calls.push(['setSearchQuery', query]); },
    setDeepSearchRequested: (requested) => { deepSearchRequested = requested; calls.push(['setDeepSearchRequested', requested]); },
    bumpLibraryRequestGeneration: () => ++generation,
    getLibraryRequestGeneration: () => generation,
    closeLightbox: () => calls.push('closeLightbox'),
    clearWarmups: () => calls.push('clearWarmups'),
    clearPersistedSearchState: () => calls.push('clearPersistedSearchState'),
    updateDateScrubber: () => calls.push('updateDateScrubber'),
    clearBatchSelection: () => calls.push('clearBatchSelection'),
    updateCompareProgress: () => calls.push('updateCompareProgress'),
    openLightbox: (image) => calls.push(['openLightbox', image.id]),
});

await findSimilar();

assert.deepEqual(fetches, ['/api/similar/42?limit=100']);
assert.equal(searchQuery, '__similar__');
assert.equal(deepSearchRequested, false);
assert.equal(controls['search-input'].value, 'Similar to: anchor.jpg');
assert.deepEqual(controls['search-clear'].classList.removed, ['hidden']);
assert.equal(controls['sort-toggles'].style.opacity, '0.3');
assert.equal(rankingsOffset, 2);
assert.equal(rankingsExhausted, true);
assert.deepEqual(libraryImages.map((image) => image.id), [7, 8]);
assert.equal(compareStats.filtered_pool, 2);
assert.equal(compareStats.filtered_pool_visible, 2);
assert.equal(compareStats.filtered_pool_total, 5);
assert.equal(controls['rankings-grid'].children.length, 2);
const firstCard = controls['rankings-grid'].children[0];
assert.equal(firstCard.className, 'rank-card skeleton-cell flag-picked');
assert.equal(firstCard.dataset.imageId, 7);
assert.equal(firstCard.dataset.ar, 2);
assert.equal(firstCard.style.height, '180px');
assert.equal(firstCard.style.flexGrow, 2);
assert.equal(firstCard.style.flexBasis, '360px');
assert.match(firstCard.innerHTML, /near\.jpg/);
firstCard.onclick();
assert.deepEqual(calls.at(-1), ['openLightbox', 7]);

lightboxIndex = 5;
fetches.length = 0;
await findSimilar();
assert.deepEqual(fetches, []);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(shutil.which("node"), "node is required for browser-module probes")
    def test_library_map_controller_node_probe_preserves_view_and_marker_contract(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import { createLibraryMapController } from './static/js/library/map_controller.js';

function element(tag, id = '') {
    return {
        tag,
        id,
        className: '',
        textContent: '',
        innerHTML: '',
        scrollTop: 12,
        children: [],
        style: {},
        classList: {
            values: new Set(),
            add(value) { this.values.add(value); },
            remove(value) { this.values.delete(value); },
            contains(value) { return this.values.has(value); },
        },
        appendChild(child) {
            this.children.push(child);
            if (child.tag === 'script') {
                setTimeout(() => child.onload?.(), 0);
            }
            return child;
        },
        addEventListener(event, handler) {
            this[`on${event}`] = handler;
        },
        remove() {
            this.removed = true;
        },
        querySelector(selector) {
            if (selector === '.map-error') return this.children.find((child) => child.className === 'map-error') || null;
            return null;
        },
        scrollTo({ top }) { this.scrollTop = top; },
    };
}

const nodes = {
    'rankings-grid': element('div', 'rankings-grid'),
    'map-container': element('div', 'map-container'),
    'view-grid-btn': element('button', 'view-grid-btn'),
    'view-map-btn': element('button', 'view-map-btn'),
};
nodes['rankings-grid'].classList.add('grid');
nodes['map-container'].classList.add('hidden');

const documentImpl = {
    head: element('head'),
    createElement: (tag) => element(tag),
    getElementById: (id) => nodes[id] || null,
};
globalThis.document = documentImpl;

const addedMarkers = [];
const mapCalls = [];
const mapInstance = {
    center: { lat: 20, lng: 0 },
    zoom: 2,
    setView(center, zoom) { this.center = { lat: center[0], lng: center[1] }; this.zoom = zoom; return this; },
    getCenter() { return this.center; },
    getZoom() { return this.zoom; },
    removeLayer(layer) { mapCalls.push(['removeLayer', layer.markers.length]); },
    addLayer(layer) { mapCalls.push(['addLayer', layer.markers.length]); },
    fitBounds(bounds, opts) { mapCalls.push(['fitBounds', bounds, opts]); },
    invalidateSize() { mapCalls.push('invalidateSize'); },
};
const windowImpl = {
    L: {
        map(container) { mapCalls.push(['map', container.id]); return mapInstance; },
        tileLayer(url, opts) { return { addTo: () => mapCalls.push(['tileLayer', url, opts.maxZoom]) }; },
        markerClusterGroup() {
            return {
                markers: [],
                addLayer(marker) { this.markers.push(marker); addedMarkers.push(marker); },
                getBounds() { return [[1, 2], [3, 4]]; },
            };
        },
        marker(coords) {
            return {
                coords,
                popup: null,
                bindPopup(popup) { this.popup = popup; return this; },
            };
        },
    },
    requestAnimationFrame: (fn) => fn(),
    setTimeout: (fn) => fn(),
};
globalThis.window = windowImpl;

const fetched = [];
const calls = [];
const scrollRoot = element('div', 'scroll-root');
const controller = createLibraryMapController({
    documentImpl,
    windowImpl,
    fetchImpl: async (url) => {
        fetched.push(url);
        return {
            async json() {
                return {
                    markers: [{ id: 7, filename: 'map.jpg', lat: 45, lng: -93, thumb_url: '/api/thumb/sm/7' }],
                    gps_count: 1,
                    gps_total_count: 2,
                    total_count: 5,
                };
            },
        };
    },
    clearWarmups: () => calls.push('clearWarmups'),
    clearBatchSelection: () => calls.push('clearBatchSelection'),
    currentFilterState: () => ({ orientation: 'portrait', flag: 'picked' }),
    currentQueryState: () => ({ searchMode: 'search', searchQuery: 'lake', deepSearch: true }),
    libraryScrollRoot: () => scrollRoot,
    openImageById: (id) => calls.push(['openImageById', id]),
    syncDateScrubberVisibility: () => calls.push('syncDateScrubberVisibility'),
});

controller.setLibraryView('map');
await new Promise((resolve) => setTimeout(resolve, 50));

assert.equal(controller.getLibraryView(), 'map');
assert.equal(scrollRoot.classList.contains('map-active'), true);
assert.equal(nodes['rankings-grid'].classList.contains('hidden'), true);
assert.equal(nodes['map-container'].classList.contains('hidden'), false);
assert.deepEqual(calls.slice(0, 3), ['clearWarmups', 'clearBatchSelection', 'syncDateScrubberVisibility']);
assert.equal(fetched.length, 1);
assert.match(fetched[0], /^\/api\/map\/markers\?/);
const params = new URL(fetched[0], 'http://local').searchParams;
assert.equal(params.get('orientation'), 'portrait');
assert.equal(params.get('flag'), 'picked');
assert.equal(params.get('q'), 'lake');
assert.equal(params.get('deep'), '1');
assert.deepEqual(mapCalls.slice(0, 2), [['map', 'map-container'], ['tileLayer', 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', 19]]);
assert.equal(addedMarkers.length, 1);
assert.deepEqual(addedMarkers[0].coords, [45, -93]);
assert.equal(nodes['map-container'].children.some((child) => child.id === 'map-info'), true);

controller.setLibraryView('grid');
assert.equal(controller.getLibraryView(), 'grid');
assert.equal(scrollRoot.classList.contains('map-active'), false);
assert.equal(nodes['rankings-grid'].classList.contains('hidden'), false);
assert.equal(nodes['map-container'].classList.contains('hidden'), true);
assert.equal(scrollRoot.scrollTop, 12);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_loupe_loading_node_probe_preserves_probe_lifecycle(self):
        base_dir = os.path.dirname(__file__)
        script = r"""
import assert from 'node:assert/strict';
import {
    cancelLoupeProbes,
    loadLoupeTier,
} from './static/js/loupe/loading.js';

const created = [];
class MockImage {
    constructor() {
        this.decoding = '';
        this.fetchPriority = '';
        this.naturalWidth = 0;
        this.naturalHeight = 0;
        this.onload = null;
        this.onerror = null;
        this._src = '';
        created.push(this);
    }
    set src(value) {
        this._src = value;
    }
    get src() {
        return this._src;
    }
}

function createTimers() {
    const timers = [];
    return {
        timers,
        setTimeoutImpl: (fn, ms) => {
            const timer = { fn, ms, cleared: false };
            timers.push(timer);
            return timer;
        },
        clearTimeoutImpl: (timer) => {
            timer.cleared = true;
        },
    };
}

let displayed = -1;
let fullToken = 0;
const loadingEvents = [];
const renders = [];
const refreshes = [];
const adopted = [];
const probes = new Set();
const loupeImg = { src: '', style: { opacity: '0' } };
const documentImpl = {
    getElementById: (id) => {
        assert.equal(id, 'loupe-img');
        return loupeImg;
    },
};
const image = { id: 7, flag: 'picked' };
const token = 11;
const isCurrentLoupeImage = (candidate, candidateToken) => candidate?.id === image.id && candidateToken === token;
const commonOptions = {
    probes,
    ImageImpl: MockImage,
    documentImpl,
    isCurrentLoupeImage,
    getDisplayedTierRank: () => displayed,
    setDisplayedTierRank: (rank) => {
        displayed = rank;
    },
    setFullLoadToken: (nextToken) => {
        fullToken = nextToken;
    },
    setLoupeTierLoading: (rank) => loadingEvents.push(['set', rank]),
    clearLoupeTierLoading: (rank) => loadingEvents.push(['clear', rank]),
    renderLoupeStatusLine: () => renders.push(displayed),
    refreshLoupeMediaStatus: (imgArg, tokenArg) => refreshes.push([imgArg.id, tokenArg]),
    adoptSourceDimensions: (width, height) => adopted.push([width, height]),
};

const lateTimers = createTimers();
const latePromise = loadLoupeTier(image, '/api/thumb/md/7', 1, token, {
    ...commonOptions,
    timeoutMs: 50,
    setTimeoutImpl: lateTimers.setTimeoutImpl,
    clearTimeoutImpl: lateTimers.clearTimeoutImpl,
});
assert.equal(probes.size, 1);
assert.equal(lateTimers.timers[0].ms, 50);
assert.equal(created[0].fetchPriority, 'auto');
assert.deepEqual(loadingEvents, [['set', 1]]);
lateTimers.timers[0].fn();
assert.equal(await latePromise, false);
assert.equal(probes.size, 1);
assert.deepEqual(loadingEvents, [['set', 1], ['clear', 1]]);
created[0].naturalWidth = 800;
created[0].naturalHeight = 600;
created[0].onload();
assert.equal(displayed, 1);
assert.equal(loupeImg.src, '/api/thumb/md/7');
assert.equal(loupeImg.style.opacity, '1');
assert.deepEqual(refreshes, [[7, 11]]);
assert.deepEqual(renders, [1]);
assert.equal(probes.size, 0);
assert.equal(fullToken, 0);
assert.deepEqual(adopted, []);

const cancelTimers = createTimers();
const cancelPromise = loadLoupeTier(image, '/api/thumb/lg/7', 2, token, {
    ...commonOptions,
    timeoutMs: 100,
    setTimeoutImpl: cancelTimers.setTimeoutImpl,
    clearTimeoutImpl: cancelTimers.clearTimeoutImpl,
});
assert.equal(probes.size, 1);
assert.equal(created[1].fetchPriority, 'high');
cancelLoupeProbes(probes, {
    clearLoupeTierLoadingImpl: () => loadingEvents.push(['cancel-clear']),
});
assert.equal(await cancelPromise, false);
assert.equal(cancelTimers.timers[0].cleared, true);
assert.equal(created[1].onload, null);
assert.equal(created[1].onerror, null);
assert.equal(created[1].src, '');
assert.equal(probes.size, 0);
assert.deepEqual(loadingEvents.slice(-2), [['set', 2], ['cancel-clear']]);
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_browser_smoke_covers_photoarchive_inline_handlers(self):
        base_dir = os.path.dirname(__file__)
        repo_dir = os.path.dirname(base_dir)
        smoke_script_path = os.path.join(repo_dir, "scripts", "photoarchive-browser-smoke")
        with open(smoke_script_path, encoding="utf-8") as fh:
            smoke_script = fh.read()
        with open(os.path.join(base_dir, "static", "js", "legacy", "app.js"), encoding="utf-8") as fh:
            legacy = fh.read()

        required_block = re.search(r"const REQUIRED_API = \[(.*?)\];", smoke_script, re.DOTALL)
        self.assertIsNotNone(required_block)
        smoke_required = set(re.findall(r'"([A-Za-z_$][A-Za-z0-9_$]*)"', required_block.group(1)))
        self.assertIn('"/rankings"', smoke_script)
        workflow_block = re.search(r"const SMOKE_WORKFLOW_CONTRACT = \[(.*?)\];", smoke_script, re.DOTALL)
        self.assertIsNotNone(workflow_block)
        workflow_contract = workflow_block.group(1)
        workflow_names = set(re.findall(r'name: "([^"]+)"', workflow_contract))
        goal_workflows = {
            "Settings",
            "People",
            "Library",
            "Compare/Mosaic",
            "Loupe",
            "Filters",
            "Search",
            "Export",
            "Cache Status",
            "AI Status",
        }
        self.assertFalse(
            goal_workflows - workflow_names,
            f"Browser smoke workflow contract missing goal workflows: {sorted(goal_workflows - workflow_names)}",
        )
        workflow_api = set()
        for api_block in re.findall(r"api: \[(.*?)\]", workflow_contract, re.DOTALL):
            workflow_api.update(re.findall(r'"([A-Za-z_$][A-Za-z0-9_$]*)"', api_block))
        missing_required_api = sorted(workflow_api - smoke_required)
        self.assertFalse(
            missing_required_api,
            f"Workflow contract API is not in REQUIRED_API: {missing_required_api}",
        )
        self.assertIn("workflowProbeExpression", smoke_script)

        legacy_return_start = legacy.rfind("    return {")
        self.assertGreater(legacy_return_start, -1)
        legacy_exports = set(
            re.findall(
                r"^\s*([A-Za-z_$][A-Za-z0-9_$]*),\s*$",
                legacy[legacy_return_start:],
                re.MULTILINE,
            )
        )
        missing_exports = sorted(smoke_required - legacy_exports)
        self.assertFalse(missing_exports, f"Smoke-required API missing from legacy export: {missing_exports}")

        inline_methods = set()
        for root in (
            os.path.join(base_dir, "templates"),
            os.path.join(base_dir, "static", "js"),
        ):
            for current_root, _dirs, filenames in os.walk(root):
                for filename in filenames:
                    if not filename.endswith((".html", ".js")):
                        continue
                    with open(os.path.join(current_root, filename), encoding="utf-8") as fh:
                        inline_methods.update(
                            re.findall(r"PhotoArchive\.([A-Za-z_][A-Za-z0-9_]*)", fh.read())
                        )

        missing_smoke_coverage = sorted(inline_methods - smoke_required)
        self.assertFalse(
            missing_smoke_coverage,
            f"PhotoArchive inline handlers missing from browser smoke REQUIRED_API: {missing_smoke_coverage}",
        )

    def test_thumbnail_package_exposes_config_slice(self):
        thumbnails = importlib.import_module("thumbnails")
        cache_entries = importlib.import_module("thumbnails.cache_entries")
        thumbnail_config = importlib.import_module("thumbnails.config")
        data_providers = importlib.import_module("thumbnails.data_providers")
        disk_store = importlib.import_module("thumbnails.disk_store")
        full_cache = importlib.import_module("thumbnails.full_cache")
        generation = importlib.import_module("thumbnails.generation")
        maintenance = importlib.import_module("thumbnails.maintenance")
        memory_store = importlib.import_module("thumbnails.memory_store")
        pregen = importlib.import_module("thumbnails.pregen")
        source_identity = importlib.import_module("thumbnails.source_identity")
        thumbnail_status = importlib.import_module("thumbnails.status")

        self.assertTrue(callable(thumbnail_config.allocate_disk_budget))
        self.assertTrue(callable(thumbnail_config.cache_budget_config))
        self.assertIs(thumbnails._normalize_ratios, thumbnail_config.normalize_ratios)
        self.assertIs(thumbnails._allocate_by_ratios, thumbnail_config.allocate_by_ratios)
        self.assertIs(thumbnails._allocate_weighted_capped, thumbnail_config.allocate_weighted_capped)
        self.assertTrue(callable(thumbnails.configure_data_providers))
        self.assertIs(thumbnails.configure_data_providers, data_providers.configure)
        self.assertIs(thumbnails._write_queue, cache_entries._write_queue)
        self.assertIs(thumbnails._write_queue_lock, cache_entries._write_queue_lock)
        self.assertIs(thumbnails._tier_byte_totals, cache_entries._tier_byte_totals)
        self.assertIs(thumbnails._disk_stats_cache, cache_entries._disk_stats_cache)
        self.assertIs(thumbnails._disk_path_index, cache_entries._disk_path_index)
        self.assertIs(thumbnails._disk_index_lock, cache_entries._disk_index_lock)
        self.assertTrue(callable(cache_entries._db_connect))
        self.assertTrue(callable(cache_entries._store_disk_entry))
        self.assertTrue(callable(cache_entries._flush_write_queue))
        self.assertTrue(callable(cache_entries.fast_disk_read_entry))
        self.assertTrue(callable(thumbnails._flush_write_queue))
        self.assertTrue(callable(thumbnails.fast_disk_read_entry))
        self.assertTrue(callable(data_providers.db_path))
        self.assertTrue(callable(data_providers.get_db))
        self.assertTrue(callable(data_providers.mark_image_missing_sync))
        self.assertTrue(callable(disk_store.cache_dir_safe_to_clear))
        self.assertTrue(callable(disk_store.lookup_index_entry))
        self.assertTrue(callable(full_cache.cache_full_image))
        self.assertTrue(callable(full_cache.cache_full_image_bytes))
        self.assertTrue(callable(generation.resize_to_long_side))
        self.assertTrue(callable(generation.thumbnail_jpeg_bytes))
        self.assertIs(thumbnails._load_raw_preview, generation.load_raw_preview)
        self.assertIs(thumbnails._resize_to_long_side, generation.resize_to_long_side)
        self.assertTrue(callable(maintenance.cache_dir_safe_to_clear))
        self.assertTrue(callable(maintenance.cleanup_stale_cache_temps))
        self.assertTrue(callable(memory_store.MemoryThumbnailStore))
        self.assertTrue(callable(pregen.generate_batch_for_decision))
        self.assertTrue(callable(pregen.rates))
        self.assertIs(thumbnails._source_missing_error, source_identity.source_missing_error)
        self.assertTrue(callable(thumbnail_status.copy_disk_stats))
        self.assertTrue(callable(thumbnail_status.cache_stats))
        self.assertTrue(callable(thumbnail_status.original_cache_status))
        self.assertTrue(callable(thumbnail_status.pregen_status))
        self.assertIs(thumbnails._copy_disk_stats, thumbnail_status.copy_disk_stats)
        self.assertEqual(
            thumbnails.estimated_tier_bytes("md"),
            thumbnail_config.estimated_tier_bytes("md", thumbnails.THUMB_QUALITY),
        )
        with open(os.path.join(os.path.dirname(__file__), "thumbnails", "__init__.py"), encoding="utf-8") as fh:
            contents = fh.read()
            self.assertNotIn("import db", contents)
            self.assertNotIn("db.", contents)

        old_path = app_module.db.DB_PATH
        try:
            app_module.db.DB_PATH = "/tmp/photoarchive-thumbnail-provider.db"
            self.assertEqual(data_providers.db_path(), "/tmp/photoarchive-thumbnail-provider.db")
        finally:
            app_module.db.DB_PATH = old_path

    def test_db_facade_uses_mutable_db_path(self):
        async def create_marker(path):
            old_path = db.DB_PATH
            db.DB_PATH = path
            try:
                conn = await db.get_db()
                try:
                    await conn.execute("CREATE TABLE marker (id INTEGER)")
                    await conn.commit()
                finally:
                    await conn.close()
            finally:
                db.DB_PATH = old_path

        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, "facade-path.db")
            asyncio.run(create_marker(path))
            self.assertTrue(os.path.exists(path))

    def test_public_route_registry_is_preserved(self):
        app_factory = importlib.import_module("core.app_factory")
        factory_app = app_factory.create_app(base_dir=os.path.dirname(__file__))

        for label, fastapi_app in (
            ("app_module.app", app_module.app),
            ("app_factory.create_app", factory_app),
        ):
            actual_counts = Counter()
            for route in fastapi_app.routes:
                if not isinstance(route, APIRoute):
                    continue
                for method in route.methods or ():
                    if method in {"HEAD", "OPTIONS"}:
                        continue
                    actual_counts[(method, route.path)] += 1

            actual = set(actual_counts)
            missing = PUBLIC_ROUTE_CONTRACT - actual
            self.assertFalse(missing, f"{label} missing public routes: {sorted(missing)}")
            extra = actual - PUBLIC_ROUTE_CONTRACT
            self.assertFalse(extra, f"{label} unexpected public routes: {sorted(extra)}")
            duplicated = {
                route: count
                for route, count in actual_counts.items()
                if route in PUBLIC_ROUTE_CONTRACT and count != 1
            }
            self.assertFalse(duplicated, f"{label} duplicated public routes: {duplicated}")

    def test_people_filter_composes_with_text_filter(self):
        async def resolve_text_search(_q, *, deep=False):
            self.assertFalse(deep)
            return {"active": True, "id_filter": {1, 2, 3}, "text_query": "party"}

        def parse_people_ids(_people):
            return [10, 11]

        async def get_people_image_id_filter(people_ids):
            self.assertEqual(people_ids, [10, 11])
            return {2, 3, 4}

        result = asyncio.run(
            query_constraints.resolve_library_constraints(
                q="party",
                people="10,11",
                deep=False,
                resolve_text_search=resolve_text_search,
                parse_people_ids=parse_people_ids,
                get_people_image_id_filter=get_people_image_id_filter,
            )
        )

        self.assertEqual(result["id_filter"], {2, 3})
        self.assertEqual(result["people_ids"], [10, 11])
        self.assertTrue(result["people_active"])

    def test_empty_people_intersection_stays_active_constraint(self):
        async def resolve_text_search(_q, *, deep=False):
            return {"active": True, "id_filter": {1}, "text_query": "party"}

        result = asyncio.run(
            query_constraints.resolve_library_constraints(
                q="party",
                people="42",
                deep=False,
                resolve_text_search=resolve_text_search,
                parse_people_ids=lambda _people: [42],
                get_people_image_id_filter=lambda _ids: asyncio.sleep(0, result={2}),
            )
        )

        self.assertEqual(result["id_filter"], set())
        self.assertTrue(query_constraints.search_constraint_active(result))


if __name__ == "__main__":
    unittest.main()
