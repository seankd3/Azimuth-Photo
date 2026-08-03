"""Satellite-only, hub-confirmed removal of local originals."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.path_groups import safe_commonpath
from core.runtime_paths import resolve_runtime_paths
from core.source_files import inspect_source_file
from data import connection
from features.sync import oplog, satellite
from features.sync.executor import run_sync_work
from features.sync.hashing import compute_full_hash

from archive import transport


HAVE_BATCH_SIZE = 1000
HaveFn = Callable[[list[str]], Awaitable[set[str]]]
FullHaveFn = Callable[[dict[str, str]], Awaitable[set[str]]]
HashFn = Callable[[str], str]
CancelFn = Callable[[], bool]


@dataclass
class FreeUpJob:
    older_than_days: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    phase: str = "queued"
    files_total: int = 0
    bytes_total: int = 0
    files_done: int = 0
    bytes_freed: int = 0
    skipped_modified: int = 0
    skipped_state: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    cancel_requested: bool = False

    def status(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "phase": self.phase,
            "files_done": self.files_done,
            "files_total": self.files_total,
            "bytes_freed": self.bytes_freed,
            "bytes_total": self.bytes_total,
            "skipped_modified": self.skipped_modified,
            "skipped_state": self.skipped_state,
            "errors": list(self.errors),
            "cancel_requested": self.cancel_requested,
        }


_jobs: dict[str, FreeUpJob] = {}
_tasks: dict[str, asyncio.Task] = {}


def deletion_log_path() -> Path:
    return Path(resolve_runtime_paths().transfer_dir) / "freeup-deletions.jsonl"


def _append_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": time.time(), **payload}, separators=(",", ":"), sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _batched(values: list[str], size: int = HAVE_BATCH_SIZE) -> Iterable[list[str]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


async def confirm_hub_hashes(content_hashes: list[str]) -> set[str]:
    hub = satellite.hub_url().rstrip("/")
    if not hub:
        raise RuntimeError("A connected hub is required to free local originals")

    def request() -> set[str]:
        body = json.dumps({"content_hashes": content_hashes}, separators=(",", ":")).encode()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers.update(satellite.hub_request_headers())
        reply = transport.request(
            "POST", f"{hub}/api/sync/have", body=body, headers=headers, timeout=30
        )
        status = reply.status
        payload = json.loads(reply.body or b"{}") if reply.ok else {}
        if not reply.ok:
            raise RuntimeError(f"Hub confirmation failed ({status})")
        present = payload.get("present") if isinstance(payload, dict) else None
        if not isinstance(present, list):
            raise RuntimeError("Hub returned an invalid confirmation")
        return {str(value) for value in present}

    return await run_sync_work(request)


async def confirm_hub_full_hashes(proofs: dict[str, str]) -> set[str]:
    hub = satellite.hub_url().rstrip("/")
    if not hub:
        raise RuntimeError("A connected hub is required to verify local originals")

    def request() -> set[str]:
        body = json.dumps(
            {
                "items": [
                    {"content_hash": content_hash, "full_hash": full_hash}
                    for content_hash, full_hash in proofs.items()
                ]
            },
            separators=(",", ":"),
        ).encode()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers.update(satellite.hub_request_headers())
        reply = transport.request(
            "POST",
            f"{hub}/api/sync/have/full",
            body=body,
            headers=headers,
            timeout=transport.BULK,
        )
        status = reply.status
        payload = json.loads(reply.body or b"{}") if reply.ok else {}
        if not reply.ok:
            raise RuntimeError(f"Hub full-file verification failed ({status})")
        present = payload.get("present") if isinstance(payload, dict) else None
        if not isinstance(present, list):
            raise RuntimeError("Hub returned an invalid full-file verification")
        return {str(value) for value in present}

    return await run_sync_work(request)


def _parse_date(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _old_enough(row: dict[str, Any], file_stat: os.stat_result, older_than_days: int) -> bool:
    if older_than_days <= 0:
        return True
    captured_at = _parse_date(row.get("date_taken"))
    if captured_at is None:
        captured_at = _parse_date(row.get("file_modified_at"))
    if captured_at is None:
        captured_at = float(file_stat.st_mtime)
    return captured_at <= time.time() - older_than_days * 86400


def _fingerprint_matches(
    filepath: str,
    *,
    expected_size: int,
    expected_modified_ns: int,
) -> bool:
    try:
        current = os.stat(filepath)
    except OSError:
        return False
    return (
        int(current.st_size) == int(expected_size)
        and int(current.st_mtime_ns) == int(expected_modified_ns)
    )


def _eligible_local_rows(
    rows: Iterable[dict[str, Any]], older_than_days: int
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for row in rows:
        filepath = str(row.get("filepath") or "")
        source_path = str(row.get("source_path") or "")
        if not filepath or filepath in seen_paths:
            continue
        state, file_stat = inspect_source_file(filepath, source_path)
        if state != "available" or file_stat is None:
            continue
        if not _old_enough(row, file_stat, older_than_days):
            continue
        row["bytes"] = int(file_stat.st_size)
        candidates.append(row)
        seen_paths.add(filepath)
    return candidates


async def _candidate_rows(db_path: str, older_than_days: int) -> list[dict[str, Any]]:
    await satellite.ensure_sync_state(db_path)
    await oplog.ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            """
            SELECT i.id, i.filepath, i.content_hash, sync.full_hash,
                   sync.file_size AS proof_file_size,
                   sync.file_modified_ns AS proof_modified_ns,
                   i.hub_image_id, i.file_size,
                   i.file_modified_at, i.date_taken, s.path AS source_path
            FROM images i
            JOIN catalog_sources s ON s.id = i.source_id
            JOIN sync_state sync ON sync.content_hash = i.content_hash
            WHERE COALESCE(i.hub_remote, 0) = 0
              AND i.hub_image_id IS NOT NULL
              AND i.vc_of IS NULL
              AND i.status IN ('kept', 'maybe')
              AND i.missing_at IS NULL
              AND COALESCE(sync.uploaded, 0) = 1
              AND sync.full_hash IS NOT NULL
              AND sync.file_size IS NOT NULL
              AND sync.file_modified_ns IS NOT NULL
              AND sync.last_local_change_at <= COALESCE(sync.last_pushed_at, 0)
              AND NOT EXISTS (
                  SELECT 1
                  FROM oplog pending
                  JOIN oplog_settings device ON device.key = 'device_id'
                  LEFT JOIN oplog_settings pushed ON pushed.key = 'last_pushed_origin_seq'
                  WHERE pending.content_hash = i.content_hash
                    AND pending.origin = device.value
                    AND pending.origin_seq > CAST(COALESCE(pushed.value, '0') AS INTEGER)
              )
            ORDER BY i.id
            """
        )
        rows = [dict(row) for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)
    return await run_sync_work(_eligible_local_rows, rows, older_than_days)


async def _still_clean(
    db_path: str, image_id: int, content_hash: str, filepath: str
) -> bool:
    conn = await connection.open_async(db_path)
    try:
        row = await (
            await conn.execute(
                """
                SELECT i.filepath, s.path AS source_path
                FROM images i
                JOIN catalog_sources s ON s.id = i.source_id
                JOIN sync_state sync ON sync.content_hash = i.content_hash
                WHERE i.id = ?
                  AND i.filepath = ?
                  AND i.content_hash = ?
                  AND COALESCE(i.hub_remote, 0) = 0
                  AND i.hub_image_id IS NOT NULL
                  AND i.vc_of IS NULL
                  AND i.status IN ('kept', 'maybe')
                  AND i.missing_at IS NULL
                  AND COALESCE(sync.uploaded, 0) = 1
                  AND sync.full_hash IS NOT NULL
                  AND sync.last_local_change_at <= COALESCE(sync.last_pushed_at, 0)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM oplog pending
                      JOIN oplog_settings device ON device.key = 'device_id'
                      LEFT JOIN oplog_settings pushed ON pushed.key = 'last_pushed_origin_seq'
                      WHERE pending.content_hash = i.content_hash
                        AND pending.origin = device.value
                        AND pending.origin_seq > CAST(COALESCE(pushed.value, '0') AS INTEGER)
                  )
                """,
                (int(image_id), filepath, content_hash),
            )
        ).fetchone()
    finally:
        await connection.close_async(conn, db_path=db_path)
    if row is None:
        return False
    state, _file_stat = await run_sync_work(
        inspect_source_file, str(row["filepath"]), str(row["source_path"] or "")
    )
    return state == "available"


async def _confirmed_candidates(
    db_path: str,
    older_than_days: int,
    confirm: HaveFn,
    should_cancel: CancelFn | None = None,
) -> list[dict[str, Any]]:
    candidates = await _candidate_rows(db_path, older_than_days)
    confirmed: list[dict[str, Any]] = []
    by_hash: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_hash.setdefault(str(candidate["content_hash"]), []).append(candidate)
    for batch in _batched(list(by_hash)):
        if should_cancel is not None and should_cancel():
            break
        present = await confirm(batch)
        for content_hash in batch:
            if content_hash in present:
                confirmed.extend(by_hash[content_hash])
    return confirmed


def _path_under_root(path: str, root: str) -> bool:
    try:
        real_root = os.path.realpath(root)
        return safe_commonpath((real_root, os.path.realpath(path))) == real_root
    except (OSError, ValueError):
        return False


async def _catalog_source_root(db_path: str, image_id: int, content_hash: str) -> str:
    """Resolve the source root for a journal line that predates source_path journaling."""

    conn = await connection.open_async(db_path)
    try:
        row = await (
            await conn.execute(
                """
                SELECT s.path AS source_path
                FROM images i
                JOIN catalog_sources s ON s.id = i.source_id
                WHERE i.id = ? AND i.content_hash = ?
                """,
                (image_id, content_hash),
            )
        ).fetchone()
    finally:
        await connection.close_async(conn, db_path=db_path)
    return str(row["source_path"] or "") if row else ""


async def recover_incomplete_deletions(db_path: str, *, log_path: Path | None = None) -> int:
    path = log_path or deletion_log_path()
    if not path.is_file():
        return 0
    pending: dict[tuple[int, str], dict[str, Any]] = {}
    try:
        lines = await run_sync_work(path.read_text, encoding="utf-8")
    except OSError:
        return 0
    for line in lines.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        key = (int(event.get("image_id") or 0), str(event.get("content_hash") or ""))
        if key[0] <= 0 or not key[1]:
            continue
        if event.get("event") == "delete_ready":
            pending[key] = event
        elif event.get("event") in {"delete_aborted", "deleted", "recovered"}:
            pending.pop(key, None)

    recovered = 0
    for (image_id, content_hash), event in pending.items():
        filepath = str(event.get("path") or "")
        if not filepath:
            continue
        # Only a *definitively* missing file on an *online* source is a completed
        # deletion. If the source root itself is absent (unplugged card/drive), the
        # file's absence is meaningless and must never be marked remote -- that would
        # hide a photo still physically on disk once the volume returns.
        # Legacy journal lines predate source_path journaling; resolve the root from
        # the catalog so they get the same protection instead of silently skipping it.
        source_path = str(event.get("source_path") or "")
        if not source_path:
            source_path = await _catalog_source_root(db_path, image_id, content_hash)
        # No resolvable root, or an offline root, means the file's absence proves
        # nothing -- leave the line pending in the journal for a later, online run.
        if not source_path or not await run_sync_work(os.path.isdir, source_path):
            continue
        # A root can only vouch for paths inside it: if the source was remapped
        # elsewhere since the journal line was written, the old path's absence is
        # equally meaningless -- refuse rather than hide a photo.
        if not await run_sync_work(_path_under_root, filepath, source_path):
            continue
        state, _file_stat = await run_sync_work(inspect_source_file, filepath, source_path)
        if state != "missing":
            continue
        conn = await connection.open_async(db_path)
        try:
            cursor = await conn.execute(
                "UPDATE images SET hub_remote = 1, missing_at = NULL "
                "WHERE id = ? AND content_hash = ? AND hub_image_id IS NOT NULL",
                (image_id, content_hash),
            )
            await conn.commit()
            changed = int(cursor.rowcount or 0)
        finally:
            await connection.close_async(conn, db_path=db_path)
        if changed:
            recovered += 1
            await run_sync_work(
                _append_log,
                path,
                {**event, "event": "recovered", "hub_confirmed": True},
            )
    return recovered


async def freeable(
    db_path: str,
    older_than_days: int,
    *,
    confirm: HaveFn = confirm_hub_hashes,
    log_path: Path | None = None,
) -> dict[str, int]:
    await recover_incomplete_deletions(db_path, log_path=log_path)
    candidates = await _confirmed_candidates(db_path, older_than_days, confirm)
    return {
        "files": len(candidates),
        "bytes": sum(int(candidate["bytes"]) for candidate in candidates),
    }


async def _mark_remote(db_path: str, image_id: int, content_hash: str) -> bool:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "UPDATE images SET hub_remote = 1, missing_at = NULL "
            "WHERE id = ? AND content_hash = ? AND hub_image_id IS NOT NULL AND COALESCE(hub_remote, 0) = 0",
            (int(image_id), content_hash),
        )
        await conn.commit()
        return bool(cursor.rowcount)
    finally:
        await connection.close_async(conn, db_path=db_path)


async def run_job(
    job: FreeUpJob,
    db_path: str,
    *,
    confirm: HaveFn = confirm_hub_hashes,
    confirm_full: FullHaveFn | None = None,
    hash_file: HashFn = compute_full_hash,
    log_path: Path | None = None,
) -> None:
    path = log_path or deletion_log_path()
    if confirm_full is None:
        if confirm is confirm_hub_hashes:
            confirm_full = confirm_hub_full_hashes
        else:
            async def confirm_full(proofs: dict[str, str]) -> set[str]:
                return await confirm(list(proofs))

    try:
        await recover_incomplete_deletions(db_path, log_path=path)
        job.phase = "confirming"
        candidates = await _confirmed_candidates(
            db_path,
            job.older_than_days,
            confirm,
            should_cancel=lambda: job.cancel_requested,
        )
        if job.cancel_requested:
            job.phase = "cancelled"
            return
        job.files_total = len(candidates)
        job.bytes_total = sum(int(candidate["bytes"]) for candidate in candidates)
        job.phase = "deleting"
        for candidate in candidates:
            if job.cancel_requested:
                job.phase = "cancelled"
                return
            image_id = int(candidate["id"])
            content_hash = str(candidate["content_hash"])
            full_hash = str(candidate["full_hash"])
            filepath = str(candidate["filepath"])
            proof_file_size = int(candidate["proof_file_size"])
            proof_modified_ns = int(candidate["proof_modified_ns"])
            if not await _still_clean(db_path, image_id, content_hash, filepath):
                job.skipped_state += 1
                continue
            if not await run_sync_work(
                _fingerprint_matches,
                filepath,
                expected_size=proof_file_size,
                expected_modified_ns=proof_modified_ns,
            ):
                job.skipped_modified += 1
                await run_sync_work(
                    _append_log,
                    path,
                    {
                        "event": "skipped_modified",
                        "job_id": job.id,
                        "image_id": image_id,
                        "path": filepath,
                        "content_hash": content_hash,
                        "full_hash": full_hash,
                        "hub_confirmed": True,
                    },
                )
                continue
            try:
                actual_hash = await run_sync_work(hash_file, filepath)
            except OSError as error:
                job.errors.append({"path": filepath, "error": str(error)})
                continue
            fingerprint_stable = await run_sync_work(
                _fingerprint_matches,
                filepath,
                expected_size=proof_file_size,
                expected_modified_ns=proof_modified_ns,
            )
            if actual_hash != full_hash or not fingerprint_stable:
                job.skipped_modified += 1
                await run_sync_work(
                    _append_log,
                    path,
                    {
                        "event": "skipped_modified",
                        "job_id": job.id,
                        "image_id": image_id,
                        "path": filepath,
                        "content_hash": content_hash,
                        "full_hash": full_hash,
                        "actual_full_hash": actual_hash,
                        "fingerprint_stable": fingerprint_stable,
                        "hub_confirmed": True,
                    },
                )
                continue
            if job.cancel_requested:
                job.phase = "cancelled"
                return
            # The batch confirmation can be minutes old; re-confirm THIS hash with the
            # hub immediately before unlinking so a hub-side trash/purge/loss since the
            # batch check cannot cost the only remaining copy.
            try:
                fresh = await confirm_full({content_hash: full_hash})
            except Exception as error:  # noqa: BLE001 - any confirm failure must NOT delete
                job.errors.append({"path": filepath, "error": str(error)})
                continue
            if content_hash not in fresh:
                job.skipped_state += 1
                continue
            ready = {
                "event": "delete_ready",
                "job_id": job.id,
                "image_id": image_id,
                "path": filepath,
                "source_path": str(candidate.get("source_path") or ""),
                "content_hash": content_hash,
                "full_hash": full_hash,
                "file_size": proof_file_size,
                "file_modified_ns": proof_modified_ns,
                "hub_image_id": int(candidate["hub_image_id"]),
                "hub_confirmed": True,
                "bytes": int(candidate["bytes"]),
            }
            await run_sync_work(_append_log, path, ready)
            if not await run_sync_work(
                _fingerprint_matches,
                filepath,
                expected_size=proof_file_size,
                expected_modified_ns=proof_modified_ns,
            ):
                job.skipped_modified += 1
                await run_sync_work(
                    _append_log,
                    path,
                    {**ready, "event": "delete_aborted", "reason": "local_file_changed"},
                )
                continue
            try:
                await run_sync_work(os.unlink, filepath)
                if not await _mark_remote(db_path, image_id, content_hash):
                    await recover_incomplete_deletions(db_path, log_path=path)
            except OSError as error:
                job.errors.append({"path": filepath, "error": str(error)})
                continue
            job.files_done += 1
            job.bytes_freed += int(candidate["bytes"])
            await run_sync_work(_append_log, path, {**ready, "event": "deleted"})
        job.phase = "completed"
    except asyncio.CancelledError:
        job.phase = "cancelled"
        raise
    except Exception as error:
        try:
            await recover_incomplete_deletions(db_path, log_path=path)
        except Exception:
            pass
        job.phase = "failed"
        job.errors.append({"path": "", "error": str(error)})


def active_job() -> FreeUpJob | None:
    return next(
        (job for job in reversed(list(_jobs.values())) if job.phase in {"queued", "confirming", "deleting"}),
        None,
    )


def start_job(db_path: str, older_than_days: int) -> FreeUpJob:
    current = active_job()
    if current is not None:
        return current
    job = FreeUpJob(older_than_days=older_than_days)
    _jobs[job.id] = job
    task = asyncio.create_task(run_job(job, db_path))
    _tasks[job.id] = task
    task.add_done_callback(lambda _done, job_id=job.id: _tasks.pop(job_id, None))
    return job


def job_for_id(job_id: str) -> FreeUpJob | None:
    return _jobs.get(str(job_id))


def request_cancel(job: FreeUpJob) -> None:
    job.cancel_requested = True
