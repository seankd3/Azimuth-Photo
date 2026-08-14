"""The attached-archive location rule: strip, verify, remember."""

import os
import tempfile
import unittest
from unittest import mock

from photo import location


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.volume = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.volume.name, "Photos", "Edits"), exist_ok=True)
        self.on_disk = os.path.join(self.volume.name, "Photos", "Edits", "a.jpg")
        with open(self.on_disk, "wb") as handle:
            handle.write(b"x" * 10)
        location._mappings.clear()
        self.addCleanup(location._mappings.clear)
        self.addCleanup(self.volume.cleanup)

    def _roots(self):
        return mock.patch.object(location, "_drive_roots", return_value=[self.volume.name])

    def test_archive_path_resolves_by_stripping_prefixes(self):
        with self._roots():
            resolved = location.local_path("/mnt/expansion/Photos/Edits/a.jpg")
        self.assertEqual(resolved, os.path.join(self.volume.name, "Photos", "Edits", "a.jpg"))
        self.assertEqual(location._mappings.get("/mnt/expansion"), self.volume.name)

    def test_a_remembered_mapping_answers_without_rediscovery(self):
        with self._roots():
            location.local_path("/mnt/expansion/Photos/Edits/a.jpg")
        # No drives visible any more, but the volume dir still exists — the
        # cached mapping must answer on its own.
        with mock.patch.object(location, "_drive_roots", return_value=[]):
            resolved = location.local_path("/mnt/expansion/Photos/Edits/a.jpg")
        self.assertIsNotNone(resolved)

    def test_the_recorded_size_gates_a_same_named_stranger(self):
        with self._roots():
            self.assertIsNone(
                location.local_path("/mnt/expansion/Photos/Edits/a.jpg", expected_size=999)
            )
            self.assertIsNotNone(
                location.local_path("/mnt/expansion/Photos/Edits/a.jpg", expected_size=10)
            )

    def test_one_absent_file_does_not_drop_the_volume_mapping(self):
        with self._roots():
            location.local_path("/mnt/expansion/Photos/Edits/a.jpg")
            self.assertIsNone(location.local_path("/mnt/expansion/Photos/Edits/missing.jpg"))
        self.assertEqual(location._mappings.get("/mnt/expansion"), self.volume.name)

    def test_a_native_existing_path_returns_itself(self):
        self.assertEqual(location.local_path(self.on_disk), self.on_disk)

    def test_unreachable_paths_return_none(self):
        with mock.patch.object(location, "_drive_roots", return_value=[]):
            self.assertIsNone(location.local_path("/mnt/expansion/Photos/Edits/a.jpg"))
        self.assertIsNone(location.local_path(""))


if __name__ == "__main__":
    unittest.main()
