"""Hub-side catalog and cached-thumbnail exports for the v2 mirror."""

from __future__ import annotations

import json
import tarfile
import zlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import thumbnails

from data import connection
from features.library import keywords


CATALOG_FIELDS = frozenset({
    "hub_image_id", "content_hash", "filename", "file_ext", "file_size", "date_taken",
    "width", "height", "orientation", "camera_make", "camera_model", "lens", "latitude",
    "longitude", "location_source", "flag", "elo", "comparisons", "quality_score",
    "quality_sharpness", "quality_subject_sharpness", "quality_exposure_clip",
    "quality_motion_blur", "quality_eyes_open", "stack_id", "stack_kind",
    "stack_is_representative", "kind", "is_representative", "collection_ids", "keywords", "develop_settings",
    "develop_updated_at", "develop_origin", "rating", "rating_winner_key", "status", "filepath",
    "missing_at", "row_version",
})

_IMAGE_COLUMNS = (
    "i.id AS hub_image_id, i.content_hash, i.filename, i.file_ext, i.file_size, i.date_taken, "
    "i.width, i.height, i.orientation, i.camera_make, i.camera_model, i.lens, i.latitude, "
    "i.longitude, i.location_source, i.flag, i.elo, i.comparisons, i.status, i.filepath, "
    "i.missing_at, i.row_version, sm.stack_id, s.kind AS stack_kind, "
    "CASE WHEN s.representative_image_id = i.id THEN 1 ELSE 0 END AS stack_is_representative, "
    "s.kind AS kind, CASE WHEN s.representative_image_id = i.id THEN 1 ELSE 0 END AS is_representative, "
    "ds.settings AS develop_settings, ds.updated_at AS develop_updated_at, ds.origin AS develop_origin, "
    "json_extract(ds.settings, '$._lr_rating') AS rating, "
    "rating_state.ts AS rating_ts, rating_state.origin AS rating_origin, "
    "rating_state.origin_seq AS rating_origin_seq, "
    "q.score AS quality_score, q.sharpness AS quality_sharpness, "
    "q.subject_sharpness AS quality_subject_sharpness, q.exposure_clip AS quality_exposure_clip, "
    "q.motion_blur AS quality_motion_blur, q.eyes_open AS quality_eyes_open"
)


def parse_cursor(value: int | str | None) -> int:
    try:
        cursor = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("cursor must be a non-negative integer") from exc
    if cursor < 0:
        raise ValueError("cursor must be a non-negative integer")
    return cursor


async def _collection_ids(conn, image_id: int) -> list[int]:
    rows = await (await conn.execute(
        "SELECT collection_id FROM collection_images WHERE image_id = ? ORDER BY collection_id",
        (image_id,),
    )).fetchall()
    return [int(row["collection_id"]) for row in rows]


async def _keyword_paths(image_id: int) -> list[str]:
    # Keep the catalog's canonical hierarchical-path rules in one place.
    assigned = await keywords.image_keywords(image_id, include_ancestors=False)
    return [str(row["path"]) for row in assigned if row.get("path")]


async def build_row_payload(conn, row: Any) -> dict[str, Any]:
    """Serialize one hub image using the frozen mirror row contract."""

    generated_fields = {"collection_ids", "keywords", "rating", "rating_winner_key"}
    payload = {field: row[field] for field in CATALOG_FIELDS - generated_fields}
    payload["stack_is_representative"] = bool(payload["stack_is_representative"])
    payload["is_representative"] = bool(payload["is_representative"])
    payload["collection_ids"] = await _collection_ids(conn, int(payload["hub_image_id"]))
    payload["keywords"] = await _keyword_paths(int(payload["hub_image_id"]))
    payload["rating"] = row["rating"]
    payload["rating_winner_key"] = (
        {
            "ts": float(row["rating_ts"]),
            "origin": str(row["rating_origin"]),
            "origin_seq": int(row["rating_origin_seq"]),
        }
        if row["rating_ts"] is not None
        else None
    )
    settings = payload["develop_settings"]
    if isinstance(settings, str):
        try:
            payload["develop_settings"] = json.loads(settings)
        except json.JSONDecodeError:
            payload["develop_settings"] = {}
    elif settings is None:
        payload["develop_settings"] = {}
    if not isinstance(payload["develop_settings"], dict):
        payload["develop_settings"] = {}
    payload["develop_settings"].pop("_lr_rating", None)
    return payload


async def _catalog_snapshot(conn) -> int:
    row = await (await conn.execute("SELECT COALESCE(MAX(row_version), 0) AS cursor FROM images")).fetchone()
    return int(row["cursor"])


