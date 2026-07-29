from fastapi.testclient import TestClient

from test_support import *  # noqa: F401,F403
from features.imports import service as import_service


class _FakeUpload:
    def __init__(self, name: str, payload: bytes):
        self.filename = name
        self._chunks = [payload]

    async def read(self, _size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class ImportCopySafetyTests(BackendTestCase):
    async def test_copy_claims_destination_with_exclusive_create(self):
        """A same-named destination must survive untruncated; the upload lands at -2."""
        destination = os.path.join(self.tempdir.name, "shoot")
        os.makedirs(destination)
        existing = os.path.join(destination, "alpha.jpg")
        with open(existing, "wb") as fh:
            fh.write(b"first-import-original")

        result = await import_service.copy_import_files(
            uploads=[_FakeUpload("alpha.jpg", b"second-upload")],
            relative_paths=["alpha.jpg"],
            destination_path=destination,
            preserve_structure=False,
        )

        with open(existing, "rb") as fh:
            self.assertEqual(fh.read(), b"first-import-original")
        self.assertEqual(result["collision_count"], 1)
        copied = result["copied"][0]
        self.assertEqual(copied["filename"], "alpha-2.jpg")
        with open(copied["filepath"], "rb") as fh:
            self.assertEqual(fh.read(), b"second-upload")

    async def test_failed_upload_copy_leaves_no_partial_file(self):
        class _ExplodingUpload:
            filename = "boom.jpg"

            async def read(self, _size: int) -> bytes:
                raise RuntimeError("stream died")

        destination = os.path.join(self.tempdir.name, "shoot-fail")
        with self.assertRaisesRegex(RuntimeError, "stream died"):
            await import_service.copy_import_files(
                uploads=[_ExplodingUpload()],
                relative_paths=["boom.jpg"],
                destination_path=destination,
                preserve_structure=False,
            )
        self.assertEqual(os.listdir(destination), [])


class ImportTests(BackendTestCase):
    async def test_browser_import_creates_batch_and_library_scope(self):
        import_root = os.path.join(self.tempdir.name, "imports")
        settings.save_settings({"import_root": import_root})

        with TestClient(app_module.app) as client:
            response = client.post(
                "/api/imports",
                data={
                    "destination_mode": "date_shoot",
                    "import_root": import_root,
                    "shoot_date": "2026-06-02",
                    "shoot_name": "Laptop Import",
                    "preserve_structure": "true",
                    "relative_paths": ["camera/alpha.jpg", "camera/notes.txt"],
                },
                files=[
                    ("files", ("alpha.jpg", b"jpeg-ish", "image/jpeg")),
                    ("files", ("notes.txt", b"notes", "text/plain")),
                ],
            )

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["imported_files"], 1)
            self.assertEqual(payload["skipped_files"], 1)
            self.assertEqual(payload["library_url"], f"/#import_batch={int(payload['batch_id'])}")
            self.assertTrue(os.path.exists(os.path.join(
                import_root,
                "2026",
                "2026-06-02 - Laptop Import",
                "camera",
                "alpha.jpg",
            )))

            batch_id = int(payload["batch_id"])
            batch = client.get(f"/api/imports/{batch_id}")
            self.assertEqual(batch.status_code, 200, batch.text)
            self.assertEqual(len(batch.json()["batch"]["images"]), 1)
            self.assertEqual(batch.json()["library_url"], f"/#import_batch={batch_id}")

            listed = client.get("/api/imports")
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual(listed.json()["imports"][0]["id"], batch_id)
            self.assertIn("started_at", listed.json()["imports"][0])
            self.assertEqual(listed.json()["imports"][0]["total_files"], 2)
            self.assertEqual(listed.json()["imports"][0]["imported_files"], 1)
            self.assertEqual(listed.json()["imports"][0]["skipped_files"], 1)

            rankings = client.get(f"/api/rankings?import_batch={batch_id}&limit=10")
            self.assertEqual(rankings.status_code, 200, rankings.text)
            images = rankings.json()["images"]
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]["filename"], "alpha.jpg")

    async def test_import_ui_contract(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "templates", "desktop.html"), encoding="utf-8") as fh:
            desktop_template = fh.read()
        with open(os.path.join(base_dir, "static", "js", "desktop", "importer.js"), encoding="utf-8") as fh:
            importer = fh.read()
        with open(os.path.join(base_dir, "static", "js", "desktop", "drawer.js"), encoding="utf-8") as fh:
            drawer = fh.read()

        self.assertIn('id="import-view"', desktop_template)
        self.assertIn('id="system-btn"', desktop_template)
        self.assertIn('id="drawer-body"', desktop_template)
        self.assertIn("document.getElementById('import-view')?.addEventListener('click', openImport)", importer)
        self.assertIn("formData.set('import_root'", importer)
        self.assertIn("xhr.open('POST', '/api/imports')", importer)
        self.assertIn("Past imports", importer)
        self.assertIn("listImports(12)", importer)
        self.assertIn("import_root: { type: 'text' }", drawer)
