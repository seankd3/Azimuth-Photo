from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.runtime_paths import RuntimePaths, ensure_runtime_dirs, resolve_runtime_paths
import settings


class RuntimePathTests(unittest.TestCase):
    def test_clean_linux_defaults_follow_xdg(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            web.mkdir()
            paths = resolve_runtime_paths(
                web,
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
        self.assertEqual(paths.catalog_db, "/data/photoarchive/catalog/photoarchive.db")
        self.assertEqual(paths.settings_file, "/config/photoarchive/settings.json")
        self.assertEqual(paths.thumb_cache_dir, "/cache/photoarchive/previews")
        self.assertEqual(paths.model_root, "/data/photoarchive/models")
        self.assertEqual(paths.server_log, "/state/photoarchive/logs/server.log")

    def test_clean_windows_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            web.mkdir()
            paths = resolve_runtime_paths(
                web,
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
            r"C:\Users\Alex\AppData\Local\photoArchive\catalog\photoarchive.db",
        )
        self.assertEqual(
            paths.settings_file,
            r"C:\Users\Alex\AppData\Roaming\photoArchive\settings.json",
        )
        self.assertEqual(
            paths.thumb_cache_dir,
            r"C:\Users\Alex\AppData\Local\photoArchive\cache\previews",
        )

    def test_clean_macos_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            web.mkdir()
            paths = resolve_runtime_paths(web, {"HOME": "/Users/alex"}, "darwin", "/Users/alex")
        self.assertEqual(
            paths.catalog_db,
            "/Users/alex/Library/Application Support/photoArchive/catalog/photoarchive.db",
        )
        self.assertEqual(paths.thumb_cache_dir, "/Users/alex/Library/Caches/photoArchive/previews")
        self.assertEqual(paths.server_log, "/Users/alex/Library/Logs/photoArchive/server.log")

    def test_photoarchive_home_opts_out_of_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            web.mkdir()
            (web / "photoarchive.db").touch()
            paths = resolve_runtime_paths(
                web,
                {"HOME": "/home/alex", "PHOTOARCHIVE_HOME": "/srv/photoarchive"},
                "linux",
                "/home/alex",
            )
        self.assertEqual(paths.layout, "custom")
        self.assertEqual(paths.catalog_db, "/srv/photoarchive/data/catalog/photoarchive.db")
        self.assertEqual(paths.settings_file, "/srv/photoarchive/config/settings.json")
        self.assertEqual(paths.thumb_cache_dir, "/srv/photoarchive/cache/previews")

    def test_granular_overrides_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            web.mkdir()
            (web / "photoarchive.db").touch()
            paths = resolve_runtime_paths(
                web,
                {
                    "HOME": "/home/alex",
                    "PHOTOARCHIVE_DB_PATH": "/catalogs/main.db",
                    "PHOTOARCHIVE_THUMB_CACHE_DIR": "/fast/previews",
                    "PHOTOARCHIVE_MODELS_DIR": "/models",
                    "PHOTOARCHIVE_DEVELOP_CACHE_DIR": "/develop",
                    "PHOTOARCHIVE_BACKUP_DIR": "/backups",
                    "PHOTOARCHIVE_RUN_DIR": "/run/photoarchive",
                    "PHOTOARCHIVE_LOG_DIR": "/logs/photoarchive",
                },
                "linux",
                "/home/alex",
            )
        self.assertEqual(paths.catalog_db, "/catalogs/main.db")
        self.assertEqual(paths.thumb_cache_dir, "/fast/previews")
        self.assertEqual(paths.model_root, "/models")
        self.assertEqual(paths.develop_cache_dir, "/develop")
        self.assertEqual(paths.backup_dir, "/backups")
        self.assertEqual(paths.run_dir, "/run/photoarchive")
        self.assertEqual(paths.server_log, "/logs/photoarchive/server.log")

    def test_root_overrides_beat_legacy_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            web.mkdir()
            (web / "photoarchive.db").touch()
            paths = resolve_runtime_paths(
                web,
                {
                    "HOME": "/home/alex",
                    "PHOTOARCHIVE_DATA_DIR": "/data",
                    "PHOTOARCHIVE_CONFIG_DIR": "/config",
                    "PHOTOARCHIVE_CACHE_DIR": "/cache",
                    "PHOTOARCHIVE_STATE_DIR": "/state",
                },
                "linux",
                "/home/alex",
            )
        self.assertEqual(paths.layout, "legacy")
        self.assertEqual(paths.catalog_db, "/data/catalog/photoarchive.db")
        self.assertEqual(paths.settings_file, "/config/settings.json")
        self.assertEqual(paths.thumb_cache_dir, "/cache/previews")
        self.assertEqual(paths.model_root, "/data/models")
        self.assertEqual(paths.embed_cache_dir, "/cache/embeddings")
        self.assertEqual(paths.develop_cache_dir, "/cache/develop")
        self.assertEqual(paths.backup_dir, "/data/backups")
        self.assertEqual(paths.run_dir, "/state/run")

    def test_legacy_paths_remain_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            web.mkdir()
            for name in ("photoarchive.db", "settings.local.json"):
                (web / name).touch()
            for name in (".thumbcache", ".models", ".embedcache", ".run"):
                (web / name).mkdir()
            paths = resolve_runtime_paths(web, {"HOME": str(Path(tmp) / "home")}, "linux")
            self.assertEqual(paths.layout, "legacy")
            self.assertEqual(paths.catalog_db, str(web / "photoarchive.db"))
            self.assertEqual(paths.settings_file, str(web / "settings.local.json"))
            self.assertEqual(paths.thumb_cache_dir, str(web / ".thumbcache"))
            self.assertEqual(paths.model_root, str(web / ".models"))
            self.assertEqual(paths.embed_cache_dir, str(web / ".embedcache"))
            self.assertEqual(paths.run_dir, str(web / ".run"))

    def test_backup_file_alone_does_not_trigger_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            web.mkdir()
            (web / "photoarchive.db.pre-dev7.bak").touch()
            paths = resolve_runtime_paths(web, {"HOME": "/home/alex"}, "linux", "/home/alex")
        self.assertEqual(paths.layout, "native")
        self.assertEqual(paths.catalog_db, "/home/alex/.local/share/photoarchive/catalog/photoarchive.db")

    def test_resolution_has_no_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "web"
            web.mkdir()
            before = sorted(path.relative_to(tmp) for path in Path(tmp).rglob("*"))
            resolve_runtime_paths(web, {"HOME": str(Path(tmp) / "home")}, "linux")
            after = sorted(path.relative_to(tmp) for path in Path(tmp).rglob("*"))
        self.assertEqual(after, before)

    def test_ensure_runtime_dirs_only_makes_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_runtime_paths(
                Path(tmp) / "empty-web",
                {"HOME": str(Path(tmp) / "home"), "PHOTOARCHIVE_HOME": str(Path(tmp) / "app")},
                "linux",
            )
            with mock.patch("shutil.copy", side_effect=AssertionError("must not copy")), mock.patch(
                "os.replace", side_effect=AssertionError("must not move")
            ):
                ensure_runtime_dirs(paths)
            self.assertFalse(Path(paths.catalog_db).exists())
            self.assertFalse(Path(paths.settings_file).exists())
            self.assertTrue(Path(paths.thumb_cache_dir).is_dir())
            self.assertTrue(Path(paths.model_root).is_dir())
            self.assertTrue(Path(paths.backup_dir).is_dir())

    def test_current_checkout_selects_legacy_without_mutation(self):
        web = Path(__file__).resolve().parent
        if not (web / "photoarchive.db").exists():
            self.skipTest("current checkout has no legacy catalog")
        catalog = web / "photoarchive.db"
        before = (catalog.stat().st_ino, catalog.stat().st_size)
        paths = resolve_runtime_paths(web)
        after = (catalog.stat().st_ino, catalog.stat().st_size)
        self.assertEqual(paths.layout, "legacy")
        self.assertEqual(paths.catalog_db, str(catalog))
        self.assertEqual(paths.thumb_cache_dir, str(web / ".thumbcache"))
        self.assertEqual(paths.model_root, str(web / ".models"))
        self.assertEqual(after, before)

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
            {"PHOTOARCHIVE_MODELS_DIR": "", "PHOTOARCHIVE_THUMB_CACHE_DIR": ""},
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
                "PHOTOARCHIVE_MODELS_DIR": "/deploy/models",
                "PHOTOARCHIVE_THUMB_CACHE_DIR": "/deploy/previews",
            },
            clear=False,
        ):
            normalized = settings.normalize_settings(raw)
        self.assertEqual(normalized["ssd_cache_dir"], "/deploy/previews")
        self.assertEqual(normalized["embed_model_dir"], "/deploy/models/Qwen--Qwen3-VL-Embedding-8B")
        self.assertEqual(
            normalized["caption_model_dir"],
            "/deploy/models/Qwen--Qwen2.5-VL-7B-Instruct",
        )
        self.assertEqual(normalized["face_model_dir"], "/deploy/models/insightface")


if __name__ == "__main__":
    unittest.main()
