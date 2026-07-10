"""Cached AI and heuristic masks for the Develop local-adjustment system.

Subject masks use rembg's public U2Net release on the CPU.  Sky v1 deliberately
does not pretend to be an ML model: it is a position-plus-blue-chroma estimate,
refined by a guided filter over the base preview.  ``create_mask`` is the stable
model-swappable seam; callers only receive a raster cache key.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

from core.runtime_paths import resolve_runtime_paths

MaskKind = Literal["subject", "sky"]
DEVELOP_CACHE_ROOT = Path(resolve_runtime_paths().develop_cache_dir)
MODEL_DIRECTORY = DEVELOP_CACHE_ROOT / "models"
MASK_DIRECTORY = DEVELOP_CACHE_ROOT / "ai-masks"
U2NET_MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx"
# SHA-256 of the pinned release asset above.  Do not accept a model merely by name.
U2NET_MODEL_SHA256 = "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491"
U2NET_MODEL_PATH = MODEL_DIRECTORY / "u2net.onnx"
MASK_ALGORITHM_VERSION = "pa-aimask-v1"
U2NET_INPUT_SIZE = 320


class AiMaskError(RuntimeError):
    """A requested mask could not be generated, with an API-safe status code."""

    def __init__(self, message: str, *, status: str = "MASK_FAILED") -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AiMaskResult:
    cache_key: str
    path: Path
    kind: MaskKind
    width: int
    height: int


_model_lock = threading.RLock()
_subject_session: object | None = None
_onnxruntime: object | None = None


def _get_onnxruntime():
    """Delay the expensive native import until a subject mask is actually requested."""

    global _onnxruntime
    if _onnxruntime is None:
        try:
            import onnxruntime
        except ImportError as exc:  # pragma: no cover - deployment status path.
            raise _model_missing("MODEL_MISSING: onnxruntime is not installed") from exc
        _onnxruntime = onnxruntime
    return _onnxruntime


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_missing(message: str) -> AiMaskError:
    return AiMaskError(message, status="MODEL_MISSING")


def ensure_subject_model(*, download: bool = True) -> Path:
    """Return the verified U2Net model, downloading it atomically once if needed."""

    with _model_lock:
        if U2NET_MODEL_PATH.exists():
            if _sha256(U2NET_MODEL_PATH) == U2NET_MODEL_SHA256:
                return U2NET_MODEL_PATH
            raise _model_missing("MODEL_MISSING: u2net.onnx checksum does not match the pinned rembg release")
        if not download:
            raise _model_missing("MODEL_MISSING: pinned u2net.onnx is not installed")
        MODEL_DIRECTORY.mkdir(parents=True, exist_ok=True)
        temporary = U2NET_MODEL_PATH.with_suffix(".onnx.download")
        try:
            with urllib.request.urlopen(U2NET_MODEL_URL, timeout=30) as response, temporary.open("wb") as target:
                shutil.copyfileobj(response, target, length=1024 * 1024)
            if _sha256(temporary) != U2NET_MODEL_SHA256:
                raise _model_missing("MODEL_MISSING: downloaded u2net.onnx failed pinned checksum verification")
            os.replace(temporary, U2NET_MODEL_PATH)
        except AiMaskError:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise _model_missing(f"MODEL_MISSING: could not download pinned u2net.onnx ({exc})") from exc
        return U2NET_MODEL_PATH


def subject_model_status() -> dict[str, str | bool]:
    """Small operational status payload; never reports an unverified model as ready."""

    if importlib.util.find_spec("onnxruntime") is None:
        return {"ready": False, "status": "MODEL_MISSING", "detail": "onnxruntime is not installed"}
    if not U2NET_MODEL_PATH.exists():
        return {"ready": False, "status": "MODEL_MISSING", "detail": "pinned u2net.onnx is not installed"}
    if _sha256(U2NET_MODEL_PATH) != U2NET_MODEL_SHA256:
        return {"ready": False, "status": "MODEL_MISSING", "detail": "u2net.onnx checksum does not match pinned release"}
    return {"ready": True, "status": "READY", "detail": "rembg u2net CPU"}


def _mask_cache_key(base_preview: Path, kind: MaskKind) -> str:
    digest = hashlib.sha256()
    digest.update(MASK_ALGORITHM_VERSION.encode("ascii"))
    digest.update(b"\0")
    digest.update(kind.encode("ascii"))
    with base_preview.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mask_path(cache_key: str) -> Path | None:
    if len(cache_key) != 64 or any(character not in "0123456789abcdef" for character in cache_key):
        return None
    return MASK_DIRECTORY / f"{cache_key}.png"


def _load_base_preview(base_preview: Path) -> np.ndarray:
    try:
        with Image.open(base_preview) as image:
            return np.asarray(image.convert("RGB"), dtype=np.float32) / np.float32(255.0)
    except (OSError, ValueError) as exc:
        raise AiMaskError(f"Base preview is unreadable: {exc}") from exc


def _subject_session_for_model():
    global _subject_session
    if _subject_session is None:
        model = ensure_subject_model()
        onnxruntime = _get_onnxruntime()
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        _subject_session = onnxruntime.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
    return _subject_session


def _subject_mask(rgb: np.ndarray) -> np.ndarray:
    """Run rembg U2Net using its ImageNet-normalized 320px RGB input."""

    session = _subject_session_for_model()
    height, width = rgb.shape[:2]
    resized = np.asarray(
        Image.fromarray(np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8), mode="RGB").resize(
            (U2NET_INPUT_SIZE, U2NET_INPUT_SIZE), Image.Resampling.BILINEAR
        ),
        dtype=np.float32,
    ) / np.float32(255.0)
    normalized = (resized - np.asarray((0.485, 0.456, 0.406), dtype=np.float32)) / np.asarray(
        (0.229, 0.224, 0.225), dtype=np.float32
    )
    tensor = np.ascontiguousarray(normalized.transpose(2, 0, 1)[None, ...], dtype=np.float32)
    output = np.asarray(session.run(None, {session.get_inputs()[0].name: tensor})[0], dtype=np.float32).squeeze()
    if output.ndim != 2:
        raise AiMaskError("U2Net returned an unexpected mask shape")
    output -= output.min()
    maximum = float(output.max())
    if maximum > 1e-6:
        output /= maximum
    return np.asarray(
        Image.fromarray(np.rint(np.clip(output, 0, 1) * 255).astype(np.uint8), mode="L").resize(
            (width, height), Image.Resampling.BILINEAR
        ),
        dtype=np.float32,
    ) / np.float32(255.0)


def _box_mean(values: np.ndarray, radius: int) -> np.ndarray:
    """Edge-padded box average, used by the sky heuristic's guided filter."""

    if radius <= 0:
        return values
    padded = np.pad(values, ((radius, radius), (radius, radius)), mode="edge")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)
    span = radius * 2 + 1
    return (integral[span:, span:] - integral[:-span, span:] - integral[span:, :-span] + integral[:-span, :-span]) / float(span * span)


