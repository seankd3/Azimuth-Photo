"""Background People recognition worker.

The worker reads app-owned cached previews, never source media. Identity is
assigned from face embeddings only; whole-image Qwen embeddings remain a search
ranking layer after People filters are applied.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
import os
import subprocess
import sys
import threading
import time
from typing import Any

import settings


FACE_MODEL_LICENSE_TEXT = (
    "InsightFace model packs are non-commercial research models by default. "
    "Use face_model_dir with a licensed compatible model for other use."
)
DEPENDENCY_PACKAGES = ("insightface", "onnxruntime", "opencv-python-headless")
WORKER_SLEEP_SECONDS = 20


@dataclass(frozen=True)
class PeopleBackgroundDecision:
    work_mode: str
    mode: str
    pause: bool
    sleep_seconds: float
    thumbnail_batch_size: int
    thumbnail_pause_seconds: float
    embedding_pause_seconds: float
    reason: str
    idle_policy: str = "work_mode_only"
    load_1m: float = 0.0
    cpu_count: int = 1
    available_memory_gb: float = 0.0
    swap_used_pct: float = 0.0
    checked_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["can_start_heavy_work"] = not self.pause
        return data

_status_lock = threading.Lock()
_status: dict[str, Any] = {
    "state": "idle",
    "message": "People recognition has not scanned cached previews yet.",
    "ready": False,
    "running": False,
    "model_id": "buffalo_l",
    "model_dir": "",
    "model_license": FACE_MODEL_LICENSE_TEXT,
    "auto_install": True,
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
_face_app = None
_face_app_key: tuple[str, str, int] | None = None
_scan_now = False
AsyncDictProvider = Callable[..., Awaitable[dict[str, Any]]]
AsyncIntProvider = Callable[..., Awaitable[int]]
AsyncListProvider = Callable[..., Awaitable[list[dict[str, Any]]]]
_count_images_needing_faces: AsyncIntProvider | None = None
_get_images_needing_faces: AsyncListProvider | None = None
_store_face_scan_result: AsyncDictProvider | None = None
_cluster_unassigned_faces: AsyncDictProvider | None = None


def configure(
    *,
    count_images_needing_faces: AsyncIntProvider | None = None,
    get_images_needing_faces: AsyncListProvider | None = None,
    store_face_scan_result: AsyncDictProvider | None = None,
    cluster_unassigned_faces: AsyncDictProvider | None = None,
) -> None:
    global _count_images_needing_faces, _get_images_needing_faces
    global _store_face_scan_result, _cluster_unassigned_faces
    if count_images_needing_faces is not None:
        _count_images_needing_faces = count_images_needing_faces
    if get_images_needing_faces is not None:
        _get_images_needing_faces = get_images_needing_faces
    if store_face_scan_result is not None:
        _store_face_scan_result = store_face_scan_result
    if cluster_unassigned_faces is not None:
        _cluster_unassigned_faces = cluster_unassigned_faces


def _configured(provider, name: str):
    if provider is None:
        raise RuntimeError(f"face_worker is missing configured dependency: {name}")
    return provider


def _set_status(**updates: Any) -> None:
    with _status_lock:
        _status.update(updates)


def get_worker_status() -> dict[str, Any]:
    with _status_lock:
        return dict(_status)


def request_scan_now() -> dict[str, Any]:
    global _scan_now
    _scan_now = True
    _set_status(message="People recognition scan requested.")
    return get_worker_status()


def pause_face_worker() -> None:
    current = settings.get_settings()
    settings.save_settings({**current, "people_scan_enabled": False})
    _set_status(state="paused", ready=False, message="People recognition is paused.")


def resume_face_worker() -> None:
    current = settings.get_settings()
    settings.save_settings({**current, "people_scan_enabled": True})
    request_scan_now()
    _set_status(state="idle", message="People recognition will scan cached previews.")


def _normal_work_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"browse", "balanced", "max"}:
        return mode
    return "balanced"


def _people_background_decision(config: dict[str, Any]) -> PeopleBackgroundDecision:
    work_mode = _normal_work_mode(config.get("background_work_mode"))
    base = None
    try:
        import resource_governor

        # People recognition follows the explicit Work Mode setting. Use a high
        # idle value so activity does not suppress Light Background mode, while
        # keeping the governor's memory/load pressure checks.
        base = resource_governor.get_background_decision(999999.0, work_mode=work_mode)
    except Exception:
        base = None

    if base is not None and base.pause and base.reason in {"swap pressure", "low available memory", "browse mode"}:
        return PeopleBackgroundDecision(
            work_mode=work_mode,
            mode="paused",
            pause=True,
            sleep_seconds=float(base.sleep_seconds or 10.0),
            thumbnail_batch_size=0,
            thumbnail_pause_seconds=float(base.thumbnail_pause_seconds or 5.0),
            embedding_pause_seconds=float(base.embedding_pause_seconds or 5.0),
            reason=str(base.reason or "paused"),
            load_1m=float(base.load_1m or 0.0),
            cpu_count=int(base.cpu_count or 1),
            available_memory_gb=float(base.available_memory_gb or 0.0),
            swap_used_pct=float(base.swap_used_pct or 0.0),
            checked_at=float(base.checked_at or time.time()),
        )

    if work_mode == "browse":
        return PeopleBackgroundDecision(
            work_mode=work_mode,
            mode="paused",
            pause=True,
            sleep_seconds=10.0,
            thumbnail_batch_size=0,
            thumbnail_pause_seconds=5.0,
            embedding_pause_seconds=5.0,
            reason="browse mode",
            checked_at=time.time(),
        )

    base_mode = str(getattr(base, "mode", "") or "normal")
    base_reason = str(getattr(base, "reason", "") or "work mode enabled")
    if work_mode == "max":
        return PeopleBackgroundDecision(
            work_mode=work_mode,
            mode=base_mode if base_mode in {"normal", "gentle"} else "normal",
            pause=False,
            sleep_seconds=0.0,
            thumbnail_batch_size=16 if base_mode != "gentle" else 8,
            thumbnail_pause_seconds=0.05 if base_mode != "gentle" else 0.5,
            embedding_pause_seconds=0.05 if base_mode != "gentle" else 0.5,
            reason=base_reason,
            load_1m=float(getattr(base, "load_1m", 0.0) or 0.0),
            cpu_count=int(getattr(base, "cpu_count", 1) or 1),
            available_memory_gb=float(getattr(base, "available_memory_gb", 0.0) or 0.0),
            swap_used_pct=float(getattr(base, "swap_used_pct", 0.0) or 0.0),
            checked_at=float(getattr(base, "checked_at", time.time()) or time.time()),
        )

    return PeopleBackgroundDecision(
        work_mode="balanced",
        mode="gentle" if base_mode == "gentle" else "light",
        pause=False,
        sleep_seconds=0.0,
        thumbnail_batch_size=1 if base_mode == "gentle" else 2,
        thumbnail_pause_seconds=3.0 if base_mode == "gentle" else 1.5,
        embedding_pause_seconds=3.0 if base_mode == "gentle" else 1.5,
        reason="light background" if base_mode != "gentle" else base_reason,
        load_1m=float(getattr(base, "load_1m", 0.0) or 0.0),
        cpu_count=int(getattr(base, "cpu_count", 1) or 1),
        available_memory_gb=float(getattr(base, "available_memory_gb", 0.0) or 0.0),
        swap_used_pct=float(getattr(base, "swap_used_pct", 0.0) or 0.0),
        checked_at=float(getattr(base, "checked_at", time.time()) or time.time()),
    )


def _missing_face_dependencies() -> list[str]:
    missing = []
    for module_name, package_name in (
        ("insightface", "insightface"),
        ("onnxruntime", "onnxruntime"),
        ("cv2", "opencv-python-headless"),
    ):
        try:
            __import__(module_name)
        except Exception:
            missing.append(package_name)
    return missing


def _install_face_dependencies(missing: list[str]) -> None:
    if not missing:
        return
    _set_status(
        state="installing",
        ready=False,
        message=f"Installing People recognition dependencies: {', '.join(missing)}.",
    )
    subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])


def _load_face_app(config: dict[str, Any]):
    global _face_app, _face_app_key
    model_id = str(config.get("face_model_id") or "buffalo_l").strip() or "buffalo_l"
    model_dir = str(config.get("face_model_dir") or "").strip()
    det_size = int(config.get("face_detection_size") or 640)
    key = (model_id, model_dir, det_size)
    if _face_app is not None and _face_app_key == key:
        return _face_app

    missing = _missing_face_dependencies()
    if missing:
        if not bool(config.get("people_auto_install", True)):
            raise RuntimeError(
                "People recognition dependencies are not installed and people_auto_install is off: "
                + ", ".join(missing)
            )
        _install_face_dependencies(missing)

    from insightface.app import FaceAnalysis

    os.makedirs(model_dir, exist_ok=True)
    app = FaceAnalysis(name=model_id, root=model_dir, providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(det_size, det_size))
    _face_app = app
    _face_app_key = key
    return app


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
            if not bool(config.get("people_scan_enabled", True)):
                _set_status(
                    state="paused",
                    ready=False,
                    message="People recognition is paused.",
                    last_error="",
                )
                await asyncio.sleep(WORKER_SLEEP_SECONDS)
                continue

            decision = _people_background_decision(config)
            pending = await _configured(
                _count_images_needing_faces,
                "count_images_needing_faces",
            )(
                model_id=model_id,
                cache_root=str(config.get("ssd_cache_dir") or ""),
            )
            decision_payload = decision.to_dict() if decision is not None else {}
            _set_status(
                pending_cached_images=pending,
                background_decision=decision_payload,
                last_error="",
            )

            if pending <= 0:
                _scan_now = False
                _set_status(
                    state="idle",
                    ready=True,
                    message="People recognition is caught up on cached previews.",
                    last_batch_size=0,
                    last_batch_seconds=0.0,
                    last_scan_at=time.time(),
                )
                await asyncio.sleep(WORKER_SLEEP_SECONDS)
                continue

            if decision.pause and not _scan_now:
                _set_status(
                    state="waiting",
                    ready=True,
                    message=(
                        f"People recognition has {pending} cached previews queued; "
                        f"waiting because {decision.reason} is active."
                    ),
                    last_batch_size=0,
                    last_batch_seconds=0.0,
                    last_scan_at=time.time(),
                )
                await asyncio.sleep(max(1.0, float(decision.sleep_seconds or WORKER_SLEEP_SECONDS)))
                continue

            batch_limit = 4
            if decision.thumbnail_batch_size > 0:
                batch_limit = int(decision.thumbnail_batch_size)
            if _scan_now:
                batch_limit = max(batch_limit, 8)
            rows = await _configured(
                _get_images_needing_faces,
                "get_images_needing_faces",
            )(
                model_id=model_id,
                cache_root=str(config.get("ssd_cache_dir") or ""),
                limit=max(1, min(batch_limit, 16)),
            )
            _scan_now = False
            if not rows:
                _set_status(
                    state="idle",
                    ready=True,
                    message="People recognition is caught up on cached previews.",
                    last_batch_size=0,
                    last_batch_seconds=0.0,
                    last_scan_at=time.time(),
                )
                await asyncio.sleep(WORKER_SLEEP_SECONDS)
                continue

            loop = asyncio.get_running_loop()
            _set_status(
                state="scanning",
                ready=True,
                message=f"People recognition is scanning {len(rows)} of {pending} queued cached previews.",
                last_error="",
            )
            await loop.run_in_executor(None, _load_face_app, config)

            for row in rows:
                image_id = int(row["id"])
                cache_path = str(row.get("cache_path") or "")
                try:
                    faces = await loop.run_in_executor(None, _detect_faces, cache_path, config)
                    await _configured(
                        _store_face_scan_result,
                        "store_face_scan_result",
                    )(
                        image_id=image_id,
                        model_id=model_id,
                        cache_path=cache_path,
                        faces=faces,
                        status="scanned",
                    )
                    scanned += 1
                    detected += len(faces)
                except Exception as exc:
                    await _configured(
                        _store_face_scan_result,
                        "store_face_scan_result",
                    )(
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

            cluster = await _configured(
                _cluster_unassigned_faces,
                "cluster_unassigned_faces",
            )(
                model_id=model_id,
                similarity_threshold=float(config.get("face_similarity_threshold") or 0.52),
                merge_threshold=float(config.get("face_merge_suggestion_threshold") or 0.62),
            )
            elapsed = round(time.perf_counter() - started, 3)
            current = get_worker_status()
            remaining = max(0, pending - scanned)
            try:
                remaining = await _configured(
                    _count_images_needing_faces,
                    "count_images_needing_faces",
                )(
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
            _set_status(
                state="error",
                ready=False,
                message="People recognition is unavailable.",
                last_error=str(exc),
                last_batch_seconds=round(time.perf_counter() - started, 3),
                last_scan_at=time.time(),
            )
            await asyncio.sleep(WORKER_SLEEP_SECONDS)
