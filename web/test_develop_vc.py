import sqlite3
import tempfile
import unittest

from data import connection
from features.develop import virtual_copies


class VirtualCopiesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.tempdir.name}/vc.db"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE images (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                status TEXT DEFAULT 'kept',
                vc_of INTEGER REFERENCES images(id) ON DELETE CASCADE
            );
            CREATE TABLE develop_settings (
                image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
                settings TEXT NOT NULL DEFAULT '{}',
                origin TEXT NOT NULL DEFAULT 'user',
                xmp_path TEXT,
                xmp_mtime REAL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE develop_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                settings TEXT NOT NULL,
                label TEXT,
                created_at TEXT NOT NULL
            );
            INSERT INTO images (id, filename, filepath) VALUES (1, 'original.dng', '/photos/original.dng');
            INSERT INTO develop_settings (image_id, settings, origin, updated_at)
            VALUES (1, '{"Exposure2012":1.0}', 'user', '2026-07-10T00:00:00Z');
            """
        )
        conn.commit()
        conn.close()

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def _connection(self):
        return await connection.open_async(self.db_path)

    async def test_copy_has_shared_path_and_independent_settings(self):
        conn = await self._connection()
        try:
            created = await virtual_copies.create_virtual_copy(conn, 1)
            self.assertEqual(created["vc_of"], 1)
            self.assertEqual(created["filepath"], "/photos/original.dng")
            copy_id = created["id"]
            await conn.execute(
                "UPDATE develop_settings SET settings = '{\"Exposure2012\":2.0}' WHERE image_id = ?",
                (copy_id,),
            )
            await conn.commit()
            original = await (await conn.execute("SELECT settings FROM develop_settings WHERE image_id = 1")).fetchone()
            copied = await (await conn.execute("SELECT settings FROM develop_settings WHERE image_id = ?", (copy_id,))).fetchone()
            self.assertEqual(original["settings"], '{"Exposure2012":1.0}')
            self.assertEqual(copied["settings"], '{"Exposure2012":2.0}')
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def test_list_and_delete_only_virtual_copies(self):
        conn = await self._connection()
        try:
            first = await virtual_copies.create_virtual_copy(conn, 1)
            second = await virtual_copies.create_virtual_copy(conn, first["id"])
            await conn.commit()
            copies = await virtual_copies.list_virtual_copies(conn, 1)
            self.assertEqual([item["id"] for item in copies], [first["id"], second["id"]])
            self.assertFalse(await virtual_copies.delete_virtual_copy(conn, 1))
            self.assertTrue(await virtual_copies.delete_virtual_copy(conn, first["id"]))
            await conn.commit()
            self.assertEqual([item["id"] for item in await virtual_copies.list_virtual_copies(conn, 1)], [second["id"]])
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def test_migration_removes_legacy_filepath_unique_constraint(self):
        legacy_path = f"{self.tempdir.name}/legacy.db"
        legacy = sqlite3.connect(legacy_path)
        try:
            legacy.executescript(
                """
                CREATE TABLE images (id INTEGER PRIMARY KEY, filename TEXT NOT NULL, filepath TEXT NOT NULL UNIQUE);
                CREATE TABLE child (image_id INTEGER REFERENCES images(id));
                INSERT INTO images (id, filename, filepath) VALUES (1, 'source.dng', '/photos/source.dng');
                INSERT INTO child (image_id) VALUES (1);
                """
            )
        finally:
            legacy.close()
        conn = await connection.open_async(legacy_path)
        try:
            await virtual_copies.ensure_virtual_copies(conn)
            await conn.execute(
                "INSERT INTO images (filename, filepath, vc_of) VALUES (?, ?, ?)",
                ("copy.dng", "/photos/source.dng", 1),
            )
            await conn.commit()
            child = await (await conn.execute("SELECT image_id FROM child")).fetchone()
            self.assertEqual(child["image_id"], 1)
        finally:
            await connection.close_async(conn, db_path=legacy_path)
