"""HTTP surface for technical quality scoring."""

from __future__ import annotations

from core.catalog_path import catalog_path

import asyncio
import logging
import os
import threading
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import thumbnails
from features.quality import scorer as quality_scorer
from features.quality import autocull

router = APIRouter()
log = logging.getLogger(__name__)

_THROTTLE_SECONDS = 0.05
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500

_scan_lock = threading.Lock()
_scan_state: dict[str, Any] = {
    "running": False,
    "stop": False,
    "limit": 0,
    "scored": 0,
    "skipped": 0,
    "errors": 0,
    "pending": 0,
    "total_scored": 0,
    "last_error": "",
    "started_at": None,
    "finished_at": None,
    "message": "idle",
}




class ScanBody(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=_MAX_LIMIT)


class AutocullBody(BaseModel):
    stack_ids: list[int] | None = Field(default=None, max_length=500)
    all: bool = False


class AutocullApplyBody(BaseModel):
    stack_ids: list[int] = Field(min_length=1, max_length=500)


def _status_payload() -> dict[str, Any]:
    with _scan_lock:
        state = dict(_scan_state)
    return {
        "running": bool(state["running"]),
        "scored": int(state["scored"]),
        "skipped": int(state["skipped"]),
        "errors": int(state["errors"]),
        "pending": int(state["pending"]),
        "total_scored": int(state["total_scored"]),
        "limit": int(state["limit"] or 0),
        "last_error": state["last_error"] or "",
        "started_at": state["started_at"],
        "finished_at": state["finished_at"],
        "message": state["message"] or "idle",
        "eyes_open_note": quality_scorer.EYES_OPEN_V1_NOTE,
        "throttle_seconds": _THROTTLE_SECONDS,
    }


async def _open_conn():
    from data import connection

    return await connection.open_async(catalog_path())


async def _ensure_table(conn) -> None:
    await quality_scorer.ensure_image_quality(conn)


async def _count_scored(conn) -> int:
    cursor = await conn.execute("SELECT COUNT(*) AS c FROM image_quality")
    row = await cursor.fetchone()
    return int(row["c"] if row else 0)


