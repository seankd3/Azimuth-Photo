"""Stack repository for collapsed groups of related photos."""

from __future__ import annotations

import asyncio
import posixpath
import time
from collections.abc import Iterable

import helpers as app_helpers
from data import connection as data_connection
from data.repositories.common import chunked


VALID_KINDS = {"burst", "variant", "crosssource", "manual"}
AUTO_PRIORITIES = {"burst": 1, "variant": 2, "crosssource": 3}


class ManualStackConflict(ValueError):
    def __init__(self, image_ids: Iterable[int]):
        self.image_ids = sorted({int(image_id) for image_id in image_ids})
        super().__init__("Images already belong to a manual stack")


class UnknownStackImages(ValueError):
    def __init__(self, image_ids: Iterable[int]):
        self.image_ids = sorted({int(image_id) for image_id in image_ids})
        super().__init__("Stack images do not exist")


def _normalize_kind(kind: str) -> str:
    value = (kind or "").strip().lower()
    if value not in VALID_KINDS:
        raise ValueError(f"Unsupported stack kind: {kind}")
    return value


def _unique_ids(values) -> list[int]:
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


def _normalize_member_rows(member_rows, representative_image_id: int | None = None) -> list[dict]:
    rows: list[dict] = []
    seen: set[int] = set()
    for row in member_rows or []:
        if isinstance(row, dict):
            raw_id = row.get("image_id", row.get("id"))
            score = row.get("score")
        else:
            raw_id = row[0] if row else None
            score = row[1] if len(row) > 1 else None
        try:
            image_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if image_id <= 0 or image_id in seen:
            continue
        seen.add(image_id)
        rows.append({"image_id": image_id, "score": score})
    if representative_image_id is not None and int(representative_image_id) not in seen:
        rows.insert(0, {"image_id": int(representative_image_id), "score": None})
    return rows


def _member_score(rows: list[dict], image_id: int):
    for row in rows:
        if int(row["image_id"]) == int(image_id):
            return row.get("score")
    return None


def _path_for_label(value: str | None) -> str:
    return str(value or "").replace("\\", "/").rstrip("/")


def _folder(filepath: str | None, source_path: str | None = None) -> str:
    path = _path_for_label(filepath)
    if not path:
        return ""
    source = _path_for_label(source_path)
    label_path = path
    if source:
        source_cmp = source.lower()
        path_cmp = path.lower()
        if path_cmp.startswith(source_cmp + "/"):
            label_path = path[len(source) + 1:]
    directory = posixpath.dirname(label_path)
    if not directory or directory == ".":
        return ""
    return posixpath.basename(directory)


def _member_card(row) -> dict:
    data = dict(row)
    card = app_helpers.image_card(data, "sm")
    card["folder"] = _folder(data.get("filepath"), data.get("source_path"))
    if "score" in data:
        card["score"] = data.get("score")
    return card


async def _manual_conflicts(conn, image_ids: list[int]) -> set[int]:
    if not image_ids:
        return set()
    conflicts: set[int] = set()
    for ids in chunked(image_ids, 900):
        placeholders = ",".join("?" for _ in ids)
        cursor = await conn.execute(
            "SELECT sm.image_id "
            "FROM stack_members sm JOIN stacks s ON s.id = sm.stack_id "
            f"WHERE s.auto = 0 AND sm.image_id IN ({placeholders})",
            ids,
        )
        conflicts.update(int(row["image_id"]) for row in await cursor.fetchall())
    return conflicts


async def _missing_image_ids(conn, image_ids: list[int]) -> set[int]:
    if not image_ids:
        return set()
    found: set[int] = set()
    for ids in chunked(image_ids, 900):
        placeholders = ",".join("?" for _ in ids)
        cursor = await conn.execute(f"SELECT id FROM images WHERE id IN ({placeholders})", ids)
        found.update(int(row["id"]) for row in await cursor.fetchall())
    return set(image_ids) - found


