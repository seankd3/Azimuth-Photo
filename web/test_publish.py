import asyncio
import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from test_support import *  # noqa: F401,F403
from features.publish import routes as publish_routes
from features.publish.builder import BundleSummary, build_public_gallery_bundle
from features.publish.deployer import (
    CommandResult,
    GalleryDeployer,
    PublishConfig,
    PublishConflict,
    gallery_meta_from_index,
    write_manifest,
)


class FakeThumbnails:
    def __init__(self, root):
        self.root = Path(root)
        self.calls = []

    def fast_disk_path_entry(self, size, image_id):
        self.calls.append(("path", size, int(image_id)))
        path = self.root / f"{size}-{image_id}.jpg"
        if path.exists():
            return f"sig-{size}-{image_id}", str(path)
        return None

    def fast_disk_read_entry(self, size, image_id, _signature=None):
        self.calls.append(("read", size, int(image_id)))
        return f"sig-{size}-{image_id}", f"{size}-{image_id}".encode("ascii")

    async def get_thumbnail(self, filepath, size, image_id):
        self.calls.append(("generate", size, int(image_id), filepath))
        return f"generated-{size}-{image_id}".encode("ascii")


class PublishBuilderTests(BackendTestCase):
    async def test_bundle_uses_sm_md_stable_names_and_static_relative_urls(self):
        templates = app_module.app.state.photoarchive_shell.templates
        cache = Path(self.tempdir.name) / "cache"
        cache.mkdir()
        (cache / "sm-101.jpg").write_bytes(b"sm-a")
        (cache / "md-101.jpg").write_bytes(b"md-a")
        thumbs = FakeThumbnails(cache)
        dest = Path(self.tempdir.name) / "bundle"
        rows = {
            101: {
                "id": 101,
                "filename": "a.jpg",
                "filepath": "/photos/a.jpg",
                "aspect_ratio": 1.25,
                "date_taken": "2026-07-01T10:00:00",
            },
            202: {
                "id": 202,
                "filename": "b.jpg",
                "filepath": "/photos/b.jpg",
                "aspect_ratio": 1.5,
                "date_taken": "2026-07-03T10:00:00",
            },
        }

        async def get_collection(*_args, **_kwargs):
            return {
                "id": 7,
                "name": "Selected Landscapes",
                "smart": False,
            }

        async def collection_image_ids(_collection_id):
            return [101, 202]

        async def get_images_by_ids(image_ids):
            return {image_id: rows[image_id] for image_id in image_ids}

        summary = await build_public_gallery_bundle(
            slug="selected-landscapes",
            title="Selected Landscapes",
            destination=dest,
            templates=templates,
            get_collection=get_collection,
            collection_id=7,
            collection_image_ids=collection_image_ids,
            get_images_by_ids=get_images_by_ids,
            thumbnails=thumbs,
        )

        self.assertTrue((dest / "index.html").exists())
        self.assertEqual((dest / "thumb" / "sm" / "101.jpg").read_bytes(), b"sm-a")
        self.assertEqual((dest / "img" / "101.jpg").read_bytes(), b"md-a")
        self.assertEqual((dest / "thumb" / "sm" / "202.jpg").read_bytes(), b"sm-202")
        self.assertEqual((dest / "img" / "202.jpg").read_bytes(), b"md-202")
        self.assertFalse((dest / "lg").exists())
        html = (dest / "index.html").read_text(encoding="utf-8")
        self.assertIn("./thumb/sm/101.jpg", html)
        self.assertIn("./img/101.jpg", html)
        self.assertNotIn("/favorite", html)
        self.assertNotIn("/unlock", html)
        self.assertEqual(summary.photo_count, 2)
        self.assertEqual(summary.cover, "/g/selected-landscapes/thumb/sm/101.jpg")
        self.assertGreater(summary.bundle_bytes, 0)
        self.assertGreaterEqual(summary.file_count, 5)
        self.assertEqual({call[1] for call in thumbs.calls}, {"sm", "md"})


