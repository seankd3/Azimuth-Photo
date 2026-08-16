"""HTTP surface for Develop settings and base-preview artifacts."""

from __future__ import annotations

from core.catalog_path import catalog_path

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from features.develop import rawproc

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from data import connection
from data.repositories import images as image_repository
from data.repositories import stacks as stack_repository
from features.develop import history, presets, virtual_copies
import judgements

log = logging.getLogger(__name__)


router = APIRouter()
_pregen_tasks: set[asyncio.Task] = set()
_batch_tasks: set[asyncio.Task] = set()
_base_generation_tasks: dict[int, asyncio.Task] = {}
_base_generation_failures: dict[int, tuple[float, rawproc.RawDecodeError]] = {}
_BASE_FAILURE_TTL_SECONDS = 5.0
HISTORY_EDIT_LIMIT = 40
HISTORY_SNAPSHOT_LIMIT = 40
_batch_status: dict[str, Any] = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "total": 0,
    "done": 0,
    "current_id": None,
    "results": [],
    "errors": [],
}

# §24 sync groups — masks copies MaskGroupBasedCorrections wholesale.
_HSL_BANDS = ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta")
SYNC_GROUP_KEYS: dict[str, tuple[str, ...]] = {
    "wb": ("Temperature", "Tint", "WhiteBalance"),
    "tone": (
        "Exposure2012",
        "Contrast2012",
        "Highlights2012",
        "Shadows2012",
        "Whites2012",
        "Blacks2012",
    ),
    "presence": ("Texture", "Clarity2012", "Dehaze", "Vibrance", "Saturation"),
    "curve": (
        "ToneCurvePV2012",
        "ToneCurvePV2012Red",
        "ToneCurvePV2012Green",
        "ToneCurvePV2012Blue",
    ),
    "hsl": (
        "ConvertToGrayscale",
        *(f"HueAdjustment{band}" for band in _HSL_BANDS),
        *(f"SaturationAdjustment{band}" for band in _HSL_BANDS),
        *(f"LuminanceAdjustment{band}" for band in _HSL_BANDS),
        *(f"GrayMixer{band}" for band in _HSL_BANDS),
    ),
    "grade": (
        "ColorGradeShadowHue",
        "ColorGradeShadowSat",
        "ColorGradeShadowLum",
        "ColorGradeMidtoneHue",
        "ColorGradeMidtoneSat",
        "ColorGradeMidtoneLum",
        "ColorGradeHighlightHue",
        "ColorGradeHighlightSat",
        "ColorGradeHighlightLum",
        "ColorGradeGlobalHue",
        "ColorGradeGlobalSat",
        "ColorGradeGlobalLum",
        "ColorGradeBlending",
        "ColorGradeBalance",
    ),
    "detail": (
        "Sharpness",
        "SharpenRadius",
        "SharpenEdgeMasking",
        "LuminanceSmoothing",
        "ColorNoiseReduction",
        "LuminanceDetail",
        "LuminanceContrast",
        "ColorNoiseReductionDetail",
        "ColorNoiseReductionSmoothness",
    ),
    "effects": (
        "PostCropVignetteAmount",
        "PostCropVignetteMidpoint",
        "PostCropVignetteFeather",
        "PostCropVignetteRoundness",
        "GrainAmount",
        "GrainSize",
        "GrainFrequency",
        "DefringePurpleAmount",
        "DefringePurpleHueLo",
        "DefringePurpleHueHi",
        "DefringeGreenAmount",
        "DefringeGreenHueLo",
        "DefringeGreenHueHi",
    ),
}
SYNC_GROUP_NAMES = tuple(SYNC_GROUP_KEYS) + ("masks",)