async def _delete_auto_memberships(conn, image_ids: list[int]) -> None:
    if not image_ids:
        return
    for ids in chunked(image_ids, 900):
        placeholders = ",".join("?" for _ in ids)
        await conn.execute(
            "DELETE FROM stack_members "
            "WHERE image_id IN (" + placeholders + ") "
            "AND stack_id IN (SELECT id FROM stacks WHERE auto = 1)",
            ids,
        )


async def _repair_auto_stacks(conn, now: float) -> None:
    doomed_cursor = await conn.execute(
        "SELECT s.id FROM stacks s "
        "LEFT JOIN stack_members sm ON sm.stack_id = s.id "
        "WHERE s.auto = 1 "
        "GROUP BY s.id HAVING COUNT(sm.image_id) < 2"
    )
    doomed_ids = [int(row["id"]) for row in await doomed_cursor.fetchall()]
    if doomed_ids:
        for ids in chunked(doomed_ids, 900):
            placeholders = ",".join("?" for _ in ids)
            await conn.execute(f"DELETE FROM stack_members WHERE stack_id IN ({placeholders})", ids)
            await conn.execute(f"DELETE FROM stacks WHERE id IN ({placeholders})", ids)
    await conn.execute(
        "DELETE FROM stack_members "
        "WHERE stack_id NOT IN (SELECT id FROM stacks)"
    )


async def _repair_auto_representatives(conn, now: float) -> None:
    await conn.execute(
        "DELETE FROM stack_members "
        "WHERE stack_id NOT IN (SELECT id FROM stacks)"
    )
    cursor = await conn.execute(
        "SELECT s.id, s.representative_image_id "
        "FROM stacks s WHERE s.auto = 1 "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM stack_members sm "
        "  WHERE sm.stack_id = s.id AND sm.image_id = s.representative_image_id"
        ")"
    )
    for row in await cursor.fetchall():
        member_cursor = await conn.execute(
            "SELECT image_id FROM stack_members "
            "WHERE stack_id = ? ORDER BY score DESC, image_id ASC LIMIT 1",
            (int(row["id"]),),
        )
        member = await member_cursor.fetchone()
        if member is not None:
            await conn.execute(
                "UPDATE stacks SET representative_image_id = ?, updated_at = ? WHERE id = ?",
                (int(member["image_id"]), now, int(row["id"])),
            )


async def create_stack(
    db_path: str,
    *,
    kind: str,
    representative_image_id: int,
    member_rows,
    auto: bool = True,
) -> dict:
    kind = _normalize_kind(kind)
    rows = _normalize_member_rows(member_rows, int(representative_image_id))
    image_ids = [int(row["image_id"]) for row in rows]
    if len(image_ids) < 2:
        raise ValueError("A stack needs at least two images")
    if int(representative_image_id) not in image_ids:
        raise ValueError("Representative must be a stack member")

    now = time.time()
    conn = await data_connection.open_async(db_path)
    try:
        await conn.execute("BEGIN")
        if not auto:
            missing = await _missing_image_ids(conn, image_ids)
            if missing:
                raise UnknownStackImages(missing)
            conflicts = await _manual_conflicts(conn, image_ids)
            if conflicts:
                raise ManualStackConflict(conflicts)
            await _delete_auto_memberships(conn, image_ids)
            await _repair_auto_stacks(conn, now)
            await _repair_auto_representatives(conn, now)
        cursor = await conn.execute(
            "INSERT INTO stacks (kind, representative_image_id, auto, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, int(representative_image_id), 1 if auto else 0, now, now),
        )
        stack_id = int(cursor.lastrowid)
        await conn.executemany(
            "INSERT INTO stack_members (stack_id, image_id, score, added_at) VALUES (?, ?, ?, ?)",
            [(stack_id, int(row["image_id"]), row.get("score"), now) for row in rows],
        )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return await get_stack(db_path, stack_id) or {"id": stack_id}


