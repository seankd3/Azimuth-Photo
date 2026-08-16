"""Safe trash operations for catalog images."""

from __future__ import annotations

import os
from core.numbers import unique_image_ids
import stat
import time
from pathlib import Path

import helpers as app_helpers
from data import connection as data_connection
from data.repositories import catalog as catalog_repository
from features.stacks import builders as stack_builders
import judgements


Error = dict[str, int | str]
DEFAULT_RETENTION_SECONDS = 30 * 24 * 60 * 60
TRASH_WRITE_BUSY_TIMEOUT_SECONDS = 0.1
TRASH_WRITE_RETRY_BACKOFF_SECONDS = 0.1


_pending_hub_trash_refs_cache: dict[str, tuple[int, tuple[int, ...], tuple[int, ...]]] = {}
_pending_hub_trash_refs_versions: dict[str, int] = {}


def invalidate_pending_hub_trash_refs(db_path: str) -> None:
    _pending_hub_trash_refs_cache.pop(db_path, None)
    _pending_hub_trash_refs_versions[db_path] = _pending_hub_trash_refs_versions.get(db_path, 0) + 1




def _error(image_id: int, reason: str) -> Error:
    return {"id": int(image_id), "reason": reason}


def _source_relative_path(filepath: str, source_root: str) -> str | None:
    try:
        relpath = os.path.relpath(filepath, source_root)
    except ValueError:
        return None
    if relpath == os.curdir or relpath.startswith(os.pardir + os.sep) or relpath == os.pardir:
        return None
    return relpath


def _trash_destination(filepath: str, source_root: str) -> str | None:
    relpath = _source_relative_path(filepath, source_root)
    if relpath is None:
        return None
    return os.path.join(source_root, ".trash", relpath)


def _regular_file_lstat(path: str):
    try:
        lstat_result = os.lstat(path)
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, str(exc)
    if stat.S_ISLNK(lstat_result.st_mode):
        return None, "source path is a symlink"
    if not stat.S_ISREG(lstat_result.st_mode):
        return None, "source path is not a regular file"
    return lstat_result, ""


def _same_device_or_reason(source_stat, source_root: str) -> str:
    try:
        root_stat = os.stat(source_root)
    except OSError as exc:
        return str(exc)
    if int(source_stat.st_dev) != int(root_stat.st_dev):
        return "trash destination is on a different filesystem"
    return ""


def _stat_token(value) -> tuple[int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size or 0),
        int(getattr(value, "st_mtime_ns", 0) or 0),
    )


def _same_stat_token(value, expected: tuple[int, int, int, int] | None) -> bool:
    return expected is not None and _stat_token(value) == expected


def _prepare_trash_move(filepath: str, source_root: str) -> tuple[str | None, int, tuple[int, int, int, int] | None, str]:
    source_stat, reason = _regular_file_lstat(filepath)
    if reason == "missing":
        return None, 0, None, ""
    if source_stat is None:
        return None, 0, None, reason

    dest = _trash_destination(filepath, source_root)
    if dest is None:
        return None, 0, None, "source path is outside its catalog source"
    if os.path.lexists(dest):
        return None, 0, None, "trash destination already exists"

    device_reason = _same_device_or_reason(source_stat, source_root)
    if device_reason:
        return None, 0, None, device_reason

    return dest, int(source_stat.st_size or 0), _stat_token(source_stat), ""


def _move_to_trash(filepath: str, dest: str | None, expected_token: tuple[int, int, int, int] | None) -> tuple[int, str]:
    if not dest:
        return 0, ""
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        final_stat, reason = _regular_file_lstat(filepath)
        if reason == "missing":
            return 0, "missing"
        if final_stat is None:
            return 0, reason
        if not _same_stat_token(final_stat, expected_token):
            return 0, "source path changed before move"
        if os.path.lexists(dest):
            return 0, "trash destination already exists"
        # Re-lstat immediately before rename narrows the local TOCTOU window on
        # platforms where Python cannot express a no-follow rename.
        final_stat, reason = _regular_file_lstat(filepath)
        if final_stat is None:
            return 0, reason or "source path changed before move"
        if not _same_stat_token(final_stat, expected_token):
            return 0, "source path changed before move"
        os.rename(filepath, dest)
    except OSError as exc:
        return 0, str(exc)
    return int(final_stat.st_size or 0), ""


