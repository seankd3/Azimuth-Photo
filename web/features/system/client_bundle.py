"""Hub-served client bundles: identity at startup, tar.gz cached beside thumbs."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from core.runtime_paths import resolve_runtime_paths
from data.schema import SCHEMA_VERSION


log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_lock = threading.Lock()
_identity: "ClientIdentity | None" = None
_identity_pending = False


@dataclass(frozen=True)
class ClientIdentity:
    sha: str
    bundle_sha256: str
    schema_version: int
    bundle_path: str


def repo_root() -> Path:
    return _REPO_ROOT


def read_git_sha(root: Path | None = None) -> str:
    """Full HEAD sha of the hub checkout (or empty when git is unavailable)."""

    cwd = root or repo_root()
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.fspath(cwd),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def working_tree_dirty(root: Path | None = None) -> bool:
    cwd = root or repo_root()
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=os.fspath(cwd),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(status.strip())


def bundle_cache_dir(cache_dir: str | None = None) -> Path:
    root = cache_dir or resolve_runtime_paths().cache_dir
    path = Path(root) / "client-bundles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def archive_head_bundle(
    *,
    sha: str,
    root: Path | None = None,
    cache_dir: str | None = None,
) -> tuple[Path, str]:
    """Produce (or reuse) a git-archive tar.gz for HEAD and return path + sha256."""

    cwd = root or repo_root()
    if working_tree_dirty(cwd):
        log.warning(
            "client bundle: working tree dirty; archiving HEAD %s (not the dirty worktree)",
            sha[:12],
        )
    cached = bundle_cache_dir(cache_dir) / f"{sha}.tar.gz"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached, _sha256_file(cached)

    tmp = cached.with_name(f"{sha}.tar.gz.partial")
    if tmp.exists():
        tmp.unlink()
    try:
        subprocess.check_call(
            ["git", "archive", "--format=tar.gz", f"--output={tmp}", "HEAD"],
            cwd=os.fspath(cwd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except subprocess.CalledProcessError as error:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        detail = (error.stderr or b"").decode(errors="replace")[:300]
        raise RuntimeError(f"git archive failed: {detail}") from error
    # Write the final name via os.replace so a crash mid-archive never leaves a
    # half file under the canonical cache path.
    os.replace(tmp, cached)
    return cached, _sha256_file(cached)


def prune_stale_bundles(*, keep_sha: str, cache_dir: str | None = None) -> None:
    """Keep the live sha's archive; drop older cached bundles."""

    directory = bundle_cache_dir(cache_dir)
    keep_name = f"{keep_sha}.tar.gz"
    for path in directory.glob("*.tar.gz"):
        if path.name != keep_name:
            try:
                path.unlink()
            except OSError:
                log.warning("client bundle: could not prune %s", path)


def init_hub_client_identity(
    *,
    root: Path | None = None,
    cache_dir: str | None = None,
    sha: str | None = None,
) -> ClientIdentity:
    """Compute identity once. Regenerates the archive only on SHA change."""

    global _identity, _identity_pending
    with _lock:
        resolved_sha = (sha or read_git_sha(root)).strip()
        if not resolved_sha:
            identity = ClientIdentity(
                sha="unknown",
                bundle_sha256="",
                schema_version=int(SCHEMA_VERSION),
                bundle_path="",
            )
            _identity = identity
            _identity_pending = False
            return identity
        if _identity is not None and _identity.sha == resolved_sha and Path(_identity.bundle_path).is_file():
            _identity_pending = False
            return _identity
        path, digest = archive_head_bundle(sha=resolved_sha, root=root, cache_dir=cache_dir)
        prune_stale_bundles(keep_sha=resolved_sha, cache_dir=cache_dir)
        identity = ClientIdentity(
            sha=resolved_sha,
            bundle_sha256=digest,
            schema_version=int(SCHEMA_VERSION),
            bundle_path=os.fspath(path),
        )
        _identity = identity
        _identity_pending = False
        return identity


def mark_hub_client_identity_pending() -> None:
    """Keep updater clients idle while a hub prepares its next bundle."""

    global _identity_pending
    with _lock:
        if _identity is None:
            _identity_pending = True


def finish_hub_client_identity_attempt() -> None:
    """Release the pending state after a failed asynchronous preparation."""

    global _identity_pending
    with _lock:
        _identity_pending = False


def hub_client_identity_pending() -> bool:
    with _lock:
        return _identity is None and _identity_pending


def get_hub_client_identity() -> ClientIdentity | None:
    return _identity


def reset_hub_client_identity_for_tests() -> None:
    global _identity, _identity_pending
    with _lock:
        _identity = None
        _identity_pending = False


def identity_payload() -> dict[str, str | int]:
    """Fields the frozen auto-update contract advertises on /api/version."""

    identity = _identity
    if identity is None:
        if hub_client_identity_pending():
            return {
                "sha": "unknown",
                "bundle_sha256": "",
                "schema_version": int(SCHEMA_VERSION),
            }
        # Satellite / test processes: advertise running sha without building a hub bundle.
        env_sha = os.environ.get("AZIMUTH_CLIENT_SHA", "").strip()
        sha = env_sha or read_git_sha() or "unknown"
        return {
            "sha": sha,
            "bundle_sha256": "",
            "schema_version": int(SCHEMA_VERSION),
        }
    return {
        "sha": identity.sha,
        "bundle_sha256": identity.bundle_sha256,
        "schema_version": identity.schema_version,
    }


def cached_bundle_path() -> Path | None:
    identity = _identity
    if identity is None or not identity.bundle_path:
        return None
    path = Path(identity.bundle_path)
    return path if path.is_file() else None