async def get_stack(db_path: str, stack_id: int) -> dict | None:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT * FROM stacks WHERE id = ?", (int(stack_id),))
        stack = await cursor.fetchone()
        if stack is None:
            return None
        stack_row = dict(stack)
        member_cursor = await conn.execute(
            "SELECT i.*, sm.score, cs.path AS source_path "
            "FROM stack_members sm JOIN images i ON i.id = sm.image_id "
            "LEFT JOIN catalog_sources cs ON cs.id = i.source_id "
            "WHERE sm.stack_id = ? "
            "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
            "ORDER BY CASE WHEN i.id = ? THEN 0 ELSE 1 END, sm.score DESC, i.id ASC",
            (int(stack_id), int(stack_row["representative_image_id"])),
        )
        members = [_member_card(row) for row in await member_cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    representative = next(
        (member for member in members if int(member["id"]) == int(stack_row["representative_image_id"])),
        members[0] if members else None,
    )
    return {
        "id": int(stack_row["id"]),
        "kind": stack_row["kind"],
        "auto": bool(stack_row["auto"]),
        "created_at": stack_row.get("created_at"),
        "updated_at": stack_row.get("updated_at"),
        "member_count": len(members),
        "representative": representative,
        "members": members,
        "members_preview": members[:4],
    }


async def list_stacks(
    db_path: str,
    *,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    params: list = []
    where = ""
    if kind:
        normalized = _normalize_kind(kind)
        where = "WHERE s.kind = ?"
        params.append(normalized)
    safe_limit = max(1, min(int(limit or 50), 500))
    safe_offset = max(0, int(offset or 0))
    conn = await data_connection.open_async(db_path)
    try:
        total_cursor = await conn.execute(
            f"SELECT COUNT(*) AS count FROM stacks s {where}",
            params,
        )
        total = int((await total_cursor.fetchone())["count"] or 0)
        cursor = await conn.execute(
            "SELECT s.*, COUNT(i.id) AS member_count "
            "FROM stacks s JOIN stack_members sm ON sm.stack_id = s.id "
            "JOIN images i ON i.id = sm.image_id "
            "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
            f"{where} GROUP BY s.id "
            "ORDER BY s.updated_at DESC, s.id DESC LIMIT ? OFFSET ?",
            [*params, safe_limit, safe_offset],
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        stack_ids = [int(row["id"]) for row in rows]
        member_rows = []
        if stack_ids:
            placeholders = ",".join("?" for _ in stack_ids)
            member_cursor = await conn.execute(
                "SELECT sm.stack_id, i.*, sm.score, cs.path AS source_path "
                "FROM stack_members sm JOIN images i ON i.id = sm.image_id "
                "LEFT JOIN catalog_sources cs ON cs.id = i.source_id "
                f"WHERE sm.stack_id IN ({placeholders}) "
                "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
                "ORDER BY sm.stack_id ASC, sm.score DESC, i.id ASC",
                stack_ids,
            )
            member_rows = [dict(row) for row in await member_cursor.fetchall()]
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    members_by_stack: dict[int, list[dict]] = {}
    for row in member_rows:
        members_by_stack.setdefault(int(row["stack_id"]), []).append(_member_card(row))
    stacks = []
    for row in rows:
        stack_id = int(row["id"])
        members = members_by_stack.get(stack_id, [])
        representative_id = int(row["representative_image_id"])
        members.sort(
            key=lambda member: (
                0 if int(member["id"]) == representative_id else 1,
                -(float(member.get("score") or 0)),
                int(member["id"]),
            )
        )
        representative = next(
            (member for member in members if int(member["id"]) == representative_id),
            members[0] if members else None,
        )
        stacks.append({
            "id": stack_id,
            "kind": row["kind"],
            "auto": bool(row["auto"]),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "member_count": int(row["member_count"] or len(members)),
            "representative": representative,
            "members": members,
            "members_preview": members[:4],
        })
    return {"stacks": stacks, "total": total}


async def unstack(db_path: str, stack_id: int) -> bool:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT 1 FROM stacks WHERE id = ?", (int(stack_id),))
        exists = await cursor.fetchone() is not None
        if exists:
            await conn.execute("DELETE FROM stack_members WHERE stack_id = ?", (int(stack_id),))
            await conn.execute("DELETE FROM stacks WHERE id = ?", (int(stack_id),))
        await conn.commit()
        return exists
    finally:
        await data_connection.close_async(conn, db_path=db_path)


async def set_representative(db_path: str, stack_id: int, image_id: int) -> dict | None:
    now = time.time()
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT 1 FROM stack_members WHERE stack_id = ? AND image_id = ?",
            (int(stack_id), int(image_id)),
        )
        if await cursor.fetchone() is None:
            return None
        await conn.execute(
            "UPDATE stacks SET representative_image_id = ?, auto = 0, updated_at = ? WHERE id = ?",
            (int(image_id), now, int(stack_id)),
        )
        await conn.commit()
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return await get_stack(db_path, stack_id)


async def representative_stack_counts(db_path: str, image_ids) -> dict[int, dict]:
    ids = _unique_ids(image_ids)
    if not ids:
        return {}
    result: dict[int, dict] = {}
    conn = await data_connection.open_async(db_path)
    try:
        for chunk in chunked(ids, 900):
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                "SELECT s.representative_image_id, s.id AS stack_id, COUNT(sm.image_id) AS member_count "
                "FROM stacks s JOIN stack_members sm ON sm.stack_id = s.id "
                "JOIN images i ON i.id = sm.image_id "
                "JOIN catalog_sources cs ON cs.id = i.source_id "
                f"WHERE s.representative_image_id IN ({placeholders}) "
                "AND cs.included = 1 AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
                "GROUP BY s.id",
                chunk,
            )
            for row in await cursor.fetchall():
                result[int(row["representative_image_id"])] = {
                    "stack_id": int(row["stack_id"]),
                    "stack_count": int(row["member_count"] or 0),
                }
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return result


async def stack_for_image(db_path: str, image_id: int) -> dict | None:
    conn = await data_connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT s.id FROM stack_members sm JOIN stacks s ON s.id = sm.stack_id "
            "WHERE sm.image_id = ?",
            (int(image_id),),
        )
        row = await cursor.fetchone()
    finally:
        await data_connection.close_async(conn, db_path=db_path)
    return await get_stack(db_path, int(row["id"])) if row is not None else None