def _restore_from_trash(trash_path: str | None, filepath: str) -> tuple[str | None, str]:
    if not trash_path:
        return "Original file was missing when trashed", ""
    trash_stat, reason = _regular_file_lstat(trash_path)
    if reason == "missing":
        return None, "trash file is missing"
    if trash_stat is None:
        return None, reason
    if os.path.lexists(filepath):
        return None, "original path already exists"
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        final_stat, reason = _regular_file_lstat(trash_path)
        if reason == "missing":
            return None, "trash file is missing"
        if final_stat is None:
            return None, reason
        if not _same_stat_token(final_stat, _stat_token(trash_stat)):
            return None, "trash path changed before restore"
        if os.path.lexists(filepath):
            return None, "original path already exists"
        # Re-lstat immediately before rename narrows the local TOCTOU window on
        # platforms where Python cannot express a no-follow rename.
        final_stat, reason = _regular_file_lstat(trash_path)
        if final_stat is None:
            return (None, "trash file is missing" if reason == "missing" else reason)
        if not _same_stat_token(final_stat, _stat_token(trash_stat)):
            return None, "trash path changed before restore"
        os.rename(trash_path, filepath)
    except OSError as exc:
        return None, str(exc)
    return None, ""


async def _image_rows_by_id(conn, image_ids: list[int]) -> dict[int, dict]:
    if not image_ids:
        return {}
    rows: dict[int, dict] = {}
    for chunk in catalog_repository._chunked(image_ids):
        placeholders = ",".join("?" for _ in chunk)
        cursor = await conn.execute(
            "SELECT i.*, s.path AS source_root "
            "FROM images i LEFT JOIN catalog_sources s ON s.id = i.source_id "
            f"WHERE i.id IN ({placeholders})",
            chunk,
        )
        for row in await cursor.fetchall():
            rows[int(row["id"])] = dict(row)
    return rows


async def _repair_stacks_after_trash(conn, image_ids: list[int]) -> None:
    if not image_ids:
        return
    stack_ids: set[int] = set()
    for chunk in catalog_repository._chunked(image_ids):
        placeholders = ",".join("?" for _ in chunk)
        cursor = await conn.execute(
            f"SELECT DISTINCT stack_id FROM stack_members WHERE image_id IN ({placeholders})",
            chunk,
        )
        stack_ids.update(int(row["stack_id"]) for row in await cursor.fetchall())
    now = time.time()
    for stack_id in sorted(stack_ids):
        for chunk in catalog_repository._chunked(image_ids):
            placeholders = ",".join("?" for _ in chunk)
            await conn.execute(
                f"DELETE FROM stack_members WHERE stack_id = ? AND image_id IN ({placeholders})",
                (stack_id, *chunk),
            )
        cursor = await conn.execute(
            "SELECT s.kind, sm.image_id "
            "FROM stacks s JOIN stack_members sm ON sm.stack_id = s.id "
            "JOIN images i ON i.id = sm.image_id "
            "WHERE s.id = ? AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
            (stack_id,),
        )
        rows = await cursor.fetchall()
        if len(rows) <= 1:
            await conn.execute("DELETE FROM stack_members WHERE stack_id = ?", (stack_id,))
            await conn.execute("DELETE FROM stacks WHERE id = ?", (stack_id,))
            continue
        member_ids = [int(row["image_id"]) for row in rows]
        image_rows = await _image_rows_by_id(conn, member_ids)
        kind = rows[0]["kind"] or "variant"
        if kind == "manual":
            kind = "variant"
        representative_id = stack_builders.representative_id(member_ids, image_rows, kind=kind)
        await conn.execute(
            "UPDATE stacks SET representative_image_id = ?, updated_at = ? WHERE id = ?",
            (representative_id, now, stack_id),
        )


