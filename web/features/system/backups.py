"""Catalog time-machine snapshots and original-file integrity audits.

Snapshots use SQLite's online BACKUP API (safe against a live WAL db), then
gzip-compress to the selected backup root. Restore never hot-swaps the live catalog —
it writes ``photoarchive.restored.db`` beside it and returns human instructions.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.runtime_paths import resolve_runtime_paths
from data import connection as data_connection

log = logging.getLogger(__name__)

BACKUP_NAME_RE = re.compile(r"^photoarchive-(\d{8})-(\d{6})(?:-([a-z0-9]+))?\.db\.gz$")
DAILY_KEEP = 7
WEEKLY_KEEP = 4
# Pre-migration snapshots are the rollback safety net for a schema upgrade; they
# are always retained (newest PREMIGRATE_KEEP) regardless of the daily/weekly window.
PREMIGRATE_LABEL = "premigrate"
PREMIGRATE_KEEP = 5
INTEGRITY_SLEEP_SECONDS = 0.05
CHECKSUM_CHUNK = 1024 * 1024
RESTORE_REQUIRED_TABLES = frozenset({"images", "catalog_sources"})


class RestoreStageExistsError(RuntimeError):
    """A prepared restore already exists beside the live catalog."""


class RestoreValidationError(RuntimeError):
    """The selected backup is not a valid Azimuth Photo catalog."""


class RestoreStorageError(RuntimeError):
    """The restore could not be staged because local storage failed."""

_IMAGE_CHECKSUMS_DDL = """
CREATE TABLE IF NOT EXISTS image_checksums (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    checked_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_image_checksums_checked
ON image_checksums(checked_at);
"""

_backup_lock = threading.Lock()
_integrity_lock = threading.Lock()
_integrity_state: dict[str, Any] = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "limit": None,
    "checked": 0,
    "recorded": 0,
    "mismatches": 0,
    "skipped_offline": 0,
    "skipped_missing": 0,
    "errors": 0,
    "current_image_id": None,
    "mismatch_ids": [],
    "last_error": None,
}
_scheduler_started = False
_catalog_health_lock = threading.Lock()
_catalog_health: dict[str, dict[str, Any]] = {}


def backup_root() -> Path:
    """Return the selected backup root without relocating old snapshots."""
    root = Path(resolve_runtime_paths().backup_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _timestamp_name(when: datetime | None = None, label: str | None = None) -> str:
    moment = when or datetime.now().astimezone()
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    if label:
        return f"photoarchive-{stamp}-{label}.db.gz"
    return f"photoarchive-{stamp}.db.gz"


def _parse_backup_name(name: str) -> datetime | None:
    match = BACKUP_NAME_RE.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(f"{match.group(1)}{match.group(2)}", "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _sqlite_backup_to_path(source_db: str, dest_db: str) -> None:
    """Copy a live SQLite database via the BACKUP API (not a file copy)."""
    src = data_connection.open_sync(source_db, timeout=60.0)
    try:
        dst = data_connection.open_sync(dest_db, timeout=60.0)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            data_connection.close_sync(dst, db_path=dest_db)
    finally:
        data_connection.close_sync(src, db_path=source_db)


def catalog_quick_check(db_path: str) -> dict[str, Any]:
    """Read-only SQLite health check used before catalog startup work begins."""
    path = os.path.abspath(db_path)
    checked_at = time.time()
    if not os.path.exists(path):
        result = {"ok": True, "state": "missing", "checked_at": checked_at}
    else:
        try:
            conn = sqlite3.connect(f"{Path(path).as_uri()}?mode=ro", uri=True, timeout=30.0)
            try:
                row = conn.execute("PRAGMA quick_check").fetchone()
            finally:
                conn.close()
            if not row or str(row[0]).lower() != "ok":
                result = {
                    "ok": False,
                    "state": "corrupt",
                    "checked_at": checked_at,
                    "error": str(row[0]) if row else "SQLite quick_check returned no result",
                }
            else:
                result = {"ok": True, "state": "ok", "checked_at": checked_at}
        except sqlite3.Error as exc:
            result = {"ok": False, "state": "corrupt", "checked_at": checked_at, "error": str(exc)}
    with _catalog_health_lock:
        _catalog_health[path] = result
    return dict(result)


def catalog_health(db_path: str) -> dict[str, Any]:
    """Return the startup check result, checking lazily for status-only callers."""
    path = os.path.abspath(db_path)
    with _catalog_health_lock:
        result = _catalog_health.get(path)
    return dict(result) if result is not None else catalog_quick_check(path)


def create_snapshot(
    db_path: str, *, when: datetime | None = None, label: str | None = None
) -> dict[str, Any]:
    """Snapshot ``db_path`` to a gzipped backup and apply retention.

    ``label`` tags the snapshot (e.g. ``premigrate``); labeled pre-migration
    snapshots are protected from routine pruning by :func:`apply_retention`.
    """
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"Catalog database not found: {db_path}")

    root = backup_root()
    name = _timestamp_name(when, label)
    final_path = root / name
    tmp_db = root / f".{name}.tmp.db"
    tmp_gz = root / f".{name}.tmp.gz"

    with _backup_lock:
        try:
            if tmp_db.exists():
                tmp_db.unlink()
            if tmp_gz.exists():
                tmp_gz.unlink()

            log.info("catalog_backup start db=%s -> %s", db_path, final_path)
            _sqlite_backup_to_path(db_path, str(tmp_db))

            with open(tmp_db, "rb") as raw, gzip.open(tmp_gz, "wb", compresslevel=6) as gz:
                shutil.copyfileobj(raw, gz, length=1024 * 1024)

            os.replace(tmp_gz, final_path)
            size = final_path.stat().st_size
            pruned = apply_retention(root)
            log.info(
                "catalog_backup done name=%s bytes=%s pruned=%s",
                name,
                size,
                pruned,
            )
            return {
                "ok": True,
                "name": name,
                "path": str(final_path),
                "bytes": size,
                "created_at": (when or datetime.now().astimezone()).isoformat(),
                "label": label,
                "pruned": pruned,
            }
        finally:
            for leftover in (tmp_db, tmp_gz):
                try:
                    if leftover.exists():
                        leftover.unlink()
                except OSError:
                    pass


def backup_before_migration(
    db_path: str, from_version: int, to_version: int
) -> dict[str, Any]:
    """Snapshot an existing catalog immediately before a schema migration.

    Reuses the time-machine snapshot engine with a protected ``premigrate``
    label so the pre-upgrade catalog is always restorable if the new schema
    misbehaves. Failures propagate so startup cannot run a destructive
    migration without a verified snapshot.
    """
    try:
        result = create_snapshot(db_path, label=PREMIGRATE_LABEL)
        log.warning(
            "catalog_backup premigration from=v%s to=v%s -> %s",
            from_version,
            to_version,
            result["name"],
        )
        return result
    except Exception:
        log.exception(
            "catalog_backup premigration FAILED from=v%s to=v%s db=%s — migration refused",
            from_version,
            to_version,
            db_path,
        )
        raise


def list_backups() -> list[dict[str, Any]]:
    root = backup_root()
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("photoarchive-*.db.gz"), reverse=True):
        parsed = _parse_backup_name(path.name)
        if parsed is None:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "bytes": size,
                "created_at": parsed.isoformat(),
            }
        )
    return items


def apply_retention(root: Path | None = None, *, now: date | None = None) -> list[str]:
    """Keep 7 daily + 4 weekly snapshots; delete the rest. Returns pruned names."""
    root = root or backup_root()
    backups: list[tuple[datetime, Path]] = []
    for path in root.glob("photoarchive-*.db.gz"):
        parsed = _parse_backup_name(path.name)
        if parsed is not None:
            backups.append((parsed, path))
    backups.sort(key=lambda item: item[0], reverse=True)
    if not backups:
        return []

    today = now or date.today()
    keep: set[Path] = set()

    # Always retain the most recent pre-migration snapshots. They are the
    # rollback safety net for a schema upgrade and must survive routine pruning.
    premigrate = [
        (when, path)
        for when, path in backups
        if (match := BACKUP_NAME_RE.match(path.name)) and match.group(3) == PREMIGRATE_LABEL
    ]
    for _when, path in premigrate[:PREMIGRATE_KEEP]:
        keep.add(path)

    # Newest backup per calendar day for the last 7 days.
    daily_seen: set[date] = set()
    for when, path in backups:
        day = when.date()
        if day < today - timedelta(days=DAILY_KEEP - 1):
            continue
        if day in daily_seen:
            continue
        daily_seen.add(day)
        keep.add(path)

    # Newest backup per ISO week for the last 4 weeks.
    weekly_seen: set[tuple[int, int]] = set()
    for when, path in backups:
        iso = when.isocalendar()
        week_key = (iso.year, iso.week)
        if week_key in weekly_seen:
            continue
        # Only count weeks that fall within the weekly retention window.
        week_start = date.fromisocalendar(iso.year, iso.week, 1)
        if week_start < today - timedelta(weeks=WEEKLY_KEEP):
            continue
        weekly_seen.add(week_key)
        keep.add(path)
        if len(weekly_seen) >= WEEKLY_KEEP:
            break

    pruned: list[str] = []
    for when, path in backups:
        if path in keep:
            continue
        try:
            path.unlink()
            pruned.append(path.name)
            log.info("catalog_backup pruned name=%s", path.name)
        except OSError as exc:
            log.warning("catalog_backup prune failed name=%s err=%s", path.name, exc)
    return pruned


def _restore_paths(db_path: str) -> tuple[Path, Path, Path, Path, Path]:
    live = Path(db_path).resolve()
    staging = live.with_name("photoarchive.restored.db")
    metadata = live.with_name("photoarchive.restored.json")
    tmp = live.with_name(".photoarchive.restored.db.tmp")
    metadata_tmp = live.with_name(".photoarchive.restored.json.tmp")
    return live, staging, metadata, tmp, metadata_tmp


def _validate_restored_catalog(path: Path) -> None:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
        try:
            quick_check = conn.execute("PRAGMA quick_check").fetchone()
            if not quick_check or str(quick_check[0]).lower() != "ok":
                raise RestoreValidationError("Backup catalog failed SQLite integrity validation")
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            conn.close()
    except RestoreValidationError:
        raise
    except sqlite3.Error as exc:
        raise RestoreValidationError("Backup is not a readable SQLite catalog") from exc
    missing = sorted(RESTORE_REQUIRED_TABLES - tables)
    if missing:
        raise RestoreValidationError(f"Backup is missing required catalog tables: {', '.join(missing)}")


def restore_status(db_path: str) -> dict[str, Any]:
    """Describe a prepared restore without mutating either catalog."""
    _live, staging, metadata, _tmp, _metadata_tmp = _restore_paths(db_path)
    payload: dict[str, Any] = {
        "prepared": staging.is_file(),
        "staging_path": str(staging),
        "bytes": 0,
        "name": None,
        "created_at": None,
        "prepared_at": None,
    }
    if staging.is_file():
        try:
            payload["bytes"] = staging.stat().st_size
        except OSError:
            pass
    if metadata.is_file():
        try:
            stored = json.loads(metadata.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for key in ("name", "created_at", "prepared_at"):
                    payload[key] = stored.get(key)
        except (OSError, ValueError, TypeError):
            payload["metadata_unavailable"] = True
    payload["artifacts_present"] = bool(staging.exists() or metadata.exists())
    return payload


def discard_staged_restore(db_path: str) -> dict[str, Any]:
    """Remove only the reproducible staged catalog and its metadata."""
    _live, staging, metadata, tmp, metadata_tmp = _restore_paths(db_path)
    removed: list[str] = []
    with _backup_lock:
        for path in (staging, metadata, tmp, metadata_tmp):
            try:
                if path.exists():
                    path.unlink()
                    removed.append(path.name)
            except OSError as exc:
                raise RestoreStorageError("Could not discard the prepared restore; check file permissions") from exc
    return {"ok": True, "discarded": bool(removed), "removed": removed, **restore_status(db_path)}


def restore_backup(db_path: str, name: str) -> dict[str, Any]:
    """Validate and stage a named backup beside the live db; never replace it."""
    if not BACKUP_NAME_RE.match(name):
        raise ValueError(f"Invalid backup name: {name}")
    source = backup_root() / name
    if not source.is_file():
        raise FileNotFoundError(f"Backup not found: {name}")

    live, staging, metadata, tmp, metadata_tmp = _restore_paths(db_path)
    parsed = _parse_backup_name(name)
    prepared_at = datetime.now().astimezone().isoformat()
    meta_payload = {
        "name": name,
        "created_at": parsed.isoformat() if parsed else None,
        "prepared_at": prepared_at,
    }

    with _backup_lock:
        if staging.exists() or metadata.exists():
            raise RestoreStageExistsError("A restore is already prepared; discard it before preparing another")
        for leftover in (tmp, metadata_tmp):
            try:
                if leftover.exists():
                    leftover.unlink()
            except OSError as exc:
                raise RestoreStorageError("Could not clear an incomplete restore; check file permissions") from exc
        try:
            with gzip.open(source, "rb") as gz, open(tmp, "wb") as out:
                shutil.copyfileobj(gz, out, length=1024 * 1024)
            _validate_restored_catalog(tmp)
            size = tmp.stat().st_size
            metadata_tmp.write_text(json.dumps(meta_payload, indent=2) + "\n", encoding="utf-8")
            os.replace(metadata_tmp, metadata)
            try:
                os.replace(tmp, staging)
            except OSError:
                metadata.unlink(missing_ok=True)
                raise
        except (gzip.BadGzipFile, EOFError, RestoreValidationError) as exc:
            if isinstance(exc, RestoreValidationError):
                raise
            raise RestoreValidationError("Backup archive is corrupt or incomplete") from exc
        except OSError as exc:
            raise RestoreStorageError("Could not prepare the restore; check free space and file permissions") from exc
        finally:
            for leftover in (tmp, metadata_tmp):
                try:
                    if leftover.exists():
                        leftover.unlink()
                except OSError:
                    pass

    instructions = (
        f"Prepared backup '{name}' at '{staging}'. "
        "The live catalog was NOT replaced. A desktop app can apply this prepared restore after restart."
    )
    log.info("catalog_backup restore staged name=%s staging=%s", name, staging)
    return {
        "ok": True,
        "name": name,
        "staging_path": str(staging),
        "live_path": str(live),
        "bytes": size,
        "created_at": meta_payload["created_at"],
        "prepared_at": prepared_at,
        "instructions": instructions,
        "hot_swapped": False,
    }


def seconds_until_local_hour(hour: int = 4, *, now: datetime | None = None) -> float:
    """Seconds until the next local ``hour:00`` (default 04:00)."""
    moment = now or datetime.now().astimezone()
    target = moment.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= moment:
        target += timedelta(days=1)
    return max(1.0, (target - moment).total_seconds())


async def run_daily_backup_scheduler(db_path_provider, *, hour: int = 4) -> None:
    """Background daemon: sleep until 04:00 local, snapshot, repeat."""
    global _scheduler_started
    if _scheduler_started:
        log.info("catalog_backup scheduler already running; skip duplicate start")
        return
    _scheduler_started = True
    log.info("catalog_backup scheduler armed for daily %02d:00 local", hour)
    while True:
        delay = seconds_until_local_hour(hour)
        log.info("catalog_backup scheduler sleeping %.0fs until next %02d:00", delay, hour)
        await asyncio.sleep(delay)
        try:
            db_path = db_path_provider() if callable(db_path_provider) else db_path_provider
            result = await asyncio.to_thread(create_snapshot, db_path)
            log.info(
                "catalog_backup scheduled snapshot ok name=%s bytes=%s",
                result.get("name"),
                result.get("bytes"),
            )
        except asyncio.CancelledError:
            log.info("catalog_backup scheduler cancelled")
            raise
        except Exception:
            log.exception("catalog_backup scheduled snapshot failed")


def ensure_checksums_table(conn: sqlite3.Connection) -> None:
    conn.executescript(_IMAGE_CHECKSUMS_DDL)
    conn.commit()


def integrity_status() -> dict[str, Any]:
    with _integrity_lock:
        return dict(_integrity_state)


def _set_integrity(**kwargs: Any) -> None:
    with _integrity_lock:
        _integrity_state.update(kwargs)


def _sha256_file(path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHECKSUM_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def _select_integrity_candidates(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    ensure_checksums_table(conn)
    cursor = conn.execute(
        """
        SELECT
            i.id AS image_id,
            i.filepath AS filepath,
            COALESCE(s.online, 1) AS source_online,
            c.sha256 AS prior_sha256,
            c.bytes AS prior_bytes,
            c.checked_at AS checked_at
        FROM images i
        LEFT JOIN catalog_sources s ON s.id = i.source_id
        LEFT JOIN image_checksums c ON c.image_id = i.id
        WHERE i.missing_at IS NULL
          AND COALESCE(i.status, 'kept') != 'trashed'
        ORDER BY
            CASE WHEN c.checked_at IS NULL THEN 0 ELSE 1 END ASC,
            c.checked_at ASC,
            i.id ASC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    )
    return list(cursor.fetchall())