def _sync_fetch_manual_ids(conn, image_ids: list[int]) -> set[int]:
    if not image_ids:
        return set()
    result: set[int] = set()
    for ids in chunked(image_ids, 900):
        placeholders = ",".join("?" for _ in ids)
        cursor = conn.execute(
            "SELECT sm.image_id "
            "FROM stack_members sm JOIN stacks s ON s.id = sm.stack_id "
            f"WHERE s.auto = 0 AND sm.image_id IN ({placeholders})",
            ids,
        )
        result.update(int(row["image_id"]) for row in cursor.fetchall())
    return result


def _sync_auto_membership(conn, image_ids: list[int]) -> dict[int, dict]:
    if not image_ids:
        return {}
    result: dict[int, dict] = {}
    for ids in chunked(image_ids, 900):
        placeholders = ",".join("?" for _ in ids)
        cursor = conn.execute(
            "SELECT sm.image_id, s.id AS stack_id, s.kind, s.auto "
            "FROM stack_members sm JOIN stacks s ON s.id = sm.stack_id "
            f"WHERE sm.image_id IN ({placeholders})",
            ids,
        )
        for row in cursor.fetchall():
            result[int(row["image_id"])] = {
                "stack_id": int(row["stack_id"]),
                "kind": row["kind"],
                "auto": bool(row["auto"]),
            }
    return result


