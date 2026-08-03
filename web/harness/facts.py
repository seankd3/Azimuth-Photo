"""How a pixel buffer is turned into a fact worth freezing.

An exact byte hash of decoded pixels would be a golden that breaks whenever
libjpeg or LibRaw ships a rounding change, on a machine that did nothing wrong.
Shape, dtype and the distribution catch every change that matters — a different
gamma, a different bit depth, a different demosaic, a different colour matrix
all move these numbers well past the third decimal — and ignore last-bit noise.
"""

from __future__ import annotations

import hashlib

import numpy as np


def digest(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=8).hexdigest()


def pixels(array) -> dict:
    """Shape, type and distribution of a decoded image."""

    arr = np.asarray(array)
    if arr.size == 0:
        return {"shape": list(arr.shape), "dtype": str(arr.dtype), "empty": True}
    flat = arr.astype(np.float64).ravel()
    scale = float(np.iinfo(arr.dtype).max) if np.issubdtype(arr.dtype, np.integer) else 1.0
    if scale > 1.0:
        flat = flat / scale
    percentiles = np.percentile(flat, [0, 5, 50, 95, 100])
    return {
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "mean": round(float(flat.mean()), 3),
        "std": round(float(flat.std()), 3),
        "p0": round(float(percentiles[0]), 3),
        "p5": round(float(percentiles[1]), 3),
        "p50": round(float(percentiles[2]), 3),
        "p95": round(float(percentiles[3]), 3),
        "p100": round(float(percentiles[4]), 3),
    }


def image(pil_image) -> dict:
    """Same, for a PIL image, plus its mode and size."""

    facts = pixels(np.asarray(pil_image))
    facts["mode"] = pil_image.mode
    facts["size"] = list(pil_image.size)
    return facts


def failure(exc: BaseException) -> dict:
    """A refusal is a fact too — record the type, never the message.

    Messages carry paths and library versions; the type is the behaviour.
    """

    return {"failed": type(exc).__name__}
