"""Shared ML device selection for Azimuth Photo workers.

One policy for every model loader: prefer CUDA when it is available and
allowed, honor ``AZIMUTH_ML_DEVICE=cpu|cuda``, and never crash when
CUDA is missing — callers get the same CPU path they had before.
"""

from __future__ import annotations

import os


ENV_NAME = "AZIMUTH_ML_DEVICE"


def _env_override() -> str | None:
    raw = os.environ.get(ENV_NAME, "").strip().lower()
    if raw in ("cpu", "cuda"):
        return raw
    return None


def cuda_available() -> bool:
    """True when torch can see a CUDA device. Never raises."""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def preferred_device() -> str:
    """Return ``cuda`` or ``cpu`` for loaders to consume."""
    override = _env_override()
    if override == "cpu":
        return "cpu"
    if override == "cuda":
        return "cuda" if cuda_available() else "cpu"
    return "cuda" if cuda_available() else "cpu"


def sentence_transformers_device() -> str:
    """``device=`` argument for ``SentenceTransformer``."""
    return preferred_device()


def transformers_device_map(*, quantized: bool = False) -> str | dict[str, int | str]:
    """``device_map`` for Hugging Face ``from_pretrained``.

    Matches today's caption behavior on CUDA (bnb → whole model on GPU 0,
    otherwise ``auto``). On CPU, pin the whole model to CPU so accelerate
    never tries a missing GPU index.
    """
    if preferred_device() == "cuda":
        return {"": 0} if quantized else "auto"
    return {"": "cpu"} if quantized else "cpu"


def onnx_providers() -> list[str]:
    """ORT provider list: CUDA when preferred and installed, else CPU.

    Installed CPU-only ``onnxruntime`` has no CUDA EP — this returns CPU and
    behavior stays identical to today's hardcoded path.
    """
    prefer = preferred_device()
    try:
        import onnxruntime as ort

        available = set(ort.get_available_providers())
    except Exception:
        return ["CPUExecutionProvider"]

    providers: list[str] = []
    if prefer == "cuda" and "CUDAExecutionProvider" in available:
        providers.append("CUDAExecutionProvider")
    if "CPUExecutionProvider" in available:
        providers.append("CPUExecutionProvider")
    return providers or ["CPUExecutionProvider"]


def insightface_ctx_id() -> int:
    """InsightFace prepare ctx: GPU 0 when ORT CUDA is actually usable, else -1."""
    providers = onnx_providers()
    if preferred_device() == "cuda" and providers and providers[0] == "CUDAExecutionProvider":
        return 0
    return -1


def empty_cuda_cache() -> None:
    """Release cached CUDA blocks after model unload. Never raises."""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
