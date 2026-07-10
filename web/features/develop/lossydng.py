"""Decoder for lossy (JPEG XL compressed) DNG 1.7 files that LibRaw cannot unpack.

Lightroom 14+ writes lossy DNGs as demosaiced LinearRaw (camera-space RGB) JXL
tiles inside the TIFF container, with a full-resolution SubIFD plus a pyramid of
reduced-resolution SubIFDs. tifffile + imagecodecs (libjxl) read them directly.

Color pipeline (DNG spec, ForwardMatrix path):
    v      = (x - BlackLevel) / (WhiteLevel - BlackLevel)      per channel
    v_wb   = v / AsShotNeutral                                  camera neutral -> equal RGB
    XYZd50 = ForwardMatrix @ v_wb
    sRGB   = XYZD50_TO_SRGB @ XYZd50                            Bradford-adapted
Output is linear sRGB-primary data scaled to uint16, matching rawpy's
postprocess(gamma=(1,1), output_color=sRGB, use_camera_wb=True) base format.
"""

from __future__ import annotations

import numpy as np

# XYZ (D50) -> linear sRGB (D65 primaries), Bradford chromatic adaptation.
XYZD50_TO_SRGB = np.array(
    [
        [3.1338561, -1.6168667, -0.4906146],
        [-0.9787684, 1.9161415, 0.0334540],
        [0.0719453, -0.2289914, 1.4052427],
    ],
    dtype=np.float64,
)


class LossyDngError(RuntimeError):
    pass


def _rationals(value) -> list[float]:
    seq = list(value) if isinstance(value, (tuple, list)) else [value]
    if len(seq) >= 2 and len(seq) % 2 == 0 and all(isinstance(x, (int, np.integer)) for x in seq):
        pairs = [(float(seq[i]), float(seq[i + 1])) for i in range(0, len(seq), 2)]
        if all(d != 0 for _, d in pairs) and any(d != 1 for _, d in pairs):
            return [n / d for n, d in pairs]
    return [float(x) for x in seq]


def _tag(page, ifd0, name, default=None):
    for holder in (page, ifd0):
        if holder is None:
            continue
        tag = holder.tags.get(name)
        if tag is not None:
            return tag.value
    return default


def is_lossy_dng(path: str) -> bool:
    """Cheap probe: DNG whose full-res IFD is JXL-compressed LinearRaw."""
    try:
        import tifffile

        with tifffile.TiffFile(path) as tf:
            for page in _walk_pages(tf):
                if page.subfiletype == 0:
                    return int(page.compression) == 52546
    except Exception:
        return False
    return False


def _walk_pages(tf):
    stack = list(tf.pages)
    while stack:
        page = stack.pop(0)
        yield page
        subs = getattr(page, "pages", None)
        if subs:
            stack.extend(subs)


def decode_lossy_dng(path: str, max_px: int | None = None):
    """Decode to linear sRGB uint16 (H, W, 3) plus WB metadata.

    max_px: if set, decode the smallest SubIFD level whose longest edge is
    >= max_px (falls back to full resolution). Full res when None.
    """
    import tifffile

    with tifffile.TiffFile(path) as tf:
        ifd0 = tf.pages[0]
        levels = []
        full = None
        for page in _walk_pages(tf):
            if getattr(page, "photometric", None) is None:
                continue
            if int(getattr(page, "photometric", 0)) != 34892:  # LinearRaw
                continue
            if page.subfiletype == 0:
                full = page
            levels.append(page)
        if full is None:
            raise LossyDngError(f"No LinearRaw full-resolution IFD in {path}")

        target = full
        if max_px is not None:
            candidates = [p for p in levels if max(p.shape[0], p.shape[1]) >= max_px]
            if candidates:
                target = min(candidates, key=lambda p: p.shape[0] * p.shape[1])

        data = target.asarray()
        if data.ndim != 3 or data.shape[2] != 3:
            raise LossyDngError(f"Unexpected LinearRaw shape {data.shape} in {path}")

        black = _rationals(_tag(target, ifd0, "BlackLevel", 0))
        white = _rationals(_tag(target, ifd0, "WhiteLevel", 65535))
        as_shot = _rationals(_tag(target, ifd0, "AsShotNeutral", (1.0, 1.0, 1.0)))
        forward = _rationals(_tag(target, ifd0, "ForwardMatrix1")) if _tag(target, ifd0, "ForwardMatrix1") is not None else None
        forward2 = _rationals(_tag(target, ifd0, "ForwardMatrix2")) if _tag(target, ifd0, "ForwardMatrix2") is not None else None
        baseline_ev = _rationals(_tag(target, ifd0, "BaselineExposure", 0.0))[0]

        black3 = np.array((black * 3)[:3] if len(black) < 3 else black[:3], dtype=np.float64)
        white3 = np.array((white * 3)[:3] if len(white) < 3 else white[:3], dtype=np.float64)
        asn = np.array((as_shot * 3)[:3] if len(as_shot) < 3 else as_shot[:3], dtype=np.float64)
        asn = np.where(asn <= 0, 1.0, asn)

        v = (data.astype(np.float32) - black3.astype(np.float32)) / np.maximum(
            (white3 - black3).astype(np.float32), 1.0
        )
        np.clip(v, 0.0, None, out=v)
        v /= asn.astype(np.float32)

        fm = forward2 or forward
        if fm is not None and len(fm) == 9:
            m = XYZD50_TO_SRGB @ np.array(fm, dtype=np.float64).reshape(3, 3)
        else:
            # No ForwardMatrix: assume data is already close to sRGB primaries.
            m = np.eye(3)
        flat = v.reshape(-1, 3) @ m.T.astype(np.float32)
        v = flat.reshape(v.shape)

        # Match Adobe's intended brightness for lossy DNGs.
        if baseline_ev:
            v *= float(2.0 ** baseline_ev)

        np.clip(v, 0.0, 1.0, out=v)
        out = (v * 65535.0 + 0.5).astype(np.uint16)

        # As-shot multipliers relative to green, for WB slider estimates.
        cam_mul = (asn[1] / asn).tolist()
        meta = {
            "lossy_dng": True,
            "level_shape": [int(target.shape[0]), int(target.shape[1])],
            "full_shape": [int(full.shape[0]), int(full.shape[1])],
            "cam_mul": [float(cam_mul[0]), 1.0, float(cam_mul[2]), 0.0],
            "baseline_exposure": float(baseline_ev),
        }
        return out, meta