def _guided_filter(guide: np.ndarray, initial: np.ndarray, radius: int, epsilon: float = 0.003) -> np.ndarray:
    """Classic gray guided filter, retaining edges in the base preview."""

    mean_guide = _box_mean(guide, radius)
    mean_initial = _box_mean(initial, radius)
    variance = _box_mean(guide * guide, radius) - mean_guide * mean_guide
    covariance = _box_mean(guide * initial, radius) - mean_guide * mean_initial
    a = covariance / (variance + epsilon)
    b = mean_initial - a * mean_guide
    return _box_mean(a, radius) * guide + _box_mean(b, radius)


def _sky_mask(rgb: np.ndarray) -> np.ndarray:
    """Honest v1 sky heuristic: top-of-frame and blue-chroma prior, edge refined."""

    height, width = rgb.shape[:2]
    red, green, blue = (rgb[..., channel] for channel in range(3))
    luminance = red * 0.2126 + green * 0.7152 + blue * 0.0722
    vertical = np.clip(1.0 - np.arange(height, dtype=np.float32)[:, None] / max(height * 0.86, 1.0), 0.0, 1.0)
    blue_chroma = np.clip((blue - np.maximum(red, green) + 0.10) / 0.34, 0.0, 1.0)
    coarse = vertical * (0.16 + 0.84 * blue_chroma)
    refined = _guided_filter(luminance, coarse, max(2, min(height, width) // 80))
    return np.clip(refined, 0.0, 1.0)


def _render_mask(base_preview: Path, kind: MaskKind) -> tuple[np.ndarray, int, int]:
    rgb = _load_base_preview(base_preview)
    if kind == "subject":
        mask = _subject_mask(rgb)
    elif kind == "sky":
        mask = _sky_mask(rgb)
    else:  # Literal typing protects callers; keep the public function defensive.
        raise AiMaskError("kind must be subject or sky", status="INVALID_KIND")
    return np.clip(mask, 0.0, 1.0), int(rgb.shape[1]), int(rgb.shape[0])


def create_mask(base_preview: Path, kind: MaskKind) -> AiMaskResult:
    """Create or reuse the PNG raster keyed by preview content and mask kind."""

    cache_key = _mask_cache_key(base_preview, kind)
    output_path = mask_path(cache_key)
    if output_path is None:  # pragma: no cover - generated keys are always valid.
        raise AiMaskError("Generated an invalid mask cache key")
    if output_path.exists():
        with Image.open(output_path) as image:
            return AiMaskResult(cache_key, output_path, kind, image.width, image.height)
    with _model_lock:
        if output_path.exists():
            with Image.open(output_path) as image:
                return AiMaskResult(cache_key, output_path, kind, image.width, image.height)
        mask, width, height = _render_mask(base_preview, kind)
        MASK_DIRECTORY.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".png.tmp")
        Image.fromarray(np.rint(mask * 255).astype(np.uint8), mode="L").save(temporary, format="PNG")
        os.replace(temporary, output_path)
    return AiMaskResult(cache_key, output_path, kind, width, height)
