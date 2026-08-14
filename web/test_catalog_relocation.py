"""Catalog-follows-renames repair — rename, reshuffle, collision, unmatched."""

from __future__ import annotations

import unittest
from pathlib import Path

from test_support import *  # noqa: F401,F403
from data import connection
from features.imports import relocation
from photo.identity import compute_content_hash


class RelativeCandidatesTests(unittest.TestCase):
    """Pure mapping — covers RAWS→Raws, which case-folding filesystems
    cannot exercise with real directories, plus the Raws/Digital shelf."""

    def test_legacy_roots_map_to_canonical_first(self):
        cases = [
            (("RAWS", "2024", "x.cr3"), ("Raws", "2024", "x.cr3")),
            (("Personal Photos", "2024", "a.jpg"), ("Snapshots", "2024", "a.jpg")),
            (("Exported Edits", "2025", "b.jpg"), ("Edits", "2025", "b.jpg")),
            (("Film Scans", "roll12", "c.tif"), ("Raws", "Film Scans", "roll12", "c.tif")),
        ]
        for old, expected in cases:
            candidates = relocation._relative_candidates(old)
            self.assertTrue(candidates, old)
            self.assertEqual(candidates[0], expected, old)

    def test_bare_raws_paths_also_probe_the_digital_shelf(self):
        self.assertEqual(
            relocation._relative_candidates(("Raws", "2024", "x.cr3")),
            [("Raws", "Digital", "2024", "x.cr3")],
        )
        self.assertEqual(
            relocation._relative_candidates(("RAWS", "2024", "x.cr3")),
            [("Raws", "2024", "x.cr3"), ("Raws", "Digital", "2024", "x.cr3")],
        )
        # Paths already on a shelf never get re-shelved.
        self.assertEqual(
            relocation._relative_candidates(("Raws", "Digital", "2024", "x.cr3")), []
        )
        self.assertEqual(
            relocation._relative_candidates(("Raws", "Film Scans", "roll12", "c.tif")), []
        )

    def test_canonical_roots_are_left_alone(self):
        self.assertEqual(relocation._relative_candidates(("Snapshots", "2024", "a.jpg")), [])
        self.assertEqual(relocation._relative_candidates(("Custom Folder", "a.jpg")), [])


class RelocationFixtureTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.library = Path(self.tempdir.name) / "Photos"
        self.library.mkdir()

    def _file(self, relative: str, payload: bytes) -> Path:
        path = self.library.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    async def _row(
        self,
        relative: str,
        *,
        size: int,
        source_top: str,
        content_hash: str | None = None,
        elo: float | None = None,
        comparisons: int | None = None,
        flag: str | None = None,
    ) -> int:
        filepath = str(self.library.joinpath(*relative.split("/")))
        source = await db.add_or_restore_source(str(self.library / source_top))
        name = relative.rsplit("/", 1)[-1]
        await db.insert_images_batch(
            [(name, filepath, Path(name).suffix.lower(), size, 1000.0)],
            source_id=source["id"],
        )
        conn = await connection.open_async(db.DB_PATH)
        try:
            row = await (
                await conn.execute("SELECT id FROM images WHERE filepath = ?", (filepath,))
            ).fetchone()
            image_id = int(row["id"])
            for column, value in (
                ("content_hash", content_hash),
                ("elo", elo),
                ("comparisons", comparisons),
                ("flag", flag),
            ):
                if value is not None:
                    await conn.execute(
                        f"UPDATE images SET {column} = ? WHERE id = ?", (value, image_id)
                    )
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=db.DB_PATH)
        return image_id

    async def _images(self, image_id: int) -> dict:
        conn = await connection.open_async(db.DB_PATH)
        try:
            row = await (
                await conn.execute("SELECT * FROM images WHERE id = ?", (image_id,))
            ).fetchone()
        finally:
            await connection.close_async(conn, db_path=db.DB_PATH)
        return dict(row) if row is not None else None

    async def test_rename_matches_by_relative_path_and_dry_run_is_read_only(self):
        snap = self._file("Snapshots/2024/2024-06-07/a.jpg", b"phone-bytes-a")
        edit = self._file("Edits/2025/2025-01-02/b.jpg", b"edit-bytes-b")
        scan = self._file("Raws/Film Scans/roll12/c.tif", b"tiff-bytes-c")
        ids = [
            await self._row(
                "Personal Photos/2024/2024-06-07/a.jpg",
                size=snap.stat().st_size,
                source_top="Personal Photos",
                content_hash=compute_content_hash(snap),
            ),
            await self._row(
                "Exported Edits/2025/2025-01-02/b.jpg",
                size=edit.stat().st_size,
                source_top="Exported Edits",
            ),
            await self._row(
                "Film Scans/roll12/c.tif",
                size=scan.stat().st_size,
                source_top="Film Scans",
            ),
        ]

        before = {image_id: (await self._images(image_id))["filepath"] for image_id in ids}
        report = await relocation.relocate_catalog(db.DB_PATH, self.library)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["relocated"], 3)
        self.assertEqual(report["matched_by"]["relative_path"], 3)
        self.assertEqual(report["unmatched_total"], 0)
        for image_id in ids:
            row = await self._images(image_id)
            self.assertEqual(row["filepath"], before[image_id], "dry-run must not rewrite")

        applied = await relocation.relocate_catalog(db.DB_PATH, self.library, apply=True)
        self.assertFalse(applied["dry_run"])
        self.assertEqual(applied["relocated"], 3)
        expected = {ids[0]: snap, ids[1]: edit, ids[2]: scan}
        for image_id, path in expected.items():
            row = await self._images(image_id)
            self.assertEqual(row["filepath"], str(path))
            self.assertIsNone(row["missing_at"])
            top = path.relative_to(self.library).parts[0]
            conn = await connection.open_async(db.DB_PATH)
            try:
                source = await (
                    await conn.execute(
                        "SELECT path FROM catalog_sources WHERE id = ?", (row["source_id"],)
                    )
                ).fetchone()
            finally:
                await connection.close_async(conn, db_path=db.DB_PATH)
            self.assertEqual(source["path"], str(self.library / top))

        # Idempotent: nothing left to repair on a second pass.
        again = await relocation.relocate_catalog(db.DB_PATH, self.library, apply=True)
        self.assertEqual(again["rows_missing_file"], 0)

    async def test_file_moved_onto_digital_shelf_is_found_by_relative_path(self):
        moved = self._file("Raws/Digital/2024/2024-06-07/d.cr3", b"digital-shelf-raw")
        ids = [
            # Bare under the canonical Raws root before the shelf existed.
            await self._row(
                "Raws/2024/2024-06-07/d.cr3",
                size=moved.stat().st_size,
                source_top="Raws",
                content_hash=compute_content_hash(moved),
            ),
        ]
        legacy = self._file("Raws/Digital/2023/2023-01-05/e.cr3", b"legacy-root-raw")
        ids.append(
            # Bare under the retired RAWS spelling — both renames at once.
            await self._row(
                "RAWS/2023/2023-01-05/e.cr3",
                size=legacy.stat().st_size,
                source_top="RAWS",
                content_hash=compute_content_hash(legacy),
            )
        )

        report = await relocation.relocate_catalog(db.DB_PATH, self.library, apply=True)
        self.assertEqual(report["relocated"], 2)
        self.assertEqual(report["matched_by"]["relative_path"], 2)
        self.assertEqual(report["unmatched_total"], 0)
        self.assertEqual((await self._images(ids[0]))["filepath"], str(moved))
        self.assertEqual((await self._images(ids[1]))["filepath"], str(legacy))

    async def test_reshuffle_matches_by_basename_size_then_content_hash(self):
        moved = self._file("Snapshots/2020/2020-05-05/moved.jpg", b"reshuffled-payload")
        moved_id = await self._row(
            "Snapshots/old-folder/moved.jpg",
            size=moved.stat().st_size,
            source_top="Snapshots",
        )

        first = self._file("Raws/2024/a/dup.jpg", b"payload-AAAA")
        second = self._file("Raws/2024/b/dup.jpg", b"payload-BBBB")
        hashed_id = await self._row(
            "Raws/old/dup.jpg",
            size=second.stat().st_size,
            source_top="Raws",
            content_hash=compute_content_hash(second),
        )
        ambiguous_id = await self._row(
            "Raws/old/twin.jpg", size=9, source_top="Raws"
        )
        self._file("Raws/2024/a/twin.jpg", b"twin-jpg1")
        self._file("Raws/2024/b/twin.jpg", b"twin-jpg2")

        report = await relocation.relocate_catalog(db.DB_PATH, self.library, apply=True)
        self.assertEqual(report["matched_by"]["basename_size"], 1)
        self.assertEqual(report["matched_by"]["content_hash"], 1)
        self.assertEqual((await self._images(moved_id))["filepath"], str(moved))
        self.assertEqual((await self._images(hashed_id))["filepath"], str(second))
        self.assertTrue(first.is_file())
        self.assertEqual(report["unmatched_total"], 1)
        self.assertEqual(report["unmatched"][0]["id"], ambiguous_id)
        row = await self._images(ambiguous_id)
        self.assertEqual(row["filepath"], str(self.library / "Raws" / "old" / "twin.jpg"))

    async def test_collision_merge_keeps_elder_earned_data_and_retires_fresh_row(self):
        payload = b"collision-photo-bytes"
        target = self._file("Snapshots/2024/2024-06-07/c.jpg", payload)
        elder_id = await self._row(
            "Personal Photos/2024/2024-06-07/c.jpg",
            size=len(payload),
            source_top="Personal Photos",
            content_hash=compute_content_hash(target),
            elo=1503.25,
            comparisons=17,
            flag="picked",
        )
        fresh_id = await self._row(
            "Snapshots/2024/2024-06-07/c.jpg",
            size=len(payload),
            source_top="Snapshots",
        )
        bystander = self._file("Raws/2024/2024-06-07/d.cr3", b"raw-bystander")
        bystander_id = await self._row(
            "Raws/2024/2024-06-07/d.cr3",
            size=bystander.stat().st_size,
            source_top="Raws",
        )
        conn = await connection.open_async(db.DB_PATH)
        try:
            await conn.execute(
                "INSERT INTO comparisons (winner_id, loser_id, mode) VALUES (?, ?, 'duel')",
                (bystander_id, fresh_id),
            )
            await conn.execute(
                "UPDATE images SET comparisons = 1 WHERE id IN (?, ?)",
                (bystander_id, fresh_id),
            )
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=db.DB_PATH)

        report = await relocation.relocate_catalog(db.DB_PATH, self.library, apply=True)
        self.assertEqual(report["merged"], 1)
        self.assertEqual(report["actions_total"], 1)
        self.assertEqual(report["actions"][0]["retired_image_id"], fresh_id)

        elder = await self._images(elder_id)
        self.assertEqual(elder["filepath"], str(target))
        self.assertEqual(elder["elo"], 1503.25)
        self.assertEqual(elder["comparisons"], 17)
        self.assertEqual(elder["flag"], "picked")
        self.assertIsNone(await self._images(fresh_id))
        self.assertEqual(target.read_bytes(), payload, "photo bytes must never change")
        # Fresh row retired through the standard deletion path: its comparison
        # is gone and the bystander's counter was decremented.
        self.assertEqual((await self._images(bystander_id))["comparisons"], 0)

    async def test_unmatched_rows_are_reported_never_deleted_and_astro_is_off_limits(self):
        payload = b"gone-bytes!"
        # An identical file inside Astrophotography/ must never be considered.
        astro = self.library / "Astrophotography" / "gone.jpg"
        astro.parent.mkdir(parents=True)
        astro.write_bytes(payload)
        gone_id = await self._row(
            "Personal Photos/2024/gone.jpg",
            size=len(payload),
            source_top="Personal Photos",
        )

        report = await relocation.relocate_catalog(db.DB_PATH, self.library, apply=True)
        self.assertEqual(report["unmatched_total"], 1)
        self.assertEqual(report["unmatched"][0]["id"], gone_id)
        self.assertEqual(report["unmatched"][0]["reason"], "no matching file found")
        row = await self._images(gone_id)
        self.assertIsNotNone(row, "unmatched rows are reported, never deleted")
        self.assertEqual(row["filepath"], str(self.library / "Personal Photos" / "2024" / "gone.jpg"))
        self.assertTrue(astro.is_file())


if __name__ == "__main__":
    unittest.main()
