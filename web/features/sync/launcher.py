"""Satellite launcher: resolve current.txt, supervise boot, two-failure rollback.

Windows-compatible (no symlinks). Ready = the satellite HTTP server answers its
own /api/version. A boot-attempt counter is cleared on ready; two consecutive
failed boots of the new version flip the pointer back to previous.txt.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from features.sync.client_update import (
    RESTART_EXIT_CODE,
    UI_ROLLED_BACK,
    atomic_write_text,
    resolve_install_root,
)


BOOT_ATTEMPTS_FILE = "boot_attempts.txt"
ROLLBACK_NOTICE_FILE = "rollback_notice.txt"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8010
READY_TIMEOUT_SECONDS = 45.0
READY_POLL_SECONDS = 0.5


def pointer_path(install_root: Path) -> Path:
    return install_root / "current.txt"


def previous_path(install_root: Path) -> Path:
    return install_root / "previous.txt"


def boot_attempts_path(install_root: Path) -> Path:
    return install_root / BOOT_ATTEMPTS_FILE


def rollback_notice_path(install_root: Path) -> Path:
    return install_root / ROLLBACK_NOTICE_FILE


def read_pointer(path: Path) -> Path | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return Path(text)


def write_pointer(path: Path, target: Path) -> None:
    atomic_write_text(path, os.fspath(target) + "\n")


def read_boot_attempts(install_root: Path) -> tuple[str, int]:
    path = boot_attempts_path(install_root)
    if not path.is_file():
        return "", 0
    raw = path.read_text(encoding="utf-8").strip().splitlines()
    if not raw:
        return "", 0
    sha = raw[0].strip()
    try:
        count = int(raw[1].strip()) if len(raw) > 1 else 0
    except ValueError:
        count = 0
    return sha, count


def write_boot_attempts(install_root: Path, sha: str, count: int) -> None:
    atomic_write_text(boot_attempts_path(install_root), f"{sha}\n{count}\n")


def clear_boot_attempts(install_root: Path) -> None:
    path = boot_attempts_path(install_root)
    if path.exists():
        path.unlink()


def record_boot_attempt(install_root: Path, sha: str) -> int:
    current_sha, count = read_boot_attempts(install_root)
    if current_sha != sha:
        count = 0
    count += 1
    write_boot_attempts(install_root, sha, count)
    return count


def mark_rollback_notice(install_root: Path) -> None:
    atomic_write_text(rollback_notice_path(install_root), UI_ROLLED_BACK + "\n")


def consume_rollback_notice(install_root: Path) -> str | None:
    path = rollback_notice_path(install_root)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    path.unlink(missing_ok=True)
    return text or UI_ROLLED_BACK


def rollback_to_previous(install_root: Path) -> Path | None:
    previous = read_pointer(previous_path(install_root))
    if previous is None or not previous.is_dir():
        return None
    write_pointer(pointer_path(install_root), previous)
    clear_boot_attempts(install_root)
    mark_rollback_notice(install_root)
    return previous


def resolve_version_dir(install_root: Path) -> Path:
    current = read_pointer(pointer_path(install_root))
    if current is not None and current.is_dir():
        return current
    # Dev / non-versioned: run from the active checkout's repo root.
    return Path(__file__).resolve().parents[3]


def version_web_dir(version_dir: Path) -> Path:
    web = version_dir / "web"
    return web if web.is_dir() else version_dir


def resolve_python(install_root: Path, version_dir: Path) -> str:
    env = os.environ.get("PHOTOARCHIVE_PYTHON", "").strip()
    if env:
        return env
    # Prefer venv matching requirements in this version when present.
    requirements = version_web_dir(version_dir) / "requirements.txt"
    if requirements.is_file():
        from features.sync.client_update import deps_hash, _venv_python

        venv = install_root / "venvs" / deps_hash(requirements)
        python = _venv_python(venv)
        if python.is_file():
            return os.fspath(python)
    return sys.executable


def version_ready(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - local satellite
            return 200 <= int(response.status) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def wait_until_ready(
    *,
    base_url: str,
    process: subprocess.Popen,
    timeout: float = READY_TIMEOUT_SECONDS,
    poll: float = READY_POLL_SECONDS,
) -> bool:
    deadline = time.monotonic() + timeout
    version_url = base_url.rstrip("/") + "/api/version"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if version_ready(version_url):
            return True
        time.sleep(poll)
    return False


def apply_two_failed_boots_rule(install_root: Path, attempts: int) -> bool:
    """After two consecutive failed boots, flip current.txt back to previous.

    Returns True when a rollback was performed.
    """

    if attempts < 2:
        return False
    return rollback_to_previous(install_root) is not None


def spawn_server(
    *,
    python: str,
    web_dir: Path,
    host: str,
    port: int,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    command = [
        python,
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged.setdefault("PHOTOARCHIVE_MODE", "satellite")
    return subprocess.Popen(  # noqa: S603 - launcher owns the satellite process
        command,
        cwd=os.fspath(web_dir),
        env=merged,
    )


def run_supervised(
    *,
    install_root: Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    ready_timeout: float = READY_TIMEOUT_SECONDS,
    spawn=spawn_server,
    wait_ready=wait_until_ready,
) -> int:
    """Main launcher loop. Returns process exit code."""

    root = Path(install_root) if install_root else resolve_install_root()
    root.mkdir(parents=True, exist_ok=True)
    base_url = f"http://{host}:{port}"

    while True:
        version_dir = resolve_version_dir(root)
        sha = version_dir.name
        attempts = record_boot_attempt(root, sha)
        web_dir = version_web_dir(version_dir)
        python = resolve_python(root, version_dir)
        env = {
            "PHOTOARCHIVE_INSTALL_ROOT": os.fspath(root),
            "PHOTOARCHIVE_CLIENT_SHA": sha,
            "PHOTOARCHIVE_PORT": str(port),
        }
        process = spawn(
            python=python,
            web_dir=web_dir,
            host=host,
            port=port,
            env=env,
        )
        ready = wait_ready(base_url=base_url, process=process, timeout=ready_timeout)
        if not ready:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            # Two consecutive crash-before-ready boots → flip pointer back.
            apply_two_failed_boots_rule(root, attempts)
            continue

        clear_boot_attempts(root)
        code = process.wait()
        if code == RESTART_EXIT_CODE:
            # Intentional update restart — do not count as a failed boot.
            continue
        return int(code or 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Azimuth Photo satellite launcher")
    parser.add_argument("--install-root", default=os.environ.get("PHOTOARCHIVE_INSTALL_ROOT") or "")
    parser.add_argument("--host", default=os.environ.get("PHOTOARCHIVE_HOST") or DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PHOTOARCHIVE_PORT") or DEFAULT_PORT))
    args = parser.parse_args(argv)
    root = resolve_install_root(args.install_root or None)
    return run_supervised(install_root=root, host=args.host, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