async def _repair_collection_covers(conn, image_ids: list[int]) -> None:
    if not image_ids:
        return
    collection_ids: set[int] = set()
    for chunk in catalog_repository._chunked(image_ids):
        placeholders = ",".join("?" for _ in chunk)
        cursor = await conn.execute(
            f"SELECT DISTINCT collection_id FROM collection_images WHERE image_id IN ({placeholders})",
            chunk,
        )
        collection_ids.update(int(row["collection_id"]) for row in await cursor.fetchall())
    now = time.time()
    for collection_id in sorted(collection_ids):
        await conn.execute(
            "UPDATE collections SET cover_image_id = ("
            "  SELECT ci.image_id FROM collection_images ci "
            "  JOIN images i ON i.id = ci.image_id "
            "  WHERE ci.collection_id = collections.id "
            "    AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
            "  ORDER BY ci.position ASC, ci.added_at ASC, ci.image_id ASC LIMIT 1"
            "), updated_at = ? WHERE id = ?",
            (now, collection_id),
        )


async def _expand_trash_family_ids(conn, ids: list[int]) -> list[int]:
    """Trashing a master takes its virtual copies along — they share its file."""
    expanded = list(ids)
    seen = set(ids)
    for chunk in catalog_repository._chunked(ids):
        placeholders = ",".join("?" for _ in chunk)
        cursor = await conn.execute(
            f"SELECT id FROM images WHERE vc_of IN ({placeholders}) AND status != 'trashed'",
            chunk,
        )
        for row in await cursor.fetchall():
            copy_id = int(row["id"])
            if copy_id not in seen:
                seen.add(copy_id)
                expanded.append(copy_id)
    return expanded


async def _expand_restore_family_ids(conn, ids: list[int]) -> list[int]:
    """Restore every trashed row sharing the requested image family's file."""
    expanded = list(ids)
    seen = set(ids)
    master_ids: set[int] = set()
    for chunk in catalog_repository._chunked(ids):
        placeholders = ",".join("?" for _ in chunk)
        cursor = await conn.execute(
            f"SELECT id, vc_of FROM images WHERE id IN ({placeholders})",
            chunk,
        )
        for row in await cursor.fetchall():
            master_ids.add(int(row["vc_of"] if row["vc_of"] is not None else row["id"]))
    for chunk in catalog_repository._chunked(sorted(master_ids)):
        placeholders = ",".join("?" for _ in chunk)
        cursor = await conn.execute(
            "SELECT id FROM images WHERE status = 'trashed' "
            f"AND (id IN ({placeholders}) OR vc_of IN ({placeholders})) ORDER BY id",
            (*chunk, *chunk),
        )
        for row in await cursor.fetchall():
            image_id = int(row["id"])
            if image_id not in seen:
                seen.add(image_id)
                expanded.append(image_id)
    return expanded


def _failed_family_ids(plans, failure_ids):
    """Resolve failures to the shared-file families that must fail together."""
    family_by_image_id = {int(plan["id"]): int(plan["family_id"]) for plan in plans}
    return {family_by_image_id.get(int(image_id), int(image_id)) for image_id in failure_ids}


