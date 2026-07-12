"""Resolve photoArchive runtime storage without moving existing data.

Path discovery is intentionally read-only.  A legacy checkout keeps using its
historic in-repo data, while a clean install receives platform-native roots.
"""

from __future__ import annotations

from dataclasses import dataclass
import ntpath
import os
from pathlib import Path
import posixpath
import sys
from typing import Mapping


APP_DIR_NAME = "photoArchive"
XDG_DIR_NAME = "photoarchive"
LEGACY_DEVELOP_DIR = "/mnt/expansion/PhotoArchiveCache/develop"
LEGACY_BACKUP_DIR = "/mnt/expansion/PhotoArchiveCache/backups"


@dataclass(frozen=True)
class RuntimePaths:
    layout: str
    data_dir: str
    config_dir: str
    cache_dir: str
    state_dir: str
    catalog_db: str
    settings_file: str
    thumb_cache_dir: str
    model_root: str
    embed_cache_dir: str
    develop_cache_dir: str
    temporary_export_dir: str
    library_export_dir: str
    backup_dir: str
    run_dir: str
    log_dir: str
    server_log: str


def _platform_family(platform_name: str) -> str:
    value = str(platform_name or "").lower()
    if value.startswith("win"):
        return "windows"
    if value == "darwin":
        return "macos"
    return "linux"


def _joiner(family: str):
    return ntpath if family == "windows" else posixpath


def _clean_path(value: str | os.PathLike[str] | None, *, family: str) -> str:
    text = os.fspath(value or "").strip()
    if not text:
        return ""
    return _joiner(family).normpath(text)


def _first(environment: Mapping[str, str], key: str, fallback: str, *, family: str) -> str:
    return _clean_path(environment.get(key) or fallback, family=family)


def _legacy_install(web_dir: str) -> bool:
    root = Path(web_dir)
    files = (root / "photoarchive.db", root / "settings.local.json")
    directories = (
        root / ".thumbcache",
        root / ".models",
        root / ".embedcache",
        root / ".run",
    )
    return any(path.exists() for path in (*files, *directories))


def _native_roots(
    *,
    environment: Mapping[str, str],
    family: str,
    home: str,
) -> tuple[str, str, str, str, str]:
    paths = _joiner(family)
    if family == "windows":
        local = environment.get("LOCALAPPDATA") or paths.join(home, "AppData", "Local")
        roaming = environment.get("APPDATA") or paths.join(local, APP_DIR_NAME, "config")
        data = paths.join(local, APP_DIR_NAME)
        config = paths.join(roaming, APP_DIR_NAME) if environment.get("APPDATA") else roaming
        cache = paths.join(local, APP_DIR_NAME, "cache")
        state = paths.join(local, APP_DIR_NAME, "state")
        log_dir = paths.join(state, "logs")
        return data, config, cache, state, log_dir
    if family == "macos":
        data = paths.join(home, "Library", "Application Support", APP_DIR_NAME)
        config = paths.join(data, "config")
        cache = paths.join(home, "Library", "Caches", APP_DIR_NAME)
        state = paths.join(data, "state")
        log_dir = paths.join(home, "Library", "Logs", APP_DIR_NAME)
        return data, config, cache, state, log_dir

    data = environment.get("XDG_DATA_HOME") or paths.join(home, ".local", "share")
    config = environment.get("XDG_CONFIG_HOME") or paths.join(home, ".config")
    cache = environment.get("XDG_CACHE_HOME") or paths.join(home, ".cache")
    state = environment.get("XDG_STATE_HOME") or paths.join(home, ".local", "state")
    data = paths.join(data, XDG_DIR_NAME)
    config = paths.join(config, XDG_DIR_NAME)
    cache = paths.join(cache, XDG_DIR_NAME)
    state = paths.join(state, XDG_DIR_NAME)
    return data, config, cache, state, paths.join(state, "logs")