def _sync_repair_auto_stacks(conn, now: float) -> None:
    doomed_ids = [
        int(row["id"])
        for row in conn.execute(
            "SELECT s.id FROM stacks s "
            "LEFT JOIN stack_members sm ON sm.stack_id = s.id "
            "WHERE s.auto = 1 "
            "GROUP BY s.id HAVING COUNT(sm.image_id) < 2"
        ).fetchall()
    ]
    for ids in chunked(doomed_ids, 900):
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM stack_members WHERE stack_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM stacks WHERE id IN ({placeholders})", ids)
    conn.execute("DELETE FROM stack_members WHERE stack_id NOT IN (SELECT id FROM stacks)")
    for row in conn.execute(
        "SELECT s.id, s.representative_image_id "
        "FROM stacks s WHERE s.auto = 1 "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM stack_members sm "
        "  WHERE sm.stack_id = s.id AND sm.image_id = s.representative_image_id"
        ")"
    ).fetchall():
        member = conn.execute(
            "SELECT image_id FROM stack_members "
            "WHERE stack_id = ? ORDER BY score DESC, image_id ASC LIMIT 1",
            (int(row["id"]),),
        ).fetchone()
        if member is not None:
            conn.execute(
                "UPDATE stacks SET representative_image_id = ?, updated_at = ? WHERE id = ?",
                (int(member["image_id"]), now, int(row["id"])),
            )


def upsert_auto_stacks_sync(db_path: str, kind: str, groups) -> dict:
    kind = _normalize_kind(kind)
    if kind == "manual":
        raise ValueError("Manual stacks are not rebuilt automatically")
    priority = AUTO_PRIORITIES[kind]
    now = time.time()
    normalized_groups = []
    all_ids: list[int] = []
    for group in groups or []:
        member_ids, representative_id, scores = group
        rows = _normalize_member_rows(
            [{"image_id": image_id, "score": (scores or {}).get(image_id)} for image_id in member_ids],
            int(representative_id),
        )
        ids = [int(row["image_id"]) for row in rows]
        if len(ids) < 2:
            continue
        normalized_groups.append((ids, int(representative_id), rows))
        all_ids.extend(ids)

    conn = data_connection.open_sync(db_path)
    created = 0
    try:
        conn.execute("BEGIN")
        auto_kind_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id FROM stacks WHERE auto = 1 AND kind = ?", (kind,)).fetchall()
        ]
        for ids in chunked(auto_kind_ids, 900):
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM stack_members WHERE stack_id IN ({placeholders})", ids)
        conn.execute("DELETE FROM stacks WHERE auto = 1 AND kind = ?", (kind,))
        manual_ids = _sync_fetch_manual_ids(conn, _unique_ids(all_ids))
        for member_ids, representative_id, rows in normalized_groups:
            available = [image_id for image_id in member_ids if image_id not in manual_ids]
            if len(available) < 2:
                continue
            memberships = _sync_auto_membership(conn, available)
            final_ids: list[int] = []
            displaced_memberships: list[tuple[int, int]] = []
            for image_id in available:
                membership = memberships.get(image_id)
                if membership is None:
                    final_ids.append(image_id)
                    continue
                if not membership["auto"]:
                    continue
                existing_priority = AUTO_PRIORITIES.get(membership["kind"], 0)
                if existing_priority < priority:
                    displaced_memberships.append((membership["stack_id"], image_id))
                    final_ids.append(image_id)
            if len(final_ids) < 2:
                continue
            if displaced_memberships:
                conn.executemany(
                    "DELETE FROM stack_members WHERE stack_id = ? AND image_id = ?",
                    displaced_memberships,
                )
            rep_id = representative_id if representative_id in final_ids else final_ids[0]
            cursor = conn.execute(
                "INSERT INTO stacks (kind, representative_image_id, auto, created_at, updated_at) "
                "VALUES (?, ?, 1, ?, ?)",
                (kind, rep_id, now, now),
            )
            stack_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO stack_members (stack_id, image_id, score, added_at) VALUES (?, ?, ?, ?)",
                [
                    (stack_id, image_id, _member_score(rows, image_id), now)
                    for image_id in final_ids
                ],
            )
            created += 1
        _sync_repair_auto_stacks(conn, now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        data_connection.close_sync(conn, db_path=db_path)
    return {"kind": kind, "created": created, "groups": len(normalized_groups)}


async def upsert_auto_stacks(db_path: str, kind: str, groups) -> dict:
    return await asyncio.to_thread(upsert_auto_stacks_sync, db_path, kind, groups)