async def gzip_catalog_export_stream(db_path: str, cursor: int, limit: int = 0) -> AsyncIterator[bytes]:
    """Yield gzip-compressed NDJSON and a final cursor line.

    With a limit, the page ends at a clean row_version boundary (a version's
    rows are never split across pages), and the cursor line names the boundary
    so the next page resumes exactly there. A whole-catalog export over a slow
    link cannot finish inside any sane timeout; pages can.
    """

    compressor = zlib.compressobj(wbits=31)
    conn = await connection.open_async(db_path)
    try:
        await keywords.ensure_schema(conn)
        snapshot = await _catalog_snapshot(conn)
        rows = await conn.execute(
            f"SELECT {_IMAGE_COLUMNS} FROM images i "
            "LEFT JOIN stack_members sm ON sm.image_id = i.id "
            "LEFT JOIN stacks s ON s.id = sm.stack_id "
            "LEFT JOIN develop_settings ds ON ds.image_id = i.id "
            "LEFT JOIN oplog_family_state rating_state "
            "ON rating_state.content_hash = i.content_hash AND rating_state.family = 'rating' "
            "LEFT JOIN image_quality q ON q.image_id = i.id "
            "WHERE i.row_version > ? AND i.row_version <= ? "
            "ORDER BY i.row_version ASC, i.id ASC",
            (cursor, snapshot),
        )
        emitted = 0
        last_version = cursor
        page_cursor = snapshot
        async for row in rows:
            row_version = int(row["row_version"] or 0)
            if limit and emitted >= limit and row_version != last_version:
                page_cursor = last_version
                break
            payload = await build_row_payload(conn, row)
            encoded = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
            chunk = compressor.compress(encoded)
            if chunk:
                yield chunk
            emitted += 1
            last_version = row_version
        final = compressor.compress((json.dumps({"cursor": page_cursor}, separators=(",", ":")) + "\n").encode())
        if final:
            yield final
        yield compressor.flush()
    finally:
        await connection.close_async(conn, db_path=db_path)


def _tar_header(name: str, size: int, mtime: float) -> bytes:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o644
    info.mtime = int(mtime)
    return info.tobuf(format=tarfile.USTAR_FORMAT)


def _tar_padding(size: int) -> bytes:
    return b"\0" * ((-size) % tarfile.BLOCKSIZE)


def _cached_thumb_path(size: str, image_id: int) -> Path | None:
    # This only consults the thumbnail disk index; it deliberately does not
    # route through thumbnail generation or decode code.
    entry = thumbnails.fast_disk_path_entry(size, image_id)
    if entry is None:
        return None
    _signature, path = entry
    candidate = Path(path)
    return candidate if candidate.is_file() else None


async def thumbnail_pack_stream(
    db_path: str,
    *,
    size: str,
    after_id: int,
    limit: int,
    order: str = "asc",
) -> AsyncIterator[bytes]:
    """Yield an uncompressed tar followed by the v2 skipped-id trailer line.

    ``order=asc`` (default, frozen v2): ``id > after_id ORDER BY id ASC``.
    ``order=newest`` (additive): walk highest ids first so satellites fill
    recent work before the long tail. Cursor semantics flip to a high-water
    exclusive bound: ``after_id=0`` starts at the top; the trailer ``after_id``
    is the lowest id scanned so the next page uses ``id < after_id``.
    """

    newest = order == "newest"
    conn = await connection.open_async(db_path)
    try:
        if newest:
            if after_id <= 0:
                rows = await (
                    await conn.execute(
                        "SELECT id FROM images ORDER BY id DESC LIMIT ?",
                        (limit,),
                    )
                ).fetchall()
            else:
                rows = await (
                    await conn.execute(
                        "SELECT id FROM images WHERE id < ? ORDER BY id DESC LIMIT ?",
                        (after_id, limit),
                    )
                ).fetchall()
        else:
            rows = await (
                await conn.execute(
                    "SELECT id FROM images WHERE id > ? ORDER BY id ASC LIMIT ?",
                    (after_id, limit),
                )
            ).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)

    skipped: list[int] = []
    scanned_through = after_id
    for row in rows:
        image_id = int(row["id"])
        scanned_through = image_id
        path = _cached_thumb_path(size, image_id)
        if path is None:
            skipped.append(image_id)
            continue
        try:
            stat = path.stat()
            yield _tar_header(f"{image_id}.jpg", stat.st_size, stat.st_mtime)
            with path.open("rb") as handle:
                while chunk := handle.read(64 * 1024):
                    yield chunk
            padding = _tar_padding(stat.st_size)
            if padding:
                yield padding
        except OSError:
            skipped.append(image_id)
    trailer = (
        json.dumps(
            {"skipped": skipped, "after_id": scanned_through, "order": order},
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    yield _tar_header(".azimuth-trailer.json", len(trailer), 0)
    yield trailer
    padding = _tar_padding(len(trailer))
    if padding:
        yield padding
    yield b"\0" * (tarfile.BLOCKSIZE * 2)


def validate_thumb_pack_request(
    size: str,
    after_id: int | str | None,
    limit: int | str | None,
    order: str | None = None,
) -> tuple[str, int, int, str]:
    normalized_size = str(size or "").strip().lower()
    if normalized_size not in {"sm", "md", "lg"}:
        raise ValueError("size must be one of sm, md, or lg")
    cursor = parse_cursor(after_id)
    try:
        page_size = int(limit or 500)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer from 1 through 500") from exc
    if not 1 <= page_size <= 500:
        raise ValueError("limit must be an integer from 1 through 500")
    normalized_order = str(order or "asc").strip().lower() or "asc"
    if normalized_order not in {"asc", "newest"}:
        raise ValueError("order must be asc or newest")
    return normalized_size, cursor, page_size, normalized_order
