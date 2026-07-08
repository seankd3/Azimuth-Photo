from fastapi.testclient import TestClient

from test_support import *  # noqa: F401,F403


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
        self.assertIn("import_root: { type: 'text' }", drawer)
