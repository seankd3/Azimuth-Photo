"""Verified-identical review and cleanup planning.

The catalog's ``content_hash`` is deliberately cheap (the first 8 MiB plus
file size).  It is excellent for finding candidates, but it is never treated
as permission to remove a file.  This module promotes a candidate group to
``ready`` only after every member has the same full-file digest and remains
unchanged for the duration of that read.
"""

from __future__ import annotations

import os
import stat
import threading
import time
import uuid
from copy import deepcopy
from pathlib import PurePath

from data import connection as data_connection
from photo.identity import compute_full_hash


_ACTIVE = "i.status IN ('kept', 'maybe') AND i.missing_at IS NULL AND i.vc_of IS NULL"
_lock = threading.Lock()
_candidate_lock = threading.Lock()
_candidate_cache: dict = {"db_path": "", "expires_at": 0.0, "groups": [], "summary": {}}
_summary_cache: dict = {"db_path": "", "expires_at": 0.0, "summary": {}}
_status: dict = {
    "state": "idle",
    "token": "",
    "started_at": None,
    "finished_at": None,
    "total_groups": 0,
    "scanned_groups": 0,
    "ready_groups": 0,
    "exception_groups": 0,
    "removable_count": 0,
    "reclaim_bytes": 0,
    "error": "",
}
_plans: list[dict] = []
_outcomes: dict[str, dict] = {}


def _folder(row: dict) -> str:
    source_path = str(row.get("source_path") or "")
    filepath = str(row.get("filepath") or "")
    try:
        relative = os.path.relpath(filepath, source_path) if source_path else filepath
    except ValueError:
        relative = filepath
    parts = PurePath(relative).parts
    return str(parts[-2]) if len(parts) > 1 else ""


def _card(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "filename": row.get("filename") or "",
        "filepath": row.get("filepath") or "",
        "folder": _folder(row),
        "source_name": row.get("source_name") or "",
        "source_online": bool(row.get("source_online", True)),
        "file_ext": row.get("file_ext"),
        "file_size": row.get("file_size"),
        "file_modified_at": row.get("file_modified_at"),
        "date_taken": row.get("date_taken"),
        "created_at": row.get("created_at"),
        "width": row.get("width"),
        "height": row.get("height"),
        "elo": row.get("elo"),
        "flag": row.get("flag") or "unflagged",
    }


def _catalog_keeper_key(row: dict) -> tuple:
    modified = float(row.get("file_modified_at") or 0)
    return (
        modified if modified > 0 else float("inf"),
        str(row.get("created_at") or "9999"),
        int(row["id"]),
    )


def candidate_groups_sync(db_path: str) -> tuple[list[dict], dict]:
    """Return all repeated fast-identity groups and an archive summary."""
    conn = data_connection.open_sync(db_path)
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                "WITH repeated AS ("
                " SELECT content_hash FROM images i INDEXED BY idx_images_active_content_hash_size WHERE " + _ACTIVE +
                " AND i.content_hash IS NOT NULL AND trim(i.content_hash) != ''"
                " GROUP BY content_hash HAVING COUNT(*) > 1"
                ") SELECT i.*, s.path AS source_path, s.display_name AS source_name, "
                "COALESCE(s.online, 1) AS source_online "
                "FROM images i INDEXED BY idx_images_active_content_hash_size "
                "JOIN repeated r ON r.content_hash = i.content_hash "
                "LEFT JOIN catalog_sources s ON s.id = i.source_id WHERE " + _ACTIVE +
                " AND i.content_hash IS NOT NULL AND trim(i.content_hash) != ''"
                " ORDER BY i.content_hash, i.id"
            ).fetchall()
        ]
        counts = conn.execute(
            "SELECT COUNT(*) AS active, "
            "SUM(CASE WHEN content_hash IS NOT NULL AND trim(content_hash) != '' THEN 1 ELSE 0 END) AS hashed "
            "FROM images i WHERE " + _ACTIVE
        ).fetchone()
    finally:
        data_connection.close_sync(conn, db_path=db_path)

    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(str(row["content_hash"]), []).append(row)

    groups: list[dict] = []
    for identity, members in buckets.items():
        members.sort(key=_catalog_keeper_key)
        cards = [_card(member) for member in members]
        groups.append({
            "key": identity,
            "kind": "identical",
            "member_count": len(cards),
            "representative": cards[0],
            "members": cards,
            "potential_reclaim_bytes": sum(int(member.get("file_size") or 0) for member in members[1:]),
        })
    groups.sort(
        key=lambda group: (
            -int(group["potential_reclaim_bytes"]),
            -int(group["member_count"]),
            group["key"],
        )
    )
    summary = {
        "total_groups": len(groups),
        "candidate_members": len(rows),
        "potential_removable_count": max(0, len(rows) - len(groups)),
        "potential_reclaim_bytes": sum(int(group["potential_reclaim_bytes"]) for group in groups),
        "active_images": int(counts["active"] or 0),
        "hashed_images": int(counts["hashed"] or 0),
    }
    return groups, summary


