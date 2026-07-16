import asyncio
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

from fastapi.testclient import TestClient

from test_support import *  # noqa: F401,F403
from features.publish import builder as publish_builder
from features.publish import routes as publish_routes
from features.publish.builder import BundleSummary, build_public_gallery_bundle
from features.publish.deployer import (
    CommandResult,
    GalleryDeployer,
    PublishConfig,
    PublishDeployError,
    default_command_runner,
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
    async def test_export_pruning_skips_symlinked_directories(self):
        root = Path(self.tempdir.name) / "export-root"
        stale = root / "stale"
        outside = Path(self.tempdir.name) / "outside"
        stale.mkdir(parents=True)
        outside.mkdir()
        sentinel = outside / "keep.txt"
        sentinel.write_text("outside", encoding="utf-8")
        (root / "linked-outside").symlink_to(outside, target_is_directory=True)

        await asyncio.to_thread(publish_builder._prune_orphaned_export_dirs, root, set())

        self.assertFalse(stale.exists())
        self.assertTrue((root / "linked-outside").is_symlink())
        self.assertTrue(sentinel.exists())

    async def test_bundle_uses_sm_md_lg_stable_names_and_static_relative_urls(self):
        settings.save_settings({
            "share_brand_name": "Northstar Studio",
            "publish_site_base_url": "https://photos.example.test",
        })
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
        self.assertEqual((dest / "lg" / "101.jpg").read_bytes(), b"lg-101")
        self.assertEqual((dest / "thumb" / "sm" / "202.jpg").read_bytes(), b"sm-202")
        self.assertEqual((dest / "img" / "202.jpg").read_bytes(), b"md-202")
        self.assertEqual((dest / "lg" / "202.jpg").read_bytes(), b"lg-202")
        html = (dest / "index.html").read_text(encoding="utf-8")
        self.assertIn("./thumb/sm/101.jpg", html)
        self.assertIn("./img/101.jpg", html)
        self.assertIn("./lg/101.jpg", html)
        self.assertIn("Northstar Studio", html)
        self.assertIn("https://photos.example.test", html)
        self.assertIn("Download photo", html)
        self.assertIn("Photo 1 of 2", html)
        self.assertIn("gallery-size copy", html)
        self.assertIn('"download_name": "a.jpg"', html)
        self.assertNotIn("/favorite", html)
        self.assertNotIn("/unlock", html)
        self.assertEqual(summary.photo_count, 2)
        self.assertEqual(summary.cover, "/g/selected-landscapes/thumb/sm/101.jpg")
        self.assertGreater(summary.bundle_bytes, 0)
        self.assertGreaterEqual(summary.file_count, 7)
        self.assertEqual({call[1] for call in thumbs.calls}, {"sm", "md", "lg"})

    async def test_bundle_skips_one_unreadable_member_instead_of_aborting_publish(self):
        templates = app_module.app.state.photoarchive_shell.templates
        cache = Path(self.tempdir.name) / "skip-cache"
        cache.mkdir()

        class OneBadThumbnail(FakeThumbnails):
            def fast_disk_read_entry(self, size, image_id, _signature=None):
                if int(image_id) == 202:
                    return None
                return super().fast_disk_read_entry(size, image_id, _signature)

            async def get_thumbnail(self, filepath, size, image_id):
                if int(image_id) == 202:
                    return b""
                return await super().get_thumbnail(filepath, size, image_id)

        rows = {
            101: {"id": 101, "filename": "good.jpg", "filepath": "/photos/good.jpg"},
            202: {"id": 202, "filename": "bad.jpg", "filepath": "/photos/bad.jpg"},
        }

        async def get_collection(*_args, **_kwargs):
            return {"id": 7, "name": "Mixed", "smart": False}

        async def get_images_by_ids(image_ids):
            return {image_id: rows[image_id] for image_id in image_ids}

        with self.assertLogs("features.publish.builder", level="WARNING") as logs:
            summary = await build_public_gallery_bundle(
                slug="mixed",
                title="Mixed",
                destination=Path(self.tempdir.name) / "mixed-bundle",
                templates=templates,
                get_collection=get_collection,
                collection_id=7,
                collection_image_ids=lambda _collection_id: asyncio.sleep(0, result=[101, 202]),
                get_images_by_ids=get_images_by_ids,
                thumbnails=OneBadThumbnail(cache),
            )

        self.assertEqual(summary.photo_count, 1)
        self.assertIn("image_id=202", logs.output[0])
        self.assertFalse((Path(self.tempdir.name) / "mixed-bundle" / "img" / "202.jpg").exists())


class PublishDeployerTests(unittest.TestCase):
    def test_publish_writes_bundle_manifest_and_successful_hook(self):
        with tempfile.TemporaryDirectory() as temp_name:
            public_g = Path(temp_name) / "g"
            calls = []

            def runner(command, cwd, timeout):
                calls.append((command, cwd, timeout))
                return CommandResult(0, "deployed\n", "")

            def write_bundle(target):
                target.mkdir(parents=True)
                (target / "index.html").write_text(
                    '<script type="application/json" id="gallery-data">'
                    '{"photo_count":1,"images":[{"id":101}]}'
                    "</script>",
                    encoding="utf-8",
                )
                return BundleSummary(
                    slug="selected",
                    title="Selected",
                    photo_count=1,
                    date_range="",
                    cover="/g/selected/thumb/sm/101.jpg",
                    bundle_bytes=10,
                    file_count=1,
                )

            deployer = GalleryDeployer(
                PublishConfig(publish_dir=str(public_g), publish_hook="scripts/deploy.sh"),
                command_runner=runner,
            )
            result = deployer._publish_sync(
                "selected",
                "Selected",
                7,
                [],
                write_bundle,
                None,
            )

            self.assertTrue((public_g / "selected" / "index.html").exists())
            self.assertEqual(calls, [("scripts/deploy.sh", public_g, 900)])
            self.assertTrue(result.hook.ok)
            manifest = json.loads((public_g / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["galleries"][0]["slug"], "selected")

    def test_publish_succeeds_when_hook_fails(self):
        with tempfile.TemporaryDirectory() as temp_name:
            public_g = Path(temp_name) / "g"

            def runner(_command, _cwd, _timeout):
                return CommandResult(7, "first\n", "last\n")

            def write_bundle(target):
                target.mkdir(parents=True)
                (target / "index.html").write_text("{}", encoding="utf-8")
                return BundleSummary("broken-hook", "Broken Hook", 0, "", "", 2, 1)

            deployer = GalleryDeployer(
                PublishConfig(publish_dir=str(public_g), publish_hook="deploy"),
                command_runner=runner,
            )
            result = deployer._publish_sync("broken-hook", "Broken Hook", 1, [], write_bundle, None)

            self.assertTrue((public_g / "broken-hook").exists())
            self.assertFalse(result.hook.ok)
            self.assertEqual(result.hook.returncode, 7)
            self.assertIn("last", result.hook.output)

    def test_hook_timeout_is_recorded_without_raising(self):
        with tempfile.TemporaryDirectory() as temp_name:
            public_g = Path(temp_name) / "g"

            def runner(_command, _cwd, _timeout):
                return CommandResult(124, "still running", "", timed_out=True)

            def write_bundle(target):
                target.mkdir(parents=True)
                (target / "index.html").write_text("{}", encoding="utf-8")
                return BundleSummary("slow-hook", "Slow Hook", 0, "", "", 2, 1)

            deployer = GalleryDeployer(
                PublishConfig(publish_dir=str(public_g), publish_hook="deploy", hook_timeout_seconds=1),
                command_runner=runner,
            )
            result = deployer._publish_sync("slow-hook", "Slow Hook", 1, [], write_bundle, None)

            self.assertFalse(result.hook.ok)
            self.assertTrue(result.hook.timed_out)
            self.assertEqual(result.hook.returncode, 124)

    def test_hook_timeout_kills_process_group_children(self):
        with tempfile.TemporaryDirectory() as temp_name:
            public_g = Path(temp_name)
            pid_path = public_g / "child.pid"
            # A script file sidesteps sh-vs-cmd quoting; python-as-sleep works
            # on every platform (there is no `sleep` binary on Windows).
            hang_script = public_g / "hang_hook.py"
            hang_script.write_text(
                "import pathlib, subprocess, sys, time\n"
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid))\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            command = f'"{sys.executable}" "{hang_script}"'

            result = default_command_runner(command, public_g, 1)

            self.assertTrue(result.timed_out)
            self.assertEqual(result.returncode, 124)
            child_pid = int(pid_path.read_text(encoding="utf-8"))
            self.assertTrue(self._process_exited(child_pid), f"child process {child_pid} was still running")

    def test_publish_lock_covers_persisted_row_mutation(self):
        with tempfile.TemporaryDirectory() as temp_name:
            public_g = Path(temp_name) / "g"
            deployer = GalleryDeployer(PublishConfig(publish_dir=str(public_g)))
            first_persist_started = threading.Event()
            release_first_persist = threading.Event()
            second_bundle_started = threading.Event()

            def write_bundle(slug):
                def _write(target):
                    if slug == "second":
                        second_bundle_started.set()
                    target.mkdir(parents=True)
                    (target / "index.html").write_text("{}", encoding="utf-8")
                    return BundleSummary(slug, slug.title(), 0, "", "", 2, 1)

                return _write

            def persist_publish(summary, _hook):
                if summary.slug == "first":
                    first_persist_started.set()
                    self.assertTrue(release_first_persist.wait(2))
                return {"collection_id": 1, "slug": summary.slug}

            async def run_race():
                first = asyncio.create_task(
                    deployer.publish(
                        slug="first",
                        title="First",
                        collection_id=1,
                        published_rows=[],
                        write_bundle=write_bundle("first"),
                        persist_publish=persist_publish,
                    )
                )
                self.assertTrue(await asyncio.to_thread(first_persist_started.wait, 2))
                second = asyncio.create_task(
                    deployer.publish(
                        slug="second",
                        title="Second",
                        collection_id=2,
                        published_rows=[],
                        write_bundle=write_bundle("second"),
                        persist_publish=persist_publish,
                    )
                )
                await asyncio.sleep(0.1)
                self.assertFalse(second_bundle_started.is_set())
                release_first_persist.set()
                await asyncio.gather(first, second)

            asyncio.run(run_race())

    def test_revoke_failure_keeps_manifest_and_skips_db_delete(self):
        with tempfile.TemporaryDirectory() as temp_name:
            public_g = Path(temp_name) / "g"
            live = public_g / "selected"
            live.mkdir(parents=True)
            (live / "index.html").write_text("still live", encoding="utf-8")
            row = {
                "collection_id": 7,
                "slug": "selected",
                "title": "Selected",
                "image_count": 1,
                "published_at": 123.4,
            }
            write_manifest(public_g, [row])
            deleted = []
            deployer = GalleryDeployer(PublishConfig(publish_dir=str(public_g)))

            with unittest.mock.patch(
                "features.publish.deployer.shutil.rmtree",
                side_effect=OSError("permission denied"),
            ):
                with self.assertRaises(PublishDeployError) as raised:
                    deployer._revoke_sync(
                        "selected",
                        7,
                        [row],
                        lambda _hook: deleted.append(True),
                        None,
                    )

            self.assertEqual(raised.exception.status_code, 502)
            self.assertTrue((live / "index.html").exists())
            self.assertFalse(deleted)
            manifest = json.loads((public_g / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["galleries"][0]["slug"], "selected")

    def test_republish_uses_atomic_directory_exchange(self):
        with tempfile.TemporaryDirectory() as temp_name:
            parent = Path(temp_name)
            target = parent / "selected"
            work = parent / ".selected.tmp-test"
            target.mkdir()
            work.mkdir()
            (target / "index.html").write_text("old", encoding="utf-8")
            (work / "index.html").write_text("new", encoding="utf-8")
            calls = []
            real_exchange = publish_builder._atomic_exchange_paths

            def wrapped_exchange(source, destination):
                calls.append((source, destination, destination.exists()))
                return real_exchange(source, destination)

            with unittest.mock.patch("features.publish.builder._atomic_exchange_paths", wrapped_exchange):
                publish_builder._replace_bundle_dir(work, target)

            self.assertEqual(calls, [(work, target, True)])
            self.assertEqual((target / "index.html").read_text(encoding="utf-8"), "new")
            self.assertFalse(work.exists())

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

    def _process_exited(self, pid):
        for _ in range(20):
            if os.name == "nt":
                status = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if str(pid) not in status.stdout:
                    return True
            else:
                status = subprocess.run(
                    ["ps", "-o", "stat=", "-p", str(pid)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if status.returncode != 0 or not status.stdout.strip():
                    return True
                if status.stdout.strip().startswith("Z"):
                    return True
            time.sleep(0.1)
        return False


class FakeDeployer:
    async def publish(
        self,
        *,
        slug,
        title,
        collection_id,
        published_rows,
        write_bundle,
        persist_publish=None,
        progress=None,
    ):
        if progress:
            progress("building")
        summary = await asyncio.to_thread(write_bundle, Path(tempfile.mkdtemp()) / slug)
        if progress:
            progress("deploying")
        publish_row = await asyncio.to_thread(persist_publish, summary, None) if persist_publish else None
        return SimpleNamespace(summary=summary, last_commit=None, push_error=None, hook=None, publish_row=publish_row)

    async def revoke(self, *, slug, collection_id, published_rows, persist_revoke=None, progress=None):
        if progress:
            progress("deploying")
        if persist_revoke:
            await asyncio.to_thread(persist_revoke, None)
        return SimpleNamespace(summary=None, last_commit=None, push_error=None, hook=None)


class PublishRouteTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.tasks = []
        publish_routes._jobs.clear()
        templates = app_module.app.state.photoarchive_shell.templates
        self.cache = Path(self.tempdir.name) / "cache"
        self.cache.mkdir()
        settings.save_settings({
            "publish_dir": str(Path(self.tempdir.name) / "published"),
            "publish_site_base_url": "https://www.seankennethdoherty.com",
        })
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

    async def test_publish_and_revoke_reject_conflicting_queued_job(self):
        source = await self._source()
        image_id = await self._image(source["id"], "queued.jpg")
        collection = await db.create_collection(name="Queued gallery", image_ids=[image_id])
        await db.upsert_collection_publish(
            collection_id=collection["id"],
            slug="queued-gallery",
            title="Queued gallery",
            image_count=1,
            bundle_bytes=0,
            last_commit=None,
        )

        def publish_then_revoke():
            with TestClient(app_module.app) as client:
                publish = client.post(
                    f"/api/user-collections/{collection['id']}/publish",
                    json={"slug": "queued-gallery", "title": "Queued gallery"},
                )
                revoke = client.post(f"/api/user-collections/{collection['id']}/publish/revoke")
                return publish, revoke

        publish, blocked_revoke = await asyncio.to_thread(publish_then_revoke)
        self.assertEqual(publish.status_code, 202)
        self.assertEqual(blocked_revoke.status_code, 409)
        await asyncio.gather(*self.tasks)
        self.tasks.clear()

        def revoke_then_publish():
            with TestClient(app_module.app) as client:
                revoke = client.post(f"/api/user-collections/{collection['id']}/publish/revoke")
                publish = client.post(
                    f"/api/user-collections/{collection['id']}/publish",
                    json={"slug": "queued-gallery", "title": "Queued gallery"},
                )
                return revoke, publish

        revoke, blocked_publish = await asyncio.to_thread(revoke_then_publish)
        self.assertEqual(revoke.status_code, 202)
        self.assertEqual(blocked_publish.status_code, 409)
        await asyncio.gather(*self.tasks)

    async def test_unexpected_publish_failure_is_logged_without_leaking_internal_path(self):
        publish_routes._start_job(77, "publishing", slug="private", title="Private")
        secret = "/home/sean/private/source.jpg"

        with unittest.mock.patch.object(publish_routes.log, "error") as error_log:
            publish_routes._fail_job(
                77,
                RuntimeError(f"decoder failed at {secret}"),
                operation="publish",
            )

        job = publish_routes._jobs[77]
        self.assertEqual(job["status_code"], 500)
        self.assertNotIn(secret, job["error"])
        self.assertIn("Check the server log", job["error"])
        error_log.assert_called_once()
