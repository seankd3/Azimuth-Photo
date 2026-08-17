from core.catalog_path import catalog_path, use as catalog_path_use
import pytest
import json
import os
import pathlib
import re
import sqlite3

from model import photos, sets
import time
from contextlib import closing
from unittest import mock
import tempfile
import unittest

import db

from features.develop import lrcat_import


class LightroomCatalogImportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.catalog_path = os.path.join(self.tempdir.name, "2023-v13.lrcat")
        self.db_path = os.path.join(self.tempdir.name, "azimuth.db")
        self._make_library()
        self._make_catalog()

    def tearDown(self):
        self.tempdir.cleanup()

    def _open(self, path=None):
        # The model reads rows by name, as every connection from `data.connection`
        # does. A fixture that opens sqlite3 raw is a fixture testing a
        # connection the app never hands out.
        conn = sqlite3.connect(path or self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _make_library(self, destination=None):
        with closing(self._open(destination)) as conn, conn:
            conn.executescript("""
                CREATE TABLE images (id INTEGER PRIMARY KEY, filename TEXT, filepath TEXT UNIQUE,
                    date_taken TEXT, flag TEXT DEFAULT 'unflagged', status TEXT DEFAULT 'kept',
                    missing_at REAL, content_hash TEXT);
                CREATE TABLE develop_settings (image_id INTEGER PRIMARY KEY, settings TEXT NOT NULL DEFAULT '{}',
                    origin TEXT NOT NULL DEFAULT 'user', xmp_path TEXT, xmp_mtime REAL, updated_at TEXT NOT NULL);
            """)
            # The core's own tables, from the file the running app applies at boot.
            # A catalog assembled by hand here is a catalog the app never builds.
            core = re.sub(r"--[^\n]*", "", (pathlib.Path(__file__).parent / "model" / "schema.sql").read_text(encoding="utf-8"))
            for statement in [s.strip() for s in core.split(";") if "decisions" in s and s.strip()]:
                conn.execute(statement)
            conn.executemany("INSERT INTO images (id, filename, filepath, date_taken, flag, content_hash) VALUES (?, ?, ?, ?, ?, ?)", [
                (1, "IMG_0001.DNG", "/mnt/expansion/Photos/RAWS/2023/2023-06-01/IMG_0001.DNG", "2023-06-01 10:00:00", "unflagged", "hash-1"),
                (2, "IMG_0002.CR3", "/mnt/expansion/Photos/RAWS/2023/2023-06-02/IMG_0002.CR3", "2023-06-02 11:00:00", "picked", "hash-2"),
                (3, "IMG_0003.DNG", "/mnt/expansion/Photos/RAWS/2023/2023-06-03/IMG_0003.DNG", "2023-06-03 12:00:00", "unflagged", "hash-3"),
            ])

    def _make_catalog(self):
        with closing(sqlite3.connect(self.catalog_path)) as conn, conn:
            conn.executescript("""
                CREATE TABLE Adobe_images (id_local INTEGER PRIMARY KEY, rootFile INTEGER, pick INTEGER, rating INTEGER, captureTime TEXT, touchTime REAL);
                CREATE TABLE Adobe_imageDevelopSettings (id_local INTEGER PRIMARY KEY, image INTEGER, text TEXT);
                CREATE TABLE AgLibraryFile (id_local INTEGER PRIMARY KEY, folder INTEGER, baseName TEXT, extension TEXT);
                CREATE TABLE AgLibraryFolder (id_local INTEGER PRIMARY KEY, rootFolder INTEGER, pathFromRoot TEXT);
                CREATE TABLE AgLibraryRootFolder (id_local INTEGER PRIMARY KEY, absolutePath TEXT);
                CREATE TABLE AgLibraryKeyword (id_local INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE AgLibraryKeywordImage (image INTEGER, tag INTEGER);
                CREATE TABLE AgLibraryCollection (id_local INTEGER PRIMARY KEY, name TEXT, creationId TEXT);
                CREATE TABLE AgLibraryCollectionImage (collection INTEGER, image INTEGER);
            """)
            conn.execute("INSERT INTO AgLibraryRootFolder VALUES (1, 'D:\\Photos')")
            conn.executemany("INSERT INTO AgLibraryFolder VALUES (?, 1, ?)", [(1, 'RAWS/2023/2023-06-01'), (2, 'RAWS/2023/2023-06-02'), (3, 'Other')])
            conn.executemany("INSERT INTO AgLibraryFile VALUES (?, ?, ?, ?)", [(11, 1, 'IMG_0001', 'DNG'), (12, 2, 'IMG_0002', 'CR3'), (13, 3, 'IMG_0003', 'DNG')])
            conn.executemany("INSERT INTO Adobe_images VALUES (?, ?, ?, ?, ?, ?)", [
                (101, 11, 1, 5, '2023-06-01 10:00:00', 700000000.0),
                (102, 12, -1, 3, '2023-06-02 11:00:00', 700000100.0),
                (103, 13, 0, 2, '2023-06-03 12:00:00', 700000200.0),
            ])
            conn.executemany("INSERT INTO Adobe_imageDevelopSettings VALUES (?, ?, ?)", [
                (1, 101, '''s = { Exposure2012 = -0.25, ToneCurvePV2012 = { 0, 0, 64, 56, 255, 255 },
                    LensProfileEnable = 1, MaskGroupBasedCorrections = { { CorrectionID = "mask-1", CorrectionActive = true } },
                    Look = { Name = "Vintage", Parameters = { ToneCurvePV2012 = { 0, 0, 128, 110, 255, 255 } } } }'''),
                (2, 102, 's = { Exposure2012 = 1.0, LensProfileEnable = 0 }'),
                (3, 103, 's = { Exposure2012 = 2.0 }'),
            ])
            conn.execute("INSERT INTO AgLibraryKeyword VALUES (1, 'travel')")
            conn.execute("INSERT INTO AgLibraryKeywordImage VALUES (101, 1)")
            conn.executemany("INSERT INTO AgLibraryCollection VALUES (?, ?, ?)", [(1, 'Favorites', 'com.adobe.ag.library.collection'), (2, 'Smart', 'com.adobe.ag.library.smart_collection')])
            conn.executemany("INSERT INTO AgLibraryCollectionImage VALUES (?, ?)", [(1, 101), (1, 102), (2, 103)])

    def test_imports_picks_ratings_and_real_collections_without_overwriting_flags(self):
        result = lrcat_import.import_lrcat(self.catalog_path, self.db_path)
        self.assertEqual(result['matched'], 3)
        self.assertEqual(result['suffix_matches'], 2)
        self.assertEqual(result['filename_date_matches'], 1)
        self.assertEqual(result['picks_updated'], 1)
        self.assertEqual(result['picks_protected'], 1)
        self.assertEqual(result['keywords_skipped'], 1)
        self.assertEqual(result['collections_created'], 1)
        with closing(self._open()) as conn, conn:
            flags = dict(conn.execute('SELECT id, flag FROM images'))
            settings = dict(conn.execute('SELECT image_id, settings FROM develop_settings'))
            named = [s for s in sets.all(conn) if s["name"] == 'LR 2023/Favorites']
            self.assertEqual(len(named), 1)
            members = photos.ids(conn, sets.members(conn, named[0]["id"]))
        self.assertEqual(flags, {1: 'picked', 2: 'picked', 3: 'unflagged'})
        self.assertEqual(json.loads(settings[1])['_lr_rating'], 5)
        self.assertEqual(json.loads(settings[2])['_lr_rating'], 3)
        self.assertEqual(members, [1, 2])

    def test_imports_full_catalog_settings_and_respects_fresher_xmp_or_user_work(self):
        epoch = 978307200.0
        with closing(self._open()) as conn, conn:
            conn.executemany(
                "INSERT INTO develop_settings (image_id, settings, origin, xmp_path, xmp_mtime, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (1, '{"Exposure2012":9}', 'xmp', '/tmp/one.xmp', epoch + 699999999, '2024-01-01T00:00:00Z'),
                    (2, '{"Exposure2012":8}', 'xmp', '/tmp/two.xmp', epoch + 700000101, '2024-01-01T00:00:00Z'),
                    (3, '{"Exposure2012":7}', 'user', None, None, '2024-01-01T00:00:00Z'),
                ],
            )
        result = lrcat_import.import_lrcat(self.catalog_path, self.db_path)
        with closing(self._open()) as conn, conn:
            stored = {
                image_id: (json.loads(settings), origin)
                for image_id, settings, origin in conn.execute(
                    'SELECT image_id, settings, origin FROM develop_settings ORDER BY image_id'
                )
            }
        imported, imported_origin = stored[1]
        self.assertEqual(imported_origin, 'lrcat')
        self.assertEqual(imported['ToneCurvePV2012'], ['0, 0', '64, 56', '255, 255'])
        self.assertEqual(
            imported['Look']['Parameters']['ToneCurvePV2012'],
            ['0, 0', '128, 110', '255, 255'],
        )
        self.assertEqual(imported['MaskGroupBasedCorrections'][0]['CorrectionID'], 'mask-1')
        self.assertEqual(imported['Look']['Name'], 'Vintage')
        self.assertEqual(imported['LensProfileEnable'], 1)
        self.assertEqual(stored[2][0]['Exposure2012'], 8)
        self.assertEqual(stored[2][1], 'xmp')
        self.assertEqual(stored[3][0]['Exposure2012'], 7)
        self.assertEqual(stored[3][1], 'user')
        self.assertEqual(result['develop_settings_updated'], 1)
        self.assertEqual(result['develop_settings_skipped'], 2)

    @pytest.mark.contract
    def test_rating_import_does_not_advance_the_develop_clock(self):
        with closing(self._open()) as conn, conn:
            conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) "
                "VALUES (1, ?, 'user', 'develop-save')",
                ('{"Exposure2012":0.5}',),
            )
            lrcat_import._store_rating(conn, 1, 5, rating_column=False)
            lrcat_import._store_rating(conn, 2, 3, rating_column=False)
            clocks = dict(conn.execute(
                "SELECT image_id, updated_at FROM develop_settings WHERE image_id IN (1, 2)"
            ))

        self.assertEqual(clocks[1], "develop-save")
        self.assertEqual(clocks[2], "")

    def test_is_idempotent_and_dry_run_does_not_write(self):
        first = lrcat_import.import_lrcat(self.catalog_path, self.db_path)
        second = lrcat_import.import_lrcat(self.catalog_path, self.db_path)
        dry_db = os.path.join(self.tempdir.name, 'dry.db')
        self._make_library(dry_db)
        dry = lrcat_import.import_lrcat(self.catalog_path, dry_db, dry_run=True)
        self.assertEqual(first['collections_created'], 1)
        self.assertEqual(second['collections_created'], 0)
        self.assertEqual(second['picks_updated'], 0)
        self.assertEqual(second['ratings_updated'], 0)
        self.assertEqual(dry['matched'], 3)
        with closing(self._open(dry_db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM develop_settings').fetchone()[0], 0)
            self.assertEqual(sets.all(conn), [])

    def _wait_for_scan(self, client, timeout=15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = client.get('/api/develop/lrcat/status').json()
            if not status['running']:
                return status
            time.sleep(0.05)
        raise AssertionError('Lightroom catalog scan did not finish in time')

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from features.develop import import_routes

        app = FastAPI()
        app.include_router(import_routes.router)
        catalog_path_use(self.db_path)
        return TestClient(app)

    def test_catalogs_route_lists_discovered_catalogs(self):
        with self._client() as client, mock.patch.dict(os.environ, {'AZIMUTH_LIGHTROOM_CATALOG_DIRS': self.tempdir.name}):
            payload = client.get('/api/develop/lrcat/catalogs').json()
        self.assertEqual(payload['catalogs'], [self.catalog_path])

    def test_scan_route_dry_run_reports_counts_without_writing(self):
        with self._client() as client:
            started = client.post('/api/develop/lrcat/scan', json={'catalog_path': self.catalog_path, 'dry_run': True})
            self.assertEqual(started.status_code, 200)
            self.assertTrue(started.json()['status']['dry_run'])
            status = self._wait_for_scan(client)
        self.assertEqual(status['catalogs'], 1)
        self.assertEqual(status['matched'], 3)
        result = status['results'][0]
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['picks_updated'], 1)
        self.assertEqual(result['ratings_updated'], 3)
        self.assertEqual(result['develop_settings_updated'], 3)
        self.assertEqual(result['collections_created'], 1)
        with closing(self._open()) as conn:
            self.assertEqual(sets.all(conn), [])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM develop_settings').fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM images WHERE flag != 'unflagged'").fetchone()[0], 1)

    def test_scan_route_runs_import_after_preview(self):
        with self._client() as client:
            client.post('/api/develop/lrcat/scan', json={'catalog_path': self.catalog_path, 'dry_run': True})
            self._wait_for_scan(client)
            started = client.post('/api/develop/lrcat/scan', json={'catalog_path': self.catalog_path})
            self.assertEqual(started.status_code, 200)
            self.assertFalse(started.json()['status']['dry_run'])
            status = self._wait_for_scan(client)
        self.assertEqual(status['results'][0]['collections_created'], 1)
        with closing(self._open()) as conn:
            self.assertEqual([s["name"] for s in sets.all(conn)], ['LR 2023/Favorites'])
            self.assertEqual(conn.execute("SELECT flag FROM images WHERE id = 1").fetchone()[0], 'picked')

