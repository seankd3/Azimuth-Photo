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

            rankings = client.get(f"/api/rankings?import_batch={batch_id}&limit=10")
            self.assertEqual(rankings.status_code, 200, rankings.text)
            images = rankings.json()["images"]
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0]["filename"], "alpha.jpg")

    async def test_import_ui_contract(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "templates", "settings.html"), encoding="utf-8") as fh:
            settings_template = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "page.js"), encoding="utf-8") as fh:
            settings_page = fh.read()

        self.assertIn('id="remote-access-card"', settings_template)
        self.assertIn('id="import-section"', settings_template)
        self.assertIn('id="import_root"', settings_template)
        self.assertIn("Omarchy Folders", settings_template)
        self.assertIn("Use Import above to copy laptop photos into Omarchy.", settings_template)
        self.assertIn("createCatalogImportController", settings_page)
        self.assertIn("createRemoteAccessController", settings_page)
        self.assertIn("case 'start-import'", settings_page)
