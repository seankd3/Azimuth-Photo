"""One-click Lightroom plugin install into the auto-load Modules folder.

Windows-first (LR Classic Modules auto-load). Detect LR → show Connect;
connect copies the bundled plugin and writes satellite URL config;
disconnect removes it. Idempotent.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

PLUGIN_DIR_NAME = "azimuth-sync.lrplugin"
CONFIG_NAME = "satellite_url.json"


def _family(platform_name: str | None = None) -> str:
    value = str(platform_name or sys.platform).lower()
    if value.startswith("win"):
        return "windows"
    if value == "darwin":
        return "macos"
    return "linux"


def _home(environ: Mapping[str, str]) -> str:
    return str(environ.get("USERPROFILE") or environ.get("HOME") or Path.home())


def lightroom_modules_dir(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> Path | None:
    """Return the LR Classic auto-load Modules directory for this OS, if known."""

    env = os.environ if environ is None else environ
    family = _family(platform_name)
    explicit = str(env.get("PHOTOARCHIVE_LR_MODULES_DIR") or "").strip()
    if explicit:
        return Path(explicit)
    if family == "windows":
        appdata = str(env.get("APPDATA") or "").strip()
        if not appdata:
            appdata = str(Path(_home(env)) / "AppData" / "Roaming")
        return Path(appdata) / "Adobe" / "Lightroom" / "Modules"
    if family == "macos":
        return Path(_home(env)) / "Library" / "Application Support" / "Adobe" / "Lightroom" / "Modules"
    # Linux: no official Classic auto-load path; allow explicit override only.
    return None


def lightroom_install_detected(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    path_exists=os.path.exists,
) -> bool:
    """True when LR Classic appears installed (Modules parent or Program Files)."""

    env = os.environ if environ is None else environ
    forced = str(env.get("PHOTOARCHIVE_LR_FORCE_DETECT") or "").strip().lower()
    if forced in {"1", "true", "yes"}:
        return True
    if forced in {"0", "false", "no"}:
        return False
    family = _family(platform_name)
    modules = lightroom_modules_dir(environ=env, platform_name=platform_name)
    if modules is not None:
        parent = modules.parent  # .../Adobe/Lightroom
        if path_exists(str(parent)):
            return True
    if family == "windows":
        program_files = (
            str(env.get("PROGRAMFILES") or "").strip()
            or r"C:\Program Files"
        )
        classic = Path(program_files) / "Adobe" / "Adobe Lightroom Classic"
        if path_exists(str(classic)):
            return True
        program_files_x86 = str(env.get("PROGRAMFILES(X86)") or "").strip()
        if program_files_x86:
            classic86 = Path(program_files_x86) / "Adobe" / "Adobe Lightroom Classic"
            if path_exists(str(classic86)):
                return True
    if family == "macos":
        app = Path("/Applications/Adobe Lightroom Classic")
        if path_exists(str(app)) or path_exists(str(app) + ".app"):
            return True
    return False


def bundled_plugin_dir(*, repo_root: str | Path | None = None) -> Path:
    """Resolve clients/lightroom/azimuth-sync.lrplugin from the checkout."""

    if repo_root is not None:
        root = Path(repo_root)
    else:
        # web/features/sync/lr_connect.py → repo root is parents[3]
        root = Path(__file__).resolve().parents[3]
    return root / "clients" / "lightroom" / PLUGIN_DIR_NAME


def installed_plugin_dir(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> Path | None:
    modules = lightroom_modules_dir(environ=environ, platform_name=platform_name)
    if modules is None:
        return None
    return modules / PLUGIN_DIR_NAME


def connect_status(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    path_exists=os.path.exists,
    satellite_url: str | None = None,
) -> dict[str, Any]:
    detected = lightroom_install_detected(
        environ=environ,
        platform_name=platform_name,
        path_exists=path_exists,
    )
    plugin = installed_plugin_dir(environ=environ, platform_name=platform_name)
    connected = bool(plugin and path_exists(str(plugin)))
    modules = lightroom_modules_dir(environ=environ, platform_name=platform_name)
    return {
        "detected": detected,
        "connected": connected,
        "modules_dir": str(modules) if modules else None,
        "plugin_dir": str(plugin) if plugin else None,
        "satellite_url": satellite_url,
        "show_button": detected,
    }


def _write_satellite_config(plugin_dest: Path, satellite_url: str) -> None:
    config_path = plugin_dest / CONFIG_NAME
    payload = {"satelliteUrl": str(satellite_url or "").rstrip("/")}
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def connect_plugin(
    *,
    satellite_url: str,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    repo_root: str | Path | None = None,
    path_exists=os.path.exists,
) -> dict[str, Any]:
    """Copy the bundled plugin into Modules and write satellite URL config."""

    if not lightroom_install_detected(
        environ=environ, platform_name=platform_name, path_exists=path_exists
    ):
        return {"ok": False, "error": "Lightroom Classic not detected", "connected": False}
    modules = lightroom_modules_dir(environ=environ, platform_name=platform_name)
    if modules is None:
        return {"ok": False, "error": "Lightroom Modules path unknown on this OS", "connected": False}
    source = bundled_plugin_dir(repo_root=repo_root)
    if not source.is_dir():
        return {"ok": False, "error": f"Bundled plugin missing: {source}", "connected": False}
    modules.mkdir(parents=True, exist_ok=True)
    dest = modules / PLUGIN_DIR_NAME
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    _write_satellite_config(dest, satellite_url)
    return {
        "ok": True,
        "connected": True,
        "plugin_dir": str(dest),
        "satellite_url": str(satellite_url or "").rstrip("/"),
    }


def disconnect_plugin(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> dict[str, Any]:
    """Remove the installed plugin directory. Idempotent when already absent."""

    dest = installed_plugin_dir(environ=environ, platform_name=platform_name)
    if dest is None:
        return {"ok": True, "connected": False, "removed": False}
    if dest.exists():
        shutil.rmtree(dest)
        return {"ok": True, "connected": False, "removed": True, "plugin_dir": str(dest)}
    return {"ok": True, "connected": False, "removed": False, "plugin_dir": str(dest)}
