from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.runtime_paths import ensure_runtime_dirs, resolve_runtime_paths
import settings


class RuntimePathTests(unittest.TestCase):
    def test_clean_linux_defaults_follow_xdg(self):
        paths = resolve_runtime_paths(
            "/checkout/web",
            {
                "HOME": "/home/alex",
                "XDG_DATA_HOME": "/data",
                "XDG_CONFIG_HOME": "/config",
                "XDG_CACHE_HOME": "/cache",
                "XDG_STATE_HOME": "/state",
            },
            "linux",
            "/home/alex",
        )
        self.assertEqual(paths.layout, "native")
        self.assertEqual(paths.catalog_db, "/data/azimuth-photo/catalog/azimuth.db")
        self.assertEqual(paths.settings_file, "/config/azimuth-photo/settings.json")
        self.assertEqual(paths.thumb_cache_dir, "/cache/azimuth-photo/previews")
        self.assertEqual(paths.model_root, "/data/azimuth-photo/models")
        self.assertEqual(paths.transfer_dir, "/state/azimuth-photo/transfer")
        self.assertEqual(paths.server_log, "/state/azimuth-photo/logs/server.log")

    def test_clean_windows_defaults(self):
        paths = resolve_runtime_paths(
            r"C:\checkout\web",
            {
                "USERPROFILE": r"C:\Users\Alex",
                "LOCALAPPDATA": r"C:\Users\Alex\AppData\Local",
                "APPDATA": r"C:\Users\Alex\AppData\Roaming",
            },
            "win32",
            r"C:\Users\Alex",
        )
        self.assertEqual(paths.layout, "native")
        self.assertEqual(
            paths.catalog_db,
            r"C:\Users\Alex\AppData\Local\Azimuth Photo\catalog\azimuth.db",
        )
        self.assertEqual(
            paths.settings_file,
            r"C:\Users\Alex\AppData\Roaming\Azimuth Photo\settings.json",
        )
        self.assertEqual(
            paths.thumb_cache_dir,
            r"C:\Users\Alex\AppData\Local\Azimuth Photo\cache\previews",
        )
        self.assertEqual(
            paths.transfer_dir,
            r"C:\Users\Alex\AppData\Local\Azimuth Photo\state\transfer",
        )

    def test_clean_macos_defaults(self):
        paths = resolve_runtime_paths(
            "/checkout/web",
            {"HOME": "/Users/alex"},
            "darwin",
            "/Users/alex",
        )
        self.assertEqual(
            paths.catalog_db,
            "/Users/alex/Library/Application Support/Azimuth Photo/catalog/azimuth.db",
        )
        self.assertEqual(
            paths.thumb_cache_dir,
            "/Users/alex/Library/Caches/Azimuth Photo/previews",
        )

    def test_azimuth_home_selects_one_portable_tree(self):
        paths = resolve_runtime_paths(
            "/checkout/web",
            {"HOME": "/home/alex", "AZIMUTH_HOME": "/srv/azimuth-photo"},
            "linux",
            "/home/alex",
        )
        self.assertEqual(paths.layout, "custom")
        self.assertEqual(paths.catalog_db, "/srv/azimuth-photo/data/catalog/azimuth.db")
        self.assertEqual(paths.settings_file, "/srv/azimuth-photo/config/settings.json")
        self.assertEqual(paths.thumb_cache_dir, "/srv/azimuth-photo/cache/previews")

    def test_granular_overrides(self):
        paths = resolve_runtime_paths(
            "/checkout/web",
            {
                "HOME": "/home/alex",
                "AZIMUTH_DB_PATH": "/catalogs/main.db",
                "AZIMUTH_THUMB_CACHE_DIR": "/fast/previews",
                "AZIMUTH_MODELS_DIR": "/fast/models",
                "AZIMUTH_DEVELOP_CACHE_DIR": "/large/develop",
                "AZIMUTH_BACKUP_DIR": "/large/backups",
                "AZIMUTH_TRANSFER_DIR": "/state/transfers",
                "AZIMUTH_RUN_DIR": "/run/azimuth-photo",
                "AZIMUTH_LOG_DIR": "/logs/azimuth-photo",
            },
            "linux",
            "/home/alex",
        )
        self.assertEqual(paths.catalog_db, "/catalogs/main.db")
        self.assertEqual(paths.thumb_cache_dir, "/fast/previews")
        self.assertEqual(paths.model_root, "/fast/models")
        self.assertEqual(paths.develop_cache_dir, "/large/develop")
        self.assertEqual(paths.backup_dir, "/large/backups")
        self.assertEqual(paths.transfer_dir, "/state/transfers")
        self.assertEqual(paths.run_dir, "/run/azimuth-photo")

    def test_checkout_contents_never_select_runtime_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "checkout" / "web"
            web.mkdir(parents=True)
            (web / "azimuth.db").touch()
            (web / ".thumbcache").mkdir()
            paths = resolve_runtime_paths(
                web,
                {"HOME": "/home/alex"},
                "linux",
                "/home/alex",
            )
        self.assertEqual(paths.layout, "native")
        self.assertEqual(
            paths.catalog_db,
            "/home/alex/.local/share/azimuth-photo/catalog/azimuth.db",
        )
        self.assertNotIn(str(web), paths.catalog_db)
        self.assertNotIn(str(web), paths.thumb_cache_dir)

    def test_ensure_runtime_dirs_creates_no_catalog_or_settings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_runtime_paths(
                "/checkout/web",
                {
                    "HOME": str(Path(tmp) / "user"),
                    "AZIMUTH_HOME": str(Path(tmp) / "app"),
                },
                "linux",
            )
            ensure_runtime_dirs(paths)
            self.assertFalse(Path(paths.catalog_db).exists())
            self.assertFalse(Path(paths.settings_file).exists())
            self.assertTrue(Path(paths.thumb_cache_dir).is_dir())
            self.assertTrue(Path(paths.model_root).is_dir())
            self.assertTrue(Path(paths.backup_dir).is_dir())
            self.assertTrue(Path(paths.transfer_dir).is_dir())

    def test_explicit_external_backup_is_allowed_for_real_runtime(self):
        paths = resolve_runtime_paths(
            "/checkout/web",
            {
                "HOME": "/home/alex",
                "AZIMUTH_HOME": "/fast/azimuth-photo",
                "AZIMUTH_BACKUP_DIR": "/large/azimuth-photo/backups",
            },
            "linux",
            "/home/alex",
        )
        self.assertEqual(paths.backup_dir, "/large/azimuth-photo/backups")

    def test_smoke_runtime_rejects_foreign_backup_override(self):
        paths = resolve_runtime_paths(
            "/checkout/web",
            {
                "HOME": "/home/alex",
                "AZIMUTH_HOME": "/tmp/azimuth-smoke",
                "AZIMUTH_SMOKE_MODE": "1",
                "AZIMUTH_BACKUP_DIR": "/large/azimuth-photo/backups",
            },
            "linux",
            "/home/alex",
        )
        self.assertEqual(paths.backup_dir, "/tmp/azimuth-smoke/data/backups")

    def test_settings_preserve_explicit_cache_and_model_paths(self):
        raw = {
            "settings_version": settings.SETTINGS_VERSION,
            "ssd_cache_dir": "/archive/previews",
            "embed_model_preset": "qwen3-vl-embedding-8b",
            "embed_model_id": "Qwen/Qwen3-VL-Embedding-8B",
            "embed_model_dir": "/archive/models/embed",
            "caption_model_preset": "qwen2.5-vl-3b-instruct-bnb-4bit",
            "caption_model_id": "Qwen/Qwen2.5-VL-3B-Instruct",
            "caption_model_dir": "/archive/models/caption",
            "face_model_dir": "/archive/models/faces",
        }
        with mock.patch.dict(
            os.environ,
            {"AZIMUTH_MODELS_DIR": "", "AZIMUTH_THUMB_CACHE_DIR": ""},
            clear=False,
        ):
            normalized = settings.normalize_settings(raw)
        self.assertEqual(normalized["ssd_cache_dir"], "/archive/previews")
        self.assertEqual(normalized["embed_model_dir"], "/archive/models/embed")
        self.assertEqual(normalized["caption_model_dir"], "/archive/models/caption")
        self.assertEqual(normalized["face_model_dir"], "/archive/models/faces")

    def test_deployment_environment_beats_persisted_cache_and_model_paths(self):
        raw = {
            "settings_version": settings.SETTINGS_VERSION,
            "ssd_cache_dir": "/old/previews",
            "embed_model_dir": "/old/embed",
            "caption_model_dir": "/old/caption",
        }
        with mock.patch.dict(
            os.environ,
            {
                "AZIMUTH_MODELS_DIR": "/deploy/models",
                "AZIMUTH_THUMB_CACHE_DIR": "/deploy/previews",
            },
            clear=False,
        ):
            normalized = settings.normalize_settings(raw)
        self.assertEqual(normalized["ssd_cache_dir"], "/deploy/previews")
        self.assertTrue(
            str(normalized["embed_model_dir"]).replace("\\", "/").startswith(
                "/deploy/models/"
            )
        )
        self.assertTrue(
            str(normalized["caption_model_dir"]).replace("\\", "/").startswith(
                "/deploy/models/"
            )
        )
        self.assertEqual(
            normalized["face_model_dir"],
            os.path.normpath("/deploy/models/insightface"),
        )


if __name__ == "__main__":
    unittest.main()
