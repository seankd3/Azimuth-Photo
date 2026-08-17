"""System Health aggregation — compose existing signals, do not re-probe.

Cheap enough for a 30s poll. Optional subsystems (cloud vault) report
"Not configured" instead of bad when absent.
"""

from __future__ import annotations

from core.catalog_path import catalog_path

import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import work
from core import memory_pressure
from core.runtime_paths import resolve_runtime_paths
from data import connection
from features.system import backups

log = logging.getLogger(__name__)

Status = str  # ok | warn | bad

BACKUP_OK_HOURS = 36
BACKUP_WARN_HOURS = 168
VAULT_OK_DAYS = 7
VAULT_WARN_DAYS = 30
DISK_OK_FREE_PCT = 15.0
DISK_WARN_FREE_PCT = 5.0
DISK_OK_FREE_BYTES = 5 * 1024**3
DISK_WARN_FREE_BYTES = 1 * 1024**3





def _now() -> float:
    return time.time()


def _check(
    *,
    id: str,
    label: str,
    status: Status,
    detail: str,
    checked_at: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": id,
        "label": label,
        "status": status,
        "detail": detail,
        "checked_at": float(checked_at if checked_at is not None else _now()),
    }
    payload.update(extra)
    return payload


def _parse_when(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        # Heuristic: ms timestamps are rare here; treat large values as ms.
        if number > 1e12:
            number /= 1000.0
        return number if number > 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
        if number > 1e12:
            number /= 1000.0
        return number if number > 0 else None
    except ValueError:
        pass
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    except ValueError:
        return None


def _age_phrase(seconds: float | None) -> str:
    if seconds is None:
        return "unknown age"
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        mins = int(seconds // 60)
        return f"{mins}m ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours}h ago"
    days = int(seconds // 86400)
    return f"{days}d ago"


def _bytes_phrase(value: int | None) -> str:
    size = float(value or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024
        unit += 1
    if unit == 0:
        return f"{int(size)} {units[unit]}"
    return f"{size:.1f} {units[unit]}" if size < 10 else f"{int(round(size))} {units[unit]}"


def _worst(statuses: list[Status]) -> Status:
    if "bad" in statuses:
        return "bad"
    if "warn" in statuses:
        return "warn"
    return "ok"


def _disk_check(*, id: str, label: str, root: str | Path) -> dict[str, Any]:
    checked_at = _now()
    path = Path(root)
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        return _check(
            id=id,
            label=label,
            status="warn",
            detail=f"Could not read free space ({exc})",
            checked_at=checked_at,
            path=str(path),
        )
    free = int(usage.free)
    total = int(usage.total) or 1
    free_pct = (free / total) * 100.0
    detail = f"{_bytes_phrase(free)} free ({free_pct:.0f}%) on {path.name or path}"
    if free >= DISK_OK_FREE_BYTES and free_pct >= DISK_OK_FREE_PCT:
        status: Status = "ok"
    elif free >= DISK_WARN_FREE_BYTES and free_pct >= DISK_WARN_FREE_PCT:
        status = "warn"
    else:
        status = "bad"
        detail = f"Low space — {detail}"
    return _check(
        id=id,
        label=label,
        status=status,
        detail=detail,
        checked_at=checked_at,
        path=str(path),
        free_bytes=free,
        total_bytes=int(usage.total),
        free_pct=round(free_pct, 1),
    )


def check_catalog_db(db_path: str) -> dict[str, Any]:
    """Use the cached startup quick_check — do not force a fresh PRAGMA."""
    health = backups.catalog_health(db_path)
    checked_at = float(health.get("checked_at") or _now())
    age = _now() - checked_at
    state = str(health.get("state") or "")
    if state == "corrupt" or health.get("ok") is False:
        return _check(
            id="catalog_db",
            label="Catalog database",
            status="bad",
            detail=str(health.get("error") or "Catalog failed quick_check"),
            checked_at=checked_at,
            age_seconds=age,
        )
    if state == "missing":
        return _check(
            id="catalog_db",
            label="Catalog database",
            status="warn",
            detail="Catalog file is not present yet",
            checked_at=checked_at,
            age_seconds=age,
        )
    return _check(
        id="catalog_db",
        label="Catalog database",
        status="ok",
        detail=f"quick_check ok · checked {_age_phrase(age)}",
        checked_at=checked_at,
        age_seconds=age,
    )


def check_catalog_backup(db_path: str) -> dict[str, Any]:
    checked_at = _now()
    run = backups.backup_run_status()
    items = backups.list_backups(db_path)
    last_error = run.get("last_error")
    if last_error:
        when = _parse_when(run.get("last_error_at"))
        return _check(
            id="catalog_backup",
            label="Catalog backup",
            status="bad",
            detail=f"Last backup failed: {last_error}",
            checked_at=checked_at,
            verified=False,
            age_seconds=(checked_at - when) if when else None,
        )
    if not items:
        return _check(
            id="catalog_backup",
            label="Catalog backup",
            status="bad",
            detail="No catalog snapshots yet",
            checked_at=checked_at,
            verified=False,
        )
    newest = items[0]
    created = _parse_when(newest.get("created_at")) or _parse_when(run.get("last_ok_at"))
    age = (checked_at - created) if created else None
    # Published snapshots always pass verification before seal.
    verified = True
    age_hours = (age / 3600.0) if age is not None else None
    if age_hours is None:
        status: Status = "warn"
        detail = "Newest snapshot date is unavailable"
    elif age_hours <= BACKUP_OK_HOURS:
        status = "ok"
        detail = f"Verified snapshot {_age_phrase(age)}"
    elif age_hours <= BACKUP_WARN_HOURS:
        status = "warn"
        detail = f"Backup due · last verified {_age_phrase(age)}"
    else:
        status = "bad"
        detail = f"Needs attention · last verified {_age_phrase(age)}"
    return _check(
        id="catalog_backup",
        label="Catalog backup",
        status=status,
        detail=detail,
        checked_at=checked_at,
        verified=verified,
        age_seconds=age,
        name=newest.get("name"),
    )


def _load_cloud_status() -> dict[str, Any] | None:
    """Return opsvault status payload, or None when the feature is absent."""
    try:
        from features.backup import cloud as cloud_backup
    except ImportError:
        return None
    try:
        return cloud_backup.status_payload()
    except Exception as exc:
        log.debug("cloud vault status unavailable: %s", exc, exc_info=True)
        return None


def check_cloud_vault() -> dict[str, Any]:
    checked_at = _now()
    payload = _load_cloud_status()
    if payload is None:
        return _check(
            id="cloud_vault",
            label="Cloud vault",
            status="ok",
            detail="Not configured",
            checked_at=checked_at,
            configured=False,
        )

    config = payload.get("config") or {}
    remote = str(config.get("remote") or "").strip()
    if not remote:
        return _check(
            id="cloud_vault",
            label="Cloud vault",
            status="ok",
            detail="Not configured",
            checked_at=checked_at,
            configured=False,
        )

    state = str(payload.get("state") or "idle")
    last_error = payload.get("last_error")
    last_ok = _parse_when(payload.get("last_ok_at"))
    age = (checked_at - last_ok) if last_ok else None

    if state == "error" or last_error:
        return _check(
            id="cloud_vault",
            label="Cloud vault",
            status="bad",
            detail=str(last_error or payload.get("message") or "Cloud vault sync failed"),
            checked_at=checked_at,
            configured=True,
            age_seconds=age,
        )
    if state == "running":
        return _check(
            id="cloud_vault",
            label="Cloud vault",
            status="ok",
            detail=str(payload.get("message") or "Sync in progress"),
            checked_at=checked_at,
            configured=True,
            age_seconds=age,
        )
    if age is None:
        return _check(
            id="cloud_vault",
            label="Cloud vault",
            status="warn",
            detail=f"Configured ({remote}) · never completed a sync",
            checked_at=checked_at,
            configured=True,
        )
    age_days = age / 86400.0
    if age_days <= VAULT_OK_DAYS:
        status: Status = "ok"
        detail = f"Last sync {_age_phrase(age)}"
    elif age_days <= VAULT_WARN_DAYS:
        status = "warn"
        detail = f"Vault getting stale · last sync {_age_phrase(age)}"
    else:
        status = "bad"
        detail = f"Vault stale · last sync {_age_phrase(age)}"
    return _check(
        id="cloud_vault",
        label="Cloud vault",
        status=status,
        detail=detail,
        checked_at=checked_at,
        configured=True,
        age_seconds=age,
    )


def check_disk_library() -> dict[str, Any]:
    try:
        from features.imports import staging

        root = staging.originals_root()
    except Exception:
        root = Path(resolve_runtime_paths().data_dir) / "library"
    return _disk_check(id="disk_library", label="Library disk", root=root)


def check_disk_cache() -> dict[str, Any]:
    root = resolve_runtime_paths().thumb_cache_dir
    return _disk_check(id="disk_cache", label="Cache disk", root=root)


def check_memory() -> dict[str, Any]:
    """What this process holds, and how many chore workers that allows.

    This read a soft/hard watermark verdict and told the owner "bulk work
    paused". Nothing had paused bulk work on memory for as long as
    `gate_bulk_work` had had no callers — the panel was reporting a mechanism
    that no longer existed. A health check that describes machinery instead of
    behaviour is worse than no check, because it is believed.

    What is true is that `work.workers()` sizes the chore pool from the same
    memory, so that is the number shown.
    """

    checked_at = _now()
    reading = memory_pressure.read_memory()
    available = memory_pressure.read_host_available_bytes()
    slots = work.workers()
    workers = f"{slots} chore worker{'s' if slots != 1 else ''}"
    if reading is None:
        held = "Usage unreadable on this platform"
    else:
        held = (
            f"{_bytes_phrase(reading.pressure_bytes)} in use "
            f"({reading.source}: RSS {_bytes_phrase(reading.rss_bytes)}"
            f" + swap {_bytes_phrase(reading.swap_bytes)})"
        )
    free = f"{_bytes_phrase(available)} free on this machine" if available is not None else ""
    return _check(
        id="memory",
        label="Memory",
        status="ok",
        detail=" · ".join(part for part in (held, free, workers) if part),
        checked_at=checked_at,
        rss_bytes=reading.rss_bytes if reading else None,
        swap_bytes=reading.swap_bytes if reading else None,
        pressure_bytes=reading.pressure_bytes if reading else None,
        host_available_bytes=available,
        chore_workers=slots,
        signal=reading.source if reading else "unreadable",
    )


def check_background_work(db_path: str) -> dict[str, Any]:
    """What the one chore loop owes, and whether it is allowed to run.

    Two checks stood here. `check_pregen` read `thumbnails._pregen_status` and
    `check_workers` polled `face_worker`, `caption_worker` and
    `embedding_worker` — four modules deleted in 6fc7e31c, each ImportError
    caught and rendered as "unavailable" or "Pregen status unavailable". The
    panel had been describing four subsystems that do not exist, in a warning
    tone, since the day they went.

    There is one loop now, and its queue is a query, so its status is a count.
    """

    checked_at = _now()
    try:
        conn = connection.reading(db_path)
        paused = work.paused(conn)
        owed = work.debt(conn)
    except Exception as exc:
        return _check(
            id="background_work",
            label="Background work",
            status="warn",
            detail=f"Could not read what is owed ({exc})",
            checked_at=checked_at,
        )
    outstanding = {kind: count for kind, count in sorted(owed.items()) if count}
    total = sum(outstanding.values())
    if paused:
        detail = f"Paused · {total:,} owed" if total else "Paused · nothing owed"
    elif not total:
        detail = "Up to date"
    else:
        detail = " · ".join(f"{kind} {count:,}" for kind, count in outstanding.items())
    return _check(
        id="background_work",
        label="Background work",
        status="ok",
        detail=detail,
        checked_at=checked_at,
        paused=paused,
        owed=outstanding,
    )


def check_activity(db_path: str) -> dict[str, Any]:
    checked_at = _now()
    bits: list[str] = []
    status: Status = "ok"
    batches = _recent_imports_sync(db_path)

    if batches:
        batch = batches[0]
        when = _parse_when(batch.get("completed_at") or batch.get("started_at") or batch.get("created_at"))
        age = (checked_at - when) if when else None
        name = str(batch.get("name") or f"Import #{batch.get('id')}")
        batch_status = str(batch.get("status") or "")
        if batch_status == "failed":
            status = "warn"
            bits.append(f"Last import failed · {name}")
        else:
            bits.append(f"Last import {name} · {_age_phrase(age)}")
    else:
        bits.append("No imports yet")

    return _check(
        id="activity",
        label="Import activity",
        status=status,
        detail=" · ".join(bits),
        checked_at=checked_at,
    )


def _recent_imports_sync(db_path: str) -> list[dict[str, Any]]:
    """Cheap sync read so health aggregation stays thread-friendly."""
    import sqlite3

    if not os.path.isfile(db_path):
        return []
    try:
        conn = sqlite3.connect(f"{Path(db_path).as_uri()}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        try:
            rows = conn.execute(
                "SELECT id, name, status, created_at AS started_at, created_at, completed_at "
                "FROM import_batches ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchall()
        except sqlite3.Error:
            return []
        return [dict(row) for row in rows]
    finally:
        conn.close()


def collect_health(*, db_path: str | None = None) -> dict[str, Any]:
    """Compose all health checks into one owner-facing payload."""
    path = db_path or catalog_path()
    checked_at = _now()
    checks = [
        check_catalog_db(path),
        check_catalog_backup(path),
        check_cloud_vault(),
        check_disk_library(),
        check_disk_cache(),
        check_memory(),
        check_background_work(path),
        check_activity(path),
    ]
    overall = _worst([str(item.get("status") or "ok") for item in checks])
    return {
        "overall": overall,
        "checked_at": checked_at,
        "checks": checks,
    }
