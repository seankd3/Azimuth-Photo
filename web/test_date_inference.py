import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from data import connection, schema as data_schema  # noqa: E402
from data.repositories import catalog as catalog_repository  # noqa: E402
from data.repositories import images as image_repository  # noqa: E402
from date_inference import infer_image_date  # noqa: E402


class DateInferenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "dates.db")
        self.source_root = os.path.join(self.tempdir.name, "library")
        os.makedirs(self.source_root, exist_ok=True)
        conn = await connection.open_async(self.db_path)
        try:
            await data_schema.apply_schema_and_migrations(conn, db_exists=False)
            source = await catalog_repository.ensure_catalog_source_on_conn(conn, self.source_root)
            self.source_id = int(source["id"])
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=self.db_path)

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    def test_film_scan_filename_date(self):
        inferred = infer_image_date(filename="SKD-Film-2026-06-01-01.jpg")

        self.assertIsNotNone(inferred)
        self.assertEqual(inferred.date_taken, "2026-06-01 12:00:00")
        self.assertEqual(inferred.date_source, "filename")

    def test_compact_filename_prefix_date(self):
        inferred = infer_image_date(filename="20201119-IMG_4860-Edit.jpg")

        self.assertIsNotNone(inferred)
        self.assertEqual(inferred.date_taken, "2020-11-19 12:00:00")
        self.assertEqual(inferred.date_source, "filename")

    def test_folder_date_segment(self):
        inferred = infer_image_date(
            filename="x.jpg",
            filepath="/root/Exported Edits/2024/WAI-Starbase/2024-09-01/x.jpg",
            source_root="/root",
        )

        self.assertIsNotNone(inferred)
        self.assertEqual(inferred.date_taken, "2024-09-01 12:00:00")
        self.assertEqual(inferred.date_source, "folder")

    def test_year_only_folder_uses_jan_first(self):
        inferred = infer_image_date(
            filename="x.jpg",
            filepath="/root/Exported Edits/2026/Film/x.jpg",
            source_root="/root",
        )

        self.assertIsNotNone(inferred)
        self.assertEqual(inferred.date_taken, "2026-01-01 12:00:00")
        self.assertEqual(inferred.date_source, "folder")

    def test_file_modified_at_fallback(self):
        modified = datetime(2023, 5, 4, 9, 30, 15).timestamp()
        inferred = infer_image_date(
            filename="scan.jpg",
            filepath="/root/no-date/scan.jpg",
            file_modified_at=modified,
            source_root="/root",
        )

        self.assertIsNotNone(inferred)
        self.assertEqual(inferred.date_taken, "2023-05-04 09:30:15")
        self.assertEqual(inferred.date_source, "file")

    async def _row(self, image_id: int) -> dict:
        rows = await image_repository.get_images_by_ids(self.db_path, [image_id])
        return rows[image_id]

    async def test_backfill_is_idempotent(self):
        dated_path = os.path.join(self.source_root, "already-dated.jpg")
        filename_path = os.path.join(
            self.source_root,
            "Exported Edits",
            "2026",
            "Film",
            "SKD-Film-2026-06-01-01.jpg",
        )
        conn = await connection.open_async(self.db_path)
        try:
            await conn.execute(
                "INSERT INTO images (source_id, filename, filepath, status, date_taken) "
                "VALUES (?, ?, ?, 'kept', ?)",
                (self.source_id, "already-dated.jpg", dated_path, "2024-01-02 03:04:05"),
            )
            await conn.execute(
                "INSERT INTO images (source_id, filename, filepath, status) "
                "VALUES (?, ?, ?, 'kept')",
                (self.source_id, "SKD-Film-2026-06-01-01.jpg", filename_path),
            )
            await conn.commit()

            self.assertEqual(await data_schema.backfill_image_date_sources(conn), 2)
            await conn.commit()
            self.assertEqual(await data_schema.backfill_image_date_sources(conn), 0)

            cursor = await conn.execute(
                "SELECT filename, date_taken, date_source FROM images ORDER BY filename"
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        finally:
            await connection.close_async(conn, db_path=self.db_path)

        self.assertEqual(rows[0]["date_taken"], "2026-06-01 12:00:00")
        self.assertEqual(rows[0]["date_source"], "filename")
        self.assertEqual(rows[1]["date_taken"], "2024-01-02 03:04:05")
        self.assertEqual(rows[1]["date_source"], "exif")


if __name__ == "__main__":
    unittest.main()