async def trash_images(db_path: str, image_ids: list[int]) -> dict:
    ids = unique_image_ids(image_ids)
    if not ids:
        return {"trashed": [], "errors": [], "freed_estimate_bytes": 0}
    trashed: list[int] = []
    errors: list[Error] = []
    freed_estimate_bytes = 0
    plans: list[dict] = []
    now = time.time()

    conn = await data_connection.open_async(db_path)
    try:
        ids = await _expand_trash_family_ids(conn, ids)
        rows = await _image_rows_by_id(conn, ids)
        failed_prepare_ids: set[int] = set()
        for image_id in ids:
            row = rows.get(image_id)
            if row is None:
                errors.append(_error(image_id, "image not found"))
                continue
            if row.get("status") == "trashed":
                trashed.append(image_id)
                continue
            if row.get("vc_of") is not None:
                # Virtual copies never own their file: trash them catalog-only so
                # the single move stays with the master row that owns the bytes.
                plans.append({
                    "id": image_id,
                    "family_id": int(row["vc_of"]),
                    "filepath": row.get("filepath") or "",
                    "trash_path": None,
                    "size": 0,
                    "token": None,
                })
                continue
            source_root = row.get("source_root") or ""
            filepath = row.get("filepath") or ""
            if not source_root or not filepath:
                errors.append(_error(image_id, "source path missing"))
                failed_prepare_ids.add(image_id)
                continue
            dest, moved_bytes, expected_token, reason = await __to_thread_prepare_trash_move(filepath, source_root)
            if reason:
                errors.append(_error(image_id, reason))
                failed_prepare_ids.add(image_id)
                continue
            plans.append({
                "id": image_id,
                "family_id": image_id,
                "filepath": filepath,
                "trash_path": dest,
                "size": moved_bytes,
                "token": expected_token,
            })

        if failed_prepare_ids:
            failed_family_ids = _failed_family_ids(plans, failed_prepare_ids)
            plans = [
                plan for plan in plans
                if int(plan["family_id"]) not in failed_family_ids
            ]

        if plans:
            # Files move before any catalog write so a committed 'trashed' row
            # only ever describes a move that actually happened; a crash here
            # leaves every row untouched and never strands a phantom trash_path.
            successful: list[dict] = []
            failed_ids: set[int] = set()
            for plan in plans:
                moved_bytes, reason = await __to_thread_move_to_trash(
                    plan["filepath"],
                    plan["trash_path"],
                    plan["token"],
                )
                if reason:
                    errors.append(_error(plan["id"], reason))
                    failed_ids.add(int(plan["id"]))
                    continue
                plan["moved_bytes"] = moved_bytes
                successful.append(plan)
            if failed_ids:
                failed_family_ids = _failed_family_ids(plans, failed_ids)
                successful = [
                    plan for plan in successful
                    if int(plan["family_id"]) not in failed_family_ids
                ]
            if successful:
                trashed.extend(int(plan["id"]) for plan in successful)
                freed_estimate_bytes += sum(
                    int(plan.get("moved_bytes") or 0) for plan in successful
                )
                await conn.execute("BEGIN")
                await conn.executemany(
                    "UPDATE images SET status = 'trashed', trashed_at = ?, trash_path = ?, trash_pending_hub = 0 WHERE id = ?",
                    [(now, plan["trash_path"], plan["id"]) for plan in successful],
                )
                source_ids = sorted({
                    int(rows[plan["id"]]["source_id"])
                    for plan in successful
                    if rows[plan["id"]].get("source_id") is not None
                })
                for source_id in source_ids:
                    await catalog_repository.update_source_counts_on_conn(conn, source_id)
                successful_ids = [plan["id"] for plan in successful]
                await _repair_stacks_after_trash(conn, successful_ids)
                await _repair_collection_covers(conn, successful_ids)
                await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    invalidate_pending_hub_trash_refs(db_path)
    # Trashing is a judgement, so it goes in the log beside the column write.
    # It used to converge through an operation log, which existed because a
    # hub could revert it. There is no hub, and the log is the record.
    if trashed:
        await judgements.status(db_path, trashed, "trashed")

    return {
        "trashed": trashed,
        "errors": errors,
        "freed_estimate_bytes": int(freed_estimate_bytes),
    }


async def __to_thread_prepare_trash_move(
    filepath: str,
    source_root: str,
) -> tuple[str | None, int, tuple[int, int, int, int] | None, str]:
    import asyncio

    return await asyncio.to_thread(_prepare_trash_move, filepath, source_root)


async def __to_thread_move_to_trash(
    filepath: str,
    dest: str | None,
    expected_token: tuple[int, int, int, int] | None,
) -> tuple[int, str]:
    import asyncio

    return await asyncio.to_thread(_move_to_trash, filepath, dest, expected_token)


