"""Find the faces on a photograph. One model, loaded once, CPU on purpose.

Identity is not resemblance: CLIP-family embeddings run 51-68 points worse
than a face model on telling people apart (measured 08-17), so people get
their own encoder — InsightFace's buffalo_l, detection plus a 512-d ArcFace
identity vector per face. It runs on the CPU deliberately: the card belongs
to SigLIP and the interactive app, a face pass is an overnight lane, and
onnxruntime's CPU path needs no second CUDA stack beside torch's.

The answer is a cache kind like any other: computed from the grid tile,
never evicted, one row per photograph holding every face found — its box,
its confidence, and its identity vector, packed small.
"""

from __future__ import annotations

import base64
import json
import os
import threading

MODEL = "buffalo_l"
KEY = f"insightface--{MODEL}@arcface:512"
RECIPE = '{"model":"' + KEY + '"}'

# Below these a "face" is usually texture: a knot in wood, a pattern in
# foliage. The box side is in tile pixels.
LEAST_SCORE = 0.55
LEAST_SIDE = 28

_lock = threading.Lock()
_loaded = None


def _home() -> str:
    return os.path.join(os.path.expanduser("~"), ".insightface", "models", MODEL)


def ready() -> bool:
    """Can this machine find faces right now? Import and weights, asked
    every time — a machine without them skips the kind and records nothing."""

    try:
        import insightface  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    home = _home()
    return os.path.isdir(home) and any(name.endswith(".onnx") for name in os.listdir(home))


def fetch() -> None:
    """Bring the face model to this machine, deliberately and in the open —
    the only place its download is allowed."""

    from insightface.app import FaceAnalysis

    FaceAnalysis(name=MODEL, providers=["CPUExecutionProvider"],
                 allowed_modules=["detection", "recognition"])


def _model():
    global _loaded
    if _loaded is None:
        with _lock:
            if _loaded is None:
                from insightface.app import FaceAnalysis

                app = FaceAnalysis(name=MODEL, providers=["CPUExecutionProvider"],
                                   allowed_modules=["detection", "recognition"])
                app.prepare(ctx_id=-1, det_size=(640, 640))
                _loaded = app
    return _loaded


def found(source: str) -> dict:
    """Every face on one photograph: normalized boxes, confidences, and unit
    identity vectors, as one JSON-serializable answer."""

    import numpy as np
    import render

    image = render.pixels(source, 1024)
    try:
        frame = np.asarray(image)[:, :, ::-1]  # BGR, as the detector expects
    finally:
        image.close()
    height, width = frame.shape[:2]
    boxes, scores, vecs = [], [], []
    for face in _model().get(frame):
        x1, y1, x2, y2 = (float(v) for v in face.bbox)
        if face.det_score < LEAST_SCORE or min(x2 - x1, y2 - y1) < LEAST_SIDE:
            continue
        vec = np.asarray(face.normed_embedding, dtype=np.float16)
        boxes.append([round(x1 / width, 4), round(y1 / height, 4),
                      round((x2 - x1) / width, 4), round((y2 - y1) / height, 4)])
        scores.append(round(float(face.det_score), 3))
        vecs.append(vec)
    packed = base64.b64encode(b"".join(v.tobytes() for v in vecs)).decode("ascii")
    return {"n": len(boxes), "boxes": boxes, "scores": scores, "vecs": packed}


def unpack(value) -> tuple[list, list, "object"]:
    """The stored answer back as (boxes, scores, matrix-of-unit-vectors)."""

    import numpy as np

    held = json.loads(value if isinstance(value, str) else bytes(value).decode("utf-8"))
    if not held["n"]:
        return [], [], np.zeros((0, 512), dtype=np.float32)
    matrix = np.frombuffer(base64.b64decode(held["vecs"]), dtype=np.float16)
    matrix = matrix.reshape(held["n"], 512).astype(np.float32)
    return held["boxes"], held["scores"], matrix


def kind(tiles):
    """Faces as a cache capability over the tile store: wants what the grid
    can show, here only where the face model is, never evicted — a face pass
    is hours of CPU to remake and a kilobyte to keep."""

    import render
    from model import cache

    def compute(source, hash, model):
        answer = found(source)
        made = json.dumps(answer).encode("utf-8")
        return cache.Made(value=made, bytes=len(made))

    return cache.Kind(
        name="faces",
        compute=compute,
        cost=0.35,
        params=("model",),
        ahead=lambda: ({"model": KEY},),
        evictable=False,
        wants=tiles.ready.sql,
        here=lambda: ready(),
        source=lambda conn, row: tiles.path(row["hash"], render.GRID),
    )