class DevelopSettingsBody(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    label: str | None = Field(default=None, max_length=160)


class DevelopSnapshotBody(BaseModel):
    label: str = Field(min_length=1, max_length=160)
    settings: dict[str, Any] = Field(default_factory=dict)


class DevelopPregenBody(BaseModel):
    image_ids: list[int] = Field(default_factory=list, max_length=12)


class DevelopExportBody(BaseModel):
    format: str
    quality: int = Field(default=88, ge=1, le=100)
    max_px: int | None = Field(default=None, ge=1, le=100000)
    sharpen: str | None = Field(default="none", max_length=32)
    filename_pattern: str | None = Field(default=None, max_length=160)
    save_to_library: bool = False


class DevelopBatchExportBody(BaseModel):
    image_ids: list[int] = Field(default_factory=list, max_length=200)
    format: str = "jpeg"
    quality: int = Field(default=88, ge=1, le=100)
    max_px: int | None = Field(default=None, ge=1, le=100000)
    sharpen: str | None = Field(default="none", max_length=32)
    filename_pattern: str | None = Field(default=None, max_length=160)
    save_to_library: bool = False


class DevelopSyncBody(BaseModel):
    source_id: int
    target_ids: list[int] = Field(default_factory=list, max_length=500)
    groups: list[str] = Field(default_factory=list, max_length=16)
    # A clipboard carries a snapshot, rather than a mutable reference to its
    # source image.  The groups are still sliced server-side below.
    source_settings: dict[str, Any] | None = None
    full: bool = False
    label: str | None = Field(default=None, max_length=160)




def _now() -> str:
    return datetime.now(timezone.utc).isoformat()




def _resolved_settings(raw: str | None, metadata: dict[str, Any] | None, source_path: str | None) -> dict[str, Any]:
    """Add camera NR defaults at the server settings boundary, never on disk."""
    from features.develop.noise_profiles import resolve_file_defaults

    return resolve_file_defaults(presets._json_settings(raw), metadata, source_path)


def _profiled_meta(meta: dict[str, Any] | None, source_path: str | None) -> dict[str, Any]:
    """Resolve Adobe styling at request time without changing the base cache."""
    result = dict(meta or {})
    if result.get("base_kind") == "display":
        return result
    from features.develop import adobe_profiles, dng_pipeline

    profile = adobe_profiles.resolve_adobe_profile(
        source_path or result.get("source_path"),
        result.get("camera_model") or result.get("UniqueCameraModel") or "",
    )
    if profile is None:
        return result
    profile = dng_pipeline.normalize_adobe_profile(profile)
    profile["tone_curve_lut"] = dng_pipeline.tone_curve_lut(profile.get("tone_curve")).tolist()
    result["adobe_profile"] = profile
    result["adobe_profile_name"] = profile.get("profile_name") or "Adobe Standard"
    return result


def _pipeline_metadata(meta: dict[str, Any] | None, source_path: str | None = None) -> dict[str, Any] | None:
    """Carry source-kind semantics alongside RAW color metadata to exports."""
    if not isinstance(meta, dict):
        return None
    profiled = _profiled_meta(meta, source_path)
    color = dict(profiled.get("color") or {})
    for key in ("base_kind", "source_path", "camera_model", "adobe_profile", "adobe_profile_name"):
        if profiled.get(key) is not None:
            color[key] = profiled[key]
    return color or None


def _normalize_sync_groups(groups: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for group in groups or []:
        name = str(group or "").strip().lower()
        if name in SYNC_GROUP_NAMES and name not in cleaned:
            cleaned.append(name)
    return cleaned


def extract_sync_slice(settings: dict[str, Any], groups: list[str]) -> dict[str, Any]:
    """Copy the selected setting groups from one develop JSON blob."""
    selected = _normalize_sync_groups(groups)
    if not selected:
        return {}
    slice_: dict[str, Any] = {}
    for group in selected:
        if group == "masks":
            if "MaskGroupBasedCorrections" in settings:
                slice_["MaskGroupBasedCorrections"] = settings["MaskGroupBasedCorrections"]
            continue
        for key in SYNC_GROUP_KEYS[group]:
            if key in settings:
                slice_[key] = settings[key]
        if group == "grade":
            for key, value in settings.items():
                if str(key).startswith("ColorGrade") and key not in slice_:
                    slice_[key] = value
    return slice_


async def _write_synced_settings(
    image_id: int,
    incoming: dict[str, Any],
    *,
    label: str,
    replace: bool = False,
) -> dict[str, Any]:
    """Merge a sync slice into a target and append a history entry."""
    conn = await connection.open_async(catalog_path())
    try:
        await conn.execute("BEGIN")
        cursor = await conn.execute(
            "SELECT settings, origin, xmp_path, xmp_mtime FROM develop_settings WHERE image_id = ?",
            (image_id,),
        )
        existing = await cursor.fetchone()
        now = _now()
        if existing is None:
            merged = dict(incoming)
            origin = "user"
            xmp_path = None
            xmp_mtime = None
        else:
            prior = presets._json_settings(existing["settings"])
            merged = dict(incoming) if replace else {**prior, **incoming}
            origin = existing["origin"] or "user"
            xmp_path = existing["xmp_path"]
            xmp_mtime = existing["xmp_mtime"]
            if origin == "xmp":
                await history.record_async(
                    conn, image_id, json.dumps(prior, separators=(",", ":")), "Import from XMP"
                )
                origin = "user"
        settings_json = json.dumps(merged, separators=(",", ":"))
        await conn.execute(
            """
            INSERT INTO develop_settings (image_id, settings, origin, xmp_path, xmp_mtime, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(image_id) DO UPDATE SET
                settings = excluded.settings,
                origin = excluded.origin,
                xmp_path = excluded.xmp_path,
                xmp_mtime = excluded.xmp_mtime,
                updated_at = excluded.updated_at
            """,
            (image_id, settings_json, origin, xmp_path, xmp_mtime, now),
        )
        await history.record_async(conn, image_id, settings_json, label)
        await conn.commit()
        await judgements.develop(catalog_path(), image_id)
        return {"settings": merged, "origin": origin, "updated_at": now}
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=catalog_path())


async def _image_or_error(image_id: int):
    from features.develop import rawproc  # deferred: keeps RAW decoding libraries off boot until a Develop request

    image = await image_repository.get_image_by_id(catalog_path(), image_id)
    if not image:
        return None, JSONResponse({"error": "Image not found"}, status_code=404)
    image = dict(image)
    filepath = image.get("filepath") or ""
    if not rawproc.is_develop_path(filepath):
        suffix = os.path.splitext(filepath)[1].lower()
        if suffix in rawproc.UNFITTED_RAW_EXTENSIONS:
            return None, JSONResponse(
                {
                    "error": (
                        f"Develop can't render {suffix.lstrip('.').upper()} faithfully yet - "
                        "this photo stays cataloged and browsable, and its XMP settings are preserved"
                    )
                },
                status_code=400,
            )
        return None, JSONResponse(
            {
                "error": (
                    "Develop supports RAW and HDR sources plus JPEG, PNG, TIFF, and WebP images; "
                    "HEIC requires Pillow codec support"
                )
            },
            status_code=400,
        )
    if not image.get("hub_remote") and not await asyncio.to_thread(os.path.exists, image["filepath"]):
        return None, JSONResponse({"error": "Source image file is unavailable"}, status_code=404)
    return image, None


async def _load_settings(image_id: int) -> dict[str, Any] | None:
    conn = await connection.open_async(catalog_path())
    try:
        cursor = await conn.execute(
            "SELECT settings, origin, xmp_path, xmp_mtime, updated_at FROM develop_settings WHERE image_id = ?",
            (image_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        await connection.close_async(conn, db_path=catalog_path())


async def _history(image_id: int) -> list[dict[str, Any]]:
    conn = await connection.open_async(catalog_path())
    try:
        # Snapshots pin above edits, each lane capped -- which used to need a
        # WITH pinned AS (...) UNION ALL because they lived in one table told
        # apart by a label prefix. Both are just decisions now.
        return await history.entries_async(conn, image_id)
    finally:
        await connection.close_async(conn, db_path=catalog_path())


async def _snapshots(image_id: int) -> list[dict[str, Any]]:
    conn = await connection.open_async(catalog_path())
    try:
        entries = await history.entries_async(conn, image_id)
        return [e for e in entries if e["label"].startswith(history.SNAPSHOT_PREFIX)]
    finally:
        await connection.close_async(conn, db_path=catalog_path())


def _base_source_path(image: dict) -> str:
    """The path Develop decodes from — the attached archive disk counts.

    Resolving here (not deeper) keeps the base cache's recorded source_path
    consistent between the probe and the generator, and keeps write paths
    (XMP sidecars, exports) pointed at the catalog's own path, never at the
    archive volume.
    """

    from photo import location

    resolved = location.local_path(
        str(image["filepath"] or ""),
        expected_size=image["file_size"] if "file_size" in image.keys() else None,
    )
    return resolved or str(image["filepath"] or "")


async def _ensure_base(image_id: int, image: dict) -> tuple[rawproc.BasePaths, dict[str, Any]]:
    from features.develop import rawproc  # deferred: keeps RAW decoding libraries off boot until base generation

    source = await asyncio.to_thread(_base_source_path, image)
    return await asyncio.to_thread(rawproc.ensure_base_cache, image_id, source)


def _cached_base(image_id: int, image: dict) -> rawproc.BasePaths | None:
    """Cheap cache probe for progressive Develop responses; never decodes RAW."""
    from features.develop import rawproc  # deferred: keeps RAW decoding libraries off boot until a Develop request

    return rawproc.cached_base_paths(image_id, _base_source_path(image))


def _recent_base_failure(image_id: int) -> rawproc.RawDecodeError | None:
    failure = _base_generation_failures.get(image_id)
    if failure is None:
        return None
    failed_at, error = failure
    if time.monotonic() - failed_at < _BASE_FAILURE_TTL_SECONDS:
        return error
    _base_generation_failures.pop(image_id, None)
    return None


def _base_error_response(error: rawproc.RawDecodeError) -> JSONResponse:
    return JSONResponse({"error": str(error)}, status_code=422)


def _start_base_generation(image_id: int, image: dict) -> None:
    """Start the existing rawproc single-flight generator without holding a request open."""
    from features.develop import rawproc  # deferred: keeps RAW decoding libraries off boot until base generation

    if _recent_base_failure(image_id) is not None:
        return
    existing = _base_generation_tasks.get(image_id)
    if existing is not None and not existing.done():
        return

    async def generate() -> None:
        try:
            await _ensure_base(image_id, image)
            _base_generation_failures.pop(image_id, None)
        except rawproc.RawDecodeError as exc:
            _base_generation_failures[image_id] = (time.monotonic(), exc)
        except Exception as exc:
            failure = rawproc.RawDecodeError(str(exc) or "Develop base generation failed")
            failure.__cause__ = exc
            _base_generation_failures[image_id] = (time.monotonic(), failure)
        finally:
            _base_generation_tasks.pop(image_id, None)

    _base_generation_tasks[image_id] = asyncio.create_task(generate())


def _base_generating_response(image_id: int) -> JSONResponse:
    return JSONResponse(
        {"image_id": image_id, "state": "generating", "status": "pending"},
        status_code=202,
        headers={"Cache-Control": "no-store", "Retry-After": "1"},
    )


def _cached_base_or_pending(
    image_id: int,
    image: dict,
) -> tuple[rawproc.BasePaths | None, JSONResponse | None]:
    """Keep request-path Develop verbs cache-only while a base fills behind them."""

    paths = _cached_base(image_id, image)
    if paths is not None:
        return paths, None
    if failure := _recent_base_failure(image_id):
        return None, _base_error_response(failure)
    _start_base_generation(image_id, image)
    return None, _base_generating_response(image_id)


async def _upsert_settings(image_id: int, incoming: dict[str, Any], label: str | None) -> dict[str, Any]:
    async def _write() -> dict[str, Any]:
        conn = await connection.open_async(catalog_path())
        try:
            await conn.execute("BEGIN")
            cursor = await conn.execute(
                "SELECT settings, origin, xmp_path, xmp_mtime FROM develop_settings WHERE image_id = ?",
                (image_id,),
            )
            existing = await cursor.fetchone()
            now = _now()
            if existing is None:
                merged = dict(incoming)
                origin = "user"
                xmp_path = None
                xmp_mtime = None
            else:
                prior = presets._json_settings(existing["settings"])
                # Merge rather than replace: clients can render only v1 keys while
                # later phases and imported XMP keys survive every autosave.
                merged = {**prior, **incoming}
                origin = existing["origin"] or "user"
                xmp_path = existing["xmp_path"]
                xmp_mtime = existing["xmp_mtime"]
                if origin == "xmp":
                    await history.record_async(
                        conn, image_id, json.dumps(prior, separators=(",", ":")), "Import from XMP"
                    )
                    origin = "user"
            settings_json = json.dumps(merged, separators=(",", ":"))
            await conn.execute(
                """
                INSERT INTO develop_settings (image_id, settings, origin, xmp_path, xmp_mtime, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(image_id) DO UPDATE SET
                    settings = excluded.settings,
                    origin = excluded.origin,
                    xmp_path = excluded.xmp_path,
                    xmp_mtime = excluded.xmp_mtime,
                    updated_at = excluded.updated_at
                """,
                (image_id, settings_json, origin, xmp_path, xmp_mtime, now),
            )
            await history.record_async(conn, image_id, settings_json, label)
            await conn.commit()
            await judgements.develop(catalog_path(), image_id)
            return {"settings": merged, "origin": origin, "updated_at": now}
        except Exception:
            await conn.rollback()
            raise
        finally:
            await connection.close_async(conn, db_path=catalog_path())

    result = await connection.run_with_busy_retry(_write)
    await _refresh_grid_previews(image_id)
    return result


async def _reset_settings(image_id: int) -> dict[str, Any]:
    conn = await connection.open_async(catalog_path())
    try:
        await conn.execute("BEGIN")
        cursor = await conn.execute(
            "SELECT settings, origin, xmp_path, xmp_mtime FROM develop_settings WHERE image_id = ?",
            (image_id,),
        )
        current = await cursor.fetchone()
        if current is None:
            snapshot: dict[str, Any] = {}
            origin = "user"
            xmp_path = None
            xmp_mtime = None
        elif current["origin"] == "xmp":
            snapshot = presets._json_settings(current["settings"])
            origin = "xmp"
            xmp_path = current["xmp_path"]
            xmp_mtime = current["xmp_mtime"]
        else:
            entries = await history.entries_async(conn, image_id)
            imported = [e for e in entries if e["label"] == "Import from XMP"]
            baseline = imported[-1]["settings"] if imported else None
            snapshot = baseline or {}
            origin = "xmp" if baseline else "user"
            xmp_path = current["xmp_path"]
            xmp_mtime = current["xmp_mtime"]
        now = _now()
        encoded = json.dumps(snapshot, separators=(",", ":"))
        await conn.execute(
            """
            INSERT INTO develop_settings (image_id, settings, origin, xmp_path, xmp_mtime, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(image_id) DO UPDATE SET
                settings = excluded.settings, origin = excluded.origin,
                xmp_path = excluded.xmp_path, xmp_mtime = excluded.xmp_mtime,
                updated_at = excluded.updated_at
            """,
            (image_id, encoded, origin, xmp_path, xmp_mtime, now),
        )
        await history.record_async(conn, image_id, encoded, "Reset")
        await conn.commit()
        await judgements.develop(catalog_path(), image_id)
        result = {"settings": snapshot, "origin": origin, "updated_at": now}
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=catalog_path())
    await _refresh_grid_previews(image_id)
    return result


async def _refresh_grid_previews(image_id: int) -> None:
    """A saved edit owns its thumbnail.

    Purging the stale tiers makes the next request re-render them through the
    Develop pipeline (thumbnails/develop_bridge.py), so the grid tile becomes
    the edit without waiting for any sweep. Best-effort: a failed purge leaves
    yesterday's tile, which the serve-side recipe check also catches.
    """

    import thumbnails

    try:
        await asyncio.to_thread(thumbnails.purge_image_cache, [int(image_id)])
    except Exception:
        log.exception("develop save could not refresh previews image_id=%s", image_id)


@router.get("/api/develop/{image_id}/base.bin")
async def api_develop_base_bin(image_id: int):
    image, error = await _image_or_error(image_id)
    if error:
        return error
    paths = _cached_base(image_id, image)
    if paths is None:
        if failure := _recent_base_failure(image_id):
            return _base_error_response(failure)
        _start_base_generation(image_id, image)
        return _base_generating_response(image_id)
    return FileResponse(
        paths.binary,
        media_type="application/octet-stream",
        headers={"Content-Encoding": "gzip", "Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/api/develop/{image_id}/base.jpg")
async def api_develop_base_jpg(image_id: int):
    image, error = await _image_or_error(image_id)
    if error:
        return error
    paths = _cached_base(image_id, image)
    if paths is None:
        if failure := _recent_base_failure(image_id):
            return _base_error_response(failure)
        _start_base_generation(image_id, image)
        return _base_generating_response(image_id)
    return FileResponse(
        paths.preview,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.post("/api/develop/{image_id}/auto")
async def api_develop_auto_tone(image_id: int):
    """Compute a deterministic Auto tone patch for this image (no writes)."""
    import gzip as _gzip

    import numpy as _np

    from features.develop import autotone
    from features.develop.pipeline import _apply_white_balance
    from features.develop import rawproc  # deferred: keeps RAW decoding libraries off boot until Auto Tone runs

    image, error = await _image_or_error(image_id)
    if error:
        return error
    paths, pending = _cached_base_or_pending(image_id, image)
    if pending is not None:
        return pending
    cached_meta = rawproc.read_base_metadata(image_id) or {}
    row = await _load_settings(image_id)
    settings = presets._json_settings(row["settings"]) if row else {}
    asshot = cached_meta.get("as_shot") if isinstance(cached_meta, dict) else {}

    def _compute():
        linear16, _w, _h = rawproc.parse_base_payload(_gzip.decompress(paths.binary.read_bytes()))
        linear = linear16.astype(_np.float32) / _np.float32(65535.0)
        balanced = _apply_white_balance(
            linear,
            settings,
            asshot.get("temperature") if isinstance(asshot, dict) else None,
            asshot.get("tint") if isinstance(asshot, dict) else None,
            _pipeline_metadata(cached_meta),
        )
        return autotone.auto_tone_settings(balanced, settings)

    patch = await asyncio.to_thread(_compute)
    return {"image_id": image_id, "patch": patch}


@router.post("/api/develop/auto/batch")
async def api_develop_auto_tone_batch(body: dict):
    """Apply Auto tone to many images server-side (skips user-edited unless force)."""
    from features.develop import autotone

    image_ids = [int(v) for v in (body.get("image_ids") or [])][:500]
    force = bool(body.get("force"))
    results = []
    for image_id in image_ids:
        row = await _load_settings(image_id)
        if autotone.should_skip_batch_origin(row["origin"] if row else None, force=force):
            results.append({"image_id": image_id, "status": "skipped", "reason": "user-edited"})
            continue
        response = await api_develop_auto_tone(image_id)
        if not isinstance(response, dict):
            results.append({"image_id": image_id, "status": "error"})
            continue
        await _upsert_settings(image_id, dict(response["patch"]), "Auto tone")
        results.append({"image_id": image_id, "status": "applied"})
    return {
        "requested": len(image_ids),
        "applied": sum(1 for r in results if r["status"] == "applied"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "results": results,
    }


@router.get("/api/develop/{image_id}/proof-tile")
async def api_develop_proof_tile(
    image_id: int,
    u: float = Query(..., ge=0.0, le=1.0),
    v: float = Query(..., ge=0.0, le=1.0),
    edge: int = Query(1024, ge=128, le=2048),
):
    from features.develop import rawproc  # deferred: keeps RAW decoding libraries off boot until a proof tile is requested

    image, error = await _image_or_error(image_id)
    if error:
        return error
    from features.develop.render import RenderError, render_proof_tile_async

    _paths, pending = _cached_base_or_pending(image_id, image)
    if pending is not None:
        return pending
    row = await _load_settings(image_id)
    cached_meta = rawproc.read_base_metadata(image_id) or {}
    asshot = cached_meta.get("as_shot") if isinstance(cached_meta, dict) else {}
    try:
        tile = await render_proof_tile_async(
            image["filepath"],
            _resolved_settings(row["settings"] if row else None, cached_meta, image["filepath"]),
            u=u,
            v=v,
            edge=edge,
            asshot_temperature=asshot.get("temperature") if isinstance(asshot, dict) else None,
            asshot_tint=asshot.get("tint") if isinstance(asshot, dict) else None,
            color_profile=_pipeline_metadata(cached_meta, image["filepath"]),
        )
    except (RenderError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return Response(
        content=tile.png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Proof-Left": str(tile.left),
            "X-Proof-Top": str(tile.top),
            "X-Proof-Width": str(tile.width),
            "X-Proof-Height": str(tile.height),
            "X-Proof-Source-Width": str(tile.source_width),
            "X-Proof-Source-Height": str(tile.source_height),
        },
    )


@router.post("/api/develop/{image_id}/transform/auto")
async def api_develop_transform_auto(image_id: int):
    from features.develop import rawproc, transform  # deferred: keeps pixel libraries off boot until auto transform runs

    image, error = await _image_or_error(image_id)
    if error:
        return error
    paths, pending = _cached_base_or_pending(image_id, image)
    if pending is not None:
        return pending
    try:
        import gzip
        payload = await asyncio.to_thread(paths.binary.read_bytes)
        linear_u16, _width, _height = rawproc.parse_base_payload(gzip.decompress(payload))
    except rawproc.RawDecodeError as exc:
        return _base_error_response(exc)
    except (OSError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    result = await asyncio.to_thread(transform.auto_level_settings, linear_u16.astype("float32") / 65535.0)
    if result is None:
        return JSONResponse({"error": "No reliable horizon found"}, status_code=422)
    return result


@router.get("/api/develop/{image_id}")
async def api_get_develop(image_id: int):
    from features.develop import rawproc  # deferred: keeps RAW decoding libraries off boot until Develop metadata is requested

    image, error = await _image_or_error(image_id)
    if error:
        return error
    if image.get("hub_remote") and _cached_base(image_id, image) is None:
        meta = rawproc.read_base_metadata(image_id) or {}
    else:
        try:
            _paths, meta = await _ensure_base(image_id, image)
        except rawproc.RawDecodeError as exc:
            return _base_error_response(exc)
    meta = _profiled_meta(meta, image["filepath"])
    # Keep the interactive canvas on the same cache-native color contract as
    # render_display_preview().  _profiled_meta() also carries the resolved
    # Adobe profile for UI/profile workflows; without this explicit payload,
    # WebGL enabled that extra pipeline for an otherwise unedited RAW.
    from features.develop.render import default_render_color_profile

    meta["canvas_color_profile"] = default_render_color_profile(meta)
    row = await _load_settings(image_id)
    return {
        "settings": _resolved_settings(row["settings"] if row else None, meta, image["filepath"]),
        "origin": row["origin"] if row else "user",
        "meta": meta,
        "history": await _history(image_id),
    }


@router.get("/api/develop/{image_id}/history")
async def api_develop_history(image_id: int):
    _image, error = await _image_or_error(image_id)
    if error:
        return error
    return await _history(image_id)


@router.post("/api/develop/{image_id}/virtual-copy")
async def api_create_virtual_copy(image_id: int):
    _image, error = await _image_or_error(image_id)
    if error:
        return error
    conn = await connection.open_async(catalog_path())
    try:
        await conn.execute("BEGIN")
        copy = await virtual_copies.create_virtual_copy(conn, image_id)
        if copy is None:
            await conn.rollback()
            return JSONResponse({"error": "Image not found"}, status_code=404)
        await conn.commit()
        return copy
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=catalog_path())


@router.get("/api/develop/{image_id}/virtual-copies")
async def api_list_virtual_copies(image_id: int):
    _image, error = await _image_or_error(image_id)
    if error:
        return error
    conn = await connection.open_async(catalog_path())
    try:
        copies = await virtual_copies.list_virtual_copies(conn, image_id)
        return {"virtual_copies": copies or []}
    finally:
        await connection.close_async(conn, db_path=catalog_path())


@router.delete("/api/develop/{image_id}/virtual-copy/{copy_id}")
async def api_delete_virtual_copy(image_id: int, copy_id: int):
    _image, error = await _image_or_error(image_id)
    if error:
        return error
    conn = await connection.open_async(catalog_path())
    try:
        await conn.execute("BEGIN")
        if not await virtual_copies.is_virtual_copy_of(conn, image_id, copy_id):
            await conn.rollback()
            return JSONResponse({"error": "Virtual copy not found"}, status_code=404)
        deleted = await virtual_copies.delete_virtual_copy(conn, copy_id)
        if not deleted:
            await conn.rollback()
            return JSONResponse({"error": "Virtual copy not found"}, status_code=404)
        await conn.commit()
        return {"deleted": copy_id}
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=catalog_path())


@router.get("/api/develop/{image_id}/snapshots")
async def api_list_snapshots(image_id: int):
    _image, error = await _image_or_error(image_id)
    if error:
        return error
    return {"snapshots": await _snapshots(image_id)}


@router.post("/api/develop/{image_id}/snapshots")
async def api_save_snapshot(image_id: int, body: DevelopSnapshotBody):
    _image, error = await _image_or_error(image_id)
    if error:
        return error
    now = _now()
    entry = {
        "settings": body.settings,
        "label": f"Snapshot: {body.label.strip()}",
        "created_at": now,
    }
    conn = await connection.open_async(catalog_path())
    try:
        await history.record_async(
            conn, image_id, json.dumps(body.settings, separators=(",", ":")), entry["label"]
        )
        await conn.commit()
        return {"id": int(cursor.lastrowid), **entry}
    finally:
        await connection.close_async(conn, db_path=catalog_path())


@router.delete("/api/develop/{image_id}/snapshots/{history_id}")
async def api_delete_snapshot(image_id: int, history_id: int):
    conn = await connection.open_async(catalog_path())
    try:
        removed = await history.forget_snapshot(conn, image_id, history_id)
        await conn.commit()
        if not removed:
            return JSONResponse({"error": "Snapshot not found"}, status_code=404)
        return {"deleted": history_id}
    finally:
        await connection.close_async(conn, db_path=catalog_path())


@router.put("/api/develop/{image_id}")
async def api_put_develop(image_id: int, body: DevelopSettingsBody):
    _image, error = await _image_or_error(image_id)
    if error:
        return error
    return await _upsert_settings(image_id, body.settings, body.label)


@router.post("/api/develop/{image_id}/reset")
async def api_reset_develop(image_id: int):
    _image, error = await _image_or_error(image_id)
    if error:
        return error
    return await _reset_settings(image_id)


async def _pregen(image_ids: list[int]) -> None:
    from features.develop import rawproc  # deferred: keeps RAW decoding libraries off boot until preview pre-generation

    for image_id in image_ids:
        image, error = await _image_or_error(image_id)
        if error:
            continue
        try:
            await _ensure_base(image_id, image)
        except (rawproc.RawDecodeError, FileNotFoundError):
            continue


@router.post("/api/develop/pregen", status_code=202)
async def api_develop_pregen(body: DevelopPregenBody):
    image_ids = list(dict.fromkeys(image_id for image_id in body.image_ids if image_id > 0))
    task = asyncio.create_task(_pregen(image_ids))
    _pregen_tasks.add(task)
    task.add_done_callback(_pregen_tasks.discard)
    return {"queued": image_ids}


@router.post("/api/develop/{image_id}/export")
async def api_export_develop(image_id: int, body: DevelopExportBody):
    from features.develop import rawproc  # deferred: keeps RAW decoding libraries off boot until a Develop export runs

    image, error = await _image_or_error(image_id)
    if error:
        return error
    if body.format not in {"jpeg", "tiff16"}:
        return JSONResponse({"error": "format must be jpeg or tiff16"}, status_code=400)
    _paths, pending = _cached_base_or_pending(image_id, image)
    if pending is not None:
        return pending
    # The RENDER lane owns the shared full-resolution pipeline. Keep this
    # endpoint's contract ready without duplicating color math in RAWPROC.
    try:
        from features.develop.render import (
            RenderError,
            _download_name_for,
            format_export_filename,
            render_export_async,
            save_export_to_library,
        )
    except ImportError:
        return JSONResponse({"error": "Develop export renderer is not installed yet"}, status_code=503)
    row = await _load_settings(image_id)
    cached_meta = rawproc.read_base_metadata(image_id) or {}
    asshot = cached_meta.get("as_shot") if isinstance(cached_meta, dict) else {}
    settings = _resolved_settings(row["settings"] if row else None, cached_meta, image["filepath"])
    try:
        output_path = await render_export_async(
            image["filepath"],
            settings,
            output_format=body.format,
            quality=body.quality,
            max_px=body.max_px,
            sharpen=body.sharpen,
            filename_pattern=body.filename_pattern,
            image_id=image_id,
            asshot_temperature=asshot.get("temperature") if isinstance(asshot, dict) else None,
            asshot_tint=asshot.get("tint") if isinstance(asshot, dict) else None,
            color_profile=_pipeline_metadata(cached_meta, image["filepath"]),
        )
    except (rawproc.RawDecodeError, RenderError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)

    fallback = format_export_filename(
        raw_path=image["filepath"],
        image_id=image_id,
        output_format=body.format,
        pattern=body.filename_pattern,
    )
    filename = _download_name_for(output_path, fallback)
    library_info = None
    if body.save_to_library:
        try:
            library_info = await asyncio.to_thread(
                save_export_to_library,
                output_path,
                db_path=catalog_path(),
                source_image=image,
                download_name=filename,
            )
            library_info["version_stack"] = await stack_repository.join_version_stack(
                catalog_path(), int(image.get("vc_of") or image_id), int(library_info["library_image_id"])
            )
        except Exception as exc:
            return JSONResponse({"error": f"Export rendered but library save failed: {exc}"}, status_code=422)

    from starlette.background import BackgroundTask
    from features.develop.render import _cleanup_export

    headers = {}
    if library_info:
        headers["X-Develop-Library-Image-Id"] = str(library_info["library_image_id"])
    return FileResponse(
        output_path,
        media_type="image/jpeg" if body.format == "jpeg" else "image/tiff",
        filename=filename,
        headers=headers,
        background=BackgroundTask(_cleanup_export, output_path),
    )


async def _run_batch_export(body: DevelopBatchExportBody) -> None:
    from features.develop import rawproc  # deferred: keeps RAW decoding libraries off boot until batch export runs

    from features.develop.render import (
        RenderError,
        _download_name_for,
        format_export_filename,
        render_export_async,
        save_export_to_library,
    )

    image_ids = list(dict.fromkeys(image_id for image_id in body.image_ids if image_id > 0))
    _batch_status.update(
        {
            "state": "running",
            "started_at": time.time(),
            "finished_at": None,
            "total": len(image_ids),
            "done": 0,
            "current_id": None,
            "results": [],
            "errors": [],
        }
    )
    for image_id in image_ids:
        _batch_status["current_id"] = image_id
        image, error = await _image_or_error(image_id)
        if error:
            _batch_status["errors"].append({"image_id": image_id, "error": "unavailable"})
            _batch_status["done"] += 1
            continue
        row = await _load_settings(image_id)
        cached_meta = rawproc.read_base_metadata(image_id) or {}
        asshot = cached_meta.get("as_shot") if isinstance(cached_meta, dict) else {}
        try:
            output_path = await render_export_async(
                image["filepath"],
                _resolved_settings(row["settings"] if row else None, cached_meta, image["filepath"]),
                output_format=body.format,
                quality=body.quality,
                max_px=body.max_px,
                sharpen=body.sharpen,
                filename_pattern=body.filename_pattern,
                image_id=image_id,
                asshot_temperature=asshot.get("temperature") if isinstance(asshot, dict) else None,
                asshot_tint=asshot.get("tint") if isinstance(asshot, dict) else None,
                color_profile=_pipeline_metadata(cached_meta, image["filepath"]),
            )
            filename = _download_name_for(
                output_path,
                format_export_filename(
                    raw_path=image["filepath"],
                    image_id=image_id,
                    output_format=body.format,
                    pattern=body.filename_pattern,
                ),
            )
            result = {
                "image_id": image_id,
                "path": str(output_path),
                "filename": filename,
                "bytes": output_path.stat().st_size,
            }
            if body.save_to_library:
                result["library"] = await asyncio.to_thread(
                    save_export_to_library,
                    output_path,
                    db_path=catalog_path(),
                    source_image=image,
                    download_name=filename,
                )
                result["library"]["version_stack"] = await stack_repository.join_version_stack(
                    catalog_path(), int(image.get("vc_of") or image_id), int(result["library"]["library_image_id"])
                )
            _batch_status["results"].append(result)
        except (rawproc.RawDecodeError, RenderError, ValueError, OSError) as exc:
            _batch_status["errors"].append({"image_id": image_id, "error": str(exc)})
        _batch_status["done"] += 1
    _batch_status.update({"state": "complete", "finished_at": time.time(), "current_id": None})


@router.post("/api/develop/export/batch", status_code=202)
async def api_batch_export_develop(body: DevelopBatchExportBody):
    if body.format not in {"jpeg", "tiff16"}:
        return JSONResponse({"error": "format must be jpeg or tiff16"}, status_code=400)
    image_ids = list(dict.fromkeys(image_id for image_id in body.image_ids if image_id > 0))
    if not image_ids:
        return JSONResponse({"error": "image_ids required"}, status_code=400)
    if _batch_status.get("state") == "running":
        return JSONResponse({"error": "A batch export is already running", "status": dict(_batch_status)}, status_code=409)
    try:
        from features.develop.render import resolve_output_sharpen

        resolve_output_sharpen(body.sharpen)
    except (ImportError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    task = asyncio.create_task(_run_batch_export(body))
    _batch_tasks.add(task)
    task.add_done_callback(_batch_tasks.discard)
    return {"queued": image_ids, "status": dict(_batch_status)}


@router.get("/api/develop/export/batch/status")
async def api_batch_export_status():
    return dict(_batch_status)


@router.post("/api/develop/sync")
async def api_sync_develop(body: DevelopSyncBody):
    _source, error = await _image_or_error(body.source_id)
    if error:
        return error
    groups = _normalize_sync_groups(body.groups)
    if not body.full and not groups:
        return JSONResponse(
            {"error": "groups must include one or more of: " + ", ".join(SYNC_GROUP_NAMES)},
            status_code=400,
        )
    target_ids = [
        image_id
        for image_id in dict.fromkeys(body.target_ids)
        if image_id > 0 and image_id != body.source_id
    ]
    if not target_ids:
        return JSONResponse({"error": "target_ids required"}, status_code=400)

    source_row = await _load_settings(body.source_id)
    source_settings = body.source_settings if body.source_settings is not None else (presets._json_settings(source_row["settings"]) if source_row else {})
    slice_ = dict(source_settings) if body.full else extract_sync_slice(source_settings, groups)
    if not slice_:
        return JSONResponse({"error": "Source has no settings in the selected groups"}, status_code=400)

    label = body.label or f"Sync from #{body.source_id}"
    synced: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for target_id in target_ids:
        _target, target_error = await _image_or_error(target_id)
        if target_error:
            skipped.append({"image_id": target_id, "error": "unavailable"})
            continue
        result = await _write_synced_settings(target_id, slice_, label=label, replace=body.full)
        synced.append({"image_id": target_id, "origin": result["origin"], "updated_at": result["updated_at"], "settings": result["settings"]})
    return {
        "source_id": body.source_id,
        "groups": groups,
        "full": body.full,
        "synced": synced,
        "skipped": skipped,
        "keys": sorted(slice_.keys()),
    }

@router.get("/api/develop/film/stocks")
async def api_film_stocks():
    from features.develop import film

    return {"stocks": film.list_stocks()}


@router.get("/api/develop/film/stocks/{slug}")
async def api_film_stock(slug: str):
    from features.develop import film

    stock = film.load_stock(slug)
    if stock is None:
        return JSONResponse({"error": f"Unknown film stock: {slug}"}, status_code=404)
    return stock
