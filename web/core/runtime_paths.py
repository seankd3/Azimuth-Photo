"""Resolve Azimuth Photo runtime storage outside the source checkout."""

from __future__ import annotations

from dataclasses import dataclass
import ntpath
import os
from pathlib import Path
import posixpath
import sys
from typing import Mapping

from core import env_names


APP_DIR_NAME = "Azimuth Photo"
XDG_DIR_NAME = "azimuth-photo"
CATALOG_DB_NAME = "azimuth.db"


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
    transfer_dir: str
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
    return _joiner(family).normpath(text) if text else ""


def _env_path(
    environment: Mapping[str, str],
    suffix: str,
    fallback: str,
    *,
    family: str,
) -> str:
    value = env_names.env_get(suffix, fallback, environ=environment)
    return _clean_path(value or fallback, family=family)


def _native_roots(
    *,
    environment: Mapping[str, str],
    family: str,
    home: str,
) -> tuple[str, str, str, str, str]:
    paths = _joiner(family)
    if family == "windows":
        local = environment.get("LOCALAPPDATA") or paths.join(home, "AppData", "Local")
        roaming = environment.get("APPDATA") or paths.join(home, "AppData", "Roaming")
        data = paths.join(local, APP_DIR_NAME)
        config = paths.join(roaming, APP_DIR_NAME)
        cache = paths.join(local, APP_DIR_NAME, "cache")
        state = paths.join(local, APP_DIR_NAME, "state")
        return data, config, cache, state, paths.join(state, "logs")

    if family == "macos":
        data = paths.join(home, "Library", "Application Support", APP_DIR_NAME)
        config = paths.join(data, "config")
        cache = paths.join(home, "Library", "Caches", APP_DIR_NAME)
        state = paths.join(data, "state")
        return data, config, cache, state, paths.join(home, "Library", "Logs", APP_DIR_NAME)

    data_base = environment.get("XDG_DATA_HOME") or paths.join(home, ".local", "share")
    config_base = environment.get("XDG_CONFIG_HOME") or paths.join(home, ".config")
    cache_base = environment.get("XDG_CACHE_HOME") or paths.join(home, ".cache")
    state_base = environment.get("XDG_STATE_HOME") or paths.join(home, ".local", "state")
    data = paths.join(data_base, XDG_DIR_NAME)
    config = paths.join(config_base, XDG_DIR_NAME)
    cache = paths.join(cache_base, XDG_DIR_NAME)
    state = paths.join(state_base, XDG_DIR_NAME)
    return data, config, cache, state, paths.join(state, "logs")


def _backup_path(
    *,
    environment: Mapping[str, str],
    fallback: str,
    data_dir: str,
    family: str,
) -> str:
    override = _clean_path(
        env_names.env_get("BACKUP_DIR", environ=environment),
        family=family,
    )
    if not override:
        return fallback
    if not env_names.env_truthy("SMOKE_MODE", environ=environment):
        return override

    # Disposable/smoke instances may never publish into another catalog's
    # durable backup root, even when the parent process exports one.
    try:
        Path(override).resolve().relative_to(Path(data_dir).resolve())
    except (OSError, ValueError):
        return fallback
    return override


def resolve_runtime_paths(
    web_dir: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: str | os.PathLike[str] | None = None,
) -> RuntimePaths:
    """Return the canonical runtime layout without changing the filesystem.

    ``web_dir`` remains accepted for caller compatibility, but it is never used
    as a storage fallback. A checkout is code, not a data directory.
    """

    del web_dir
    environment = os.environ if environ is None else environ
    family = _platform_family(platform_name or sys.platform)
    paths = _joiner(family)
    resolved_home = _clean_path(
        home
        or environment.get("USERPROFILE")
        or environment.get("HOME")
        or str(Path.home()),
        family=family,
    )
    native_data, native_config, native_cache, native_state, native_logs = _native_roots(
        environment=environment,
        family=family,
        home=resolved_home,
    )

    app_home = _clean_path(
        env_names.env_get("HOME", environ=environment),
        family=family,
    )
    if app_home:
        layout = "custom"
        default_data = paths.join(app_home, "data")
        default_config = paths.join(app_home, "config")
        default_cache = paths.join(app_home, "cache")
        default_state = paths.join(app_home, "state")
        default_logs = paths.join(default_state, "logs")
    else:
        layout = "native"
        default_data = native_data
        default_config = native_config
        default_cache = native_cache
        default_state = native_state
        default_logs = native_logs

    data_dir = _env_path(environment, "DATA_DIR", default_data, family=family)
    config_dir = _env_path(environment, "CONFIG_DIR", default_config, family=family)
    cache_dir = _env_path(environment, "CACHE_DIR", default_cache, family=family)
    state_dir = _env_path(environment, "STATE_DIR", default_state, family=family)

    catalog_db = _env_path(
        environment,
        "DB_PATH",
        paths.join(data_dir, "catalog", CATALOG_DB_NAME),
        family=family,
    )
    settings_file = _env_path(
        environment,
        "SETTINGS_PATH",
        paths.join(config_dir, "settings.json"),
        family=family,
    )
    thumb_cache_dir = _env_path(
        environment,
        "THUMB_CACHE_DIR",
        paths.join(cache_dir, "previews"),
        family=family,
    )
    model_root = _env_path(
        environment,
        "MODELS_DIR",
        paths.join(data_dir, "models"),
        family=family,
    )
    embed_cache_dir = _env_path(
        environment,
        "EMBED_CACHE_DIR",
        paths.join(cache_dir, "embeddings"),
        family=family,
    )
    develop_cache_dir = _env_path(
        environment,
        "DEVELOP_CACHE_DIR",
        paths.join(cache_dir, "develop"),
        family=family,
    )
    temporary_export_dir = _env_path(
        environment,
        "EXPORT_DIR",
        paths.join(develop_cache_dir, "exports"),
        family=family,
    )
    library_export_dir = _env_path(
        environment,
        "LIBRARY_EXPORT_DIR",
        paths.join(resolved_home, "Pictures", "Azimuth Exports"),
        family=family,
    )
    backup_default = paths.join(data_dir, "backups")
    backup_dir = _backup_path(
        environment=environment,
        fallback=backup_default,
        data_dir=data_dir,
        family=family,
    )
    transfer_dir = _env_path(
        environment,
        "TRANSFER_DIR",
        paths.join(state_dir, "transfer"),
        family=family,
    )
    run_dir = _env_path(
        environment,
        "RUN_DIR",
        paths.join(state_dir, "run"),
        family=family,
    )
    log_dir = _env_path(environment, "LOG_DIR", default_logs, family=family)

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
        transfer_dir=transfer_dir,
        run_dir=run_dir,
        log_dir=log_dir,
        server_log=paths.join(log_dir, "server.log"),
    )


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    """Create runtime directories only; original media is never relocated."""

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
        paths.transfer_dir,
        paths.run_dir,
        paths.log_dir,
    }
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)


def apply_environment_defaults(paths: RuntimePaths | None = None) -> RuntimePaths:
    """Expose early-import paths and create the selected runtime directories."""

    selected = paths or resolve_runtime_paths()
    env_names.setdefault("DEVELOP_CACHE_DIR", selected.develop_cache_dir)
    ensure_runtime_dirs(selected)
    return selected
