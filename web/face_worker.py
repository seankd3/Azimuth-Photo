"""Background People recognition worker.

The worker reads app-owned cached previews, never source media. Identity is
assigned from face embeddings only; whole-image Qwen embeddings remain a search
ranking layer after People filters are applied.
"""

from __future__ import annotations

import asyncio
import db
from dataclasses import asdict, dataclass
from importlib.util import find_spec
import os
import threading
import time
from typing import Any

import settings
from core import memory_pressure, work_coordination


FACE_MODEL_LICENSE_TEXT = (
    "InsightFace model packs are non-commercial research models by default. "
    "Use face_model_dir with a licensed compatible model for other use."
)
WORKER_SLEEP_SECONDS = 20


@dataclass(frozen=True)
class PeopleBackgroundDecision:
    mode: str
    pause: bool
    sleep_seconds: float
    thumbnail_batch_size: int
    thumbnail_pause_seconds: float
    embedding_pause_seconds: float
    reason: str
    idle_policy: str = "manual_people_scan"
    load_1m: float = 0.0
    cpu_count: int = 1
    available_memory_gb: float = 0.0
    swap_used_pct: float = 0.0
    checked_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["can_start_heavy_work"] = not self.pause and self.mode != "paused"
        return data

_status_lock = threading.Lock()
_status: dict[str, Any] = {
    "state": "idle",
    "message": "People has not scanned cached previews yet.",
    "ready": False,
    "running": False,
    "model_id": "buffalo_l",
    "model_dir": "",
    "model_license": FACE_MODEL_LICENSE_TEXT,
    "auto_install": True,
    "runtime_install": False,
    "last_error": "",
    "last_scan_at": None,
    "last_batch_size": 0,
    "last_batch_seconds": 0.0,
    "pending_cached_images": 0,
    "background_decision": {},
    "session_detected_faces": 0,
    "session_scanned_images": 0,
    "session_started_at": None,
    "source_files_preserved": True,
    "source_media_read": "app_owned_cached_previews_only",
}
_face_app_key: tuple[str, str, int] | None = None
_scan_now = False
def _initial_manual_pause() -> bool:
    try:
        return not bool(settings.get_settings()["people_scan_enabled"])
    except Exception:
        return True


_face_manual_pause = _initial_manual_pause()
_face_manual_pause_message = "People is stopped until you start it from Background Work."


def _set_status(**updates: Any) -> None:
    with _status_lock:
        _status.update(updates)


def get_worker_status() -> dict[str, Any]:
    with _status_lock:
        status = dict(_status)
    status["manual_pause"] = _face_manual_pause
    return status


def mark_dependencies_unavailable(capability: dict[str, Any]) -> None:
    """Publish one stable missing-pack state without starting the worker loop."""

    _set_status(
        state="unavailable",
        ready=False,
        running=False,
        runtime_install=False,
        message=capability["message"],
        last_error="",
    )


def manual_pause_active() -> bool:
    return _face_manual_pause


def request_scan_now() -> dict[str, Any]:
    global _scan_now
    _scan_now = True
    _set_status(message="People scan requested.")
    return get_worker_status()


def _enter_paused(message: str) -> None:
    work_coordination.release_manual_owner("people")
    _set_status(state="paused", ready=False, message=message, last_error="")


def pause_face_worker(*, persist: bool = True) -> None:
    global _face_manual_pause, _face_manual_pause_message
    if persist:
        config = settings.get_settings()
        if bool(config["people_scan_enabled"]):
            settings.save_settings({**config, "people_scan_enabled": False})
    _face_manual_pause = True
    _face_manual_pause_message = "People is stopped."
    _enter_paused(_face_manual_pause_message)


def resume_face_worker(*, persist: bool = True) -> None:
    global _face_manual_pause, _face_manual_pause_message
    if persist:
        config = settings.get_settings()
        if not bool(config["people_scan_enabled"]):
            settings.save_settings({**config, "people_scan_enabled": True})
    _face_manual_pause = False
    _face_manual_pause_message = ""
    work_coordination.claim_manual_owner("people")
    request_scan_now()
    _set_status(state="idle", message="People will scan cached previews.")


def _people_background_decision(config: dict[str, Any]) -> PeopleBackgroundDecision:
    del config
    return memory_pressure.apply_to_decision(
        PeopleBackgroundDecision(
            mode="normal",
            pause=False,
            sleep_seconds=0.0,
            thumbnail_batch_size=16,
            thumbnail_pause_seconds=0.0,
            embedding_pause_seconds=0.0,
            reason="people scan enabled",
            idle_policy="manual_people_scan",
            checked_at=time.time(),
        )
    )


