"""RAW/export version-stack matching contracts (§20)."""

from __future__ import annotations

import unittest
from unittest import mock
import sqlite3
import tempfile

from features.stacks import builders


class VersionStackBuilderTests(unittest.TestCase):
    def _row(self, image_id: int, filename: str, **overrides):
        return {
            "id": image_id,
            "filename": filename,
            "filepath": f"/catalog/{filename}",
            "file_ext": filename.rsplit(".", 1)[-1],
            "status": "kept",
            "missing_at": None,
            "date_taken": None,
            "camera_model": None,
            "file_modified_at": 0,
            **overrides,
        }

    def test_exact_basename_pairs_raw_with_newest_edit_representative(self):
        raw = self._row(10, "IMG_4021.dng")
        old_edit = self._row(11, "IMG_4021.jpg", file_modified_at=100.0)
        new_edit = self._row(12, "IMG_4021.tif", file_modified_at=200.0)

        groups = builders.build_version_groups("unused.db", {10: raw, 11: old_edit, 12: new_edit})

        self.assertEqual(len(groups), 1)
        member_ids, representative_id, scores = groups[0]
        self.assertEqual(set(member_ids), {10, 11, 12})
        self.assertEqual(representative_id, 12)
        self.assertEqual(scores[10], 1.0)

    def test_capture_second_and_model_pairs_renamed_export(self):
        raw = self._row(
            20, "IMG_4022.dng", date_taken="2024-06-01 14:10:09", camera_model="Canon EOS R5"
        )
        edit = self._row(
            21, "SKD-Starbase-2024-06-01-N00007.jpg",
            date_taken="2024-06-01T14:10:09",
            camera_model="Canon EOS R5",
            file_modified_at=300.0,
        )

        groups = builders.build_version_groups("unused.db", {20: raw, 21: edit})

        self.assertEqual([(set(group[0]), group[1]) for group in groups], [({20, 21}, 21)])

    def test_exiftool_fallback_is_batched_for_missing_catalog_metadata(self):
        raw = self._row(30, "IMG_4023.dng")
        edit = self._row(31, "renamed-export.jpg", file_modified_at=400.0)
        fallback = {
            builders._metadata_key(raw["filepath"]): ("2024-06-01 14:10:10", "canon eos r5"),
            builders._metadata_key(edit["filepath"]): ("2024-06-01 14:10:10", "canon eos r5"),
        }

        with mock.patch.object(builders, "_exiftool_version_metadata", return_value=fallback) as exiftool:
            groups = builders.build_version_groups("unused.db", {30: raw, 31: edit})

        self.assertEqual([(set(group[0]), group[1]) for group in groups], [({30, 31}, 31)])
        self.assertEqual(exiftool.call_count, 1)
        self.assertEqual({row["id"] for row in exiftool.call_args.args[0]}, {30, 31})

    def test_raws_never_form_a_version_without_an_edit(self):
        rows = {
            40: self._row(40, "IMG_4024.dng", date_taken="2024-06-01 14:10:11", camera_model="R5"),
            41: self._row(41, "IMG_4025.cr3", date_taken="2024-06-01 14:10:11", camera_model="R5"),
            42: self._row(42, "same-second.jpg", date_taken="2024-06-01 14:10:11", camera_model="Other"),
        }

        self.assertEqual(builders.build_version_groups("unused.db", rows), [])

    def test_active_rows_exclude_virtual_copies_from_version_matching(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = f"{tempdir}/versions.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE catalog_sources (id INTEGER PRIMARY KEY, path TEXT, display_name TEXT);
                    CREATE TABLE images (
                        id INTEGER PRIMARY KEY,
                        filename TEXT NOT NULL,
                        filepath TEXT NOT NULL,
                        source_id INTEGER,
                        file_ext TEXT,
                        status TEXT,
                        missing_at REAL,
                        vc_of INTEGER REFERENCES images(id)
                    );
                    INSERT INTO images (id, filename, filepath, file_ext, status) VALUES
                        (1, 'IMG_4026.dng', '/catalog/IMG_4026.dng', 'dng', 'kept'),
                        (2, 'IMG_4026.dng', '/catalog/IMG_4026.dng', 'dng', 'kept'),
                        (3, 'IMG_4026.jpg', '/catalog/IMG_4026.jpg', 'jpg', 'kept');
                    UPDATE images SET vc_of = 1 WHERE id = 2;
                    """
                )
                conn.commit()
            finally:
                conn.close()

            rows = builders._active_rows(db_path)
            groups = builders.build_version_groups(db_path, rows)

        self.assertEqual(set(rows), {1, 3})
        self.assertEqual([(set(group[0]), group[1]) for group in groups], [({1, 3}, 3)])


if __name__ == "__main__":
    unittest.main()
