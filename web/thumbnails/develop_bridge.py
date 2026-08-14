"""Edited photos take the Develop pipeline into the thumbnail cache.

The grid tile of an edited photo must be the edit, not the camera's embedded
preview of the unedited frame. This bridge answers one question — does this
photo have develop settings? — and, when it does, renders every thumb tier
from the same working base Develop edits on, stamping each entry with the
recipe that names the edit (``pixels/rendition.py``). Unedited photos never
enter this module and keep their fast embedded-preview path.
"""

from __future__ import annotations

import json
import sqlite3

from pixels.rendition import recipe_for
from thumbnails import cache_entries, config, data_providers


def edit_state(image_id: int) -> tuple[str, dict] | None:
    """(fingerprint, settings) when the photo has real develop edits, else None.

    A settings row whose JSON is empty is not an edit — reset-to-default
    photos render identically to the unedited pipeline and stay on it. The
    fingerprint is the row's updated_at, the same value the serve path reads
    by LEFT JOIN, so a saved edit changes every tier's expected recipe at
    once.
    """

    try:
        conn = sqlite3.connect(data_providers.db_path(), timeout=0.25)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT settings, updated_at FROM develop_settings WHERE image_id = ?",
                (int(image_id),),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        settings = json.loads(row["settings"] or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(settings, dict) or not settings:
        return None
    return str(row["updated_at"] or ""), settings


def thumb_recipe(size: str, fingerprint: str) -> str:
    return recipe_for(
        cache_version=config.CACHE_VERSION,
        kind=size,
        pixels=config.SIZES[size],
        quality=config.THUMB_QUALITY,
        develop_fingerprint=fingerprint,
    )


def render_and_store(
    filepath: str,
    image_id: int,
    sizes: list[str],
    *,
    fingerprint: str,
    settings: dict,
    build_source_signature,
    memory_put,
    hot: bool,
) -> dict[str, bytes] | None:
    """Render the requested thumb tiers of one edited photo and cache them.

    One pipeline pass at the largest tier, downscaled for the rest — the same
    trick the unedited encode ladder uses. Tiers larger than the working base
    (lg at 3840 vs the 2048 base) come out at base resolution, which is what
    Develop itself shows. Returns the rendered bytes per tier, or None when no
    base can be produced (original not local yet, decode failure) so callers
    fall back to the unedited pipeline rather than leave a hole.
    """

    from features.develop.rawproc import RawDecodeError
    from features.develop.render import render_edited_thumbnail
    from PIL import Image
    import io

    wanted = sorted(
        (size for size in sizes if size in config.THUMB_TIERS),
        key=lambda size: config.SIZES[size],
        reverse=True,
    )
    if not wanted:
        return {}
    try:
        largest = render_edited_thumbnail(
            image_id,
            filepath,
            settings,
            long_side=config.SIZES[wanted[0]],
            quality=config.THUMB_QUALITY,
        )
    except (RawDecodeError, FileNotFoundError, OSError):
        return None

    data_by_size = {wanted[0]: largest}
    if len(wanted) > 1:
        with Image.open(io.BytesIO(largest)) as source:
            source.load()
            for size in wanted[1:]:
                target = config.SIZES[size]
                scaled = source.copy()
                scaled.thumbnail((target, target), Image.LANCZOS)
                buffer = io.BytesIO()
                scaled.save(buffer, format="JPEG", quality=config.THUMB_QUALITY)
                data_by_size[size] = buffer.getvalue()

    for size, data in data_by_size.items():
        signature = build_source_signature(filepath, size, image_id)
        cache_entries._write_thumbnail_to_disk(
            size,
            image_id,
            signature,
            data,
            hot=hot,
            recipe=thumb_recipe(size, fingerprint),
        )
        memory_put(size, image_id, signature, data)
    return data_by_size
