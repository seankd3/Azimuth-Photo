"""In-app Cloud Backup: rclone vault sync for original library trees.

Universal primitive — remote name, destination prefix, and tree paths come from
settings. No host-specific paths. Trashed/rejected exclusions are catalog-driven.
"""

from __future__ import annotations

from core.dates import seconds_until_local_hour

import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import settings as app_settings
from core import bulk_scheduler
from core.runtime_paths import resolve_runtime_paths

log = logging.getLogger(__name__)

DEFAULT_BWLIMIT = "07:00,3M 23:00,off"
STATUS_FILENAME = "cloud_backup_status.json"
_VAULT_WAIT_POLL_SECONDS = 2.0

# rclone --stats-one-line examples:
#   Transferred:   	  123.456 MiB / 1.234 GiB, 10%, 12.345 MiB/s, ETA 1m23s
#   Transferred:   	          0 B / 0 B, -, 0 B/s, ETA -
_STATS_RE = re.compile(
    r"Transferred:\s+"
    r"(?P<done>[0-9.]+\s*[KMGTP]?i?B)\s*/\s*"
    r"(?P<total>[0-9.]+\s*[KMGTP]?i?B|"
    r"-),"
    r"\s*(?P<pct>[0-9]+%|-),"
    r"\s*(?P<speed>[0-9.]+\s*[KMGTP]?i?B/s|"
    r"-),"
    r"\s*ETA\s+(?P<eta>\S+)",
    re.IGNORECASE,
)
_SIZE_RE = re.compile(
    r"^(?P<n>[0-9.]+)\s*(?P<u>[KMGTP]?i?B)$",
    re.IGNORECASE,
)
_SIZE_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
}

DbPathProvider = Callable[[], str]

_runner_lock = threading.Lock()
_scheduler_started = False
_process: subprocess.Popen | None = None
_worker_thread: threading.Thread | None = None
_stop_requested = False
_runtime: dict[str, Any] = {
    # idle | waiting | running | stopping | unavailable | error
    "state": "idle",
    "message": "",
    "current_tree": None,
    "bytes_done": 0,
    "bytes_total": 0,
    "pct": 0.0,
    "speed": "",
    "eta": "",
    "started_at": None,
    "finished_at": None,
    "last_error": None,
    "manual_override": False,
}


def rclone_binary() -> str | None:
    return shutil.which("rclone")


def rclone_available() -> bool:
    return rclone_binary() is not None


