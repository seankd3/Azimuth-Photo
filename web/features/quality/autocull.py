"""Review-first best-of-stack suggestions for the culling brief.

Suggestions are derived at request time and never alter a photo.  Applying a
brief is a separate, explicit action; it writes the previous flags to a small
audit trail so an accepted scene remains explainable even after the grid has
changed.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Any

from features.quality import scorer as quality_scorer


QUALITY_WEIGHT = 0.70
TASTE_WEIGHT = 0.30
TASTE_ELO_LOW = 800.0
TASTE_ELO_HIGH = 1600.0
SUPPORTED_STACK_KINDS = ("burst", "variant")

AUTOCULL_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS autocull_history (
    id INTEGER PRIMARY KEY,
    stack_id INTEGER NOT NULL REFERENCES stacks(id) ON DELETE CASCADE,
    picked_image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    applied_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS autocull_history_items (
    history_id INTEGER NOT NULL REFERENCES autocull_history(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    previous_flag TEXT NOT NULL,
    applied_flag TEXT NOT NULL,
    PRIMARY KEY(history_id, image_id)
);
"""


async def ensure_autocull_tables(conn) -> None:
    """Install the additive audit tables alongside image_quality."""
    await quality_scorer.ensure_image_quality(conn)
    await conn.executescript(AUTOCULL_HISTORY_DDL)


def normalize_stack_ids(stack_ids: list[int] | None) -> list[int]:
    seen: set[int] = set()
    cleaned: list[int] = []
    for value in stack_ids or []:
        try:
            stack_id = int(value)
        except (TypeError, ValueError):
            continue
        if stack_id > 0 and stack_id not in seen:
            seen.add(stack_id)
            cleaned.append(stack_id)
    return cleaned


async def _taste_scores() -> tuple[dict[int, float], bool]:
    """Reuse the library's learned-taste calculation without requiring it."""
    try:
        from features.library import taste

        vector = await taste.taste_vector()
        scores = await taste.taste_scaled_scores(vector)
        return (scores or {}, bool(vector.get("available") and scores))
    except Exception:
        # Technical quality remains useful before enough taste/embedding signal
        # exists.  The endpoint reports that omission so the UI does not imply
        # a personal recommendation where none was available.
        return {}, False


def _normalized_taste(taste_elo: float | None) -> float | None:
    if taste_elo is None:
        return None
    try:
        value = float(taste_elo)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return max(0.0, min(100.0, (value - TASTE_ELO_LOW) * 100.0 / (TASTE_ELO_HIGH - TASTE_ELO_LOW)))


def blended_score(quality_score: float, taste_elo: float | None) -> tuple[float, float | None]:
    """Blend 0–100 technical quality with the available taste-scaled Elo."""
    quality = max(0.0, min(100.0, float(quality_score)))
    taste = _normalized_taste(taste_elo)
    if taste is None:
        return round(quality, 3), None
    return round((QUALITY_WEIGHT * quality) + (TASTE_WEIGHT * taste), 3), round(taste, 3)


async def _eligible_members(conn, stack_ids: list[int] | None, cache_root: str) -> list[dict[str, Any]]:
    params: list[Any] = [cache_root, *SUPPORTED_STACK_KINDS]
    where = "s.kind IN (?, ?)"
    if stack_ids:
        where += f" AND s.id IN ({','.join('?' for _ in stack_ids)})"
        params.extend(stack_ids)
    cursor = await conn.execute(
        f"""
        SELECT s.id AS stack_id, s.kind AS stack_kind, i.id AS image_id,
               i.filename, i.flag, i.elo, q.score AS quality_score,
               EXISTS (
                   SELECT 1 FROM cache_entries c
                   WHERE c.image_id = i.id AND c.size = 'sm' AND c.cache_root = ?
               ) AS preview_ready
        FROM stacks s
        JOIN stack_members sm ON sm.stack_id = s.id
        JOIN images i ON i.id = sm.image_id
        JOIN image_quality q ON q.image_id = i.id
        WHERE {where}
          AND i.status IN ('kept', 'maybe')
          AND i.missing_at IS NULL
          AND i.trashed_at IS NULL
        ORDER BY s.id ASC, i.id ASC
        """,
        params,
    )
    return [dict(row) for row in await cursor.fetchall()]