async def _count_pending(conn) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM images i
        WHERE i.trashed_at IS NULL
          AND i.missing_at IS NULL
          AND i.status IN ('kept', 'maybe')
          AND NOT EXISTS (SELECT 1 FROM image_quality q WHERE q.image_id = i.id)
          AND EXISTS (
            SELECT 1 FROM cache_entries c
            WHERE c.image_id = i.id AND c.size IN ('sm', 'md')
          )
        """
    )
    row = await cursor.fetchone()
    return int(row["c"] if row else 0)


async def _fetch_unscored(conn, limit: int) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        SELECT i.id AS image_id, i.filename,
               (
                 SELECT c.path FROM cache_entries c
                 WHERE c.image_id = i.id AND c.size IN ('sm', 'md')
                 ORDER BY CASE c.size WHEN 'md' THEN 0 ELSE 1 END
                 LIMIT 1
               ) AS thumb_path,
               (
                 SELECT c.size FROM cache_entries c
                 WHERE c.image_id = i.id AND c.size IN ('sm', 'md')
                 ORDER BY CASE c.size WHEN 'md' THEN 0 ELSE 1 END
                 LIMIT 1
               ) AS thumb_size
        FROM images i
        WHERE i.trashed_at IS NULL
          AND i.missing_at IS NULL
          AND i.status IN ('kept', 'maybe')
          AND NOT EXISTS (SELECT 1 FROM image_quality q WHERE q.image_id = i.id)
          AND EXISTS (
            SELECT 1 FROM cache_entries c
            WHERE c.image_id = i.id AND c.size IN ('sm', 'md')
          )
        ORDER BY i.id ASC
        LIMIT ?
        """,
        (int(limit),),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def _fetch_faces(conn, image_id: int) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        SELECT bbox_x, bbox_y, bbox_w, bbox_h, cache_path
        FROM face_detections
        WHERE image_id = ? AND ignored = 0 AND bbox_w > 0 AND bbox_h > 0
        ORDER BY quality DESC, confidence DESC
        """,
        (int(image_id),),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def _upsert_quality(conn, image_id: int, metrics: dict[str, Any]) -> None:
    await conn.execute(
        """
        INSERT INTO image_quality (
            image_id, sharpness, subject_sharpness, exposure_clip,
            motion_blur, eyes_open, score, scored_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(image_id) DO UPDATE SET
            sharpness = excluded.sharpness,
            subject_sharpness = excluded.subject_sharpness,
            exposure_clip = excluded.exposure_clip,
            motion_blur = excluded.motion_blur,
            eyes_open = excluded.eyes_open,
            score = excluded.score,
            scored_at = excluded.scored_at
        """,
        (
            int(image_id),
            metrics.get("sharpness"),
            metrics.get("subject_sharpness"),
            metrics.get("exposure_clip"),
            metrics.get("motion_blur"),
            metrics.get("eyes_open"),
            metrics.get("score"),
            quality_scorer._now(),
        ),
    )
    await conn.commit()


def _probe_image_size(path: str) -> tuple[int, int]:
    import cv2

    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return 0, 0
    h, w = image.shape[:2]
    return int(w), int(h)


def _score_one_sync(
    thumb_path: str,
    face_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    with open(thumb_path, "rb") as handle:
        jpeg_bytes = handle.read()
    gray, dst_w, dst_h = quality_scorer._decode_gray(jpeg_bytes)

    boxes: list[tuple[float, float, float, float]] = []
    if face_rows:
        # Group by detection cache_path so we scale from the correct source size.
        by_path: dict[str, list[dict[str, float]]] = {}
        for row in face_rows:
            path = str(row.get("cache_path") or "")
            by_path.setdefault(path, []).append(
                {
                    "x": float(row["bbox_x"] or 0.0),
                    "y": float(row["bbox_y"] or 0.0),
                    "w": float(row["bbox_w"] or 0.0),
                    "h": float(row["bbox_h"] or 0.0),
                }
            )
        for path, group in by_path.items():
            if path and os.path.isfile(path):
                src_w, src_h = _probe_image_size(path)
            else:
                src_w, src_h = dst_w, dst_h
            boxes.extend(
                quality_scorer.scale_face_boxes(
                    group,
                    src_width=src_w or dst_w,
                    src_height=src_h or dst_h,
                    dst_width=dst_w,
                    dst_height=dst_h,
                )
            )

    result = quality_scorer.score_gray(gray, face_boxes=boxes or None)
    result["thumb_width"] = dst_w
    result["thumb_height"] = dst_h
    result["thumb_path"] = thumb_path
    return result


async def _score_image(conn, row: dict[str, Any]) -> dict[str, Any]:
    image_id = int(row["image_id"])
    thumb_path = str(row.get("thumb_path") or "")
    if not thumb_path or not os.path.isfile(thumb_path):
        raise FileNotFoundError(f"missing thumb for image {image_id}")
    faces = await _fetch_faces(conn, image_id)
    metrics = await asyncio.to_thread(_score_one_sync, thumb_path, faces)
    await _upsert_quality(conn, image_id, metrics)
    return metrics


def _run_scan(limit: int) -> None:
    with _scan_lock:
        _scan_state.update(
            {
                "running": True,
                "stop": False,
                "limit": limit,
                "scored": 0,
                "skipped": 0,
                "errors": 0,
                "last_error": "",
                "started_at": time.time(),
                "finished_at": None,
                "message": f"scanning up to {limit}",
            }
        )

    try:
        import asyncio as _asyncio

        loop = _asyncio.new_event_loop()
        try:
            loop.run_until_complete(_scan_async(limit))
        finally:
            loop.close()
    except Exception as exc:
        log.exception("quality scan failed")
        with _scan_lock:
            _scan_state["last_error"] = str(exc)
            _scan_state["message"] = "failed"
    finally:
        with _scan_lock:
            _scan_state["running"] = False
            _scan_state["finished_at"] = time.time()
            if _scan_state["message"] not in ("failed",):
                _scan_state["message"] = "idle"


async def _scan_async(limit: int) -> None:
    conn = await _open_conn()
    try:
        await _ensure_table(conn)
        pending = await _count_pending(conn)
        total_scored = await _count_scored(conn)
        with _scan_lock:
            _scan_state["pending"] = pending
            _scan_state["total_scored"] = total_scored

        rows = await _fetch_unscored(conn, limit)
        for row in rows:
            with _scan_lock:
                if _scan_state.get("stop"):
                    _scan_state["message"] = "stopped"
                    break
            try:
                await _score_image(conn, row)
                with _scan_lock:
                    _scan_state["scored"] += 1
                    _scan_state["total_scored"] += 1
                    _scan_state["pending"] = max(0, int(_scan_state["pending"]) - 1)
            except FileNotFoundError as exc:
                log.warning("quality skip: %s", exc)
                with _scan_lock:
                    _scan_state["skipped"] += 1
            except Exception as exc:
                log.exception("quality score error image_id=%s", row.get("image_id"))
                with _scan_lock:
                    _scan_state["errors"] += 1
                    _scan_state["last_error"] = str(exc)
            await asyncio.sleep(_THROTTLE_SECONDS)
    finally:
        await conn.close()


async def scan_image_ids(image_ids: list[int]) -> dict[str, int]:
    """Score only the supplied imported images, then refresh cull suggestions."""
    ids = sorted({int(image_id) for image_id in image_ids if int(image_id) > 0})
    result = {"scored": 0, "skipped": 0, "errors": 0}
    for image_id in ids:
        try:
            payload = await api_quality_image(image_id)
            if isinstance(payload, JSONResponse) or not payload.get("scored"):
                result["skipped"] += 1
            else:
                result["scored"] += 1
        except Exception:
            log.exception("quality score error image_id=%s", image_id)
            result["errors"] += 1
        await asyncio.sleep(_THROTTLE_SECONDS)
    invalidate_autocull_cache()
    return result






@router.get("/api/quality/{image_id}")
async def api_quality_image(image_id: int):
    conn = await _open_conn()
    try:
        await _ensure_table(conn)
        cursor = await conn.execute(
            "SELECT * FROM image_quality WHERE image_id = ?",
            (int(image_id),),
        )
        row = await cursor.fetchone()
        if row is not None:
            return quality_scorer.row_payload(dict(row))

        # On-demand score if a thumb exists.
        cursor = await conn.execute(
            """
            SELECT i.id AS image_id, i.filename,
                   (
                     SELECT c.path FROM cache_entries c
                     WHERE c.image_id = i.id AND c.size IN ('sm', 'md')
                     ORDER BY CASE c.size WHEN 'md' THEN 0 ELSE 1 END
                     LIMIT 1
                   ) AS thumb_path
            FROM images i
            WHERE i.id = ?
            """,
            (int(image_id),),
        )
        image_row = await cursor.fetchone()
        if image_row is None:
            return JSONResponse({"error": "Image not found"}, status_code=404)
        image_row = dict(image_row)
        if not image_row.get("thumb_path"):
            return quality_scorer.row_payload(None, image_id=image_id)

        metrics = await _score_image(conn, image_row)
        cursor = await conn.execute(
            "SELECT * FROM image_quality WHERE image_id = ?",
            (int(image_id),),
        )
        stored = await cursor.fetchone()
        payload = quality_scorer.row_payload(dict(stored) if stored else None, image_id=image_id)
        payload["subject_region"] = metrics.get("subject_region")
        payload["thumb_size"] = {
            "width": metrics.get("thumb_width"),
            "height": metrics.get("thumb_height"),
        }
        return payload
    finally:
        await conn.close()


_autocull_cache: dict = {"payload": None, "at": 0.0}
_AUTOCULL_CACHE_TTL_SECONDS = 900.0


def invalidate_autocull_cache() -> None:
    _autocull_cache["payload"] = None
    _autocull_cache["at"] = 0.0


@router.post("/api/quality/autocull")
async def api_quality_autocull(body: AutocullBody | None = None):
    """Suggest one best photo per fully-scored burst/variant stack; no writes.

    The whole-library pass is expensive at 141k images and the desktop banner
    requests it on every session, so the all-stacks payload is cached briefly
    and invalidated whenever a suggestion is applied or a scan finishes.
    """
    stack_ids = None if body is None or body.all or not body.stack_ids else body.stack_ids
    if stack_ids is None:
        cached = _autocull_cache["payload"]
        if cached is not None and time.time() - _autocull_cache["at"] < _AUTOCULL_CACHE_TTL_SECONDS:
            return cached
    conn = await _open_conn()
    try:
        payload = await autocull.suggestions(
            conn,
            stack_ids=stack_ids,
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
    finally:
        await conn.close()
    if stack_ids is None:
        _autocull_cache["payload"] = payload
        _autocull_cache["at"] = time.time()
    return payload


@router.post("/api/quality/autocull/apply")
async def api_quality_autocull_apply(body: AutocullApplyBody):
    """Explicitly accept the current stack suggestions and retain an audit row."""
    conn = await _open_conn()
    try:
        payload = await autocull.apply(
            conn,
            stack_ids=body.stack_ids,
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
    finally:
        await conn.close()
    if payload.get("ok"):
        from core import cache_events

        cache_events.invalidate_rankings_cache()
        cache_events.invalidate_pairing_cache(matchups=True)
        invalidate_autocull_cache()
    return JSONResponse(payload, status_code=200 if payload.get("ok") else 400)