def list_remotes(*, rclone_bin: str | None = None) -> list[str]:
    """Return configured rclone remote names (without trailing colon)."""
    binary = rclone_bin or rclone_binary()
    if not binary:
        return []
    try:
        completed = subprocess.run(
            [binary, "listremotes"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("rclone listremotes failed: %s", exc)
        return []
    if completed.returncode != 0:
        log.warning("rclone listremotes exit=%s stderr=%s", completed.returncode, completed.stderr)
        return []
    remotes: list[str] = []
    for line in (completed.stdout or "").splitlines():
        name = line.strip().rstrip(":")
        if name:
            remotes.append(name)
    return remotes


def parse_size_bytes(text: str) -> int | None:
    raw = str(text or "").strip()
    if not raw or raw == "-":
        return None
    match = _SIZE_RE.match(raw.replace(",", ""))
    if not match:
        return None
    try:
        amount = float(match.group("n"))
    except ValueError:
        return None
    unit = match.group("u").upper()
    factor = _SIZE_UNITS.get(unit)
    if factor is None:
        return None
    return int(amount * factor)


def parse_rclone_stats(line: str) -> dict[str, Any] | None:
    """Parse one rclone --stats-one-line transfer summary into a status dict."""
    text = str(line or "").strip()
    if "Transferred:" not in text:
        return None
    match = _STATS_RE.search(text)
    if not match:
        return None
    done = parse_size_bytes(match.group("done"))
    total = parse_size_bytes(match.group("total"))
    pct_raw = match.group("pct")
    try:
        pct = float(pct_raw.rstrip("%")) if pct_raw != "-" else 0.0
    except ValueError:
        pct = 0.0
    speed = match.group("speed")
    eta = match.group("eta")
    return {
        "bytes_done": done or 0,
        "bytes_total": total or 0,
        "pct": pct,
        "speed": "" if speed == "-" else speed,
        "eta": "" if eta == "-" else eta,
        "raw": text,
    }


def _normalize_tree_paths(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        path = os.path.abspath(os.path.expanduser(str(item or "").strip()))
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def config_from_settings(raw: dict | None = None) -> dict[str, Any]:
    values = raw if isinstance(raw, dict) else app_settings.get_settings()
    return {
        "remote": str(values.get("cloud_backup_remote") or "").strip().rstrip(":"),
        "dest_prefix": str(values.get("cloud_backup_dest_prefix") or "").strip().rstrip("/"),
        "trees": _normalize_tree_paths(values.get("cloud_backup_trees")),
        "bwlimit": str(values.get("cloud_backup_bwlimit") or DEFAULT_BWLIMIT).strip() or DEFAULT_BWLIMIT,
        "exclude_from_catalog": app_settings._normalize_bool(
            values.get("cloud_backup_exclude_from_catalog", True),
            True,
        ),
        "nightly_enabled": app_settings._normalize_bool(
            values.get("cloud_backup_nightly_enabled", False),
            False,
        ),
    }


def apply_config_to_settings_dict(base: dict, config: dict[str, Any]) -> dict:
    next_settings = dict(base)
    next_settings["cloud_backup_remote"] = str(config.get("remote") or "").strip().rstrip(":")
    next_settings["cloud_backup_dest_prefix"] = str(config.get("dest_prefix") or "").strip().rstrip("/")
    next_settings["cloud_backup_trees"] = _normalize_tree_paths(config.get("trees"))
    next_settings["cloud_backup_bwlimit"] = (
        str(config.get("bwlimit") or DEFAULT_BWLIMIT).strip() or DEFAULT_BWLIMIT
    )
    next_settings["cloud_backup_exclude_from_catalog"] = bool(config.get("exclude_from_catalog", True))
    next_settings["cloud_backup_nightly_enabled"] = bool(config.get("nightly_enabled", False))
    return next_settings


def validate_config(
    config: dict[str, Any],
    *,
    remotes: list[str] | None = None,
    require_trees: bool = False,
) -> dict[str, Any]:
    """Validate cloud backup config. Raises ValueError on bad input."""
    if not rclone_available():
        raise ValueError("rclone is not installed — Cloud Backup is unavailable")
    remote = str(config.get("remote") or "").strip().rstrip(":")
    if not remote:
        raise ValueError("Choose an rclone remote")
    known = remotes if remotes is not None else list_remotes()
    if remote not in known:
        raise ValueError(f"rclone remote “{remote}” is not configured")
    dest_prefix = str(config.get("dest_prefix") or "").strip().rstrip("/")
    trees = _normalize_tree_paths(config.get("trees"))
    if require_trees and not trees:
        raise ValueError("Select at least one library tree to include")
    for tree in trees:
        if not os.path.isdir(tree):
            raise ValueError(f"Library tree is not an accessible folder: {tree}")
    bwlimit = str(config.get("bwlimit") or DEFAULT_BWLIMIT).strip() or DEFAULT_BWLIMIT
    return {
        "remote": remote,
        "dest_prefix": dest_prefix,
        "trees": trees,
        "bwlimit": bwlimit,
        "exclude_from_catalog": bool(config.get("exclude_from_catalog", True)),
        "nightly_enabled": bool(config.get("nightly_enabled", False)),
    }


def save_config(config: dict[str, Any], *, remotes: list[str] | None = None) -> dict[str, Any]:
    validated = validate_config(config, remotes=remotes, require_trees=False)
    current = app_settings.get_settings()
    saved = app_settings.save_settings(apply_config_to_settings_dict(current, validated))
    return config_from_settings(saved)


def status_path() -> Path:
    return Path(resolve_runtime_paths().state_dir) / STATUS_FILENAME


def load_persisted_status() -> dict[str, Any]:
    path = status_path()
    if not path.is_file():
        return {
            "last_ok_at": None,
            "last_bytes": 0,
            "last_error": None,
            "last_error_at": None,
            "trees": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "last_ok_at": None,
            "last_bytes": 0,
            "last_error": None,
            "last_error_at": None,
            "trees": {},
        }
    trees = data.get("trees") if isinstance(data.get("trees"), dict) else {}
    return {
        "last_ok_at": data.get("last_ok_at"),
        "last_bytes": int(data.get("last_bytes") or 0),
        "last_error": data.get("last_error"),
        "last_error_at": data.get("last_error_at"),
        "trees": {
            str(key): {
                "last_ok_at": value.get("last_ok_at") if isinstance(value, dict) else None,
                "bytes": int(value.get("bytes") or 0) if isinstance(value, dict) else 0,
            }
            for key, value in trees.items()
        },
    }


def _write_persisted_status(payload: dict[str, Any]) -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def record_sync_success(*, trees: dict[str, dict[str, Any]], total_bytes: int) -> None:
    current = load_persisted_status()
    merged_trees = dict(current.get("trees") or {})
    now = time.time()
    for tree_path, info in trees.items():
        merged_trees[tree_path] = {
            "last_ok_at": now,
            "bytes": int(info.get("bytes") or 0),
        }
    _write_persisted_status(
        {
            "last_ok_at": now,
            "last_bytes": int(total_bytes),
            "last_error": None,
            "last_error_at": None,
            "trees": merged_trees,
        }
    )


def record_sync_failure(message: str) -> None:
    current = load_persisted_status()
    current["last_error"] = str(message or "Cloud Backup failed")
    current["last_error_at"] = time.time()
    _write_persisted_status(current)


def relative_exclude_patterns(filepaths: list[str], tree_root: str) -> list[str]:
    """Turn absolute catalog filepaths into rclone --exclude-from patterns."""
    root = os.path.abspath(tree_root)
    prefix = root.rstrip(os.sep) + os.sep
    patterns: list[str] = []
    seen: set[str] = set()
    for filepath in filepaths:
        absolute = os.path.abspath(str(filepath or ""))
        if not absolute.startswith(prefix):
            continue
        relative = absolute[len(prefix) :].replace("\\", "/")
        if not relative or relative in seen:
            continue
        seen.add(relative)
        patterns.append("/" + relative)
    patterns.sort()
    return patterns


def catalog_excluded_filepaths(db_path: str, tree_root: str) -> list[str]:
    """Trashed or rejected images under a tree root (absolute paths)."""
    root = os.path.abspath(tree_root)
    like = root.rstrip(os.sep) + os.sep + "%"
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT filepath FROM images "
            "WHERE (status = 'trashed' OR flag = 'rejected') AND filepath LIKE ?",
            (like,),
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows if row and row[0]]


def write_exclude_file(db_path: str, tree_root: str, dest: str | Path) -> int:
    """Write catalog-driven exclude patterns; return pattern count."""
    paths = catalog_excluded_filepaths(db_path, tree_root)
    patterns = relative_exclude_patterns(paths, tree_root)
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(patterns) + ("\n" if patterns else ""), encoding="utf-8")
    return len(patterns)


def dest_for_tree(remote: str, dest_prefix: str, tree_root: str) -> str:
    tree_name = Path(tree_root).name or "library"
    prefix = str(dest_prefix or "").strip().rstrip("/")
    if prefix:
        return f"{remote}:{prefix}/{tree_name}"
    return f"{remote}:{tree_name}"


def build_rclone_copy_argv(
    *,
    rclone_bin: str,
    source: str,
    dest: str,
    exclude_file: str | None,
    bwlimit: str,
) -> list[str]:
    argv = [
        rclone_bin,
        "copy",
        source,
        dest,
        "--transfers",
        "4",
        "--checkers",
        "8",
        "--stats",
        "1s",
        "--stats-one-line",
        "--log-level",
        "INFO",
    ]
    if bwlimit:
        argv.extend(["--bwlimit", bwlimit])
    if exclude_file:
        argv.extend(["--exclude-from", exclude_file])
    # Idle IO class so kernel BFQ deprioritizes vault sync against app reads.
    ionice = shutil.which("ionice")
    if ionice and os.name != "nt":
        return [ionice, "-c3", *argv]
    return argv


def _set_runtime(**kwargs: Any) -> None:
    with _runner_lock:
        _runtime.update(kwargs)


def _runtime_snapshot() -> dict[str, Any]:
    with _runner_lock:
        return dict(_runtime)


def feature_availability() -> dict[str, Any]:
    if not rclone_available():
        return {
            "available": False,
            "reason": "rclone is not installed on this computer",
            "remotes": [],
        }
    remotes = list_remotes()
    return {
        "available": True,
        "reason": None,
        "remotes": remotes,
    }


def status_payload(*, db_path: str | None = None) -> dict[str, Any]:
    del db_path  # reserved for future freshness checks against catalog
    availability = feature_availability()
    config = config_from_settings()
    persisted = load_persisted_status()
    runtime = _runtime_snapshot()
    state = runtime.get("state") or "idle"
    if not availability["available"] and state == "idle":
        state = "unavailable"
    tree_freshness = []
    for tree in config["trees"]:
        info = (persisted.get("trees") or {}).get(tree) or {}
        tree_freshness.append(
            {
                "path": tree,
                "name": Path(tree).name or tree,
                "last_ok_at": info.get("last_ok_at"),
                "bytes": int(info.get("bytes") or 0),
            }
        )
    return {
        "available": availability["available"],
        "unavailable_reason": availability["reason"],
        "remotes": availability["remotes"],
        "config": config,
        "state": state,
        "message": runtime.get("message") or "",
        "current_tree": runtime.get("current_tree"),
        "bytes_done": int(runtime.get("bytes_done") or 0),
        "bytes_total": int(runtime.get("bytes_total") or 0),
        "pct": float(runtime.get("pct") or 0.0),
        "speed": runtime.get("speed") or "",
        "eta": runtime.get("eta") or "",
        "started_at": runtime.get("started_at"),
        "finished_at": runtime.get("finished_at"),
        "last_error": runtime.get("last_error") or persisted.get("last_error"),
        "last_ok_at": persisted.get("last_ok_at"),
        "last_bytes": int(persisted.get("last_bytes") or 0),
        "trees": tree_freshness,
    }


def _terminate_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            return
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def _clear_desired_after_job() -> None:
    """Sync finished or failed — do not auto-resume a completed one-shot on boot."""
    try:
        bulk_scheduler.set_vault_desired(False)
    except Exception:
        log.exception("cloud_backup failed to clear desired_running flag")


def _wait_for_disk_turn() -> bool:
    """Block until previews release the disk (or stop). Return True to proceed."""
    while not _stop_requested:
        if not bulk_scheduler.sequencing_enabled() or not bulk_scheduler.previews_pending():
            return True
        _set_runtime(
            state="waiting",
            message=bulk_scheduler.VAULT_WAIT_MESSAGE,
            current_tree=None,
            speed="",
            eta="",
        )
        time.sleep(_VAULT_WAIT_POLL_SECONDS)
    return False


def _run_sync_job(
    db_path: str,
    config: dict[str, Any],
    *,
    wait_for_previews: bool = False,
    override_warning: str = "",
) -> None:
    global _process, _stop_requested
    if wait_for_previews:
        _set_runtime(
            state="waiting",
            message=bulk_scheduler.VAULT_WAIT_MESSAGE,
            current_tree=None,
        )
        if not _wait_for_disk_turn():
            _set_runtime(
                state="idle",
                message="Cloud Backup paused",
                finished_at=time.time(),
                current_tree=None,
            )
            return

    binary = rclone_binary()
    if not binary:
        _set_runtime(
            state="unavailable",
            message="rclone is not installed",
            last_error="rclone is not installed",
            finished_at=time.time(),
        )
        _clear_desired_after_job()
        return

    work_dir = tempfile.mkdtemp(prefix="cloud-backup-")
    tree_results: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    try:
        for tree in config["trees"]:
            if _stop_requested:
                break
            tree_name = Path(tree).name or tree
            message = f"Syncing {tree_name}"
            if override_warning:
                message = f"{override_warning} — {message}"
            _set_runtime(
                state="running",
                current_tree=tree,
                message=message,
                bytes_done=0,
                bytes_total=0,
                pct=0.0,
                speed="",
                eta="",
            )
            exclude_path = None
            if config.get("exclude_from_catalog"):
                exclude_path = os.path.join(
                    work_dir,
                    f"exclude-{re.sub(r'[^A-Za-z0-9_.-]+', '_', tree_name)}.txt",
                )
                count = write_exclude_file(db_path, tree, exclude_path)
                log.info("cloud_backup tree=%s excluding %s trashed/rejected", tree_name, count)

            dest = dest_for_tree(config["remote"], config["dest_prefix"], tree)
            argv = build_rclone_copy_argv(
                rclone_bin=binary,
                source=tree,
                dest=dest,
                exclude_file=exclude_path,
                bwlimit=config["bwlimit"],
            )
            log.info("cloud_backup starting %s", " ".join(argv))
            popen_kwargs: dict[str, Any] = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
            }
            if os.name != "nt":
                popen_kwargs["start_new_session"] = True
            with _runner_lock:
                if _stop_requested:
                    break
                _process = subprocess.Popen(argv, **popen_kwargs)
                proc = _process

            tree_bytes = 0
            assert proc.stdout is not None
            for line in proc.stdout:
                if _stop_requested:
                    _terminate_process(proc)
                    break
                stats = parse_rclone_stats(line)
                if not stats:
                    continue
                tree_bytes = max(tree_bytes, int(stats["bytes_done"] or 0))
                _set_runtime(
                    bytes_done=stats["bytes_done"],
                    bytes_total=stats["bytes_total"],
                    pct=stats["pct"],
                    speed=stats["speed"],
                    eta=stats["eta"],
                )
            exit_code = proc.wait()
            with _runner_lock:
                _process = None
            if _stop_requested:
                # Intentional pause/stop (SIGTERM etc.) — not a vault failure.
                break
            if exit_code != 0:
                # Negative codes are signals (e.g. -15 SIGTERM). If the hub or
                # operator killed rclone without going through stop_sync, still
                # avoid permanent "bad" health for a clean signal death — record
                # a soft pause message instead of last_error sticky poison.
                if exit_code < 0:
                    message = (
                        f"Cloud Backup interrupted (signal {-exit_code}) "
                        f"while syncing {tree_name}"
                    )
                    _set_runtime(
                        state="idle",
                        message=message,
                        finished_at=time.time(),
                        current_tree=None,
                    )
                    _clear_desired_after_job()
                    return
                message = f"rclone exited {exit_code} while syncing {tree_name}"
                record_sync_failure(message)
                _set_runtime(
                    state="error",
                    message=message,
                    last_error=message,
                    finished_at=time.time(),
                    current_tree=tree,
                )
                _clear_desired_after_job()
                return
            tree_results[tree] = {"bytes": tree_bytes}
            total_bytes += tree_bytes

        if _stop_requested:
            _set_runtime(
                state="idle",
                message="Cloud Backup paused",
                finished_at=time.time(),
                current_tree=None,
            )
            # Stop is explicit — desired already cleared in stop_sync.
            return

        record_sync_success(trees=tree_results, total_bytes=total_bytes)
        _set_runtime(
            state="idle",
            message="Cloud Backup finished",
            finished_at=time.time(),
            current_tree=None,
            pct=100.0 if tree_results else 0.0,
            last_error=None,
        )
        _clear_desired_after_job()
    except Exception as exc:
        log.exception("cloud_backup job failed")
        record_sync_failure(str(exc))
        _set_runtime(
            state="error",
            message=str(exc),
            last_error=str(exc),
            finished_at=time.time(),
        )
        _clear_desired_after_job()
    finally:
        with _runner_lock:
            _process = None
        shutil.rmtree(work_dir, ignore_errors=True)