def run_integrity_scan(db_path: str, *, limit: int = 50, sleep_seconds: float = INTEGRITY_SLEEP_SECONDS) -> dict[str, Any]:
    """Checksum unchecksummed / oldest-checked originals. Skip offline sources."""
    with _integrity_lock:
        if _integrity_state["state"] == "running":
            return dict(_integrity_state)

    started = time.time()
    _set_integrity(
        state="running",
        started_at=started,
        finished_at=None,
        limit=limit,
        checked=0,
        recorded=0,
        mismatches=0,
        skipped_offline=0,
        skipped_missing=0,
        errors=0,
        current_image_id=None,
        mismatch_ids=[],
        last_error=None,
    )

    mismatch_ids: list[int] = []
    checked = recorded = mismatches = skipped_offline = skipped_missing = errors = 0

    try:
        conn = sqlite3.connect(db_path, timeout=60.0)
        conn.row_factory = sqlite3.Row
        try:
            ensure_checksums_table(conn)
            candidates = _select_integrity_candidates(conn, limit)
            for row in candidates:
                image_id = int(row["image_id"])
                _set_integrity(current_image_id=image_id)
                if not int(row["source_online"] or 0):
                    skipped_offline += 1
                    _set_integrity(skipped_offline=skipped_offline)
                    continue

                filepath = row["filepath"]
                if not filepath or not os.path.isfile(filepath):
                    skipped_missing += 1
                    _set_integrity(skipped_missing=skipped_missing)
                    continue

                try:
                    sha256, size = _sha256_file(filepath)
                except OSError as exc:
                    errors += 1
                    _set_integrity(errors=errors, last_error=str(exc))
                    log.warning("integrity read failed image_id=%s err=%s", image_id, exc)
                    continue

                prior = row["prior_sha256"]
                if prior and prior != sha256:
                    mismatches += 1
                    mismatch_ids.append(image_id)
                    log.error(
                        "integrity BIT ROT image_id=%s path=%s prior=%s now=%s",
                        image_id,
                        filepath,
                        prior,
                        sha256,
                    )

                conn.execute(
                    """
                    INSERT INTO image_checksums (image_id, sha256, bytes, checked_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(image_id) DO UPDATE SET
                        sha256 = excluded.sha256,
                        bytes = excluded.bytes,
                        checked_at = excluded.checked_at
                    """,
                    (image_id, sha256, size, time.time()),
                )
                conn.commit()
                checked += 1
                recorded += 1
                _set_integrity(
                    checked=checked,
                    recorded=recorded,
                    mismatches=mismatches,
                    mismatch_ids=list(mismatch_ids),
                )
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
        finally:
            conn.close()
    except Exception as exc:
        _set_integrity(state="error", finished_at=time.time(), last_error=str(exc))
        log.exception("integrity scan failed")
        return integrity_status()

    finished = time.time()
    _set_integrity(
        state="idle",
        finished_at=finished,
        current_image_id=None,
        checked=checked,
        recorded=recorded,
        mismatches=mismatches,
        skipped_offline=skipped_offline,
        skipped_missing=skipped_missing,
        errors=errors,
        mismatch_ids=list(mismatch_ids),
    )
    log.info(
        "integrity scan done checked=%s mismatches=%s offline=%s missing=%s errors=%s",
        checked,
        mismatches,
        skipped_offline,
        skipped_missing,
        errors,
    )
    return integrity_status()


