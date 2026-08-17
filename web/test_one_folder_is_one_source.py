"""A folder registers once, however its path is spelled.

The owner's library held every RAW twice: one directory registered as both
`/Photos/Raws` and `/Photos/RAWS`, which are the same directory on that volume
and were two different strings to `ON CONFLICT(path)`. The catalog went from
147,000 photos to 244,000 and the second copy of each had no previews, no
ratings and no comparisons.

Whether two paths are one directory is a question only the filesystem can
answer, so this asks it.
"""

from core.catalog_path import catalog_path, use as catalog_path_use
import asyncio
import os
import tempfile
import unittest

import db
from data import connection
from data.repositories import catalog as catalog_repository


class OneFolderIsOneSourceTests(unittest.IsolatedAsyncioTestCase):
    def _setupAsyncioRunner(self):
        self._asyncioRunner = asyncio.Runner(debug=False)

    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = os.path.join(self.tempdir.name, "catalog.db")
        catalog_path_use(self.db_path)
        await db.init_db()
        self.photos = os.path.join(self.tempdir.name, "Photos")
        os.makedirs(self.photos, exist_ok=True)

    async def asyncTearDown(self):
        await connection.close_shared_readers()

    async def _sources(self):
        conn = await connection.open_async(self.db_path)
        try:
            rows = await (await conn.execute("SELECT path FROM catalog_sources")).fetchall()
            return [str(row["path"]) for row in rows]
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def test_the_same_folder_reached_by_a_different_path_is_one_source(self):
        """`Photos` and `sub/../Photos` are one directory on every platform."""

        os.makedirs(os.path.join(self.photos, "sub"), exist_ok=True)
        indirect = os.path.join(self.photos, "sub", "..")

        await catalog_repository.add_or_restore_source(self.db_path, self.photos)
        await catalog_repository.add_or_restore_source(self.db_path, indirect)

        self.assertEqual(len(await self._sources()), 1)

    @unittest.skipUnless(os.path.normcase("A") == os.path.normcase("a"), "case-insensitive only")
    async def test_the_same_folder_in_different_case_is_one_source(self):
        """The exact shape of the owner's incident."""

        await catalog_repository.add_or_restore_source(self.db_path, self.photos)
        await catalog_repository.add_or_restore_source(self.db_path, self.photos.upper())

        self.assertEqual(len(await self._sources()), 1)

    async def test_two_real_folders_stay_two_sources(self):
        other = os.path.join(self.tempdir.name, "Elsewhere")
        os.makedirs(other, exist_ok=True)

        await catalog_repository.add_or_restore_source(self.db_path, self.photos)
        await catalog_repository.add_or_restore_source(self.db_path, other)

        self.assertEqual(len(await self._sources()), 2)

    async def test_registering_the_same_folder_again_keeps_its_id(self):
        """Re-adding must not orphan the photos already filed under it."""

        first = await catalog_repository.add_or_restore_source(self.db_path, self.photos)
        again = await catalog_repository.add_or_restore_source(self.db_path, self.photos)

        self.assertEqual(int(first["id"]), int(again["id"]))


if __name__ == "__main__":
    unittest.main()
