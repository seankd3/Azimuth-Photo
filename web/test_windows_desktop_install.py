"""Contracts for the self-contained Windows install-to-library path."""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

from features.catalog.folder_roots import quick_browse_roots


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
TAURI = DESKTOP / "src-tauri"


class WindowsFolderRootsTests(unittest.TestCase):
    def test_windows_roots_include_local_and_mapped_drives(self):
        existing = {
            r"C:\Users\Sean",
            r"C:\Users\Sean\Pictures",
            "C:\\",
            "Z:\\",
        }
        roots = quick_browse_roots(
            platform_name="win32",
            home=r"C:\Users\Sean",
            environ={},
            is_directory=existing.__contains__,
        )

        self.assertEqual(
            roots,
            [
                {"label": "Home", "path": r"C:\Users\Sean"},
                {"label": "Pictures", "path": r"C:\Users\Sean\Pictures"},
                {"label": "C:", "path": "C:\\"},
                {"label": "Z:", "path": "Z:\\"},
            ],
        )

    def test_windows_roots_surface_existing_onedrive(self):
        roots = quick_browse_roots(
            platform_name="win32",
            home=r"C:\Users\Sean",
            environ={"OneDrive": r"C:\Users\Sean\OneDrive"},
            is_directory=lambda path: path == r"C:\Users\Sean\OneDrive",
        )
        self.assertEqual(roots, [{"label": "OneDrive", "path": r"C:\Users\Sean\OneDrive"}])


class WindowsDesktopPackageContracts(unittest.TestCase):
    def test_desktop_versions_match_release_version(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        config = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
        cargo = tomllib.loads((TAURI / "Cargo.toml").read_text(encoding="utf-8"))

        self.assertEqual(config["version"], version)
        self.assertEqual(cargo["package"]["version"], version)

    def test_windows_bundle_contains_the_complete_frozen_engine(self):
        config = json.loads(
            (TAURI / "tauri.windows.conf.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            config["bundle"]["resources"],
            {"../../dist/photoarchive-server/": "photoarchive-server/"},
        )

    def test_installed_launch_has_no_personal_or_remote_fallback(self):
        engine = (TAURI / "src" / "engine.rs").read_text(encoding="utf-8")
        server = (TAURI / "src" / "server.rs").read_text(encoding="utf-8")
        combined = engine + server

        self.assertNotIn(r"C:\Users\smast", combined)
        self.assertNotIn("100.102.150.104", combined)
        self.assertNotIn("PHOTOARCHIVE_HUB_URL", combined)
        self.assertNotIn("python.exe", combined.lower())
        self.assertIn('.env("PHOTOARCHIVE_MODE", "standalone")', engine)
        self.assertIn("BaseDirectory::Resource", engine)

    def test_unsigned_build_script_builds_engine_before_nsis(self):
        script = (ROOT / "scripts" / "build_windows_desktop.ps1").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            script.index('"scripts/build_server.py"'),
            script.index("cargo tauri build --bundles nsis"),
        )
        self.assertIn("photoarchive-server.exe", script)
        self.assertIn("*-setup.exe", script)

    def test_splash_is_customer_facing(self):
        splash = (DESKTOP / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Azimuth <span>Photo</span>", splash)
        self.assertNotIn("photo<span>Archive", splash)
        self.assertNotIn("server", splash.lower())


if __name__ == "__main__":
    unittest.main()
