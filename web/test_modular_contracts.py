import asyncio
import importlib
import os
from types import SimpleNamespace
import unittest

from fastapi.routing import APIRoute

import app as app_module
import db
import thumbnails
from core import app_factory


PUBLIC_ROUTE_CONTRACT = {
    ("GET", "/"),
    ("GET", "/m"),
    ("GET", "/d"),
    ("GET", "/setup"),
    ("POST", "/api/setup/complete"),
    ("GET", "/sw.js"),
    ("GET", "/api/dev/status"),
    ("GET", "/api/people/status"),
    ("GET", "/api/captions/status"),
    ("GET", "/api/tags"),
    ("GET", "/api/image/{image_id}/caption"),
    ("POST", "/api/image/{image_id}/caption"),
    ("GET", "/api/people"),
    ("GET", "/api/people/faces/{face_id}/thumb"),
    ("POST", "/api/people/scan/pause"),
    ("POST", "/api/people/scan/resume"),
    ("POST", "/api/captions/scan/pause"),
    ("POST", "/api/captions/scan/resume"),
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
    ("POST", "/api/devices/link"),
    ("POST", "/api/pair"),
    ("GET", "/api/devices"),
    ("POST", "/api/devices/{device_id}/revoke"),
    ("GET", "/api/discover"),
    ("POST", "/api/pair/connect"),
    ("GET", "/api/pair/status"),
    ("POST", "/api/remote-access/serve"),
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
    ("GET", "/api/date-histogram"),
    ("GET", "/api/collections/suggestions"),
    ("GET", "/api/collections/tree"),
    ("GET", "/api/collections/{collection_id}/images"),
    ("POST", "/api/collections/{collection_id}/links"),
    ("DELETE", "/api/collections/{collection_id}/links"),
    ("GET", "/api/counts"),
    ("GET", "/api/map/markers"),
    ("GET", "/api/filter-options"),
    ("GET", "/api/stats"),
    ("GET", "/api/stacks"),
    ("GET", "/api/stacks/rebuild/status"),
    ("GET", "/api/stacks/representatives"),
    ("GET", "/api/stacks/{stack_id}"),
    ("POST", "/api/stacks"),
    ("POST", "/api/stacks/rebuild"),
    ("POST", "/api/stacks/version/scan"),
    ("POST", "/api/stacks/{stack_id}/representative"),
    ("POST", "/api/stacks/{stack_id}/unstack"),
    ("POST", "/api/images/trash"),
    ("POST", "/api/images/restore"),
    ("GET", "/api/trash"),
    ("POST", "/api/trash/empty"),
    ("GET", "/api/export"),
    ("GET", "/api/imports"),
    ("GET", "/api/imports/options"),
    ("POST", "/api/imports"),
    ("GET", "/api/imports/{batch_id}"),
    ("GET", "/api/import/sources"),
    ("GET", "/api/import/browse"),
    ("POST", "/api/import/scan"),
    ("GET", "/api/import/scan/{scan_id}"),
    ("GET", "/api/import/scan/{scan_id}/thumb/{key}"),
    ("POST", "/api/import/commit"),
    ("GET", "/api/import/jobs/{job_id}"),
    ("POST", "/api/import/jobs/{job_id}/cancel"),
    ("GET", "/api/user-collections"),
    ("POST", "/api/user-collections"),
    ("GET", "/api/user-collections/{collection_id}"),
    ("POST", "/api/user-collections/{collection_id}"),
    ("GET", "/api/user-collections/{collection_id}/share"),
    ("GET", "/api/user-collections/{collection_id}/share/favorites"),
    ("POST", "/api/user-collections/{collection_id}/share"),
    ("POST", "/api/user-collections/{collection_id}/share/revoke"),
    ("GET", "/api/user-collections/{collection_id}/publish"),
    ("POST", "/api/user-collections/{collection_id}/publish"),
    ("POST", "/api/user-collections/{collection_id}/publish/revoke"),
    ("GET", "/api/publishes"),
    ("GET", "/api/published/tree"),
    ("POST", "/api/published/nodes"),
    ("PATCH", "/api/published/nodes/{node_id}"),
    ("DELETE", "/api/published/nodes/{node_id}"),
    ("GET", "/api/published/nodes/{node_id}/diff"),
    ("POST", "/api/published/nodes/{node_id}/update"),
    ("POST", "/api/published/nodes/{node_id}/share"),
    ("DELETE", "/api/published/nodes/{node_id}/share"),
    ("POST", "/api/published/export"),
    ("GET", "/api/shares"),
    ("POST", "/api/user-collections/{collection_id}/rename"),
    ("POST", "/api/user-collections/{collection_id}/delete"),
    ("POST", "/api/user-collections/{collection_id}/images"),
    ("POST", "/api/user-collections/{collection_id}/images/remove"),
    ("DELETE", "/api/user-collections/{collection_id}/images"),
    ("GET", "/s/{token}"),
    ("GET", "/s/{token}/favorites"),
    ("POST", "/s/{token}/favorite"),
    ("POST", "/s/{token}/unlock"),
    ("GET", "/s/{token}/thumb/{size}/{image_id}"),
    ("GET", "/s/{token}/img/{image_id}"),
    ("GET", "/api/search"),
    ("GET", "/api/similar/{image_id}"),
    ("GET", "/api/duplicates"),
    ("GET", "/api/image/{image_id}/exif"),
    ("GET", "/api/folders"),
    ("GET", "/api/folders/tree"),
    ("GET", "/api/develop/import/status"),
    ("GET", "/api/develop/{image_id}"),
    ("GET", "/api/develop/{image_id}/history"),
    ("GET", "/api/develop/{image_id}/base.bin"),
    ("GET", "/api/develop/{image_id}/base.jpg"),
    ("GET", "/api/develop/{image_id}/proof-tile"),
    ("POST", "/api/develop/{image_id}/auto"),
    ("POST", "/api/develop/auto/batch"),
    ("POST", "/api/develop/{image_id}/transform/auto"),
    ("POST", "/api/develop/{image_id}/virtual-copy"),
    ("GET", "/api/develop/{image_id}/virtual-copies"),
    ("DELETE", "/api/develop/{image_id}/virtual-copy/{copy_id}"),
    ("GET", "/api/develop/{image_id}/snapshots"),
    ("POST", "/api/develop/{image_id}/snapshots"),
    ("DELETE", "/api/develop/{image_id}/snapshots/{history_id}"),
    ("POST", "/api/develop/import/scan"),
    ("POST", "/api/develop/pregen"),
    ("POST", "/api/develop/{image_id}/export"),
    ("POST", "/api/develop/export/batch"),
    ("GET", "/api/develop/export/batch/status"),
    ("POST", "/api/develop/sync"),
    ("POST", "/api/develop/{image_id}/reset"),
    ("PUT", "/api/develop/{image_id}"),
    ("GET", "/api/develop/lrcat/status"),
    ("POST", "/api/develop/lrcat/scan"),
    ("POST", "/api/develop/{image_id}/ai-mask"),
    ("GET", "/api/develop/ai-mask/{cache_key}.png"),
    ("GET", "/api/develop/presets"),
    ("POST", "/api/develop/presets"),
    ("GET", "/api/develop/presets/{preset_id}"),
    ("PATCH", "/api/develop/presets/{preset_id}"),
    ("DELETE", "/api/develop/presets/{preset_id}"),
    ("POST", "/api/develop/presets/{preset_id}/apply"),
    ("POST", "/api/develop/presets/import-lightroom"),
    ("POST", "/api/develop/hdr/detect"),
    ("POST", "/api/develop/hdr/merge"),
    ("GET", "/api/develop/hdr/status"),
    ("GET", "/api/develop/pano/status"),
    ("POST", "/api/develop/pano/detect"),
    ("POST", "/api/develop/pano/merge"),
    ("GET", "/api/develop/film/stocks"),
    ("GET", "/api/develop/film/stocks/{slug}"),
    ("POST", "/api/develop/{image_id}/write-xmp"),
    ("POST", "/api/develop/write-xmp/batch"),
    # PATCH: satellite lane — local controls are inert in hub mode.
    ("GET", "/api/sync/status"),
    ("POST", "/api/sync/hub"),
    ("POST", "/api/sync/pause"),
    ("POST", "/api/sync/resume"),
    ("POST", "/api/sync/now"),
    ("POST", "/api/sync/mirror/refresh"),
    ("POST", "/api/sync/prefetch"),
    ("POST", "/api/sync/prefetch/loupe/{image_id}"),
    ("POST", "/api/sync/prefetch/develop/{image_id}"),
    ("GET", "/api/sync/catalog/export"),
    ("GET", "/api/sync/thumbs/pack"),
    ("POST", "/api/sync/manifest"),
    ("POST", "/api/sync/upload/{content_hash}"),
    ("GET", "/api/sync/upload/{content_hash}/status"),
    ("POST", "/api/sync/metadata"),
    ("GET", "/api/sync/base/{content_hash}"),
    ("POST", "/api/sync/hash-backfill"),
    ("POST", "/api/sync/oplog/pull"),
    ("POST", "/api/sync/oplog/push"),
    ("GET", "/api/watched-folders"),
    ("POST", "/api/watched-folders"),
    ("PATCH", "/api/watched-folders/{folder_id}"),
    ("DELETE", "/api/watched-folders/{folder_id}"),
    ("POST", "/api/watched-folders/{folder_id}/scan"),
    ("GET", "/api/saved-views"),
    ("POST", "/api/saved-views"),
    ("PATCH", "/api/saved-views/{view_id}"),
    ("DELETE", "/api/saved-views/{view_id}"),
    ("POST", "/api/system/backup/now"),
    ("GET", "/api/system/backup/list"),
    ("POST", "/api/system/backup/restore"),
    ("GET", "/api/system/backup/restore-status"),
    ("DELETE", "/api/system/backup/restore-staged"),
    ("POST", "/api/system/integrity/scan"),
    ("GET", "/api/system/integrity/status"),
    # PATCH: quality lane
    ("GET", "/api/keywords"),
    ("POST", "/api/keywords"),
    ("POST", "/api/keywords/resolve"),
    ("PATCH", "/api/keywords/{keyword_id}"),
    ("DELETE", "/api/keywords/{keyword_id}"),
    ("GET", "/api/keywords/{keyword_id}/images"),
    ("POST", "/api/keywords/assign"),
    ("POST", "/api/keywords/unassign"),
    ("GET", "/api/images/{image_id}/keywords"),
    ("GET", "/api/images/{image_id}/iptc"),
    ("PUT", "/api/images/{image_id}/iptc"),
    ("GET", "/api/user-collections/{collection_id}/galleries"),
    ("POST", "/api/user-collections/{collection_id}/galleries"),
    ("PATCH", "/api/user-collections/{collection_id}/galleries/{gallery_id}"),
    ("GET", "/s/gallery/{token}"),
    ("POST", "/s/gallery/{token}/unlock"),
    ("GET", "/s/gallery/{token}/thumb/{size}/{image_id}"),
    ("GET", "/s/gallery/{token}/download/{size}/{image_id}"),
    ("GET", "/s/gallery/{token}/download-all"),
    ("GET", "/api/develop/export-presets"),
    ("POST", "/api/develop/export-presets"),
    ("DELETE", "/api/develop/export-presets/{preset_id}"),
    ("GET", "/api/geo/status"),
    ("POST", "/api/geo/backfill/start"),
    ("POST", "/api/geo/infer/start"),
    ("POST", "/api/geo/timeline/import"),
    ("GET", "/api/quality/status"),
    ("POST", "/api/quality/scan"),
    ("GET", "/api/quality/{image_id}"),
    ("POST", "/api/quality/autocull"),
    ("POST", "/api/quality/autocull/apply"),
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
                caption_worker=SimpleNamespace(),
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

    def test_frontend_shells_use_living_entrypoints(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "templates", "desktop.html"), encoding="utf-8") as fh:
            desktop_template = fh.read()
        with open(os.path.join(base_dir, "templates", "mobile.html"), encoding="utf-8") as fh:
            mobile_template = fh.read()
        with open(os.path.join(base_dir, "static", "js", "desktop", "api.js"), encoding="utf-8") as fh:
            desktop_api = fh.read()
        with open(os.path.join(base_dir, "static", "js", "mobile", "api.js"), encoding="utf-8") as fh:
            mobile_api = fh.read()

        self.assertIn('href="/static/desktop.css', desktop_template)
        self.assertIn('src="/static/js/desktop/bootstrap.js', desktop_template)
        self.assertIn('href="/static/mobile.css', mobile_template)
        self.assertIn('src="/static/js/mobile/bootstrap.js', mobile_template)
        self.assertIn("window.isSecureContext", mobile_template)
        self.assertIn("navigator.serviceWorker.register(url)", mobile_template)
        self.assertIn("/sw.js?v=", mobile_template)
        self.assertIn("from '../api.js'", desktop_api)
        self.assertIn("from '../api.js'", mobile_api)


if __name__ == "__main__":
    unittest.main()
