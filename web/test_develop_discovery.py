from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from features.develop import discovery, lrcat_import, presets


class DevelopDiscoveryTests(unittest.TestCase):
    def test_raw_import_defaults_to_user_pictures_or_explicit_root(self):
        self.assertEqual(
            discovery.default_raw_import_root(environ={"HOME": "/home/alex"}, platform_name="linux"),
            "/home/alex/Pictures",
        )
        self.assertEqual(
            discovery.default_raw_import_root(
                environ={"HOME": "/home/alex", "PHOTOARCHIVE_RAW_IMPORT_ROOT": "/photos/raw"},
                platform_name="linux",
            ),
            "/photos/raw",
        )

    def test_windows_lightroom_roots_use_real_user_locations(self):
        environment = {
            "USERPROFILE": r"C:\Users\Alex",
            "APPDATA": r"C:\Users\Alex\AppData\Roaming",
        }
        catalogs = discovery.lightroom_catalog_roots(
            environ=environment,
            platform_name="win32",
        )
        presets_roots = discovery.lightroom_preset_roots(
            environ=environment,
            platform_name="win32",
        )
        self.assertIn(r"C:\Users\Alex\Pictures\Lightroom", catalogs)
        self.assertIn(r"C:\Users\Alex\AppData\Roaming\Adobe\Lightroom", catalogs)
        self.assertIn(r"C:\Users\Alex\AppData\Roaming\Adobe\CameraRaw\Settings", presets_roots)

    def test_macos_and_linux_lightroom_roots_are_user_scoped(self):
        mac = discovery.lightroom_preset_roots(
            environ={"HOME": "/Users/alex"},
            platform_name="darwin",
        )
        linux = discovery.lightroom_preset_roots(
            environ={"HOME": "/home/alex"},
            platform_name="linux",
        )
        self.assertIn(
            "/Users/alex/Library/Application Support/Adobe/CameraRaw/Settings",
            mac,
        )
        self.assertIn("/home/alex/Pictures/Lightroom/Presets", linux)
        self.assertFalse(any("/mnt/expansion" in path for path in (*mac, *linux)))

    def test_explicit_lightroom_roots_replace_discovery(self):
        self.assertEqual(
            discovery.lightroom_catalog_roots(
                environ={"PHOTOARCHIVE_LIGHTROOM_CATALOG_DIRS": "/one:/two:/one"},
                platform_name="linux",
            ),
            ("/one", "/two"),
        )
        self.assertEqual(
            discovery.lightroom_preset_roots(
                environ={"PHOTOARCHIVE_LIGHTROOM_PRESET_DIRS": r"D:\Presets;E:\More"},
                platform_name="win32",
                home=r"C:\Users\Alex",
            ),
            (r"D:\Presets", r"E:\More"),
        )

    def test_catalog_discovery_keeps_highest_version_per_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            year = root / "2026"
            other = root / "Wedding"
            year.mkdir()
            other.mkdir()
            (year / "Photos-v12.lrcat").touch()
            (year / "Photos-v13.lrcat").touch()
            (other / "Wedding-v2.lrcat").touch()
            found = lrcat_import.catalog_paths(str(root))
        self.assertEqual(
            {Path(path).name for path in found},
            {"Photos-v13.lrcat", "Wedding-v2.lrcat"},
        )

    def test_preset_discovery_and_import_use_only_explicit_test_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Film").mkdir()
            preset = root / "Film" / "Clean.xmp"
            preset.write_text(
                '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
                'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                '<rdf:Description xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" '
                'crs:Exposure2012="0.25"/></rdf:RDF></x:xmpmeta>',
                encoding="utf-8",
            )
            self.assertEqual(presets.discover_lr_preset_paths((root,)), [preset])
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            try:
                report = presets.import_lightroom_presets_sync(conn, roots=(root,))
            finally:
                conn.close()
        self.assertEqual(report["roots_found"], [str(root)])
        self.assertEqual(report["xmp_found"], 1)

    def test_direct_cache_modules_respect_selected_develop_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = os.environ.copy()
            environment.pop("PHOTOARCHIVE_DEVELOP_CACHE_DIR", None)
            environment["AZIMUTH_DEVELOP_CACHE_DIR"] = str(Path(tmp) / "inherited-default")
            environment["PHOTOARCHIVE_HOME"] = str(Path(tmp) / "app")
            script = (
                "from features.develop import ai_masks, hdr, pano, rawproc; "
                "print(ai_masks.DEVELOP_CACHE_ROOT); print(hdr.HDR_CACHE_DIR); "
                "print(pano.PANO_CACHE_DIR); print(rawproc.BASE_CACHE_ROOT)"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parent,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            root = Path(tmp) / "app" / "cache" / "develop"
        self.assertEqual(
            result.stdout.splitlines(),
            [str(root), str(root / "hdr"), str(root / "pano"), str(root)],
        )

    def test_direct_cache_modules_preserve_explicit_develop_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = os.environ.copy()
            environment.pop("AZIMUTH_DEVELOP_CACHE_DIR", None)
            root = Path(tmp) / "configured-develop"
            environment["PHOTOARCHIVE_DEVELOP_CACHE_DIR"] = str(root)
            script = (
                "from features.develop import ai_masks, hdr, pano, rawproc; "
                "print(ai_masks.DEVELOP_CACHE_ROOT); print(hdr.HDR_CACHE_DIR); "
                "print(pano.PANO_CACHE_DIR); print(rawproc.BASE_CACHE_ROOT)"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parent,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
        self.assertEqual(
            result.stdout.splitlines(),
            [str(root), str(root / "hdr"), str(root / "pano"), str(root)],
        )


if __name__ == "__main__":
    unittest.main()
