"""HDD governor + read-once harvest contracts."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from core import hdd_governor
from features.sync import hashing
from thumbnails import harvest


class HddGovernorTests(unittest.TestCase):
    def setUp(self):
        hdd_governor.reset_for_tests(1)

    def tearDown(self):
        hdd_governor.reset_for_tests(1)

    def test_bulk_slot_serializes_to_one(self):
        peaks: list[int] = []
        lock = threading.Lock()
        current = 0

        def worker():
            nonlocal current
            with hdd_governor.bulk_hdd_slot_sync():
                with lock:
                    current += 1
                    peaks.append(current)
                time.sleep(0.05)
                with lock:
                    current -= 1

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(max(peaks), 1)

    def test_concurrency_env_respected(self):
        hdd_governor.reset_for_tests(2)
        peaks: list[int] = []
        lock = threading.Lock()
        current = 0

        def worker():
            nonlocal current
            with hdd_governor.bulk_hdd_slot_sync():
                with lock:
                    current += 1
                    peaks.append(current)
                time.sleep(0.04)
                with lock:
                    current -= 1

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertLessEqual(max(peaks), 2)
        self.assertGreaterEqual(max(peaks), 2)


class HarvestReadOnceTests(unittest.TestCase):
    def setUp(self):
        hdd_governor.reset_for_tests(1)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "catalog.db"
        self.image = self.root / "shot.jpg"
        # Minimal JPEG so Pillow + hash both work.
        from PIL import Image

        Image.new("RGB", (64, 48), color=(12, 34, 56)).save(self.image, "JPEG")
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE images ("
            "id INTEGER PRIMARY KEY, content_hash TEXT, "
            "date_taken TEXT, date_source TEXT, "
            "camera_make TEXT, camera_model TEXT, lens TEXT, "
            "file_ext TEXT, file_size INTEGER, file_modified_at REAL, "
            "width INTEGER, height INTEGER, "
            "metadata_scanned_at REAL, metadata_version INTEGER, "
            "orientation TEXT, aspect_ratio REAL, "
            "latitude REAL, longitude REAL)"
        )
        conn.execute(
            "INSERT INTO images(id, content_hash, metadata_scanned_at, metadata_version) "
            "VALUES (1, NULL, NULL, NULL)"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()
        hdd_governor.reset_for_tests(1)

    def test_harvest_fills_hash_from_warm_bytes_without_reopen(self):
        opens: list[str] = []
        real_open = open

        def counting_open(path, *args, **kwargs):
            if str(path) == str(self.image):
                opens.append("open")
            return real_open(path, *args, **kwargs)

        data = self.image.read_bytes()
        with mock.patch("builtins.open", counting_open):
            result = harvest.harvest_side_products(
                str(self.image),
                1,
                need_hash=True,
                need_metadata=False,
                source_data=data,
                source_bytes=len(data),
                persist_hash=lambda iid, digest: harvest.persist_content_hash_sync(
                    str(self.db), iid, digest
                ),
            )
        self.assertTrue(result.hash_written)
        self.assertEqual(result.content_hash, hashing.compute_content_hash(self.image))
        self.assertEqual(opens, [])

    def test_harvest_original_bulk_uses_governor_and_one_thumb_load(self):
        loads = {"count": 0}

        def fake_generate(fp, iid, sigs, **kwargs):
            loads["count"] += 1
            on_loaded = kwargs.get("on_source_loaded")
            if on_loaded is not None:
                on_loaded(self.image.read_bytes(), mock.Mock(width=64, height=48, getexif=lambda: {}))
            return {
                "source_reads": 1,
                "thumbnails_written": 3,
                "source_bytes": self.image.stat().st_size,
                "read_seconds": 0.01,
                "decode_encode_seconds": 0.01,
                "source_read_failures": 0,
                "originals_written": 0,
            }

        def persist_meta(iid, metadata):
            from features.catalog import metadata as catalog_metadata

            harvest.persist_metadata_sync(
                str(self.db),
                catalog_metadata.metadata_update_tuple(iid, metadata),
            )
            return True

        holds_during = []

        def wrapped_generate(fp, iid, sigs, **kwargs):
            holds_during.append(hdd_governor.bulk_hdd_holds())
            return fake_generate(fp, iid, sigs, **kwargs)

        result = harvest.harvest_original(
            str(self.image),
            1,
            bulk=True,
            size_signatures={"sm": "sig"},
            need_hash=True,
            need_metadata=True,
            generate_thumbnail_set=wrapped_generate,
            persist_hash=lambda iid, digest: harvest.persist_content_hash_sync(
                str(self.db), iid, digest
            ),
            persist_metadata=persist_meta,
        )
        self.assertEqual(loads["count"], 1)
        self.assertEqual(result.source_reads, 1)
        self.assertTrue(result.hash_written)
        self.assertTrue(result.metadata_written)
        self.assertEqual(holds_during, [1])
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT content_hash, metadata_scanned_at FROM images WHERE id = 1"
        ).fetchone()
        conn.close()
        self.assertTrue(row[0])
        self.assertIsNotNone(row[1])


class RcloneIoniceTests(unittest.TestCase):
    def test_build_argv_prefixes_ionice_when_available(self):
        from features.backup import cloud

        with mock.patch.object(cloud.shutil, "which", return_value="/usr/bin/ionice"):
            argv = cloud.build_rclone_copy_argv(
                rclone_bin="/usr/bin/rclone",
                source="/data",
                dest="remote:vault",
                exclude_file=None,
                bwlimit="",
            )
        self.assertEqual(argv[:3], ["/usr/bin/ionice", "-c3", "/usr/bin/rclone"])
        self.assertIn("copy", argv)


if __name__ == "__main__":
    unittest.main()