async def suggestions(conn, *, stack_ids: list[int] | None = None, cache_root: str) -> dict[str, Any]:
    """Return fully-scored, untouched burst/variant stacks in review order."""
    await ensure_autocull_tables(conn)
    requested_ids = normalize_stack_ids(stack_ids)
    rows = await _eligible_members(conn, requested_ids or None, cache_root)
    taste_scores, taste_available = await _taste_scores()
    by_stack: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_stack[int(row["stack_id"])].append(row)

    result: list[dict[str, Any]] = []
    for stack_id, members in by_stack.items():
        # A scene is eligible only when every active member has a quality score,
        # a ready preview, and is untouched. This avoids visual decisions from
        # partial stacks and keeps the cull brief off the on-demand decode path.
        all_members_cursor = await conn.execute(
            """
            SELECT i.id, i.flag
            FROM stack_members sm JOIN images i ON i.id = sm.image_id
            WHERE sm.stack_id = ?
              AND i.status IN ('kept', 'maybe')
              AND i.missing_at IS NULL AND i.trashed_at IS NULL
            """,
            (stack_id,),
        )
        all_members = [dict(row) for row in await all_members_cursor.fetchall()]
        if len(all_members) < 2 or len(members) != len(all_members):
            continue
        if any(not bool(member.get("preview_ready")) for member in members):
            continue
        if any(str(member.get("flag") or "unflagged") != "unflagged" for member in all_members):
            continue

        serialized_members = []
        for member in members:
            score, taste_normalized = blended_score(member["quality_score"], taste_scores.get(int(member["image_id"])))
            serialized_members.append({
                "id": int(member["image_id"]),
                "filename": str(member.get("filename") or ""),
                "flag": "unflagged",
                "quality_score": round(float(member["quality_score"]), 3),
                "taste_score": taste_normalized,
                "taste_elo": round(float(taste_scores[int(member["image_id"])]), 3) if int(member["image_id"]) in taste_scores else None,
                "blended_score": score,
                "preview_ready": True,
                "thumb_url": f"/api/thumb/sm/{int(member['image_id'])}",
            })
        serialized_members.sort(key=lambda item: (-item["blended_score"], -item["quality_score"], item["id"]))
        serialized_members[0]["suggested_pick"] = True
        for member in serialized_members[1:]:
            member["suggested_pick"] = False
        result.append({
            "stack_id": stack_id,
            "stack_kind": str(members[0]["stack_kind"]),
            "member_count": len(serialized_members),
            "suggested_pick_id": serialized_members[0]["id"],
            "members": serialized_members,
        })

    return {
        "suggestions": result,
        "scene_count": len(result),
        "suggested_picks": len(result),
        "taste_available": taste_available,
        "quality_weight": QUALITY_WEIGHT,
        "taste_weight": TASTE_WEIGHT if taste_available else 0.0,
    }


async def apply(conn, *, stack_ids: list[int], cache_root: str) -> dict[str, Any]:
    """Accept current suggestions atomically, keeping pre-apply flags in history."""
    ids = normalize_stack_ids(stack_ids)
    if not ids:
        return {"ok": False, "error": "stack_ids must contain at least one stack", "applied": []}
    current = await suggestions(conn, stack_ids=ids, cache_root=cache_root)
    candidates = current["suggestions"]
    if not candidates:
        return {"ok": False, "error": "No ready, untouched, fully-scored suggestions found", "applied": []}

    now = time.time()
    applied = []
    await conn.execute("BEGIN")
    try:
        for suggestion in candidates:
            members = suggestion["members"]
            picked_id = int(suggestion["suggested_pick_id"])
            cursor = await conn.execute(
                "INSERT INTO autocull_history (stack_id, picked_image_id, applied_at) VALUES (?, ?, ?)",
                (int(suggestion["stack_id"]), picked_id, now),
            )
            history_id = int(cursor.lastrowid)
            history_rows = []
            for member in members:
                image_id = int(member["id"])
                applied_flag = "picked" if image_id == picked_id else "rejected"
                history_rows.append((history_id, image_id, "unflagged", applied_flag))
                await conn.execute("UPDATE images SET flag = ? WHERE id = ? AND flag = 'unflagged'", (applied_flag, image_id))
            await conn.executemany(
                "INSERT INTO autocull_history_items (history_id, image_id, previous_flag, applied_flag) VALUES (?, ?, ?, ?)",
                history_rows,
            )
            applied.append({
                "stack_id": int(suggestion["stack_id"]),
                "picked_image_id": picked_id,
                "rejected_image_ids": [int(member["id"]) for member in members if int(member["id"]) != picked_id],
                "history_id": history_id,
            })
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    return {"ok": True, "applied": applied, "applied_count": len(applied)}
