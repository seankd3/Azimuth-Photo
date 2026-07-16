import json
import os
import sqlite3
import tempfile
import unittest

from features.develop import lrcat_import


class LightroomCatalogImportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.catalog_path = os.path.join(self.tempdir.name, "2023-v13.lrcat")
        self.db_path = os.path.join(self.tempdir.name, "photoarchive.db")
        self._make_library()
        self._make_catalog()

    def tearDown(self):
        self.tempdir.cleanup()

    def _make_library(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE images (id INTEGER PRIMARY KEY, filename TEXT, filepath TEXT UNIQUE,
                    date_taken TEXT, flag TEXT DEFAULT 'unflagged', status TEXT DEFAULT 'kept', missing_at REAL);
                CREATE TABLE develop_settings (image_id INTEGER PRIMARY KEY, settings TEXT NOT NULL DEFAULT '{}',
                    origin TEXT NOT NULL DEFAULT 'user', xmp_path TEXT, xmp_mtime REAL, updated_at TEXT NOT NULL);
                CREATE TABLE collections (id INTEGER PRIMARY KEY, uuid TEXT UNIQUE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    visibility TEXT NOT NULL DEFAULT 'private', status TEXT NOT NULL DEFAULT 'draft', query TEXT,
                    cover_image_id INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE collection_images (collection_id INTEGER, image_id INTEGER, position INTEGER NOT NULL DEFAULT 0,
                    added_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(collection_id, image_id));
                CREATE TABLE collection_publishes (collection_id INTEGER, slug TEXT, published_at REAL, updated_at REAL);
            """)
            conn.executemany("INSERT INTO images (id, filename, filepath, date_taken, flag) VALUES (?, ?, ?, ?, ?)", [
                (1, "IMG_0001.DNG", "/mnt/expansion/Photos/RAWS/2023/2023-06-01/IMG_0001.DNG", "2023-06-01 10:00:00", "unflagged"),
                (2, "IMG_0002.CR3", "/mnt/expansion/Photos/RAWS/2023/2023-06-02/IMG_0002.CR3", "2023-06-02 11:00:00", "picked"),
                (3, "IMG_0003.DNG", "/mnt/expansion/Photos/RAWS/2023/2023-06-03/IMG_0003.DNG", "2023-06-03 12:00:00", "unflagged"),
            ])

    def _make_catalog(self):
        with sqlite3.connect(self.catalog_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
            flags = dict(conn.execute('SELECT id, flag FROM images'))
            settings = dict(conn.execute('SELECT image_id, settings FROM develop_settings'))
            collection = conn.execute("SELECT id FROM collections WHERE name = 'LR 2023/Favorites'").fetchone()
            members = [row[0] for row in conn.execute('SELECT image_id FROM collection_images WHERE collection_id = ? ORDER BY position', collection)]
        self.assertEqual(flags, {1: 'picked', 2: 'picked', 3: 'unflagged'})
        self.assertEqual(json.loads(settings[1])['_lr_rating'], 5)
        self.assertEqual(json.loads(settings[2])['_lr_rating'], 3)
        self.assertEqual(members, [1, 2])

    def test_imports_full_catalog_settings_and_respects_fresher_xmp_or_user_work(self):
        epoch = 978307200.0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO develop_settings (image_id, settings, origin, xmp_path, xmp_mtime, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (1, '{"Exposure2012":9}', 'xmp', '/tmp/one.xmp', epoch + 699999999, '2024-01-01T00:00:00Z'),
                    (2, '{"Exposure2012":8}', 'xmp', '/tmp/two.xmp', epoch + 700000101, '2024-01-01T00:00:00Z'),
                    (3, '{"Exposure2012":7}', 'user', None, None, '2024-01-01T00:00:00Z'),
                ],
            )
        result = lrcat_import.import_lrcat(self.catalog_path, self.db_path)
        with sqlite3.connect(self.db_path) as conn:
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

    def test_rating_import_does_not_advance_the_develop_clock(self):
        with sqlite3.connect(self.db_path) as conn:
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
        self._make_library_copy(dry_db)
        dry = lrcat_import.import_lrcat(self.catalog_path, dry_db, dry_run=True)
        self.assertEqual(first['collections_created'], 1)
        self.assertEqual(second['collections_created'], 0)
        self.assertEqual(second['picks_updated'], 0)
        self.assertEqual(second['ratings_updated'], 0)
        self.assertEqual(dry['matched'], 3)
        with sqlite3.connect(dry_db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM develop_settings').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM collections').fetchone()[0], 0)

    def _make_library_copy(self, destination):
        with sqlite3.connect(destination) as conn:
            conn.executescript("""
                CREATE TABLE images (id INTEGER PRIMARY KEY, filename TEXT, filepath TEXT UNIQUE, date_taken TEXT, flag TEXT DEFAULT 'unflagged', status TEXT DEFAULT 'kept', missing_at REAL);
                CREATE TABLE develop_settings (image_id INTEGER PRIMARY KEY, settings TEXT NOT NULL DEFAULT '{}', origin TEXT NOT NULL DEFAULT 'user', xmp_path TEXT, xmp_mtime REAL, updated_at TEXT NOT NULL);
                CREATE TABLE collections (id INTEGER PRIMARY KEY, uuid TEXT UNIQUE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', visibility TEXT NOT NULL DEFAULT 'private', status TEXT NOT NULL DEFAULT 'draft', query TEXT, cover_image_id INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE collection_images (collection_id INTEGER, image_id INTEGER, position INTEGER NOT NULL DEFAULT 0, added_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(collection_id, image_id));
                CREATE TABLE collection_publishes (collection_id INTEGER, slug TEXT, published_at REAL, updated_at REAL);
            """)
            conn.executemany("INSERT INTO images (id, filename, filepath, date_taken, flag) VALUES (?, ?, ?, ?, ?)", [
                (1, 'IMG_0001.DNG', '/mnt/expansion/Photos/RAWS/2023/2023-06-01/IMG_0001.DNG', '2023-06-01', 'unflagged'),
                (2, 'IMG_0002.CR3', '/mnt/expansion/Photos/RAWS/2023/2023-06-02/IMG_0002.CR3', '2023-06-02', 'picked'),
                (3, 'IMG_0003.DNG', '/mnt/expansion/Photos/RAWS/2023/2023-06-03/IMG_0003.DNG', '2023-06-03', 'unflagged'),
            ])
