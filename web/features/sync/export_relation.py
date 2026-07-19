"""export_of — RAW↔export linkage via existing version stacks.

Finding: stacks kind ``version`` already pairs RAW+edit (stem match, then
capture-time+camera fallback in ``features.stacks.builders``). VC plumbing
joins the same way via ``join_version_stack``. No new relation table.
``export_of`` is the payload name for that membership.
"""

from __future__ import annotations

from typing import Any

from data import connection
from data.repositories import stacks as stack_repository
from features.stacks import builders
from features.sync import lr_bridge


async def link_export(
    db_path: str,
    *,
    source_filepath: str | None = None,
    export_filepath: str | None = None,
    source_image_id: int | None = None,
    export_image_id: int | None = None,
) -> dict[str, Any]:
    """Record an observed LR export as a version-stack export_of relation."""

    source_id = int(source_image_id or 0)
    export_id = int(export_image_id or 0)
    if source_id <= 0 and source_filepath:
        identity = await lr_bridge.resolve_filepath(db_path, source_filepath)
        if identity:
            source_id = int(identity["image_id"])
    if export_id <= 0 and export_filepath:
        identity = await lr_bridge.resolve_filepath(db_path, export_filepath)
        if identity:
            export_id = int(identity["image_id"])
    if source_id <= 0 or export_id <= 0:
        return {
            "linked": False,
            "reason": "unresolved",
            "source_image_id": source_id or None,
            "export_image_id": export_id or None,
        }
    if source_id == export_id:
        return {"linked": False, "reason": "same_image", "source_image_id": source_id, "export_image_id": export_id}
    result = await stack_repository.join_version_stack(db_path, source_id, export_id)
    return {
        "linked": bool(result.get("joined")),
        "reason": result.get("reason"),
        "stack_id": result.get("stack_id"),
        "source_image_id": source_id,
        "export_image_id": export_id,
        "relation": "export_of",
    }


def match_export_fallback(db_path: str, export_image_id: int) -> dict[str, Any] | None:
    """Stem + capture-time matcher — reuses version-group builder verbatim."""

    rows = builders._active_rows(db_path)
    if int(export_image_id) not in rows:
        return None
    for member_ids, representative_id, _scores in builders.build_version_groups(db_path, rows):
        if int(export_image_id) not in member_ids:
            continue
        edit = rows[int(export_image_id)]
        raw_ids = [image_id for image_id in member_ids if builders._is_raw(rows[image_id])]
        if not raw_ids:
            continue
        # Prefer exact stem match when several RAWs share a capture second.
        export_stem = builders._version_stem(edit)
        source_id = next(
            (image_id for image_id in raw_ids if builders._version_stem(rows[image_id]) == export_stem),
            raw_ids[0],
        )
        return {
            "relation": "export_of",
            "source_image_id": int(source_id),
            "export_image_id": int(export_image_id),
            "representative_image_id": int(representative_id),
            "match": "stem" if builders._version_stem(rows[source_id]) == export_stem else "capture_time",
        }
    return None


async def ensure_export_link(db_path: str, export_image_id: int) -> dict[str, Any]:
    """Link via fallback matcher when the plugin did not report a source path."""

    existing = await export_of_for_image(db_path, export_image_id)
    if existing:
        return {"linked": True, "reason": "existing", **existing}
    matched = match_export_fallback(db_path, int(export_image_id))
    if matched is None:
        return {"linked": False, "reason": "no_match", "export_image_id": int(export_image_id)}
    return await link_export(
        db_path,
        source_image_id=int(matched["source_image_id"]),
        export_image_id=int(matched["export_image_id"]),
    )


async def export_of_for_image(db_path: str, image_id: int) -> dict[str, Any] | None:
    """Payload fragment for either side of an export_of version stack."""

    stack = await stack_repository.stack_for_image(db_path, int(image_id))
    if stack is None or str(stack.get("kind") or "") != "version":
        return None
    members = list(stack.get("members") or [])
    if len(members) < 2:
        return None
    ids = [int(member.get("id") or member.get("image_id") or 0) for member in members]
    ids = [image_id for image_id in ids if image_id > 0]
    if len(ids) < 2:
        return None
    conn = await connection.open_async(db_path)
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = {
            int(row["id"]): dict(row)
            for row in await (
                await conn.execute(
                    f"SELECT id, filename, filepath, file_ext FROM images WHERE id IN ({placeholders})",
                    ids,
                )
            ).fetchall()
        }
    finally:
        await connection.close_async(conn, db_path=db_path)

    raw_ids = [mid for mid, row in rows.items() if builders._is_raw(row)]
    edit_ids = [mid for mid, row in rows.items() if mid not in raw_ids]
    if not raw_ids or not edit_ids:
        return None
    source_id = raw_ids[0]
    # Prefer this image as the export side when it is an edit; else first edit.
    export_id = int(image_id) if int(image_id) in edit_ids else edit_ids[0]
    if int(image_id) in raw_ids:
        source_id = int(image_id)
    representative = stack.get("representative") or {}
    representative_id = int(representative.get("id") or stack.get("representative_image_id") or export_id)
    return {
        "relation": "export_of",
        "stack_id": int(stack["id"]),
        "source_image_id": source_id,
        "export_image_id": export_id,
        "member_ids": sorted(ids),
        "representative_image_id": representative_id,
    }


async def attach_export_of(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of ``payload`` with export_of when linked."""

    image_id = int(payload.get("id") or 0)
    if image_id <= 0:
        return payload
    relation = await export_of_for_image(db_path, image_id)
    if relation is None:
        return payload
    enriched = dict(payload)
    enriched["export_of"] = relation
    return enriched