def _missing_face_dependencies() -> list[str]:
    return [
        package_name
        for module_name, package_name in (
        ("insightface", "insightface"),
        ("onnxruntime", "onnxruntime"),
        ("cv2", "opencv-python-headless"),
        )
        if find_spec(module_name) is None
    ]


def _drop_face_residency() -> None:
    """Clear face globals. Called by ModelPool on unload/evict."""
    global _face_app_key
    _face_app_key = None
    from core.ml_device import empty_cuda_cache

    empty_cuda_cache()


def _load_face_app(config: dict[str, Any], interactive: bool = False):
    global _face_app_key
    from core.model_pool import COST_PEOPLE_RAM, COST_PEOPLE_VRAM, get_model_pool
    from core.ml_device import insightface_ctx_id, onnx_providers, preferred_device

    model_id = str(config.get("face_model_id") or "buffalo_l").strip() or "buffalo_l"
    model_dir = str(config.get("face_model_dir") or "").strip()
    det_size = int(config.get("face_detection_size") or 640)
    providers = tuple(onnx_providers())
    ctx_id = insightface_ctx_id()
    key = (model_id, model_dir, det_size, preferred_device(), providers, ctx_id)
    if _face_app_key is not None and _face_app_key != key:
        _unload_face_app()

    def _load():
        global _face_app_key
        missing = _missing_face_dependencies()
        if missing:
            raise RuntimeError(
                "People recognition optional pack is not installed: "
                + ", ".join(missing)
                + ". Run python -m pip install -r requirements-ai-people.txt. "
                "Runtime package installation is disabled."
            )

        from insightface.app import FaceAnalysis

        os.makedirs(model_dir, exist_ok=True)
        app = FaceAnalysis(name=model_id, root=model_dir, providers=list(providers))
        app.prepare(ctx_id=ctx_id, det_size=(det_size, det_size))
        _face_app_key = key
        return app

    return get_model_pool().acquire(
        "people",
        load_fn=_load,
        unload_fn=_drop_face_residency,
        vram_bytes=COST_PEOPLE_VRAM,
        ram_bytes=COST_PEOPLE_RAM,
        interactive=interactive,
    )


def _unload_face_app() -> None:
    from core.model_pool import get_model_pool

    if not get_model_pool().unload("people"):
        _drop_face_residency()


async def _wait_for_face_turn() -> None:
    if work_coordination.manual_turn_blocked("people"):
        _set_status(
            state="waiting_for_turn",
            ready=False,
            message="People is waiting for other background work.",
            last_error="",
        )
    await work_coordination.wait_for_manual_turn("people")


async def _renew_face_turn() -> None:
    if work_coordination.lost_ownership("people"):
        _unload_face_app()
    await _wait_for_face_turn()