def begin_integrity_scan(db_path: str, *, limit: int = 50) -> dict[str, Any]:
    """Start an integrity scan on a daemon thread if idle."""
    with _integrity_lock:
        if _integrity_state["state"] == "running":
            return {"ok": False, "error": "Integrity scan already running", "status": dict(_integrity_state)}

    thread = threading.Thread(
        target=run_integrity_scan,
        kwargs={"db_path": db_path, "limit": limit},
        name="integrity-scan",
        daemon=True,
    )
    thread.start()
    # Brief yield so status flips to running for immediate GET callers.
    time.sleep(0.01)
    return {"ok": True, "status": integrity_status()}


def integrity_summary(db_path: str) -> dict[str, Any]:
    """Combine live scan status with stored mismatch-capable counts."""
    status = integrity_status()
    summary: dict[str, Any] = {
        "catalog": catalog_health(db_path),
        "scan": status,
        "checksummed": 0,
        "mismatch_count": len(status.get("mismatch_ids") or []),
        "mismatches": list(status.get("mismatch_ids") or []),
        "bit_rot": bool(status.get("mismatches")),
    }
    if not os.path.isfile(db_path):
        return summary
    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            ensure_checksums_table(conn)
            row = conn.execute("SELECT COUNT(*) AS c FROM image_checksums").fetchone()
            summary["checksummed"] = int(row[0] if row else 0)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        summary["db_error"] = str(exc)
    if summary["bit_rot"]:
        summary["alert"] = (
            f"BIT ROT DETECTED: {summary['mismatch_count']} original(s) changed "
            f"since last checksum — ids={summary['mismatches']}"
        )
    return summary