def candidate_page_sync(db_path: str, *, limit: int, offset: int) -> tuple[list[dict], bool]:
    """Fetch one page of candidate hashes before hydrating their members."""
    conn = data_connection.open_sync(db_path)
    try:
        hash_rows = conn.execute(
            "SELECT i.content_hash, COUNT(*) AS member_count FROM images i "
            "INDEXED BY idx_images_active_content_hash_size WHERE " + _ACTIVE +
            " AND i.content_hash IS NOT NULL AND trim(i.content_hash) != '' "
            "GROUP BY i.content_hash HAVING COUNT(*) > 1 "
            "ORDER BY i.content_hash LIMIT ? OFFSET ?",
            (limit + 1, offset),
        ).fetchall()
        has_more = len(hash_rows) > limit
        hash_rows = hash_rows[:limit]
        identities = [str(row["content_hash"]) for row in hash_rows]
        if not identities:
            return [], False
        placeholders = ",".join("?" for _ in identities)
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT i.*, s.path AS source_path, s.display_name AS source_name, "
                "COALESCE(s.online, 1) AS source_online FROM images i "
                "INDEXED BY idx_images_active_content_hash_size "
                "LEFT JOIN catalog_sources s ON s.id = i.source_id WHERE " + _ACTIVE +
                " AND i.content_hash IS NOT NULL AND trim(i.content_hash) != '' "
                f" AND i.content_hash IN ({placeholders}) ORDER BY i.content_hash, i.id",
                identities,
            ).fetchall()
        ]
    finally:
        data_connection.close_sync(conn, db_path=db_path)

    buckets: dict[str, list[dict]] = {identity: [] for identity in identities}
    for row in rows:
        buckets[str(row["content_hash"])].append(row)
    groups: list[dict] = []
    for identity in identities:
        members = buckets[identity]
        members.sort(key=_catalog_keeper_key)
        cards = [_card(member) for member in members]
        groups.append({
            "key": identity,
            "kind": "identical",
            "member_count": len(cards),
            "representative": cards[0],
            "members": cards,
            "potential_reclaim_bytes": sum(int(member.get("file_size") or 0) for member in members[1:]),
        })
    return groups, has_more


