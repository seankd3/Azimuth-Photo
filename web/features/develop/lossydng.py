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

from .highlights_recon import reconstruct_highlights

# XYZ (D50) -> linear sRGB (D65 primaries), Bradford chromatic adaptation.
XYZD50_TO_SRGB = np.array(
    [
        [3.1338561, -1.6168667, -0.4906146],
        [-0.9787684, 1.9161415, 0.0334540],
        [0.0719453, -0.2289914, 1.4052427],
    ],
    dtype=np.float64,
)


def _map_polynomials(page) -> list[tuple[float, ...]] | None:
    """Parse DNG OpcodeList2 MapPolynomial (id 8) coefficients per plane.

    Adobe's lossy (JPEG XL) DNGs store scene-adaptively companded values; the
    per-channel polynomial restores true linear camera-space data. Skipping it
    renders images up to ~3 EV too bright, nonlinearly.
    """
    import struct

    tag = page.tags.get("OpcodeList2")
    if tag is None:
        return None
    raw = tag.value if isinstance(tag.value, bytes) else bytes(tag.value)
    if len(raw) < 4:
        return None
    count = struct.unpack(">I", raw[:4])[0]
    offset = 4
    planes: dict[int, tuple[float, ...]] = {}
    for _ in range(count):
        if offset + 16 > len(raw):
            return None
        opcode_id, _ver, _flags, size = struct.unpack(">IIII", raw[offset : offset + 16])
        body = raw[offset + 16 : offset + 16 + size]
        offset += 16 + size
        if opcode_id != 8 or len(body) < 44:
            continue
        _top, _left, _bottom, _right, plane, _nplanes, _rp, _cp, degree = struct.unpack(">IIIIIIIII", body[:36])
        ncoef = degree + 1
        if len(body) < 36 + 8 * ncoef:
            continue
        planes[int(plane)] = struct.unpack(f">{ncoef}d", body[36 : 36 + 8 * ncoef])
    if not planes:
        return None
    return [planes.get(i, (0.0, 1.0)) for i in range(3)]


def _poly_eval(coefs, x):
    result = 0.0 if not hasattr(x, "shape") else None
    acc = None
    for c in reversed(coefs):
        if acc is None:
            acc = np.full_like(x, np.float32(c)) if hasattr(x, "shape") else float(c)
        else:
            acc = acc * x + (np.float32(c) if hasattr(x, "shape") else float(c))
    return acc


class LossyDngError(RuntimeError):
    pass


def _rationals(value) -> list[float]:
    seq = list(value) if isinstance(value, (tuple, list)) else [value]
    if len(seq) >= 2 and len(seq) % 2 == 0 and all(isinstance(x, (int, np.integer)) for x in seq):
        pairs = [(float(seq[i]), float(seq[i + 1])) for i in range(0, len(seq), 2)]
        if all(d != 0 for _, d in pairs) and any(d != 1 for _, d in pairs):
            return [n / d for n, d in pairs]
    return [float(x) for x in seq]


def _tag(name, *pages, default=None):
    """Read a DNG tag from the first page that carries it.

    Reduced LinearRaw pyramid levels commonly omit the full-resolution level
    tags.  Keep the target first, then fall back through the full LinearRaw IFD
    and IFD0 so preview-sized decodes retain the source black/white semantics.
    """
    for holder in pages:
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


def is_linear_dng(path: str) -> bool:
    """Return whether a DNG contains a full-resolution three-plane LinearRaw IFD."""
    try:
        import tifffile

        with tifffile.TiffFile(path) as tf:
            return any(
                page.subfiletype == 0
                and int(getattr(page, "photometric", 0)) == 34892
                and len(page.shape) == 3
                and page.shape[-1] == 3
                for page in _walk_pages(tf)
            )
    except Exception:
        return False


def _walk_pages(tf):
    stack = list(tf.pages)
    while stack:
        page = stack.pop(0)
        yield page
        subs = getattr(page, "pages", None)
        if subs:
            stack.extend(subs)