def resolve_runtime_paths(
    web_dir: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: str | os.PathLike[str] | None = None,
) -> RuntimePaths:
    """Return the selected runtime layout without changing the filesystem."""

    environment = os.environ if environ is None else environ
    family = _platform_family(platform_name or sys.platform)
    paths = _joiner(family)
    resolved_home = _clean_path(
        home or environment.get("USERPROFILE") or environment.get("HOME") or str(Path.home()),
        family=family,
    )
    root = _clean_path(
        web_dir or Path(__file__).resolve().parents[1],
        family=family,
    )
    native_data, native_config, native_cache, native_state, native_logs = _native_roots(
        environment=environment,
        family=family,
        home=resolved_home,
    )

    app_home = _clean_path(environment.get("PHOTOARCHIVE_HOME"), family=family)
    has_app_home = bool(app_home)
    legacy = _legacy_install(os.fspath(web_dir or Path(__file__).resolve().parents[1])) and not has_app_home
    layout = "custom" if has_app_home else "legacy" if legacy else "native"

    if has_app_home:
        default_data = paths.join(app_home, "data")
        default_config = paths.join(app_home, "config")
        default_cache = paths.join(app_home, "cache")
        default_state = paths.join(app_home, "state")
        default_logs = paths.join(default_state, "logs")
    elif legacy:
        default_data = default_config = default_cache = default_state = root
        default_logs = paths.join(root, ".run")
    else:
        default_data, default_config = native_data, native_config
        default_cache, default_state, default_logs = native_cache, native_state, native_logs

    data_dir = _first(environment, "PHOTOARCHIVE_DATA_DIR", default_data, family=family)
    config_dir = _first(environment, "PHOTOARCHIVE_CONFIG_DIR", default_config, family=family)
    cache_dir = _first(environment, "PHOTOARCHIVE_CACHE_DIR", default_cache, family=family)
    state_dir = _first(environment, "PHOTOARCHIVE_STATE_DIR", default_state, family=family)

    legacy_data = legacy and not environment.get("PHOTOARCHIVE_DATA_DIR")
    legacy_config = legacy and not environment.get("PHOTOARCHIVE_CONFIG_DIR")
    legacy_cache = legacy and not environment.get("PHOTOARCHIVE_CACHE_DIR")
    legacy_state = legacy and not environment.get("PHOTOARCHIVE_STATE_DIR")

    if legacy_data:
        catalog_default = paths.join(root, "photoarchive.db")
        models_default = paths.join(root, ".models")
    else:
        catalog_default = paths.join(data_dir, "catalog", "photoarchive.db")
        models_default = paths.join(data_dir, "models")
    if legacy_config:
        settings_default = paths.join(root, "settings.local.json")
    else:
        settings_default = paths.join(config_dir, "settings.json")
    if legacy_cache:
        thumbs_default = paths.join(root, ".thumbcache")
        embeds_default = paths.join(root, ".embedcache")
    else:
        thumbs_default = paths.join(cache_dir, "previews")
        embeds_default = paths.join(cache_dir, "embeddings")
    if legacy_state:
        run_default = paths.join(root, ".run")
    else:
        run_default = paths.join(state_dir, "run")

    develop_default = paths.join(cache_dir, "develop")
    backup_default = paths.join(data_dir, "backups")
    if legacy_cache and Path(LEGACY_DEVELOP_DIR).is_dir():
        develop_default = LEGACY_DEVELOP_DIR
    if legacy_data and Path(LEGACY_BACKUP_DIR).is_dir():
        backup_default = LEGACY_BACKUP_DIR
    elif legacy_data:
        old_fallback = Path(resolved_home) / ".cache" / "photoarchive" / "backups"
        if old_fallback.is_dir():
            backup_default = str(old_fallback)

    catalog_db = _first(environment, "PHOTOARCHIVE_DB_PATH", catalog_default, family=family)
    settings_file = _first(environment, "PHOTOARCHIVE_SETTINGS_PATH", settings_default, family=family)
    thumb_cache_dir = _first(environment, "PHOTOARCHIVE_THUMB_CACHE_DIR", thumbs_default, family=family)
    model_root = _first(environment, "PHOTOARCHIVE_MODELS_DIR", models_default, family=family)
    embed_cache_dir = _first(environment, "PHOTOARCHIVE_EMBED_CACHE_DIR", embeds_default, family=family)
    develop_cache_dir = _first(
        environment,
        "PHOTOARCHIVE_DEVELOP_CACHE_DIR",
        develop_default,
        family=family,
    )
    temporary_export_dir = _first(
        environment,
        "PHOTOARCHIVE_EXPORT_DIR",
        paths.join(develop_cache_dir, "exports"),
        family=family,
    )
    # Legacy Omarchy keeps library exports beside the develop cache; clean installs
    # use the user Pictures folder so Windows multi-drive setups stay portable.
    if legacy_cache:
        library_default = paths.join(develop_cache_dir, "library-exports")
    else:
        library_default = paths.join(resolved_home, "Pictures", "photoArchive Exports")
    library_export_dir = _first(
        environment,
        "PHOTOARCHIVE_LIBRARY_EXPORT_DIR",
        library_default,
        family=family,
    )
    backup_dir = _first(environment, "PHOTOARCHIVE_BACKUP_DIR", backup_default, family=family)
    run_dir = _first(environment, "PHOTOARCHIVE_RUN_DIR", run_default, family=family)
    log_dir = _first(environment, "PHOTOARCHIVE_LOG_DIR", default_logs, family=family)

    return RuntimePaths(
        layout=layout,
        data_dir=data_dir,
        config_dir=config_dir,
        cache_dir=cache_dir,
        state_dir=state_dir,
        catalog_db=catalog_db,
        settings_file=settings_file,
        thumb_cache_dir=thumb_cache_dir,
        model_root=model_root,
        embed_cache_dir=embed_cache_dir,
        develop_cache_dir=develop_cache_dir,
        temporary_export_dir=temporary_export_dir,
        library_export_dir=library_export_dir,
        backup_dir=backup_dir,
        run_dir=run_dir,
        log_dir=log_dir,
        server_log=paths.join(log_dir, "server.log"),
    )


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    """Create runtime directories only; existing data is never relocated."""

    directories = {
        paths.data_dir,
        paths.config_dir,
        paths.cache_dir,
        paths.state_dir,
        str(Path(paths.catalog_db).parent),
        str(Path(paths.settings_file).parent),
        paths.thumb_cache_dir,
        paths.model_root,
        paths.embed_cache_dir,
        paths.develop_cache_dir,
        paths.temporary_export_dir,
        paths.backup_dir,
        paths.run_dir,
        paths.log_dir,
    }
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)


def apply_environment_defaults(paths: RuntimePaths | None = None) -> RuntimePaths:
    """Expose early-import paths to legacy modules without overriding users."""

    selected = paths or resolve_runtime_paths()
    os.environ.setdefault("PHOTOARCHIVE_DEVELOP_CACHE_DIR", selected.develop_cache_dir)
    return selected