def _detect_faces(cache_path: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    import cv2
    import numpy as np

    app = _load_face_app(config)
    image = cv2.imread(cache_path)
    if image is None:
        raise RuntimeError("Cached preview could not be decoded for face detection.")
    height, width = image.shape[:2]
    image_area = max(float(width * height), 1.0)
    detections = []
    for face in app.get(image):
        bbox = getattr(face, "bbox", None)
        if bbox is None or len(bbox) < 4:
            continue
        x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
        w = max(0.0, x2 - x1)
        h = max(0.0, y2 - y1)
        confidence = float(getattr(face, "det_score", 0.0) or 0.0)
        area_score = min(1.0, max(0.0, (w * h) / image_area) * 8.0)
        quality = max(0.0, min(1.0, confidence * 0.75 + area_score * 0.25))
        embedding = getattr(face, "normed_embedding", None)
        if embedding is None:
            embedding = getattr(face, "embedding", None)
        if embedding is None:
            continue
        detections.append(
            {
                "bbox": {"x": x1, "y": y1, "w": w, "h": h},
                "confidence": confidence,
                "quality": quality,
                "embedding": np.asarray(embedding, dtype=np.float32),
            }
        )
    return detections


async def run_face_worker() -> None:
    try:
        await _run_face_worker_loop()
    finally:
        work_coordination.release_manual_owner("people")


async def _run_face_worker_loop() -> None:
    global _scan_now
    _set_status(running=True, session_started_at=time.time())
    while True:
        started = time.perf_counter()
        scanned = 0
        detected = 0
        try:
            config = settings.get_settings()
            model_id = str(config.get("face_model_id") or "buffalo_l")
            _set_status(
                model_id=model_id,
                model_dir=str(config.get("face_model_dir") or ""),
                auto_install=bool(config.get("people_auto_install", True)),
            )
            if _face_manual_pause or not bool(config["people_scan_enabled"]):
                _enter_paused(
                    _face_manual_pause_message or "People is stopped."
                )
                await asyncio.sleep(WORKER_SLEEP_SECONDS)
                continue

            decision = _people_background_decision(config)
            decision_payload = decision.to_dict() if decision is not None else {}
            if decision.pause:
                work_coordination.release_manual_owner("people")
                _set_status(
                    state="paused",
                    ready=False,
                    message=memory_pressure.PAUSE_MESSAGE
                    if decision.reason == memory_pressure.PAUSE_REASON
                    else f"People paused: {decision.reason}.",
                    background_decision=decision_payload,
                    last_error="",
                )
                await asyncio.sleep(max(2.0, float(decision.sleep_seconds or 0.0)))
                continue

            pending = await db.count_images_needing_faces(
                model_id=model_id,
                cache_root=str(config.get("ssd_cache_dir") or ""),
            )
            _set_status(
                pending_cached_images=pending,
                background_decision=decision_payload,
                last_error="",
            )

            if pending <= 0:
                _scan_now = False
                work_coordination.release_manual_owner("people")
                _set_status(
                    state="idle",
                    ready=True,
                    message="People is caught up on cached previews.",
                    last_batch_size=0,
                    last_batch_seconds=0.0,
                    last_scan_at=time.time(),
                )
                await asyncio.sleep(WORKER_SLEEP_SECONDS)
                continue

            batch_limit = 4
            if decision.thumbnail_batch_size > 0:
                batch_limit = int(decision.thumbnail_batch_size)
            if _scan_now:
                batch_limit = max(batch_limit, 8)
            rows = await db.get_images_needing_faces(
                model_id=model_id,
                cache_root=str(config.get("ssd_cache_dir") or ""),
                limit=max(1, min(batch_limit, 16)),
            )
            _scan_now = False
            if not rows:
                work_coordination.release_manual_owner("people")
                _set_status(
                    state="idle",
                    ready=True,
                    message="People is caught up on cached previews.",
                    last_batch_size=0,
                    last_batch_seconds=0.0,
                    last_scan_at=time.time(),
                )
                await asyncio.sleep(WORKER_SLEEP_SECONDS)
                continue

            loop = asyncio.get_running_loop()
            await _wait_for_face_turn()
            _set_status(
                state="scanning",
                ready=True,
                message=f"People is scanning {len(rows)} of {pending} queued cached previews.",
            )
            with work_coordination.manual_bulk("people"):
                async with work_coordination.lease_heartbeat("people"):
                    await loop.run_in_executor(None, _load_face_app, config)

            with work_coordination.manual_bulk("people"):
                for row in rows:
                    await _renew_face_turn()
                    image_id = int(row["id"])
                    cache_path = str(row.get("cache_path") or "")
                    try:
                        faces = await loop.run_in_executor(None, _detect_faces, cache_path, config)
                        await db.store_face_scan_result(
                            image_id=image_id,
                            model_id=model_id,
                            cache_path=cache_path,
                            faces=faces,
                            status="scanned",
                        )
                        scanned += 1
                        detected += len(faces)
                    except Exception as exc:
                        await db.store_face_scan_result(
                            image_id=image_id,
                            model_id=model_id,
                            cache_path=cache_path,
                            faces=[],
                            status="error",
                            error=str(exc),
                        )
                        _set_status(last_error=str(exc))
                    if decision.embedding_pause_seconds > 0:
                        await asyncio.sleep(float(decision.embedding_pause_seconds))

            cluster = await db.cluster_unassigned_faces(
                model_id=model_id,
                similarity_threshold=float(config.get("face_similarity_threshold") or 0.52),
                merge_threshold=float(config.get("face_merge_suggestion_threshold") or 0.62),
            )
            elapsed = round(time.perf_counter() - started, 3)
            current = get_worker_status()
            remaining = max(0, pending - scanned)
            try:
                remaining = await db.count_images_needing_faces(
                    model_id=model_id,
                    cache_root=str(config.get("ssd_cache_dir") or ""),
                )
            except Exception:
                pass
            _set_status(
                state="ready",
                ready=True,
                message=(
                    f"Scanned {scanned} cached previews, detected {detected} faces, "
                    f"assigned {int(cluster.get('assigned') or 0)} faces; "
                    f"{remaining} cached previews queued."
                ),
                last_batch_size=scanned,
                last_batch_seconds=elapsed,
                last_scan_at=time.time(),
                pending_cached_images=remaining,
                session_detected_faces=int(current.get("session_detected_faces") or 0) + detected,
                session_scanned_images=int(current.get("session_scanned_images") or 0) + scanned,
            )
        except Exception as exc:
            work_coordination.release_manual_owner("people")
            _set_status(
                state="error",
                ready=False,
                message="People is unavailable.",
                last_error=str(exc),
                last_batch_seconds=round(time.perf_counter() - started, 3),
                last_scan_at=time.time(),
            )
            await asyncio.sleep(WORKER_SLEEP_SECONDS)