def candidate_summary_sync(db_path: str) -> dict:
    conn = data_connection.open_sync(db_path)
    try:
        grouped = conn.execute(
            "SELECT COUNT(*) AS total_groups, COALESCE(SUM(member_count), 0) AS candidate_members, "
            "COALESCE(SUM(member_count - 1), 0) AS potential_removable_count, "
            "COALESCE(SUM(reclaim_bytes), 0) AS potential_reclaim_bytes FROM ("
            " SELECT COUNT(*) AS member_count, "
            " SUM(COALESCE(i.file_size, 0)) - MAX(COALESCE(i.file_size, 0)) AS reclaim_bytes "
            " FROM images i INDEXED BY idx_images_active_content_hash_size WHERE " + _ACTIVE +
            " AND i.content_hash IS NOT NULL AND trim(i.content_hash) != '' "
            " GROUP BY i.content_hash HAVING COUNT(*) > 1"
            ")"
        ).fetchone()
        counts = conn.execute(
            "SELECT COUNT(*) AS active, "
            "SUM(CASE WHEN content_hash IS NOT NULL AND trim(content_hash) != '' THEN 1 ELSE 0 END) AS hashed "
            "FROM images i WHERE " + _ACTIVE
        ).fetchone()
    finally:
        data_connection.close_sync(conn, db_path=db_path)
    return {
        "total_groups": int(grouped["total_groups"] or 0),
        "candidate_members": int(grouped["candidate_members"] or 0),
        "potential_removable_count": int(grouped["potential_removable_count"] or 0),
        "potential_reclaim_bytes": int(grouped["potential_reclaim_bytes"] or 0),
        "active_images": int(counts["active"] or 0),
        "hashed_images": int(counts["hashed"] or 0),
    }


def _summary_snapshot(db_path: str) -> dict:
    now = time.monotonic()
    with _candidate_lock:
        if _summary_cache["db_path"] == db_path and _summary_cache["expires_at"] > now:
            return _summary_cache["summary"]
        summary = candidate_summary_sync(db_path)
        _summary_cache.update({"db_path": db_path, "expires_at": now + 15.0, "summary": summary})
        return summary


def _candidate_snapshot(db_path: str) -> tuple[list[dict], dict]:
    now = time.monotonic()
    with _candidate_lock:
        if _candidate_cache["db_path"] == db_path and _candidate_cache["expires_at"] > now:
            return _candidate_cache["groups"], _candidate_cache["summary"]
        groups, summary = candidate_groups_sync(db_path)
        _candidate_cache.update({
            "db_path": db_path,
            "expires_at": now + 15.0,
            "groups": groups,
            "summary": summary,
        })
        return groups, summary


def invalidate_candidates() -> None:
    with _candidate_lock:
        _candidate_cache.update({"db_path": "", "expires_at": 0.0, "groups": [], "summary": {}})
        _summary_cache.update({"db_path": "", "expires_at": 0.0, "summary": {}})


def list_candidates(db_path: str, *, limit: int = 25, offset: int = 0) -> dict:
    safe_limit = max(1, min(int(limit or 25), 100))
    safe_offset = max(0, int(offset or 0))
    groups, has_more = candidate_page_sync(db_path, limit=safe_limit, offset=safe_offset)
    provisional_total = safe_offset + len(groups) + (1 if has_more else 0)
    with _lock:
        outcomes = deepcopy(_outcomes)
        current_status = deepcopy(_status)
    page = deepcopy(groups)
    for group in page:
        outcome = outcomes.get(group["key"])
        group["verification"] = outcome or {"state": "candidate"}
    return {
        "groups": page,
        "total": provisional_total,
        "has_more": has_more,
        "summary": {"total_groups": provisional_total, "pending": has_more},
        "verification_status": current_status,
    }


def candidate_summary(db_path: str) -> dict:
    return deepcopy(_summary_snapshot(db_path))


def verification_status() -> dict:
    with _lock:
        return deepcopy(_status)


def queue_verification() -> str | None:
    """Reserve a job token synchronously so repeated clicks cannot race."""
    with _lock:
        if _status["state"] == "running":
            return None
        token = uuid.uuid4().hex
        _plans.clear()
        _outcomes.clear()
        _status.update({
            "state": "running",
            "token": token,
            "started_at": time.time(),
            "finished_at": None,
            "total_groups": 0,
            "scanned_groups": 0,
            "ready_groups": 0,
            "exception_groups": 0,
            "removable_count": 0,
            "reclaim_bytes": 0,
            "error": "",
        })
    return token


def run_verification(db_path: str, token: str) -> None:
    groups, _summary = _candidate_snapshot(db_path)
    with _lock:
        if _status["token"] != token:
            return
        _status["total_groups"] = len(groups)
    try:
        _verify_groups(groups, token)
    except Exception as exc:
        with _lock:
            if _status["token"] == token:
                _status.update({
                    "state": "error",
                    "finished_at": time.time(),
                    "error": str(exc) or "Verification failed",
                })
        raise


