"""The Develop decode cache stays inside its budget, oldest out first."""

import os
import tempfile
import time
import unittest
from pathlib import Path

from features.develop import base_cache_budget


class BaseCacheBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.base = self.root / "base" / "v5"
        self.base.mkdir(parents=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def _decode(self, name: str, *, kilobytes: int, age_days: float) -> Path:
        path = self.base / f"{name}.bin.gz"
        path.write_bytes(b"\0" * (kilobytes * 1024))
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def test_nothing_is_removed_while_the_cache_fits(self):
        self._decode("a", kilobytes=64, age_days=10)
        result = base_cache_budget.evict_to_budget(self.root / "base", 1024 ** 3)
        self.assertEqual(result.removed_files, 0)
        self.assertTrue((self.base / "a.bin.gz").exists())

    def test_the_oldest_decodes_go_first(self):
        self._decode("oldest", kilobytes=100, age_days=90)
        self._decode("middle", kilobytes=100, age_days=30)
        self._decode("newest", kilobytes=100, age_days=1)

        # Room for roughly two of the three.
        result = base_cache_budget.evict_to_budget(self.root / "base", 220 * 1024)
        self.assertEqual(result.removed_files, 1)
        self.assertFalse((self.base / "oldest.bin.gz").exists(), "age decides, nothing else")
        self.assertTrue((self.base / "middle.bin.gz").exists())
        self.assertTrue((self.base / "newest.bin.gz").exists())

    def test_a_dry_run_reports_without_deleting(self):
        self._decode("a", kilobytes=100, age_days=40)
        self._decode("b", kilobytes=100, age_days=5)
        result = base_cache_budget.evict_to_budget(self.root / "base", 120 * 1024, apply=False)
        self.assertEqual(result.removed_files, 1)
        self.assertTrue((self.base / "a.bin.gz").exists(), "nothing is deleted on a dry run")

    def test_models_and_exports_are_never_touched(self):
        models = self.root / "models"
        models.mkdir()
        keeper = models / "u2net.onnx"
        keeper.write_bytes(b"\0" * (500 * 1024))
        old_stamp = time.time() - 900 * 86400
        os.utime(keeper, (old_stamp, old_stamp))
        self._decode("a", kilobytes=100, age_days=1)

        base_cache_budget.evict_to_budget(self.root / "base", 1024)
        self.assertTrue(keeper.exists(), "a model is not a decode result and is not cheap to recreate")

    def test_an_absent_cache_is_not_an_error(self):
        result = base_cache_budget.evict_to_budget(self.root / "nothing-here", 1024)
        self.assertEqual(result.removed_files, 0)

    def test_the_auto_budget_respects_an_explicit_setting(self):
        self.assertEqual(
            base_cache_budget.auto_budget_bytes(self.root, configured_gb=12),
            12 * 1024 ** 3,
        )
        auto = base_cache_budget.auto_budget_bytes(self.root, configured_gb=0)
        self.assertGreaterEqual(auto, 8 * 1024 ** 3, "a floor keeps a small disk usable")


if __name__ == "__main__":
    unittest.main()
