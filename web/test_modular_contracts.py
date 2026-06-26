import asyncio
import importlib
import os
import re
from types import SimpleNamespace
import unittest

from fastapi.routing import APIRoute

import app as app_module
import db
import thumbnails
from core import app_factory


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
    ("GET", "/api/remote-access"),
    ("GET", "/api/catalog/metadata/status"),
    ("POST", "/api/catalog/metadata/start"),
    ("POST", "/api/catalog/metadata/stop"),
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
    ("POST", "/api/cache/clear"),
    ("POST", "/api/ai/embeddings/pause"),
    ("POST", "/api/ai/embeddings/resume"),
    ("POST", "/api/ai/model/install"),
    ("GET", "/api/ai/status"),
    ("GET", "/api/settings"),
    ("GET", "/api/ui/settings"),
    ("POST", "/api/image/{image_id}/flag"),
    ("POST", "/api/images/flag"),
    ("POST", "/api/settings"),
    ("POST", "/api/settings/reset"),
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
    ("GET", "/api/filter-options"),
    ("GET", "/api/stats"),
    ("GET", "/api/export"),
    ("GET", "/api/imports/options"),
    ("POST", "/api/imports"),
    ("GET", "/api/imports/{batch_id}"),
    ("GET", "/api/user-collections"),
    ("POST", "/api/user-collections"),
    ("GET", "/api/user-collections/{collection_id}"),
    ("POST", "/api/user-collections/{collection_id}/images"),
    ("DELETE", "/api/user-collections/{collection_id}/images"),
    ("GET", "/api/search"),
    ("GET", "/api/similar/{image_id}"),
    ("GET", "/api/duplicates"),
    ("GET", "/api/image/{image_id}/exif"),
    ("GET", "/api/collections"),
    ("GET", "/api/folders"),
}

CORE_FRONTEND_API = {
    "initCompare",
    "initLibrary",
    "initPeople",
    "initSettings",
    "setFilter",
    "toggleMetadataFilters",
    "toggleBackgroundWorkPanel",
    "startScan",
    "addCatalogSource",
    "installAIModel",
    "setCompareMode",
    "setMosaicStrategy",
    "openLightboxById",
    "closeLightbox",
    "exportRankings",
}


class ModularContractTests(unittest.TestCase):
    def test_compatibility_imports_still_resolve(self):
        for module_name in ("app", "db", "thumbnails"):
            module = importlib.import_module(module_name)
            self.assertIsNotNone(module)

        self.assertIs(app_module.app, importlib.import_module("app").app)
        self.assertTrue(hasattr(importlib.import_module("thumbnails"), "__path__"))

    def test_public_route_registry_is_preserved(self):
        route_contract = set()
        for route in app_module.app.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods or ():
                if method != "HEAD":
                    route_contract.add((method, route.path))

        self.assertEqual(route_contract, PUBLIC_ROUTE_CONTRACT)

    def test_smoke_mode_startup_skips_archive_initialization(self):
        calls = []

        async def fail_init_db():
            raise AssertionError("smoke startup should not initialize the archive database")

        def fail_sync(*_args, **_kwargs):
            raise AssertionError("smoke startup should not configure thumbnail workers")

        async def fail_async(*_args, **_kwargs):
            raise AssertionError("smoke startup should not warm archive data")

        base_dir = os.path.dirname(__file__)
        app = app_factory.create_base_app(base_dir=base_dir)
        templates = app_factory.create_templates(base_dir=base_dir)
        shell = app_factory.AppShell(
            app=app,
            templates=templates,
            static_assets=app_factory.StaticAssetContext(
                app_dir=base_dir,
                repo_dir=os.path.dirname(base_dir),
            ),
        )
        lifecycle = app_factory.register_app_lifecycle(
            shell,
            app_factory.AppLifecycleDependencies(
                smoke_mode_enabled=lambda: True,
                warm_templates=lambda: calls.append("warm"),
                thumbnails=SimpleNamespace(configure=fail_sync, stop_prefetch=fail_sync),
                settings=SimpleNamespace(load_settings=fail_sync),
                face_worker=SimpleNamespace(),
                init_db=fail_init_db,
                get_filter_options=fail_async,
                get_date_groups=fail_async,
                get_catalog_image_counts=fail_async,
                get_stats=fail_async,
                get_ai_status_counts=fail_async,
                get_visible_orientation_pairing_pool_counts=fail_async,
                get_catalog_summary=fail_async,
                cache_root=fail_sync,
                build_ai_status=fail_async,
                build_cache_status=fail_async,
                api_rankings=fail_async,
                api_folders=fail_async,
                api_map_markers=fail_async,
                api_date_groups=fail_async,
                api_settings=fail_async,
                mosaic_next=fail_async,
                compare_next=fail_async,
                default_visible_pairing_candidates=fail_async,
                warm_filtered_visible_ranked_candidates=fail_async,
                get_visible_past_matchups=fail_async,
                classify_orientations_background=fail_async,
                scan_metadata_background=fail_async,
                swiss_pair_window=1,
                filtered_swiss_pair_window=1,
                filtered_mosaic_window=1,
                mosaic_explore_window=1,
                mosaic_diverse_window=1,
                interaction_cache_warmup_delay_seconds=0.0,
            ),
        )

        asyncio.run(lifecycle.startup())

        self.assertEqual(calls, ["warm"])

    def test_frontend_bootstrap_preserves_global_entrypoint(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "templates", "base.html"), encoding="utf-8") as fh:
            base_template = fh.read()
        with open(os.path.join(base_dir, "static", "app.js"), encoding="utf-8") as fh:
            app_entry = fh.read()
        with open(os.path.join(base_dir, "static", "js", "bootstrap.js"), encoding="utf-8") as fh:
            bootstrap = fh.read()
        with open(os.path.join(base_dir, "static", "js", "legacy", "public_api.js"), encoding="utf-8") as fh:
            public_api = fh.read()

        self.assertIn("window.__photoArchiveWhenReady", base_template)
        self.assertIn('type="module" src="/static/app.js', base_template)
        self.assertIn("window.PhotoArchiveReady", app_entry)
        self.assertIn("./js/bootstrap.js", app_entry)
        self.assertIn("./legacy/app.js", bootstrap)
        self.assertIn("installAppShellControls", bootstrap)
        self.assertIn("./work/status_panel.js", bootstrap)

        returned_api = set(
            re.findall(
                r"^\s*([A-Za-z_$][A-Za-z0-9_$]*),\s*$",
                public_api[public_api.rfind("    return {"):],
                re.MULTILINE,
            )
        )
        self.assertFalse(
            CORE_FRONTEND_API - returned_api,
            f"Core frontend API missing from public bridge: {sorted(CORE_FRONTEND_API - returned_api)}",
        )


if __name__ == "__main__":
    unittest.main()