def start_sync(
    db_path: str,
    *,
    config: dict[str, Any] | None = None,
    manual_override: bool = False,
) -> dict[str, Any]:
    """Start a vault sync. One at a time. Raises ValueError / RuntimeError.

    ``manual_override=True`` (explicit POST /api/backup/cloud/start) runs even
    when preview backfill holds the disk. Scheduled / auto-resume starts wait.
    """
    global _worker_thread, _stop_requested
    if not rclone_available():
        raise RuntimeError("rclone is not installed — Cloud Backup is unavailable")
    with _runner_lock:
        if _runtime.get("state") in {"running", "waiting", "stopping"} or (
            _worker_thread is not None and _worker_thread.is_alive()
        ):
            raise RuntimeError("Cloud Backup is already running")
        resolved = validate_config(
            config or config_from_settings(),
            require_trees=True,
        )
        bulk_scheduler.set_vault_desired(True)
        decision = bulk_scheduler.decide_vault_start(manual_override=manual_override)
        wait_for_previews = decision["action"] == "wait"
        override_warning = decision["message"] if decision["action"] == "run" else ""
        if wait_for_previews:
            initial_state = "waiting"
            initial_message = bulk_scheduler.VAULT_WAIT_MESSAGE
        else:
            initial_state = "running"
            initial_message = override_warning or "Starting Cloud Backup"
        _stop_requested = False
        _runtime.update(
            {
                "state": initial_state,
                "message": initial_message,
                "current_tree": None if wait_for_previews else (
                    resolved["trees"][0] if resolved["trees"] else None
                ),
                "bytes_done": 0,
                "bytes_total": 0,
                "pct": 0.0,
                "speed": "",
                "eta": "",
                "started_at": time.time(),
                "finished_at": None,
                "last_error": None,
                "manual_override": bool(manual_override),
            }
        )
        if wait_for_previews:
            log.info("cloud_backup yielding disk to preview build")
        elif override_warning:
            log.warning("cloud_backup manual override while previews pending")
        thread = threading.Thread(
            target=_run_sync_job,
            args=(db_path, resolved),
            kwargs={
                "wait_for_previews": wait_for_previews,
                "override_warning": override_warning,
            },
            name="cloud-backup-runner",
            daemon=True,
        )
        _worker_thread = thread
        thread.start()
    return status_payload(db_path=db_path)


