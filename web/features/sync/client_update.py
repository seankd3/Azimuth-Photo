"""Satellite client auto-update: download, verify, unpack, venv, pointer flip.

Windows-safe throughout (os.replace for the pointer; no symlinks). Restart is
requested only at a sync safe-point after an in-flight upload chunk loop drains.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

RequestFn = Callable[..., Awaitable[tuple[int, dict, bytes]]]
RESTART_EXIT_CODE = 82

STATUS_IDLE = "idle"
STATUS_UPDATING = "updating"
STATUS_RETRY = "available_retry"
STATUS_ROLLED_BACK = "rolled_back"

# Exact UI lines from CLIENT_AUTOUPDATE_SPEC.md
UI_UPDATING = "Updating…"
UI_RETRY = "Update available — will retry"
UI_ROLLED_BACK = "Update rolled back — running previous version"


@dataclass
class UpdateStatus:
    state: str = STATUS_IDLE
    message: str = ""
    hub_sha: str | None = None
    local_sha: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "update_state": self.state,
            "update_message": self.message,
            "hub_sha": self.hub_sha,
            "local_sha": self.local_sha,
        }


@dataclass
class ClientUpdater:
    """Converge a satellite install onto the hub's advertised sha."""

    install_root: Path
    hub: str
    local_sha: str
    request: RequestFn | None = None
    restart: Callable[[], None] | None = None
    ui_busy: Callable[[], bool] | None = None
    status: UpdateStatus = field(default_factory=UpdateStatus)
    _pending_restart: bool = False
    _prepared_sha: str | None = None

    def __post_init__(self) -> None:
        self.install_root = Path(self.install_root)
        self.hub = self.hub.rstrip("/")
        self.status.local_sha = self.local_sha

    @property
    def versions_dir(self) -> Path:
        return self.install_root / "versions"

    @property
    def venvs_dir(self) -> Path:
        return self.install_root / "venvs"

    @property
    def current_pointer(self) -> Path:
        return self.install_root / "current.txt"

    @property
    def previous_pointer(self) -> Path:
        return self.install_root / "previous.txt"

    def mark_rolled_back(self) -> None:
        self.status.state = STATUS_ROLLED_BACK
        self.status.message = UI_ROLLED_BACK

    def clear_rolled_back_if_current(self, sha: str) -> None:
        if self.status.state == STATUS_ROLLED_BACK and self.local_sha == sha:
            # Stay visible until the next successful converge away from rollback.
            return

    async def consider_hub_version(self, payload: dict[str, Any]) -> None:
        """Handshake step: compare shas; download+prepare on mismatch."""

        hub_sha = str(payload.get("sha") or "").strip()
        bundle_sha256 = str(payload.get("bundle_sha256") or "").strip()
        self.status.hub_sha = hub_sha or None
        if not hub_sha or hub_sha in {"unknown", self.local_sha}:
            if self.status.state == STATUS_RETRY:
                self.status.state = STATUS_IDLE
                self.status.message = ""
            return
        # Already flipped onto hub_sha — only (re)arm the safe-point restart.
        if self._prepared_sha == hub_sha or _pointer_targets_sha(self.current_pointer, hub_sha):
            self._prepared_sha = hub_sha
            self._pending_restart = True
            self.status.state = STATUS_UPDATING
            self.status.message = UI_UPDATING
            return
        self.status.state = STATUS_UPDATING
        self.status.message = UI_UPDATING
        try:
            await self._download_verify_unpack(hub_sha, bundle_sha256)
            self._ensure_venv_for_version(hub_sha)
            self._flip_current(hub_sha)
            self._prepared_sha = hub_sha
            self._pending_restart = True
        except Exception as error:
            log.warning("client update failed (will retry): %s", error)
            self.status.state = STATUS_RETRY
            self.status.message = UI_RETRY
            self._pending_restart = False

    def request_restart_if_safe(self, *, uploading: bool) -> bool:
        """Safe-point: never mid-upload chunk loop; never mid import/develop."""

        if not self._pending_restart:
            return False
        if uploading:
            return False
        if self.ui_busy is not None and self.ui_busy():
            return False
        self._pending_restart = False
        self.status.state = STATUS_UPDATING
        self.status.message = UI_UPDATING
        if self.restart is not None:
            self.restart()
        return True

    async def _download_verify_unpack(self, hub_sha: str, expected_sha256: str) -> Path:
        if self.request is None:
            raise RuntimeError("client updater request function is not configured")
        if not expected_sha256:
            raise RuntimeError("hub did not advertise bundle_sha256")
        status, _headers, body = await self.request(
            "GET",
            f"{self.hub}/api/client/bundle",
            body=None,
            headers=None,
        )
        if not 200 <= int(status) < 300:
            raise RuntimeError(f"bundle download failed ({status})")
        digest = hashlib.sha256(body).hexdigest()
        if digest != expected_sha256:
            raise RuntimeError("bundle sha256 mismatch — discarding")
        version_dir = self.versions_dir / hub_sha
        if version_dir.exists():
            shutil.rmtree(version_dir)
        version_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=os.fspath(self.install_root)) as tmp:
            archive = Path(tmp) / "bundle.tar.gz"
            archive.write_bytes(body)
            staging = Path(tmp) / "staging"
            staging.mkdir()
            with tarfile.open(archive, "r:gz") as handle:
                # Hub bytes were sha256-verified above; extractall is intentional.
                handle.extractall(staging)  # noqa: S202
            _move_tree(staging, version_dir)
        marker = version_dir / ".client_sha"
        marker.write_text(hub_sha + "\n", encoding="utf-8")
        return version_dir

    def _ensure_venv_for_version(self, hub_sha: str) -> Path:
        requirements = self.versions_dir / hub_sha / "web" / "requirements.txt"
        if not requirements.is_file():
            # Some archives may root differently; accept top-level requirements too.
            alt = self.versions_dir / hub_sha / "requirements.txt"
            requirements = alt if alt.is_file() else requirements
        deps = deps_hash(requirements) if requirements.is_file() else "nodeps"
        venv_path = self.venvs_dir / deps
        if venv_path.is_dir() and _venv_python(venv_path).is_file():
            log.info("client update: reusing venv %s", venv_path.name)
            return venv_path
        return build_venv(venv_path, requirements if requirements.is_file() else None)

    def _flip_current(self, hub_sha: str) -> None:
        version_dir = self.versions_dir / hub_sha
        if not version_dir.is_dir():
            raise RuntimeError(f"version dir missing for {hub_sha}")
        # Remember previous pointer for launcher rollback.
        if self.current_pointer.is_file():
            atomic_write_text(self.previous_pointer, self.current_pointer.read_text(encoding="utf-8"))
        atomic_write_text(self.current_pointer, os.fspath(version_dir) + "\n")
        prune_old_versions(self.versions_dir, keep={hub_sha, _sha_from_pointer(self.previous_pointer)})


