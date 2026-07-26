"""Acceptance: standalone library seeds a fresh empty hub (upload + metadata).

DISTRIBUTION_SPEC: a standalone library that pairs with a fresh hub seeds it —
the existing upload path already does this; this proves it for N=3 small images.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import db
from features.sync import device_auth, hashing, hub_routes
from features.sync.sync_worker import SyncWorker


class StandaloneSeedsHubTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.hub_db = str(self.root / "hub.db")
        self.standalone_db = str(self.root / "standalone.db")
        self.intake = self.root / "hub-intake"
        self.raws = self.root / "hub-raws"
        self.old_db_path = db.DB_PATH

        for catalog in (self.hub_db, self.standalone_db):
            db.DB_PATH = catalog
            asyncio.run(db.init_db())
        db.DB_PATH = self.hub_db

        self.auth_patch = mock.patch.object(
            device_auth, "require_device_token_enabled", return_value=False
        )
        self.auth_patch.start()
        hub_routes.configure(
            db_path=lambda: self.hub_db,
            intake_root=lambda: self.intake,
            raws_root=lambda: self.raws,
        )
        app = FastAPI()
        app.include_router(hub_routes.router)
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

        self.old_mode = os.environ.get("AZIMUTH_MODE")
        self.old_hub = os.environ.get("AZIMUTH_HUB_URL")
        # Standalone = satellite semantics, no hub yet; then we gain a hub_url.
        os.environ["AZIMUTH_MODE"] = "standalone"
        os.environ.pop("AZIMUTH_HUB_URL", None)

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.auth_patch.stop()
        db.DB_PATH = self.old_db_path
        if self.old_mode is None:
            os.environ.pop("AZIMUTH_MODE", None)
        else:
            os.environ["AZIMUTH_MODE"] = self.old_mode
        if self.old_hub is None:
            os.environ.pop("AZIMUTH_HUB_URL", None)
        else:
            os.environ["AZIMUTH_HUB_URL"] = self.old_hub
        self.tempdir.cleanup()

    def _write_image(self, name: str, color: tuple[int, int, int]) -> Path:
        path = self.root / "standalone-originals" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 6), color).save(path, format="JPEG")
        return path

    async def _request(self, method: str, url: str, *, body=None, headers=None):
        parsed = urlsplit(url)
        response = self.client.request(
            method,
            parsed.path + (f"?{parsed.query}" if parsed.query else ""),
            content=body,
            headers=headers,
        )
        content = response.content
        if response.headers.get("content-encoding") == "gzip" and not content.startswith(b"\x1f\x8b"):
            content = gzip.compress(content)
        return response.status_code, dict(response.headers), content

    def test_standalone_with_three_images_seeds_empty_hub(self):
        colors = [(40, 80, 120), (200, 40, 40), (40, 180, 90)]
        paths = [self._write_image(f"seed-{index}.jpg", color) for index, color in enumerate(colors, start=1)]

        with closing(sqlite3.connect(self.standalone_db)) as conn, conn:
            source_id = conn.execute(
                "INSERT INTO catalog_sources(path, display_name) VALUES (?, ?)",
                (str(self.root / "standalone-originals"), "Standalone roll"),
            ).lastrowid
            image_ids = []
            for path in paths:
                image_id = conn.execute(
                    "INSERT INTO images(source_id, filename, filepath, file_ext, flag, date_taken) "
                    "VALUES (?, ?, ?, '.jpg', 'unflagged', ?)",
                    (source_id, path.name, str(path), "2026-07-12"),
                ).lastrowid
                image_ids.append(image_id)
            conn.execute("UPDATE images SET flag = 'picked' WHERE id = ?", (image_ids[0],))
            conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, 'user', ?)",
                (image_ids[0], json.dumps({"Exposure2012": 0.4}), "2026-07-12T15:00:00Z"),
            )
            conn.commit()

        # Runtime upgrade standalone → satellite: gain a hub_url, then sync.
        hub_url = "http://fresh-hub"
        os.environ["AZIMUTH_MODE"] = "satellite"
        os.environ["AZIMUTH_HUB_URL"] = hub_url

        async def scenario():
            db.DB_PATH = self.standalone_db

            # Empty hub before sync.
            with closing(sqlite3.connect(self.hub_db)) as conn, conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 0)

            worker = SyncWorker(db_path=self.standalone_db, hub=hub_url, request=self._request)
            await worker.sync_once()

            with closing(sqlite3.connect(self.hub_db)) as conn, conn:
                rows = conn.execute(
                    "SELECT filename, content_hash, flag FROM images ORDER BY filename"
                ).fetchall()
            self.assertEqual(len(rows), 3, rows)
            self.assertEqual([row[0] for row in rows], ["seed-1.jpg", "seed-2.jpg", "seed-3.jpg"])

            # Originals landed under hub RAWS layout.
            for path in paths:
                content_hash = hashing.compute_content_hash(path)
                destination = self.raws / "2026" / "2026-07-12" / path.name
                self.assertTrue(destination.is_file(), destination)
                self.assertEqual(destination.read_bytes(), path.read_bytes())
                self.assertIn(content_hash, {row[1] for row in rows})

            # Metadata converged (flag + develop settings).
            picked = next(row for row in rows if row[0] == "seed-1.jpg")
            self.assertEqual(picked[2], "picked")

            with closing(sqlite3.connect(self.hub_db)) as conn, conn:
                hub_image_id = conn.execute(
                    "SELECT id FROM images WHERE filename = ?", ("seed-1.jpg",)
                ).fetchone()[0]
                develop = conn.execute(
                    "SELECT settings FROM develop_settings WHERE image_id = ?",
                    (hub_image_id,),
                ).fetchone()
                self.assertIsNotNone(develop)
                self.assertEqual(json.loads(develop[0]).get("Exposure2012"), 0.4)

            # Idempotent: second pass does not duplicate.
            await worker.sync_once()
            with closing(sqlite3.connect(self.hub_db)) as conn, conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 3)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