async def restore_images(db_path: str, image_ids: list[int]) -> dict:
    ids = unique_image_ids(image_ids)
    restored: list[int] = []
    errors: list[Error] = []
    warnings: list[Error] = []
    conn = await data_connection.open_async(db_path)
    try:
        ids = await _expand_restore_family_ids(conn, ids)
        rows = await _image_rows_by_id(conn, ids)
        updates: list[dict] = []
        for image_id in ids:
            row = rows.get(image_id)
            if row is None:
                errors.append(_error(image_id, "image not found"))
                continue
            if row.get("status") != "trashed":
                restored.append(image_id)
                continue
            updates.append({
                "id": image_id,
                "family_id": int(row["vc_of"] if row.get("vc_of") is not None else image_id),
                "filepath": row.get("filepath") or "",
                "trash_path": row.get("trash_path"),
                "catalog_only": row.get("vc_of") is not None and not row.get("trash_path"),
                "previous_status": row.get("status") or "trashed",
                "previous_trashed_at": row.get("trashed_at"),
                "previous_trash_path": row.get("trash_path"),
            })

        if updates:
            await conn.execute("BEGIN")
            update_ids = [plan["id"] for plan in updates]
            for chunk in catalog_repository._chunked(update_ids):
                placeholders = ",".join("?" for _ in chunk)
                await conn.execute(
                    f"UPDATE images SET status = 'kept', trashed_at = NULL, trash_path = NULL, trash_pending_hub = 0 "
                    f"WHERE id IN ({placeholders})",
                    chunk,
                )
            source_ids = sorted({
                int(rows[plan["id"]]["source_id"])
                for plan in updates
                if rows[plan["id"]].get("source_id") is not None
            })
            for source_id in source_ids:
                await catalog_repository.update_source_counts_on_conn(conn, source_id)
            await conn.commit()

            failed_ids: set[int] = set()
            for plan in updates:
                if plan.get("catalog_only"):
                    # Virtual copy: the master row owns the file (and its restore).
                    continue
                warning, reason = await __to_thread_restore_from_trash(plan["trash_path"], plan["filepath"])
                if reason:
                    errors.append(_error(plan["id"], reason))
                    failed_ids.add(int(plan["id"]))
                    continue
                if warning:
                    warnings.append(_error(plan["id"], warning))
            failed_family_ids = _failed_family_ids(updates, failed_ids)
            failed = [
                plan for plan in updates if int(plan["family_id"]) in failed_family_ids
            ]
            if failed:
                await _revert_failed_restores(conn, failed, rows)
            failed_ids = {int(plan["id"]) for plan in failed}
            restored_update_ids = [
                int(plan["id"]) for plan in updates if int(plan["id"]) not in failed_ids
            ]
            restored.extend(restored_update_ids)
            if restored_update_ids:
                await conn.execute("BEGIN")
                await _repair_collection_covers(conn, restored_update_ids)
                await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    invalidate_pending_hub_trash_refs(db_path)
    if restored:
        await judgements.status(db_path, restored, "kept")

    return {"restored": restored, "errors": errors, "warnings": warnings}


async def __to_thread_restore_from_trash(trash_path: str | None, filepath: str) -> tuple[str | None, str]:
    import asyncio

    return await asyncio.to_thread(_restore_from_trash, trash_path, filepath)


async def _revert_failed_restores(conn, failed: list[dict], rows: dict[int, dict]) -> None:
    await conn.execute("BEGIN")
    await conn.executemany(
        "UPDATE images SET status = ?, trashed_at = ?, trash_path = ? WHERE id = ?",
        [
            (
                plan["previous_status"],
                plan["previous_trashed_at"],
                plan["previous_trash_path"],
                plan["id"],
            )
            for plan in failed
        ],
    )
    source_ids = sorted({
        int(rows[plan["id"]]["source_id"])
        for plan in failed
        if rows[plan["id"]].get("source_id") is not None
    })
    for source_id in source_ids:
        await catalog_repository.update_source_counts_on_conn(conn, source_id)
    await conn.commit()


