"""Candidate-selection helpers for thumbnail pregeneration."""

import time
from collections.abc import Callable


def bulk_candidate_signatures(
    row,
    tier_room: dict[str, int],
    tier_budgets: dict[str, int],
    *,
    thumb_tiers: tuple[str, ...],
    replace_stale_thumbnails: bool,
    fast_disk_has: Callable[..., bool],
    build_catalog_source_signature: Callable[
        [str, str, int, object, object],
        tuple[str, int | None, bool],
    ],
    estimated_tier_bytes: Callable[[str], int],
    retry_after: dict[tuple[str, int, str], float],
    now: float | None = None,
) -> tuple[dict[str, str], int | None]:
    image_id = int(row["id"])
    filepath = row["filepath"]
    source_size = None
    source_missing = False
    signatures = {}
    active_tiers = [size for size in thumb_tiers if tier_budgets.get(size, 0) > 0]

    if (
        not replace_stale_thumbnails
        and active_tiers
        and all(fast_disk_has(size, image_id) for size in active_tiers)
    ):
        try:
            return {}, int(row["file_size"]) if row["file_size"] is not None else None
        except (TypeError, ValueError):
            return {}, None

    for size in active_tiers:
        signature, row_source_size, row_source_missing = build_catalog_source_signature(
            filepath,
            size,
            image_id,
            row["file_size"],
            row["file_modified_at"],
        )
        signatures[size] = signature
        if source_size is None and row_source_size is not None:
            source_size = row_source_size
        source_missing = source_missing or row_source_missing

    if source_missing:
        return {}, source_size

    needed = {}
    retry_now = time.time() if now is None else now
    for size in active_tiers:
        source_signature = signatures[size]
        if retry_after.get((size, image_id, source_signature), 0) > retry_now:
            continue
        if fast_disk_has(size, image_id, source_signature):
            continue

        existing_entry = fast_disk_has(size, image_id)
        if not existing_entry:
            estimated_bytes = estimated_tier_bytes(size)
            if tier_room.get(size, 0) < estimated_bytes:
                continue
            tier_room[size] = max(0, tier_room.get(size, 0) - estimated_bytes)

        needed[size] = source_signature

    return needed, source_size


def full_candidate_signature(
    row,
    full_room: dict[str, int],
    full_budget: int,
    *,
    full_tier: str,
    is_browser_displayable_original: Callable[[str], bool],
    fast_disk_path_entry: Callable[[str, int], tuple[str, str] | None],
    source_bits_from_catalog_metadata: Callable[[str, object, object], tuple[str, int | None, bool]],
    build_source_signature_from_bits: Callable[[str, str, int], str],
    full_cache_has_room: Callable[[int, int, int], bool],
) -> dict | None:
    image_id = int(row["id"])
    filepath = row["filepath"]
    if not is_browser_displayable_original(filepath):
        return None
    if fast_disk_path_entry(full_tier, image_id) is not None:
        return None

    source_bits, source_size, source_missing = source_bits_from_catalog_metadata(
        filepath,
        row["file_size"],
        row["file_modified_at"],
    )
    if source_missing:
        return None
    try:
        source_size = int(source_size)
    except (TypeError, ValueError):
        return None
    source_signature = build_source_signature_from_bits(source_bits, full_tier, image_id)
    if source_size > full_budget:
        return None
    if full_room.get("bytes", 0) < source_size and not full_cache_has_room(image_id, source_size, full_budget):
        return None

    full_room["bytes"] = max(0, int(full_room.get("bytes", 0)) - int(source_size))
    return {
        "id": image_id,
        "filepath": filepath,
        "signature": source_signature,
        "source_size": int(source_size),
    }