def _move_tree(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dest / child.name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        os.replace(child, target)


def _sha_from_pointer(pointer: Path) -> str | None:
    if not pointer.is_file():
        return None
    text = pointer.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return Path(text).name


def _pointer_targets_sha(pointer: Path, sha: str) -> bool:
    return _sha_from_pointer(pointer) == sha


def deps_hash(requirements_path: Path | str) -> str:
    data = Path(requirements_path).read_bytes()
    return hashlib.sha256(data).hexdigest()[:16]


def _venv_python(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def build_venv(venv_path: Path, requirements: Path | None) -> Path:
    """Create venvs/<deps-hash>/ when requirements changed. Testable without pip."""

    import subprocess
    import sys
    import venv

    venv_path.parent.mkdir(parents=True, exist_ok=True)
    if venv_path.exists():
        shutil.rmtree(venv_path)
    venv.create(os.fspath(venv_path), with_pip=True)
    python = _venv_python(venv_path)
    if requirements is not None and requirements.is_file():
        subprocess.check_call(
            [os.fspath(python), "-m", "pip", "install", "-r", os.fspath(requirements)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    marker = venv_path / ".deps_hash"
    marker.write_text(venv_path.name + "\n", encoding="utf-8")
    # Silence unused in environments without needing sys at call sites.
    _ = sys.platform
    return venv_path


def should_reuse_venv(venv_path: Path, requirements: Path) -> bool:
    """Decision helper: same requirements.txt hash → reuse existing venv."""

    if not venv_path.is_dir() or not _venv_python(venv_path).is_file():
        return False
    expected = deps_hash(requirements)
    return venv_path.name == expected or (venv_path / ".deps_hash").read_text(encoding="utf-8").strip() == expected


def atomic_write_text(path: Path, content: str) -> None:
    """Atomic pointer flip via write-temp + os.replace (Windows-safe)."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def prune_old_versions(versions_dir: Path, *, keep: set[str | None]) -> None:
    """Keep exactly current + previous; prune older version dirs."""

    kept = {value for value in keep if value}
    if not versions_dir.is_dir():
        return
    for child in versions_dir.iterdir():
        if child.is_dir() and child.name not in kept:
            shutil.rmtree(child, ignore_errors=True)


def resolve_install_root(explicit: str | Path | None = None) -> Path:
    env = os.environ.get("PHOTOARCHIVE_INSTALL_ROOT", "").strip()
    if explicit:
        return Path(explicit)
    if env:
        return Path(env)
    # Running from versions/<sha>/web → install root is parents[2]
    here = Path(__file__).resolve()
    # .../versions/<sha>/web/features/sync/client_update.py → parents[4] == versions, [5] == install
    parts = here.parts
    if "versions" in parts:
        idx = parts.index("versions")
        return Path(*parts[:idx]) if idx > 0 else Path(*parts[: idx + 1]).parent
    # Dev checkout: treat repo root's parent-of-web sibling install as optional; fall back to cwd.
    return Path(os.environ.get("PHOTOARCHIVE_HOME") or Path.cwd())


def resolve_local_sha(*, install_root: Path | None = None) -> str:
    env = os.environ.get("PHOTOARCHIVE_CLIENT_SHA", "").strip()
    if env:
        return env
    root = install_root or resolve_install_root()
    pointer = root / "current.txt"
    if pointer.is_file():
        target = Path(pointer.read_text(encoding="utf-8").strip())
        marker = target / ".client_sha"
        if marker.is_file():
            return marker.read_text(encoding="utf-8").strip()
        if target.name and all(c in "0123456789abcdef" for c in target.name.lower()):
            return target.name
    from features.system.client_bundle import read_git_sha

    return read_git_sha() or "unknown"


def ui_session_busy() -> bool:
    """True while an import (or develop-import) session is actively running."""

    try:
        from features.develop import importer as develop_importer

        if bool(develop_importer.import_status().get("running")):
            return True
    except Exception:
        pass
    try:
        from features.imports import staging as import_staging

        jobs = getattr(import_staging, "_jobs", {}) or {}
        for job in jobs.values():
            state = getattr(job, "state", None) or getattr(job, "status", None)
            if str(state).lower() in {"running", "copying", "importing", "active"}:
                return True
    except Exception:
        pass
    return False


def consume_rollback_notice_into_status(updater: ClientUpdater | None, install_root: Path | None = None) -> None:
    """If the launcher rolled back, surface the notice on the sync status line."""

    if updater is None:
        return
    root = Path(install_root) if install_root else updater.install_root
    notice_path = root / "rollback_notice.txt"
    if not notice_path.is_file():
        return
    text = notice_path.read_text(encoding="utf-8").strip() or UI_ROLLED_BACK
    notice_path.unlink(missing_ok=True)
    updater.status.state = STATUS_ROLLED_BACK
    updater.status.message = text


def request_process_restart() -> None:
    """Exit so the launcher respawns from current.txt (Windows-safe)."""

    raise SystemExit(RESTART_EXIT_CODE)
