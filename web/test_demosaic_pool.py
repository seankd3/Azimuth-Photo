"""Process-pool demosaic: GIL bypass + JPEG IPC contracts."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from thumbnails import demosaic_pool


class DemosaicPoolConfigTests(unittest.TestCase):
    def tearDown(self):
        demosaic_pool.reset_for_tests()
        os.environ.pop("PHOTOARCHIVE_DEMOSAIC_PROCESSES", None)
        os.environ.pop("PHOTOARCHIVE_DEMOSAIC_IPC", None)
        os.environ["PHOTOARCHIVE_DEMOSAIC_PROCESSES"] = "0"

    def test_zero_disables_pool(self):
        os.environ["PHOTOARCHIVE_DEMOSAIC_PROCESSES"] = "0"
        demosaic_pool.reset_for_tests()
        self.assertFalse(demosaic_pool.is_enabled())
        self.assertIsNone(demosaic_pool.ensure_pool())

    def test_default_bounded_for_16gb(self):
        os.environ.pop("PHOTOARCHIVE_DEMOSAIC_PROCESSES", None)
        demosaic_pool.reset_for_tests()
        n = demosaic_pool.default_demosaic_processes()
        self.assertGreaterEqual(n, 1)
        self.assertLessEqual(n, 6)
        self.assertLessEqual(n, max(1, (os.cpu_count() or 4) - 1))
        total_gib = (
            int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE")) / 1024**3
        )
        if total_gib < 24:
            self.assertEqual(n, 1)

    def test_ipc_mode_defaults_to_path(self):
        os.environ.pop("PHOTOARCHIVE_DEMOSAIC_IPC", None)
        self.assertEqual(demosaic_pool.demosaic_ipc_mode(), "path")
        os.environ["PHOTOARCHIVE_DEMOSAIC_IPC"] = "bytes"
        self.assertEqual(demosaic_pool.demosaic_ipc_mode(), "bytes")


class DemosaicPoolParityTests(unittest.TestCase):
    """Real-file parity when R5 DNGs are mounted (skipped otherwise)."""

    @classmethod
    def setUpClass(cls):
        root = Path("/mnt/expansion/Photos/RAWS/2022/2022-05-09")
        cls.sample = root / "20220509-R5__7241.DNG"
        cls.available = cls.sample.is_file()

    def tearDown(self):
        demosaic_pool.reset_for_tests()
        os.environ["PHOTOARCHIVE_DEMOSAIC_PROCESSES"] = "0"
        os.environ.pop("PHOTOARCHIVE_DEMOSAIC_IPC", None)

    def test_process_pool_jpeg_matches_inprocess(self):
        if not self.available:
            self.skipTest("R5 DNG corpus not mounted")
        from raw_thumb_ops import demosaic_tier_jpegs

        sizes = {"md": 1920, "lg": 3840}
        local = demosaic_tier_jpegs(
            str(self.sample),
            ["md", "lg"],
            sizes,
            92,
            source_data=None,
        )
        os.environ["PHOTOARCHIVE_DEMOSAIC_PROCESSES"] = "2"
        os.environ["PHOTOARCHIVE_DEMOSAIC_IPC"] = "path"
        demosaic_pool.reset_for_tests()
        pooled = demosaic_pool.run_demosaic_tier_jpegs(
            str(self.sample),
            ["md", "lg"],
            sizes=sizes,
            thumb_quality=92,
            source_data=None,
            interactive=True,
        )
        self.assertEqual(set(local["jpegs"]), set(pooled["jpegs"]))
        for size in ("md", "lg"):
            self.assertEqual(
                local["jpegs"][size],
                pooled["jpegs"][size],
                f"{size} JPEG bytes differ between in-process and process pool",
            )
        self.assertEqual(local["width"], pooled["width"])
        self.assertEqual(local["height"], pooled["height"])

    def test_broken_pool_recreates_and_surfaces_failure(self):
        from concurrent.futures.process import BrokenProcessPool

        os.environ["PHOTOARCHIVE_DEMOSAIC_PROCESSES"] = "2"
        demosaic_pool.reset_for_tests()
        self.assertIsNotNone(demosaic_pool.ensure_pool())

        with mock.patch.object(
            demosaic_pool,
            "_submit",
            side_effect=lambda *_a, **_k: mock.Mock(
                result=mock.Mock(side_effect=BrokenProcessPool("dead"))
            ),
        ):
            with self.assertRaises(BrokenProcessPool):
                demosaic_pool.run_demosaic_tier_jpegs(
                    "/no/such.dng",
                    ["md"],
                    sizes={"md": 1920},
                    thumb_quality=92,
                    interactive=False,
                )
        # Pool should have been recreated for subsequent work.
        self.assertIsNotNone(demosaic_pool.ensure_pool())


class DemosaicPoolBulkGateTests(unittest.TestCase):
    def tearDown(self):
        demosaic_pool.reset_for_tests()
        os.environ["PHOTOARCHIVE_DEMOSAIC_PROCESSES"] = "0"

    def test_interactive_bypasses_bulk_slot(self):
        os.environ["PHOTOARCHIVE_DEMOSAIC_PROCESSES"] = "2"
        demosaic_pool.reset_for_tests()
        demosaic_pool.ensure_pool()
        # Saturate the single bulk slot (workers=2 → bulk_limit=1).
        demosaic_pool._bulk_slots.acquire()
        held = {"interactive_done": False}

        def fake_submit(_pool, fn, *args):
            # Run in-process so we don't need a real DNG; proves gate bypass.
            result = {
                "jpegs": {"md": b"x"},
                "width": 1,
                "height": 1,
            }
            return mock.Mock(result=mock.Mock(return_value=result))

        with mock.patch.object(demosaic_pool, "_submit", side_effect=fake_submit):
            demosaic_pool.run_demosaic_tier_jpegs(
                "x.dng",
                ["md"],
                sizes={"md": 1920},
                thumb_quality=92,
                interactive=True,
            )
            held["interactive_done"] = True
        self.assertTrue(held["interactive_done"])
        demosaic_pool._bulk_slots.release()


if __name__ == "__main__":
    unittest.main()