async def list_trash(db_path: str, *, limit: int = 100, offset: int = 0) -> dict:
    safe_limit = max(1, min(int(limit or 100), 5000))
    safe_offset = max(0, int(offset or 0))
    conn = await data_connection.open_async(db_path)
    try:
        total_cursor = await conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(COALESCE(file_size, 0)), 0) AS total_bytes, "
            "COALESCE(SUM(CASE WHEN COALESCE(trash_pending_hub, 0) = 1 THEN 1 ELSE 0 END), 0) AS pending_hub_count "
            "FROM images WHERE status = 'trashed'"
        )
        total_row = await total_cursor.fetchone()
        edited_cursor = await conn.execute(
            "SELECT COUNT(*) AS edited FROM images i WHERE i.status = 'trashed' "
            "AND i.vc_of IS NOT NULL AND EXISTS ("
            "  SELECT 1 FROM develop_history h WHERE h.image_id = i.id "
            "  AND h.label NOT IN ('Virtual Copy', 'Import from XMP')"
            ")"
        )
        edited_row = await edited_cursor.fetchone()
        cursor = await conn.execute(
            "SELECT * FROM images WHERE status = 'trashed' "
            "ORDER BY trashed_at DESC, id DESC LIMIT ? OFFSET ?",
            (safe_limit, safe_offset),
        )
        images = []
        for row in await cursor.fetchall():
            card = app_helpers.image_card(dict(row))
            card["trashed_at"] = row["trashed_at"]
            card["file_size"] = row["file_size"]
            card["pending_hub"] = bool(row["trash_pending_hub"])
            images.append(card)
        return {
            "images": images,
            "total": int(total_row["total"] or 0),
            "total_bytes": int(total_row["total_bytes"] or 0),
            "pending_hub_count": int(total_row["pending_hub_count"] or 0),
            # Sean-signed decision: Empty Trash warns when edited virtual
            # copies would be destroyed alongside their masters.
            "edited_copy_count": int(edited_row["edited"] or 0),
        }
    finally:
        await data_connection.close_async(conn, db_path=db_path)


def _inspect_trash_file(path: str | None, source_root: str | None) -> tuple[int, str]:
    if not path:
        return 0, ""
    if not source_root:
        return 0, "source root is unavailable"
    try:
        trash_root = (Path(source_root) / ".trash").resolve()
        candidate = Path(path).resolve(strict=False)
        candidate.relative_to(trash_root)
    except (OSError, ValueError):
        return 0, "trash path is outside source trash"
    try:
        lstat_result = os.lstat(path)
    except FileNotFoundError:
        return 0, ""
    except OSError as exc:
        return 0, str(exc)
    if stat.S_ISLNK(lstat_result.st_mode):
        return 0, "trash path is a symlink"
    if not stat.S_ISREG(lstat_result.st_mode):
        return 0, "trash path is not a regular file"
    return int(lstat_result.st_size or 0), ""


def _remove_trash_file(path: str | None, source_root: str | None) -> tuple[int, str]:
    size, reason = _inspect_trash_file(path, source_root)
    if reason or not path:
        return 0, reason
    try:
        lstat_result = os.lstat(path)
    except FileNotFoundError:
        return 0, ""
    except OSError as exc:
        return 0, str(exc)
    expected_token = _stat_token(lstat_result)
    try:
        # Re-lstat immediately before remove narrows the local TOCTOU window on
        # platforms where Python cannot express a no-follow unlink.
        final_stat, reason = _regular_file_lstat(path)
        if reason == "missing":
            return 0, ""
        if final_stat is None:
            return 0, reason
        if not _same_stat_token(final_stat, expected_token):
            return 0, "trash path changed before delete"
        os.remove(path)
    except OSError as exc:
        return 0, str(exc)
    return size, ""


def _prune_empty_trash_dirs(paths: list[str]) -> None:
    roots = sorted({
        str(Path(path).parent)
        for path in paths
        if path and f"{os.sep}.trash{os.sep}" in path
    }, key=len, reverse=True)
    for start in roots:
        current = start
        while f"{os.sep}.trash" in current:
            try:
                os.rmdir(current)
            except OSError:
                break
            if os.path.basename(current) == ".trash":
                break
            current = os.path.dirname(current)