class PublishDeployerTests(unittest.TestCase):
    def test_preflight_dirty_gallery_paths_raises_409(self):
        with tempfile.TemporaryDirectory() as temp_name:
            repo = Path(temp_name)
            (repo / "app" / "public" / "g").mkdir(parents=True)

            def runner(command, _cwd, _env=None):
                if command[:3] == ["git", "status", "--porcelain"]:
                    return CommandResult(0, " M app/public/g/manifest.json\n?? app/public/g/draft/\n", "")
                return CommandResult(0, "", "")

            deployer = GalleryDeployer(PublishConfig(site_repo=str(repo)), command_runner=runner)

            with self.assertRaises(PublishConflict) as raised:
                deployer._preflight()

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(raised.exception.paths, ["app/public/g/manifest.json", "app/public/g/draft/"])

    def test_manifest_regenerates_from_gallery_index_contract(self):
        with tempfile.TemporaryDirectory() as temp_name:
            public_g = Path(temp_name) / "g"
            gallery = public_g / "selected"
            gallery.mkdir(parents=True)
            gallery_json = {
                "photo_count": 2,
                "date_range": "2026-07-01 to 2026-07-03",
                "images": [{"id": 101}, {"id": 202}],
            }
            (gallery / "index.html").write_text(
                '<script type="application/json" id="gallery-data">'
                + json.dumps(gallery_json)
                + "</script>",
                encoding="utf-8",
            )

            write_manifest(
                public_g,
                [
                    {
                        "slug": "selected",
                        "title": "Selected",
                        "image_count": 2,
                        "published_at": 123.4,
                    }
                ],
            )

            manifest = json.loads((public_g / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                manifest,
                {
                    "galleries": [
                        {
                            "slug": "selected",
                            "title": "Selected",
                            "photo_count": 2,
                            "date_range": "2026-07-01 to 2026-07-03",
                            "cover": "/g/selected/thumb/sm/101.jpg",
                            "published_at": 123.4,
                        }
                    ]
                },
            )
            self.assertEqual(gallery_meta_from_index(gallery / "index.html")["first_id"], 101)


class FakeDeployer:
    async def publish(self, *, slug, title, collection_id, published_rows, write_bundle, progress=None):
        if progress:
            progress("building")
        summary = await asyncio.to_thread(write_bundle, Path(tempfile.mkdtemp()) / slug)
        if progress:
            progress("deploying")
        return SimpleNamespace(summary=summary, last_commit="abc123", push_error=None)

    async def revoke(self, *, slug, collection_id, published_rows, progress=None):
        if progress:
            progress("deploying")
        return SimpleNamespace(summary=None, last_commit="def456", push_error=None)


class PublishRouteTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.tasks = []
        templates = app_module.app.state.photoarchive_shell.templates
        self.cache = Path(self.tempdir.name) / "cache"
        self.cache.mkdir()
        self.thumbs = FakeThumbnails(self.cache)
        publish_routes.configure(
            templates=templates,
            get_collection=lambda collection_id, **kwargs: db.get_collection(collection_id, **kwargs),
            get_images_by_ids=lambda image_ids: db.get_images_by_ids(image_ids),
            collection_image_ids=lambda collection_id: db.collection_image_ids(collection_id),
            resolve_smart_image_ids=lambda _query: [],
            get_publish=lambda collection_id: db.get_collection_publish(collection_id),
            list_publishes=lambda: db.list_collection_publishes(),
            upsert_publish=lambda **kwargs: db.upsert_collection_publish(**kwargs),
            delete_publish=lambda collection_id: db.delete_collection_publish(collection_id),
            slug_available=lambda slug, **kwargs: db.collection_publish_slug_available(slug, **kwargs),
            thumbnails=self.thumbs,
            deployer=FakeDeployer(),
            track_background_task=lambda coro: self.tasks.append(coro),
        )

    async def test_publish_routes_contract(self):
        source = await self._source()
        image_id = await self._image(source["id"], "landscape.jpg")
        collection = await db.create_collection(name="Selected Landscapes", image_ids=[image_id])

        def probe_start():
            client = TestClient(app_module.app)
            try:
                started = client.post(
                    f"/api/user-collections/{collection['id']}/publish",
                    json={"slug": "selected-landscapes", "title": "Selected Landscapes"},
                )
                status = client.get(f"/api/user-collections/{collection['id']}/publish")
                return started, status
            finally:
                client.close()

        started, status = await asyncio.to_thread(probe_start)
        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.json()["job"], "publishing")
        self.assertTrue(status.json()["in_progress"])

        await asyncio.gather(*self.tasks)
        self.tasks.clear()

        def probe_done():
            client = TestClient(app_module.app)
            try:
                status = client.get(f"/api/user-collections/{collection['id']}/publish")
                listed = client.get("/api/publishes")
                revoke = client.post(f"/api/user-collections/{collection['id']}/publish/revoke")
                return status, listed, revoke
            finally:
                client.close()

        status, listed, revoke = await asyncio.to_thread(probe_done)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["publish"]["slug"], "selected-landscapes")
        self.assertEqual(status.json()["url"], "https://www.seankennethdoherty.com/g/selected-landscapes/")
        self.assertEqual(listed.json()["publishes"][0]["slug"], "selected-landscapes")
        self.assertEqual(revoke.status_code, 202)
        await asyncio.gather(*self.tasks)
        self.assertIsNone(await db.get_collection_publish(collection["id"]))
