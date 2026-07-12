"""Windows multi-drive path grouping + develop export path resolution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import path_groups
from core.runtime_paths import resolve_runtime_paths
from features.catalog import routes as catalog_routes
from features.develop import render as develop_render


class PathGroupTests(unittest.TestCase):
    def test_group_windows_paths_by_drive(self):
        grouped = path_groups.group_paths_by_drive(
            [r"C:\Photos\a", r"D:\Archive\b", r"C:\Photos\c", r"D:\Archive\d"],
            family="windows",
        )
        self.assertEqual(list(grouped), ["C:", "D:"])
        self.assertEqual(grouped["C:"], [r"C:\Photos\a", r"C:\Photos\c"])
        self.assertEqual(grouped["D:"], [r"D:\Archive\b", r"D:\Archive\d"])

    def test_safe_commonpath_returns_none_across_drives(self):
        self.assertIsNone(
            path_groups.safe_commonpath(
                [r"C:\Photos\2024", r"D:\Photos\2024"],
                family="windows",
            )
        )
        self.assertEqual(
            path_groups.safe_commonpath(
                [r"C:\Photos\2024\a", r"C:\Photos\2024\b"],
                family="windows",
            ),
            r"C:\Photos\2024",
        )

    def test_commonpath_per_drive(self):
        roots = path_groups.commonpath_per_drive(
            [r"C:\Photos\a\1", r"C:\Photos\b\2", r"E:\Raw\x"],
            family="windows",
        )
        self.assertEqual(roots["C:"], r"C:\Photos")
        self.assertEqual(roots["E:"], r"E:\Raw\x")

    def test_safe_relpath_cross_drive(self):
        self.assertIsNone(path_groups.safe_relpath(r"D:\a\b", r"C:\a", family="windows"))
        self.assertEqual(
            path_groups.safe_relpath(r"C:\Photos\2024\img.jpg", r"C:\Photos", family="windows"),
            r"2024\img.jpg",
        )

    def test_source_level_folders_survive_cross_drive_windows_paths(self):
        with mock.patch.object(catalog_routes, "safe_commonpath", return_value=None):
            payload = catalog_routes.build_source_level_folders_payload(
                [
                    (1, r"C:\Photos", 10),
                    (2, r"D:\Archive", 5),
                ]
            )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["root"], "")
        self.assertEqual({folder["path"] for folder in payload["folders"]}, {r"C:\Photos", r"D:\Archive"})


class DevelopExportPathTests(unittest.TestCase):
    def test_omarchy_legacy_defaults_match_shipped_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            web.mkdir()
            (web / "photoarchive.db").touch()
            for name in (".thumbcache", ".models", ".embedcache", ".run"):
                (web / name).mkdir()
            paths = resolve_runtime_paths(
                web,
                {
                    "HOME": "/home/sean",
                    "PHOTOARCHIVE_DEVELOP_CACHE_DIR": "/mnt/expansion/PhotoArchiveCache/develop",
                },
                "linux",
                "/home/sean",
            )
        self.assertEqual(paths.temporary_export_dir, "/mnt/expansion/PhotoArchiveCache/develop/exports")
        self.assertEqual(
            paths.library_export_dir,
            "/mnt/expansion/PhotoArchiveCache/develop/library-exports",
        )

    def test_export_dirs_honor_env_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            web.mkdir()
            paths = resolve_runtime_paths(
                web,
                {
                    "HOME": "/home/sean",
                    "PHOTOARCHIVE_EXPORT_DIR": "/tmp/pa-exports",
                    "PHOTOARCHIVE_LIBRARY_EXPORT_DIR": "/tmp/pa-library",
                },
                "linux",
                "/home/sean",
            )
        self.assertEqual(paths.temporary_export_dir, "/tmp/pa-exports")
        self.assertEqual(paths.library_export_dir, "/tmp/pa-library")

    def test_render_module_reads_runtime_paths(self):
        export, library = develop_render._resolved_export_dirs()
        paths = resolve_runtime_paths()
        self.assertEqual(export, Path(paths.temporary_export_dir))
        self.assertEqual(library, Path(paths.library_export_dir))
        self.assertEqual(develop_render.EXPORT_DIRECTORY, export)
        self.assertEqual(develop_render.LIBRARY_EXPORT_DIRECTORY, library)


if __name__ == "__main__":
    unittest.main()