async def _trash_rows(
    db_path: str,
    *,
    older_than: float | None = None,
    image_ids: list[int] | None = None,
) -> list[dict]:
    conn = await data_connection.open_async(db_path)
    try:
        base_query = """
            SELECT i.id, i.trash_path, COALESCE(s.path, '') AS source_path,
                   COALESCE(s.online, 1) AS source_online
            FROM images i
            LEFT JOIN catalog_sources s ON s.id = i.source_id
            WHERE i.status = 'trashed'
        """
        age_clause = ""
        age_params: tuple = ()
        if older_than is not None:
            age_clause = " AND i.trashed_at IS NOT NULL AND i.trashed_at <= ?"
            age_params = (float(older_than),)
        if image_ids is None:
            cursor = await conn.execute(base_query + age_clause, age_params)
            return [dict(row) for row in await cursor.fetchall()]
        ids = unique_image_ids(image_ids)
        rows: list[dict] = []
        for chunk in catalog_repository._chunked(ids):
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                base_query + age_clause + f" AND i.id IN ({placeholders})",
                (*age_params, *chunk),
            )
            rows.extend(dict(row) for row in await cursor.fetchall())
        return rows
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def _purge_trash_rows(
    db_path: str,
    *,
    older_than: float | None = None,
    image_ids: list[int] | None = None,
) -> dict:
    rows = await _trash_rows(db_path, older_than=older_than, image_ids=image_ids)
    deletable_ids: list[int] = []
    rows_by_id: dict[int, dict] = {}
    errors: list[Error] = []
    skipped_offline = 0
    for row in rows:
        image_id = int(row["id"])
        trash_path = row.get("trash_path")
        # Offline protects source-local trash files that cannot be inspected.
        # Catalog-only entries (including hub mirrors) have no local file to
        # protect and must not remain stuck in Trash because of source state.
        if not int(row["source_online"]) and trash_path:
            skipped_offline += 1
            continue
        _size, reason = await __to_thread_inspect_trash_file(
            trash_path,
            row.get("source_path"),
        )
        if reason:
            errors.append(_error(image_id, reason))
            continue
        deletable_ids.append(image_id)
        rows_by_id[image_id] = row

    # Files go first: a catalog row may only disappear once its trash file is
    # confirmed gone. A failed unlink leaves the row in Trash for the next
    # Empty Trash to retry; a removed file whose row delete defers purges
    # cleanly next pass because a missing trash file counts as removed.
    freed_bytes = 0
    paths_to_prune: list[str] = []
    removed_ids: list[int] = []
    for image_id in deletable_ids:
        row = rows_by_id[image_id]
        trash_path = row.get("trash_path")
        freed, reason = await __to_thread_remove_trash_file(
            trash_path,
            row.get("source_path"),
        )
        if reason:
            errors.append(_error(image_id, reason))
            continue
        freed_bytes += freed
        removed_ids.append(image_id)
        if trash_path:
            paths_to_prune.append(trash_path)
    deleted_ids: list[int] = []
    if removed_ids:
        deleted_ids, delete_errors = await _delete_emptied_catalog_rows(db_path, removed_ids)
        errors.extend(delete_errors)
    await __to_thread_prune_empty_trash_dirs(paths_to_prune)
    if deleted_ids:
        invalidate_pending_hub_trash_refs(db_path)
    return {
        "deleted_count": len(deleted_ids),
        "freed_bytes": int(freed_bytes),
        "errors": errors,
        "skipped_offline": skipped_offline,
    }


async def empty_trash(db_path: str, *, image_ids: list[int] | None = None) -> dict:
    """Permanently remove local files plus catalog-only trash entries."""
    return await _purge_trash_rows(db_path, image_ids=image_ids)


