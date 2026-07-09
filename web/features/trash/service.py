"""Safe trash operations for catalog images."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import helpers as app_helpers
from data import connection as data_connection
from data.repositories import catalog as catalog_repository
from data.repositories import stacks as stack_repository
from features.stacks import builders as stack_builders


Error = dict[str, int | str]


def _clean_ids(values) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        try:
            image_id = int(value)
        except (TypeError, ValueError):
            continue
        if image_id <= 0 or image_id in seen:
            continue
        seen.add(image_id)
        ids.append(image_id)
    return ids


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
        return "Trash file is missing; restored catalog row only", ""
    if trash_stat is None:
        return None, reason
    if os.path.lexists(filepath):
        return None, "original path already exists"
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        final_stat, reason = _regular_file_lstat(trash_path)
        if reason == "missing":
            return "Trash file is missing; restored catalog row only", ""
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
            return (("Trash file is missing; restored catalog row only", "") if reason == "missing" else (None, reason))
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


async def trash_images(db_path: str, image_ids: list[int]) -> dict:
    ids = _clean_ids(image_ids)
    if not ids:
        return {"trashed": [], "errors": [], "freed_estimate_bytes": 0}
    trashed: list[int] = []
    errors: list[Error] = []
    freed_estimate_bytes = 0
    plans: list[dict] = []
    now = time.time()

    conn = await data_connection.open_async(db_path)
    try:
        rows = await _image_rows_by_id(conn, ids)
        for image_id in ids:
            row = rows.get(image_id)
            if row is None:
                errors.append(_error(image_id, "image not found"))
                continue
            if row.get("status") == "trashed":
                trashed.append(image_id)
                continue
            source_root = row.get("source_root") or ""
            filepath = row.get("filepath") or ""
            if not source_root or not filepath:
                errors.append(_error(image_id, "source path missing"))
                continue
            dest, moved_bytes, expected_token, reason = await __to_thread_prepare_trash_move(filepath, source_root)
            if reason:
                errors.append(_error(image_id, reason))
                continue
            plans.append({
                "id": image_id,
                "filepath": filepath,
                "trash_path": dest,
                "size": moved_bytes,
                "token": expected_token,
                "previous_status": row.get("status") or "kept",
                "previous_trashed_at": row.get("trashed_at"),
                "previous_trash_path": row.get("trash_path"),
            })

        if plans:
            await conn.execute("BEGIN")
            await conn.executemany(
                "UPDATE images SET status = 'trashed', trashed_at = ?, trash_path = ? WHERE id = ?",
                [(now, plan["trash_path"], plan["id"]) for plan in plans],
            )
            source_ids = sorted({
                int(rows[plan["id"]]["source_id"])
                for plan in plans
                if rows[plan["id"]].get("source_id") is not None
            })
            for source_id in source_ids:
                await catalog_repository.update_source_counts_on_conn(conn, source_id)
            await conn.commit()

            successful: list[dict] = []
            failed: list[dict] = []
            for plan in plans:
                moved_bytes, reason = await __to_thread_move_to_trash(
                    plan["filepath"],
                    plan["trash_path"],
                    plan["token"],
                )
                if reason:
                    errors.append(_error(plan["id"], reason))
                    failed.append(plan)
                    continue
                successful.append(plan)
                trashed.append(plan["id"])
                freed_estimate_bytes += moved_bytes
            if failed:
                await _revert_failed_trash_moves(conn, failed, rows)
            if successful:
                await conn.execute("BEGIN")
                await _repair_stacks_after_trash(conn, [plan["id"] for plan in successful])
                await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await data_connection.close_async(conn, db_path=db_path)
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


async def _revert_failed_trash_moves(conn, failed: list[dict], rows: dict[int, dict]) -> None:
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


async def restore_images(db_path: str, image_ids: list[int]) -> dict:
    ids = _clean_ids(image_ids)
    restored: list[int] = []
    errors: list[Error] = []
    warnings: list[Error] = []
    conn = await data_connection.open_async(db_path)
    try:
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
                "filepath": row.get("filepath") or "",
                "trash_path": row.get("trash_path"),
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
                    f"UPDATE images SET status = 'kept', trashed_at = NULL, trash_path = NULL "
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

            failed: list[dict] = []
            for plan in updates:
                warning, reason = await __to_thread_restore_from_trash(plan["trash_path"], plan["filepath"])
                if reason:
                    errors.append(_error(plan["id"], reason))
                    failed.append(plan)
                    continue
                if warning:
                    warnings.append(_error(plan["id"], warning))
                restored.append(plan["id"])
            if failed:
                await _revert_failed_restores(conn, failed, rows)
    except Exception:
        await conn.rollback()
        raise
    finally:
        await data_connection.close_async(conn, db_path=db_path)
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
            "SELECT COUNT(*) AS total, COALESCE(SUM(COALESCE(file_size, 0)), 0) AS total_bytes "
            "FROM images WHERE status = 'trashed'"
        )
        total_row = await total_cursor.fetchone()
        cursor = await conn.execute(
            "SELECT * FROM images WHERE status = 'trashed' "
            "ORDER BY trashed_at DESC, id DESC LIMIT ? OFFSET ?",
            (safe_limit, safe_offset),
        )
        images = []
        for row in await cursor.fetchall():
            card = app_helpers.image_card(dict(row), "sm")
            card["trashed_at"] = row["trashed_at"]
            card["file_size"] = row["file_size"]
            images.append(card)
        return {
            "images": images,
            "total": int(total_row["total"] or 0),
            "total_bytes": int(total_row["total_bytes"] or 0),
        }
    finally:
        await data_connection.close_async(conn, db_path=db_path)


def _remove_trash_file(path: str | None) -> tuple[int, str]:
    if not path:
        return 0, ""
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
    expected_token = _stat_token(lstat_result)
    size = int(lstat_result.st_size or 0)
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


async def empty_trash(db_path: str) -> dict:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT id, trash_path FROM images WHERE status = 'trashed'")
        rows = [dict(row) for row in await cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)

    deletable_ids: list[int] = []
    paths_to_prune: list[str] = []
    errors: list[Error] = []
    freed_bytes = 0
    for row in rows:
        image_id = int(row["id"])
        trash_path = row.get("trash_path")
        freed, reason = await __to_thread_remove_trash_file(trash_path)
        if reason:
            errors.append(_error(image_id, reason))
            continue
        freed_bytes += freed
        deletable_ids.append(image_id)
        if trash_path:
            paths_to_prune.append(trash_path)

    deleted_ids: list[int] = []
    if deletable_ids:
        deleted_ids, delete_errors = await _delete_emptied_catalog_rows(db_path, deletable_ids)
        errors.extend(delete_errors)
    await __to_thread_prune_empty_trash_dirs(paths_to_prune)
    return {
        "deleted_count": len(deleted_ids),
        "freed_bytes": int(freed_bytes),
        "errors": errors,
    }


async def _delete_emptied_catalog_rows(db_path: str, image_ids: list[int]) -> tuple[list[int], list[Error]]:
    deleted: list[int] = []
    errors: list[Error] = []
    conn = await data_connection.open_async(db_path)
    try:
        try:
            await conn.execute("BEGIN")
            await catalog_repository.delete_image_catalog_rows_on_conn(conn, image_ids)
            await catalog_repository.update_source_counts_on_conn(conn)
            await conn.commit()
            return list(image_ids), []
        except Exception:
            await conn.rollback()
        for image_id in image_ids:
            try:
                await conn.execute("BEGIN")
                await catalog_repository.delete_image_catalog_rows_on_conn(conn, [image_id])
                await catalog_repository.update_source_counts_on_conn(conn)
                await conn.commit()
                deleted.append(image_id)
            except Exception as exc:
                await conn.rollback()
                errors.append(_error(image_id, f"catalog row deletion failed: {exc}"))
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return deleted, errors


async def __to_thread_remove_trash_file(path: str | None) -> tuple[int, str]:
    import asyncio

    return await asyncio.to_thread(_remove_trash_file, path)


async def __to_thread_prune_empty_trash_dirs(paths: list[str]) -> None:
    import asyncio

    await asyncio.to_thread(_prune_empty_trash_dirs, paths)