def _stat_token(value) -> tuple[int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size or 0),
        int(getattr(value, "st_mtime_ns", 0) or 0),
    )


def _verified_member(member: dict) -> dict:
    path = str(member.get("filepath") or "")
    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise OSError("not a regular file")
    digest = compute_full_hash(path)
    after = os.lstat(path)
    if _stat_token(before) != _stat_token(after):
        raise OSError("file changed during verification")
    return {**member, "full_hash": digest, "stat_token": _stat_token(after)}


def _verified_keeper_key(member: dict) -> tuple:
    token = member["stat_token"]
    modified_ns = int(token[3] or 0)
    return (
        modified_ns if modified_ns > 0 else int(float(member.get("file_modified_at") or 0) * 1_000_000_000) or 2**63,
        str(member.get("created_at") or "9999"),
        int(member["id"]),
    )


def _verify_groups(groups: list[dict], token: str) -> None:
    for index, group in enumerate(groups, start=1):
        identity = str(group["key"])
        try:
            verified = [_verified_member(member) for member in group["members"]]
            digests = {member["full_hash"] for member in verified}
            if len(digests) != 1:
                raise ValueError("candidate files differ after full verification")
            verified.sort(key=_verified_keeper_key)
            keeper = verified[0]
            losers = verified[1:]
            plan = {
                "candidate_hash": identity,
                "full_hash": keeper["full_hash"],
                "keeper_id": int(keeper["id"]),
                "trash_ids": [int(member["id"]) for member in losers],
                "reclaim_bytes": sum(int(member.get("file_size") or member["stat_token"][2]) for member in losers),
                "members": [
                    {"id": int(member["id"]), "filepath": member["filepath"], "stat_token": member["stat_token"]}
                    for member in verified
                ],
            }
            outcome = {
                "state": "ready",
                "keeper_id": plan["keeper_id"],
                "removable_count": len(plan["trash_ids"]),
                "reclaim_bytes": plan["reclaim_bytes"],
            }
            with _lock:
                if _status["token"] != token:
                    return
                _plans.append(plan)
                _outcomes[identity] = outcome
                _status["ready_groups"] += 1
                _status["removable_count"] += len(plan["trash_ids"])
                _status["reclaim_bytes"] += int(plan["reclaim_bytes"])
        except (OSError, ValueError) as exc:
            with _lock:
                if _status["token"] != token:
                    return
                _outcomes[identity] = {"state": "exception", "reason": str(exc) or "could not verify"}
                _status["exception_groups"] += 1
        with _lock:
            if _status["token"] != token:
                return
            _status["scanned_groups"] = index

    with _lock:
        if _status["token"] == token:
            _status.update({"state": "complete", "finished_at": time.time()})


def validated_cleanup_ids(token: str) -> tuple[list[int], list[dict]]:
    """Revalidate every member's stat token before returning removable ids."""
    with _lock:
        if not token or token != _status["token"] or _status["state"] != "complete":
            raise ValueError("Verification plan is stale or incomplete")
        plans = deepcopy(_plans)
    image_ids: list[int] = []
    skipped: list[dict] = []
    for plan in plans:
        try:
            for member in plan["members"]:
                current = os.lstat(member["filepath"])
                if _stat_token(current) != tuple(member["stat_token"]):
                    raise OSError("file changed after verification")
            image_ids.extend(int(image_id) for image_id in plan["trash_ids"])
        except OSError as exc:
            skipped.append({
                "candidate_hash": plan["candidate_hash"],
                "reason": str(exc) or "file is no longer available",
            })
    return image_ids, skipped


def finish_cleanup(token: str) -> None:
    with _lock:
        if token != _status["token"]:
            return
        _status.update({"state": "idle", "token": "", "finished_at": time.time()})
        _plans.clear()
        _outcomes.clear()
    invalidate_candidates()