async def hub_mirror_trash_refs(db_path: str) -> dict:
    """Return local mirror count and the corresponding hub catalog IDs."""
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT id, hub_image_id FROM images "
            "WHERE status = 'trashed' AND COALESCE(hub_remote, 0) = 1"
        )
        rows = await cursor.fetchall()
        hub_image_ids = unique_image_ids(row["hub_image_id"] for row in rows)
        return {
            "count": len(rows),
            "image_ids": [int(row["id"]) for row in rows],
            "hub_image_ids": hub_image_ids,
        }
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def local_trash_ids(db_path: str) -> list[int]:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT id FROM images WHERE status = 'trashed' AND COALESCE(hub_remote, 0) = 0"
        )
        return [int(row["id"]) for row in await cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def mark_hub_trash_pending(db_path: str, image_ids: list[int]) -> None:
    ids = unique_image_ids(image_ids)
    if not ids:
        return
    conn = await data_connection.open_async(db_path)
    try:
        for chunk in catalog_repository._chunked(ids):
            placeholders = ",".join("?" for _ in chunk)
            await conn.execute(
                f"UPDATE images SET trash_pending_hub = 1 WHERE status = 'trashed' "
                f"AND COALESCE(hub_remote, 0) = 1 AND id IN ({placeholders})",
                chunk,
            )
        await conn.commit()
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    invalidate_pending_hub_trash_refs(db_path)


async def pending_hub_trash_refs(db_path: str) -> dict:
    cached = _pending_hub_trash_refs_cache.get(db_path)
    if cached is not None:
        count, image_ids, hub_image_ids = cached
        return {"count": count, "image_ids": list(image_ids), "hub_image_ids": list(hub_image_ids)}
    version = _pending_hub_trash_refs_versions.get(db_path, 0)
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT id, hub_image_id FROM images WHERE status = 'trashed' "
            "AND COALESCE(hub_remote, 0) = 1 AND COALESCE(trash_pending_hub, 0) = 1"
        )
        rows = await cursor.fetchall()
        result = (
            len(rows),
            tuple(int(row["id"]) for row in rows),
            tuple(unique_image_ids(row["hub_image_id"] for row in rows)),
        )
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    if version == _pending_hub_trash_refs_versions.get(db_path, 0):
        _pending_hub_trash_refs_cache[db_path] = result
    count, image_ids, hub_image_ids = result
    return {"count": count, "image_ids": list(image_ids), "hub_image_ids": list(hub_image_ids)}


async def purge_expired_trash(
    db_path: str,
    *,
    retention_seconds: float = DEFAULT_RETENTION_SECONDS,
    now: float | None = None,
) -> dict:
    """Purge expired entries when any source-local trash file is reachable."""
    cutoff = (time.time() if now is None else float(now)) - max(0.0, float(retention_seconds))
    return await _purge_trash_rows(db_path, older_than=cutoff)


async def _delete_emptied_catalog_rows(db_path: str, image_ids: list[int]) -> tuple[list[int], list[Error]]:
    async def delete_ids(ids: list[int]) -> None:
        async def write() -> None:
            conn = await data_connection.open_async(db_path)
            try:
                await conn.execute("BEGIN IMMEDIATE")
                await catalog_repository.delete_image_catalog_rows_on_conn(conn, ids)
                await catalog_repository.update_source_counts_on_conn(conn)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
            finally:
                await data_connection.close_async(conn, db_path=db_path)

        with data_connection.sqlite_timeout(TRASH_WRITE_BUSY_TIMEOUT_SECONDS):
            await data_connection.run_with_busy_retry(
                write,
                backoff_seconds=TRASH_WRITE_RETRY_BACKOFF_SECONDS,
            )

    deleted: list[int] = []
    errors: list[Error] = []
    try:
        await delete_ids(image_ids)
        return list(image_ids), []
    except Exception as exc:
        if data_connection.is_sqlite_locked_error(exc):
            reason = f"catalog row deletion deferred: {exc}"
            return [], [_error(image_id, reason) for image_id in image_ids]
    for index, image_id in enumerate(image_ids):
        try:
            await delete_ids([image_id])
            deleted.append(image_id)
        except Exception as exc:
            reason = f"catalog row deletion failed: {exc}"
            errors.append(_error(image_id, reason))
            if data_connection.is_sqlite_locked_error(exc):
                errors.extend(_error(item, reason) for item in image_ids[index + 1:])
                break
    return deleted, errors


async def __to_thread_remove_trash_file(path: str | None, source_root: str | None) -> tuple[int, str]:
    import asyncio

    return await asyncio.to_thread(_remove_trash_file, path, source_root)


async def __to_thread_inspect_trash_file(path: str | None, source_root: str | None) -> tuple[int, str]:
    import asyncio

    return await asyncio.to_thread(_inspect_trash_file, path, source_root)


async def __to_thread_prune_empty_trash_dirs(paths: list[str]) -> None:
    import asyncio

    await asyncio.to_thread(_prune_empty_trash_dirs, paths)
