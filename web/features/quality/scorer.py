"""Technical quality scorer — thumbnail Laplacian / exposure / motion heuristics.

Scores sm/md JPEG previews only (never full RAW decode). Face boxes come from
``face_detections`` when present; no new face detection runs here.

eyes_open
---------
v1 skips eyes-open: ``face_detections`` stores bbox + embedding only — no
landmarks / keypoints in the schema. Column stays NULL until landmarks exist.

Score blend (higher is better, 0–100)
-------------------------------------
Components stored as 0–1 floats:

- ``sharpness`` — Laplacian variance, resolution-normalized to ~0–1
- ``subject_sharpness`` — same over stored face boxes (scaled to the scoring
  thumb), else a center-weighted 40% region
- ``exposure_clip`` — fraction of pixels within 1% of black or white (badness)
- ``motion_blur`` — FFT directional-energy anisotropy (badness; honest heuristic,
  not a true optical-flow motion estimate — high values mean energy is
  concentrated along one angular direction, which often correlates with
  directional blur but also with strong linear structure in the scene)
- ``eyes_open`` — NULL in v1

::

    score = 100 * (
        0.35 * sharpness
      + 0.35 * subject_sharpness
      + 0.15 * (1 - exposure_clip)
      + 0.15 * (1 - motion_blur)
    )
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


IMAGE_QUALITY_DDL = """
CREATE TABLE IF NOT EXISTS image_quality (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    sharpness REAL,
    subject_sharpness REAL,
    exposure_clip REAL,
    motion_blur REAL,
    eyes_open REAL,
    score REAL,
    scored_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_image_quality_score
ON image_quality(score DESC);
"""

# Soft-saturation scale: Laplacian variance ≈ _LAP_REF at REF_PIXELS → ~0.63.
# tau scales with pixel count so sm/md thumbs stay comparable.
_REF_PIXELS = 256.0 * 256.0
_LAP_REF = 180.0
_CENTER_FRAC = 0.40

WEIGHT_SHARPNESS = 0.35
WEIGHT_SUBJECT = 0.35
WEIGHT_EXPOSURE = 0.15
WEIGHT_MOTION = 0.15

EYES_OPEN_V1_NOTE = (
    "eyes_open skipped in v1: face_detections has no landmarks/keypoints "
    "(bbox + embedding only); column stays null"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp01(value: float) -> float:
    if value != value:  # NaN
        return 0.0
    return float(max(0.0, min(1.0, value)))


async def ensure_image_quality(conn) -> None:
    """Create image_quality if missing (idempotent; safe on every init)."""
    await conn.executescript(IMAGE_QUALITY_DDL)


def _decode_gray(jpeg_bytes: bytes):
    import cv2
    import numpy as np  # deferred: keeps numpy off boot until a preview quality score is requested

    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("thumbnail decode failed")
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return gray, bgr.shape[1], bgr.shape[0]


def _laplacian_variance(gray: np.ndarray) -> float:
    import cv2

    if gray.size == 0:
        return 0.0
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _normalize_sharpness(raw_var: float, width: int, height: int) -> float:
    import math

    pixels = max(1.0, float(width) * float(height))
    # Resolution-normalized soft saturation — preserves ranking without a hard ceiling.
    tau = max(1.0, _LAP_REF * (pixels / _REF_PIXELS))
    return _clamp01(1.0 - math.exp(-float(raw_var) / tau))


def _center_region(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape[:2]
    fw = max(1, int(round(w * _CENTER_FRAC)))
    fh = max(1, int(round(h * _CENTER_FRAC)))
    x0 = max(0, (w - fw) // 2)
    y0 = max(0, (h - fh) // 2)
    return gray[y0 : y0 + fh, x0 : x0 + fw]


def _crop_box(gray: np.ndarray, x: float, y: float, bw: float, bh: float) -> np.ndarray | None:
    h, w = gray.shape[:2]
    x0 = int(max(0, min(w - 1, round(x))))
    y0 = int(max(0, min(h - 1, round(y))))
    x1 = int(max(x0 + 1, min(w, round(x + bw))))
    y1 = int(max(y0 + 1, min(h, round(y + bh))))
    if x1 <= x0 or y1 <= y0:
        return None
    return gray[y0:y1, x0:x1]


def _exposure_clip_fraction(gray: np.ndarray) -> float:
    import numpy as np  # deferred: keeps numpy off boot until a preview quality score is requested

    if gray.size == 0:
        return 0.0
    lo = int(round(255 * 0.01))
    hi = int(round(255 * 0.99))
    clipped = int(np.count_nonzero(gray <= lo) + np.count_nonzero(gray >= hi))
    return _clamp01(clipped / float(gray.size))


def _motion_blur_anisotropy(gray: np.ndarray) -> float:
    """FFT directional-energy anisotropy in [0, 1] (higher ≈ more directional).

    Honest caveat: this is not optical-flow motion detection. Strong edges,
    fences, and architecture also raise anisotropy. Treat as a soft cue.
    """
    import cv2
    import numpy as np  # deferred: keeps numpy off boot until a preview quality score is requested


    h, w = gray.shape[:2]
    if h < 16 or w < 16:
        return 0.0
    # Modest downscale keeps FFT cheap and stable across thumb tiers.
    target = 128
    scale = min(1.0, target / float(max(h, w)))
    if scale < 1.0:
        gray = cv2.resize(
            gray,
            (max(16, int(round(w * scale))), max(16, int(round(h * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    windowed = gray.astype(np.float32) * np.outer(
        np.hanning(gray.shape[0]), np.hanning(gray.shape[1])
    ).astype(np.float32)
    spectrum = np.fft.fftshift(np.fft.fft2(windowed))
    magnitude = np.abs(spectrum)
    cy, cx = magnitude.shape[0] // 2, magnitude.shape[1] // 2
    # Ignore DC neighborhood.
    yy, xx = np.ogrid[: magnitude.shape[0], : magnitude.shape[1]]
    rr = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    mask = rr >= 3.0
    if not np.any(mask):
        return 0.0
    angles = np.arctan2(yy - cy, xx - cx)
    # Fold to [0, pi) — direction without orientation sign.
    bins = 18
    folded = np.mod(angles, np.pi)
    hist = np.zeros(bins, dtype=np.float64)
    bin_idx = np.clip((folded / np.pi * bins).astype(np.int32), 0, bins - 1)
    np.add.at(hist, bin_idx[mask], magnitude[mask])
    total = float(hist.sum())
    if total <= 1e-9:
        return 0.0
    hist /= total
    # Anisotropy: peak bin vs uniform. Uniform → 0; single-bin → ~1.
    peak = float(hist.max())
    uniform = 1.0 / bins
    return _clamp01((peak - uniform) / max(1e-9, 1.0 - uniform))


def _blend_score(
    sharpness: float,
    subject_sharpness: float,
    exposure_clip: float,
    motion_blur: float,
) -> float:
    blended = (
        WEIGHT_SHARPNESS * sharpness
        + WEIGHT_SUBJECT * subject_sharpness
        + WEIGHT_EXPOSURE * (1.0 - exposure_clip)
        + WEIGHT_MOTION * (1.0 - motion_blur)
    )
    return round(100.0 * _clamp01(blended), 3)


def score_gray(
    gray: np.ndarray,
    *,
    face_boxes: list[tuple[float, float, float, float]] | None = None,
) -> dict[str, float | None]:
    """Score a grayscale uint8 image. face_boxes are (x, y, w, h) in image pixels."""
    h, w = gray.shape[:2]
    raw = _laplacian_variance(gray)
    sharpness = _normalize_sharpness(raw, w, h)

    subject_region = None
    if face_boxes:
        crops = []
        for x, y, bw, bh in face_boxes:
            crop = _crop_box(gray, x, y, bw, bh)
            if crop is not None and crop.size >= 16:
                crops.append(crop)
        if crops:
            # Area-weighted mean of per-face sharpness.
            weights = []
            values = []
            for crop in crops:
                ch, cw = crop.shape[:2]
                values.append(_normalize_sharpness(_laplacian_variance(crop), cw, ch))
                weights.append(float(cw * ch))
            total_w = sum(weights) or 1.0
            subject_sharpness = sum(v * wt for v, wt in zip(values, weights)) / total_w
            subject_region = "faces"
        else:
            center = _center_region(gray)
            ch, cw = center.shape[:2]
            subject_sharpness = _normalize_sharpness(
                _laplacian_variance(center), cw, ch
            )
            subject_region = "center"
    else:
        center = _center_region(gray)
        ch, cw = center.shape[:2]
        subject_sharpness = _normalize_sharpness(_laplacian_variance(center), cw, ch)
        subject_region = "center"

    exposure_clip = _exposure_clip_fraction(gray)
    motion_blur = _motion_blur_anisotropy(gray)
    score = _blend_score(sharpness, subject_sharpness, exposure_clip, motion_blur)
    return {
        "sharpness": round(sharpness, 4),
        "subject_sharpness": round(subject_sharpness, 4),
        "exposure_clip": round(exposure_clip, 4),
        "motion_blur": round(motion_blur, 4),
        "eyes_open": None,
        "score": score,
        "subject_region": subject_region,
    }


def score_jpeg_bytes(
    jpeg_bytes: bytes,
    *,
    face_boxes: list[tuple[float, float, float, float]] | None = None,
) -> dict[str, Any]:
    gray, width, height = _decode_gray(jpeg_bytes)
    result = score_gray(gray, face_boxes=face_boxes)
    result["thumb_width"] = width
    result["thumb_height"] = height
    return result


def scale_face_boxes(
    boxes: list[dict[str, float]],
    *,
    src_width: int,
    src_height: int,
    dst_width: int,
    dst_height: int,
) -> list[tuple[float, float, float, float]]:
    if src_width <= 0 or src_height <= 0:
        return []
    sx = float(dst_width) / float(src_width)
    sy = float(dst_height) / float(src_height)
    scaled: list[tuple[float, float, float, float]] = []
    for box in boxes:
        bw = float(box.get("w") or 0.0) * sx
        bh = float(box.get("h") or 0.0) * sy
        if bw < 2.0 or bh < 2.0:
            continue
        scaled.append(
            (
                float(box.get("x") or 0.0) * sx,
                float(box.get("y") or 0.0) * sy,
                bw,
                bh,
            )
        )
    return scaled


def row_payload(row: dict[str, Any] | None, *, image_id: int | None = None) -> dict[str, Any]:
    if row is None:
        payload = {
            "image_id": image_id,
            "sharpness": None,
            "subject_sharpness": None,
            "exposure_clip": None,
            "motion_blur": None,
            "eyes_open": None,
            "score": None,
            "scored_at": None,
            "scored": False,
        }
    else:
        payload = {
            "image_id": int(row["image_id"]),
            "sharpness": row["sharpness"],
            "subject_sharpness": row["subject_sharpness"],
            "exposure_clip": row["exposure_clip"],
            "motion_blur": row["motion_blur"],
            "eyes_open": row["eyes_open"],
            "score": row["score"],
            "scored_at": row["scored_at"],
            "scored": True,
        }
    payload["eyes_open_note"] = EYES_OPEN_V1_NOTE
    payload["score_weights"] = {
        "sharpness": WEIGHT_SHARPNESS,
        "subject_sharpness": WEIGHT_SUBJECT,
        "exposure_good": WEIGHT_EXPOSURE,
        "motion_good": WEIGHT_MOTION,
    }
    return payload
