"""One-process acceptance for hub catalog mirroring and oplog convergence."""

from __future__ import annotations

import asyncio
import gzip
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from features.sync import device_auth, hub_routes, mirror_export, oplog, oplog_routes
from features.sync.mirror import MirrorPuller
from features.sync.prefetch import ThumbPrefetcher


class SyncEndToEndAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.hub_db = str(self.root / "hub.db")
        self.satellite_db = str(self.root / "satellite.db")
        self.old_db_path = db.DB_PATH
        for catalog in (self.hub_db, self.satellite_db):
            db.DB_PATH = catalog
            asyncio.run(db.init_db())
        db.DB_PATH = self.hub_db

        self.auth_patch = mock.patch.object(
            device_auth, "require_device_token_enabled", return_value=False
        )
        self.auth_patch.start()
        hub_routes.configure(db_path=lambda: self.hub_db)
        oplog_routes.configure(db_path=lambda: self.hub_db)
        app = FastAPI()
        app.include_router(hub_routes.router)
        app.include_router(oplog_routes.router)
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

        self.thumb_paths: dict[int, Path] = {}
        with sqlite3.connect(self.hub_db) as conn:
            source_id = conn.execute(
                "INSERT INTO catalog_sources(path, display_name) VALUES (?, ?)",
                (str(self.root / "originals"), "Hub originals"),
            ).lastrowid
            for number in range(1, 51):
                conn.execute(
                    "INSERT INTO images(source_id, filename, filepath, content_hash, file_ext) "
                    "VALUES (?, ?, ?, ?, '.jpg')",
                    (
                        source_id,
                        f"photo-{number:02d}.jpg",
                        str(self.root / "originals" / f"photo-{number:02d}.jpg"),
                        f"{number:032x}",
                    ),
                )
                if number <= 10:
                    path = self.root / "thumbs" / f"{number}.jpg"
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(b"jpeg" + bytes([number]))
                    self.thumb_paths[number] = path
            conn.commit()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.auth_patch.stop()
        db.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

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

    async def _json_request(self, method: str, path: str, payload: dict):
        response = self.client.request(method, path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def _flag(catalog: str, content_hash: str) -> str:
        with sqlite3.connect(catalog) as conn:
            return str(conn.execute(
                "SELECT flag FROM images WHERE content_hash = ?", (content_hash,)
            ).fetchone()[0])

    def test_mirror_thumbs_and_bidirectional_oplog_converge(self):
        async def scenario():
            mirror = MirrorPuller(
                db_path=self.satellite_db,
                hub="http://test-hub",
                request=self._request,
            )
            mirror_status = await mirror.refresh()
            self.assertEqual(mirror_status["rows_applied"], 50)

            with sqlite3.connect(self.satellite_db) as conn:
                mirrored = conn.execute(
                    "SELECT COUNT(*), SUM(missing_at IS NOT NULL), MIN(hub_remote), MAX(hub_remote) "
                    "FROM images i JOIN catalog_sources s ON s.id = i.source_id WHERE s.path = 'hub://'"
                ).fetchone()
                first_satellite_id = int(conn.execute(
                    "SELECT id FROM images WHERE content_hash = ?", (f"{1:032x}",)
                ).fetchone()[0])
            self.assertEqual(mirrored, (50, 0, 1, 1))

            stored: list[tuple[str, int, bytes]] = []
            prefetch = ThumbPrefetcher(
                db_path=self.satellite_db,
                hub="http://test-hub",
                request=self._request,
                store=lambda size, image_id, signature, data: stored.append((size, image_id, data)),
            )
            with mock.patch.object(
                mirror_export,
                "_cached_thumb_path",
                side_effect=lambda _size, image_id: self.thumb_paths.get(image_id),
            ):
                thumb_status = await prefetch.prefetch_once(size="sm", limit=50)
            self.assertEqual(len(stored), 10)
            self.assertEqual(thumb_status["total"], 50)

            await oplog.append_flags(self.satellite_db, [first_satellite_id], "picked")
            first_exchange = await oplog.exchange_with_hub(self.satellite_db, self._json_request)
            self.assertEqual(first_exchange["pushed"], 1)
            self.assertEqual(self._flag(self.hub_db, f"{1:032x}"), "picked")

            with sqlite3.connect(self.hub_db) as conn:
                second_hub_id = int(conn.execute(
                    "SELECT id FROM images WHERE content_hash = ?", (f"{2:032x}",)
                ).fetchone()[0])
            await oplog.append_flags(self.hub_db, [second_hub_id], "rejected")
            second_exchange = await oplog.exchange_with_hub(self.satellite_db, self._json_request)
            self.assertGreaterEqual(second_exchange["pulled"], 1)
            self.assertEqual(self._flag(self.satellite_db, f"{2:032x}"), "rejected")

            satellite_cursors = await oplog.local_cursors(self.satellite_db)
            with sqlite3.connect(self.hub_db) as conn:
                hub_cursors = dict(conn.execute(
                    "SELECT origin, MAX(origin_seq) FROM oplog GROUP BY origin"
                ))
            self.assertEqual(satellite_cursors, hub_cursors)
            self.assertEqual(
                await oplog.exchange_with_hub(self.satellite_db, self._json_request),
                {"pushed": 0, "pulled": 0},
            )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