def stop_sync() -> dict[str, Any]:
    """Request pause/stop of the active sync (or waiting yield)."""
    global _stop_requested
    bulk_scheduler.set_vault_desired(False)
    with _runner_lock:
        alive = _worker_thread is not None and _worker_thread.is_alive()
        if not alive and _runtime.get("state") not in {"running", "waiting", "stopping"}:
            # Idle stop: clear sticky failure so health is not permanently bad
            # after an old SIGTERM / interrupted run.
            if _runtime.get("last_error") or load_persisted_status().get("last_error"):
                _set_runtime(last_error=None, state="idle", message="Cloud Backup idle")
                try:
                    current = load_persisted_status()
                    current["last_error"] = None
                    current["last_error_at"] = None
                    _write_persisted_status(current)
                except Exception:
                    log.debug("cloud_backup failed to clear sticky error on idle stop", exc_info=True)
            return status_payload()
        _stop_requested = True
        _runtime["state"] = "stopping"
        _runtime["message"] = "Pausing Cloud Backup"
        proc = _process
    _terminate_process(proc)
    return status_payload()


def reset_runner_for_tests() -> None:
    """Test helper: clear runner state between cases."""
    global _process, _worker_thread, _stop_requested, _scheduler_started
    _stop_requested = True
    _terminate_process(_process)
    with _runner_lock:
        _process = None
        _worker_thread = None
        _stop_requested = False
        _runtime.clear()
        _runtime.update(
            {
                "state": "idle",
                "message": "",
                "current_tree": None,
                "bytes_done": 0,
                "bytes_total": 0,
                "pct": 0.0,
                "speed": "",
                "eta": "",
                "started_at": None,
                "finished_at": None,
                "last_error": None,
                "manual_override": False,
            }
        )
    _scheduler_started = False


async def run_nightly_scheduler(db_path_provider: DbPathProvider, *, hour: int = 2) -> None:
    """Background daemon: when nightly is enabled, sync at local ``hour:00``."""
    import asyncio

    global _scheduler_started
    if _scheduler_started:
        log.info("cloud_backup scheduler already running; skip duplicate start")
        return
    _scheduler_started = True
    log.info("cloud_backup scheduler armed for nightly %02d:00 local", hour)
    while True:
        delay = seconds_until_local_hour(hour)
        log.info("cloud_backup scheduler sleeping %.0fs until next %02d:00", delay, hour)
        await asyncio.sleep(delay)
        try:
            config = config_from_settings()
            if not config.get("nightly_enabled"):
                log.info("cloud_backup nightly disabled; skip")
                continue
            if not rclone_available():
                log.info("cloud_backup nightly skipped — rclone unavailable")
                continue
            db_path = db_path_provider() if callable(db_path_provider) else db_path_provider
            start_sync(db_path, config=config)
        except asyncio.CancelledError:
            log.info("cloud_backup scheduler cancelled")
            raise
        except Exception:
            log.exception("cloud_backup nightly sync failed to start")
