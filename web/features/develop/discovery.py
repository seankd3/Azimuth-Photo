"""Platform-aware discovery for user-owned photo and Lightroom folders."""

from __future__ import annotations

import ntpath
import os
import posixpath
import sys
from pathlib import Path
from typing import Mapping


def _family(platform_name: str) -> str:
    value = str(platform_name or "").lower()
    if value.startswith("win"):
        return "windows"
    if value == "darwin":
        return "macos"
    return "linux"


def _path_module(family: str):
    return ntpath if family == "windows" else posixpath


def _home(environment: Mapping[str, str], home: str | None) -> str:
    return str(home or environment.get("USERPROFILE") or environment.get("HOME") or Path.home())


def _dedupe(paths: list[str], *, family: str) -> tuple[str, ...]:
    path_module = _path_module(family)
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        raw = str(path or "").strip()
        if not raw:
            continue
        normalized = path_module.normpath(raw)
        key = path_module.normcase(normalized)
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return tuple(result)


def _environment_roots(
    environment: Mapping[str, str],
    key: str,
    *,
    family: str,
) -> tuple[str, ...] | None:
    raw = str(environment.get(key) or "").strip()
    if not raw:
        return None
    separator = ";" if family == "windows" else ":"
    return _dedupe(raw.split(separator), family=family)


def user_pictures_dir(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: str | None = None,
) -> str:
    environment = os.environ if environ is None else environ
    family = _family(platform_name or sys.platform)
    return _path_module(family).join(_home(environment, home), "Pictures")


def default_raw_import_root(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: str | None = None,
) -> str:
    environment = os.environ if environ is None else environ
    family = _family(platform_name or sys.platform)
    explicit = str(environment.get("PHOTOARCHIVE_RAW_IMPORT_ROOT") or "").strip()
    if explicit:
        return _path_module(family).normpath(explicit)
    return user_pictures_dir(environ=environment, platform_name=platform_name, home=home)


def lightroom_catalog_roots(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: str | None = None,
) -> tuple[str, ...]:
    environment = os.environ if environ is None else environ
    family = _family(platform_name or sys.platform)
    explicit = _environment_roots(
        environment,
        "PHOTOARCHIVE_LIGHTROOM_CATALOG_DIRS",
        family=family,
    )
    if explicit is not None:
        return explicit
    path_module = _path_module(family)
    pictures = user_pictures_dir(environ=environment, platform_name=platform_name, home=home)
    roots = [path_module.join(pictures, "Lightroom")]
    if family == "macos":
        roots.append(path_module.join(_home(environment, home), "Library", "Application Support", "Adobe", "Lightroom"))
    elif family == "windows" and environment.get("APPDATA"):
        roots.append(path_module.join(environment["APPDATA"], "Adobe", "Lightroom"))
    return _dedupe(roots, family=family)


def lightroom_preset_roots(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: str | None = None,
) -> tuple[str, ...]:
    environment = os.environ if environ is None else environ
    family = _family(platform_name or sys.platform)
    explicit = _environment_roots(
        environment,
        "PHOTOARCHIVE_LIGHTROOM_PRESET_DIRS",
        family=family,
    )
    if explicit is not None:
        return explicit
    path_module = _path_module(family)
    user_home = _home(environment, home)
    pictures = user_pictures_dir(environ=environment, platform_name=platform_name, home=home)
    roots = [path_module.join(pictures, "Lightroom", "Presets")]
    if family == "windows":
        adobe = path_module.join(
            environment.get("APPDATA") or path_module.join(user_home, "AppData", "Roaming"),
            "Adobe",
        )
        roots.extend(
            (
                path_module.join(adobe, "CameraRaw", "Settings"),
                path_module.join(adobe, "Lightroom", "Develop Presets"),
            )
        )
    elif family == "macos":
        adobe = path_module.join(user_home, "Library", "Application Support", "Adobe")
        roots.extend(
            (
                path_module.join(adobe, "CameraRaw", "Settings"),
                path_module.join(adobe, "Lightroom", "Develop Presets"),
            )
        )
    else:
        roots.extend(
            (
                path_module.join(user_home, ".config", "Adobe", "CameraRaw", "Settings"),
                path_module.join(user_home, ".config", "Adobe", "Lightroom", "Develop Presets"),
            )
        )
    return _dedupe(roots, family=family)