def _apply_exif_orientation(arr, orientation: int):
    """Apply EXIF Orientation (1-8) to an (H, W, C) array."""
    import numpy as np

    if orientation == 2:
        return np.ascontiguousarray(arr[:, ::-1])
    if orientation == 3:
        return np.ascontiguousarray(arr[::-1, ::-1])
    if orientation == 4:
        return np.ascontiguousarray(arr[::-1])
    if orientation == 5:
        return np.ascontiguousarray(arr.transpose(1, 0, 2))
    if orientation == 6:
        return np.ascontiguousarray(np.rot90(arr, k=-1))
    if orientation == 7:
        return np.ascontiguousarray(arr.transpose(1, 0, 2)[::-1, ::-1])
    if orientation == 8:
        return np.ascontiguousarray(np.rot90(arr, k=1))
    return arr


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

        tag_pages = (target, full, ifd0)
        black = _rationals(_tag("BlackLevel", *tag_pages, default=0))
        white = _rationals(_tag("WhiteLevel", *tag_pages, default=65535))
        as_shot = _rationals(_tag("AsShotNeutral", *tag_pages, default=(1.0, 1.0, 1.0)))
        forward_value = _tag("ForwardMatrix1", *tag_pages)
        forward2_value = _tag("ForwardMatrix2", *tag_pages)
        color_matrix1_value = _tag("ColorMatrix1", *tag_pages)
        color_matrix2_value = _tag("ColorMatrix2", *tag_pages)
        forward = _rationals(forward_value) if forward_value is not None else None
        forward2 = _rationals(forward2_value) if forward2_value is not None else None
        color_matrix1 = _rationals(color_matrix1_value) if color_matrix1_value is not None else None
        color_matrix2 = _rationals(color_matrix2_value) if color_matrix2_value is not None else None
        baseline_ev = _rationals(_tag("BaselineExposure", *tag_pages, default=0.0))[0]
        camera_model = _tag("UniqueCameraModel", *tag_pages) or _tag("Model", *tag_pages)
        camera_make = _tag("Make", *tag_pages)
        lens_model = _tag("LensModel", *tag_pages) or _tag("Lens", *tag_pages)
        focal_length_value = _tag("FocalLength", *tag_pages)
        aperture_value = _tag("FNumber", *tag_pages)
        focal_length = _rationals(focal_length_value)[0] if focal_length_value is not None else None
        aperture = _rationals(aperture_value)[0] if aperture_value is not None else None

        black3 = np.array((black * 3)[:3] if len(black) < 3 else black[:3], dtype=np.float64)
        white3 = np.array((white * 3)[:3] if len(white) < 3 else white[:3], dtype=np.float64)
        asn = np.array((as_shot * 3)[:3] if len(as_shot) < 3 else as_shot[:3], dtype=np.float64)
        asn = np.where(asn <= 0, 1.0, asn)

        v = (data.astype(np.float32) - black3.astype(np.float32)) / np.maximum(
            (white3 - black3).astype(np.float32), 1.0
        )
        np.clip(v, 0.0, None, out=v)

        polynomials = _map_polynomials(full) or _map_polynomials(target)
        linear_white = np.ones(3, dtype=np.float32)
        if polynomials:
            for channel in range(3):
                v[..., channel] = _poly_eval(polynomials[channel], v[..., channel])
                linear_white[channel] = np.float32(_poly_eval(polynomials[channel], 1.0))
            np.clip(v, 0.0, None, out=v)

        v /= asn.astype(np.float32)
        clips = linear_white / asn.astype(np.float32)
        v = reconstruct_highlights(v, clips)

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

        # LinearRaw pixels are stored unrotated; honor the container
        # Orientation tag so the base matches the camera's framing.
        orientation = int(_tag("Orientation", *tag_pages, default=1) or 1)
        out = _apply_exif_orientation(out, orientation)

        # As-shot multipliers relative to green, for WB slider estimates.
        cam_mul = (asn[1] / asn).tolist()
        meta = {
            "lossy_dng": True,
            "orientation": orientation,
            "level_shape": [int(target.shape[0]), int(target.shape[1])],
            "full_shape": [int(full.shape[0]), int(full.shape[1])],
            "cam_mul": [float(cam_mul[0]), 1.0, float(cam_mul[2]), 0.0],
            "as_shot_neutral": [float(asn[0]), float(asn[1]), float(asn[2])],
            "color_matrix1": [float(x) for x in color_matrix1] if color_matrix1 and len(color_matrix1) == 9 else None,
            "color_matrix2": [float(x) for x in color_matrix2] if color_matrix2 and len(color_matrix2) == 9 else None,
            "forward_matrix": [float(x) for x in fm] if fm is not None and len(fm) == 9 else None,
            "baseline_exposure": float(baseline_ev),
            "camera_model": str(camera_model).strip() if camera_model else "",
            "camera_make": str(camera_make).strip() if camera_make else "",
            "lens_model": str(lens_model).strip() if lens_model else "",
            "focal_length": float(focal_length) if focal_length is not None else None,
            "aperture": float(aperture) if aperture is not None else None,
        }
        return out, meta
