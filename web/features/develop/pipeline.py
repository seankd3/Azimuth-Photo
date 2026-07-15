"""Pure NumPy implementation of the develop v1.5 color pipeline.

The input is linear sRGB float data and the result is gamma-encoded sRGB in
the inclusive 0..1 range. Geometry remains outside this module; crop values are
read only so the post-crop vignette is positioned in the same space as export.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

from . import dng_pipeline, ops_constants as C
from .film import apply_film
from .lens import distortion_auto_crop_scale
from .looks import compose_curve_luts, effective_settings, look_amount, look_curve


def _number(settings: Mapping[str, object], key: str, default: float = 0.0) -> float:
    value = settings.get(key, default)
    try:
        return float(value)  # Lightroom XMP values arrive as strings.
    except (TypeError, ValueError):
        return default


def _slider(settings: Mapping[str, object], key: str) -> float:
    return np.clip(_number(settings, key), -100.0, 100.0) / 100.0


def _bool(settings: Mapping[str, object], key: str) -> bool:
    value = settings.get(key)
    return value is True or value == 1 or str(value).strip().lower() == "true"


def _smoothstep(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    t = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _gaussian_ev(ev: np.ndarray, center: float, sigma: float = C.TONE_EV_SIGMA) -> np.ndarray:
    z = (ev - center) / max(sigma, C.TONE_EPSILON)
    return np.exp(-0.5 * z * z).astype(np.float32)


def linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    """Encode clipped linear RGB with the IEC sRGB transfer curve."""
    linear = np.clip(linear, 0.0, 1.0).astype(np.float32, copy=False)
    return np.where(
        linear <= C.SRGB_LINEAR_THRESHOLD,
        linear * C.SRGB_ENCODE_SCALE,
        C.SRGB_ENCODE_A * np.power(linear, C.SRGB_ENCODE_GAMMA) - C.SRGB_ENCODE_B,
    ).astype(np.float32)


def srgb_to_linear(srgb: np.ndarray) -> np.ndarray:
    """Decode gamma sRGB with the IEC transfer curve."""
    srgb = np.clip(srgb, 0.0, 1.0).astype(np.float32, copy=False)
    return np.where(
        srgb <= C.SRGB_DECODE_THRESHOLD,
        srgb * C.SRGB_DECODE_SCALE,
        np.power((srgb + C.SRGB_DECODE_A) / C.SRGB_ENCODE_A, C.SRGB_DECODE_GAMMA),
    ).astype(np.float32)


def luma(rgb: np.ndarray) -> np.ndarray:
    return (
        rgb[..., 0] * C.LUMA_RED
        + rgb[..., 1] * C.LUMA_GREEN
        + rgb[..., 2] * C.LUMA_BLUE
    ).astype(np.float32)


def _as_shot_temperature(settings: Mapping[str, object], explicit: float | None) -> float:
    if explicit is not None:
        return float(explicit)
    for key in ("AsShotTemperature", "asshot_temperature", "_asshot_temperature"):
        temperature = _number(settings, key)
        if temperature > 0.0:
            return temperature
    return 5500.0


def cct_to_xy(cct: float) -> tuple[float, float]:
    """Planckian locus chromaticity (Kim et al. cubic spline approximation)."""
    t = min(max(float(cct), 1667.0), 25000.0)
    inv = 1000.0 / t
    if t < 4000.0:
        x = ((-0.2661239 * inv - 0.2343589) * inv + 0.8776956) * inv + 0.179910
    else:
        x = ((-3.0258469 * inv + 2.1070379) * inv + 0.2226347) * inv + 0.240390
    if t < 2222.0:
        y = ((-1.1063814 * x - 1.34811020) * x + 2.18555832) * x - 0.20219683
    elif t < 4000.0:
        y = ((-0.9549476 * x - 1.37418593) * x + 2.09137015) * x - 0.16748867
    else:
        y = ((3.0817580 * x - 5.87338670) * x + 3.75112997) * x - 0.37001483
    return x, y


def white_linear_srgb(cct: float, tint: float) -> tuple[float, float, float]:
    """Linear-sRGB color of a Planckian white at cct with LR-style tint (Duv via v')."""
    x, y = cct_to_xy(cct)
    d = -2.0 * x + 12.0 * y + 3.0
    u = 4.0 * x / d
    v = 9.0 * y / d + float(tint) / C.TINT_UV_SCALE
    d2 = 6.0 * u - 16.0 * v + 12.0
    x2 = 9.0 * u / d2
    y2 = max(4.0 * v / d2, 1e-6)
    big_x = x2 / y2
    big_z = (1.0 - x2 - y2) / y2
    r = 3.2404542 * big_x - 1.5371385 - 0.4985314 * big_z
    g = -0.9692660 * big_x + 1.8760108 + 0.0415560 * big_z
    b = 0.0556434 * big_x - 0.2040259 + 1.0572252 * big_z
    return max(r, 1e-4), max(g, 1e-4), max(b, 1e-4)


def _xy_from_cct_tint(cct: float, tint: float) -> tuple[float, float]:
    x, y = cct_to_xy(cct)
    d = -2.0 * x + 12.0 * y + 3.0
    u = 4.0 * x / d
    v = 9.0 * y / d + float(tint) / C.TINT_UV_SCALE
    d2 = 6.0 * u - 16.0 * v + 12.0
    return 9.0 * u / d2, max(4.0 * v / d2, 1e-6)


def wb_matrix(
    color: Mapping[str, object],
    asshot_temperature: float,
    asshot_tint: float,
    temperature: float,
    tint: float,
) -> np.ndarray | None:
    """DNG-correct WB: diagonal camera-space gains ASN/n(T_user), expressed in the
    base's sRGB space as M·D·M⁻¹ with M = XYZD50→sRGB · ForwardMatrix.

    Twin of wbMatrix() in static/js/desktop/develop/gl.js — keep identical.
    Returns None when the needed matrices are absent (caller falls back to
    Planckian per-channel gains, which run hotter).
    """
    asn = color.get("as_shot_neutral") if isinstance(color, Mapping) else None
    fm = color.get("forward_matrix") if isinstance(color, Mapping) else None
    cm2 = color.get("color_matrix2") if isinstance(color, Mapping) else None
    cm1 = color.get("color_matrix1") if isinstance(color, Mapping) else None
    if not asn or fm is None or (cm2 is None and cm1 is None):
        return None
    asn = np.asarray(asn, dtype=np.float64)[:3]
    if asn.shape != (3,) or np.any(asn <= 0):
        return None
    m_cm2 = np.asarray(cm2, dtype=np.float64).reshape(3, 3) if cm2 is not None else None
    m_cm1 = np.asarray(cm1, dtype=np.float64).reshape(3, 3) if cm1 is not None else None
    mired = 1_000_000.0 / min(max(float(temperature), 2000.0), 50000.0)
    if m_cm1 is None:
        cm = m_cm2
    elif m_cm2 is None:
        cm = m_cm1
    else:
        weight_a = (mired - 1_000_000.0 / 6504.0) / (1_000_000.0 / 2856.0 - 1_000_000.0 / 6504.0)
        weight_a = min(max(weight_a, 0.0), 1.0)
        cm = m_cm2 + weight_a * (m_cm1 - m_cm2)
    x, y = _xy_from_cct_tint(float(temperature), float(tint))
    xyz = np.array([x / y, 1.0, (1.0 - x - y) / y], dtype=np.float64)
    n_user = cm @ xyz
    if np.any(~np.isfinite(n_user)) or n_user[1] <= 1e-9 or np.any(n_user <= 0):
        return None
    n_user /= n_user[1]
    gains = (asn / asn[1]) / n_user
    m = XYZD50_TO_SRGB_PIPE @ np.asarray(fm, dtype=np.float64).reshape(3, 3)
    try:
        w = m @ np.diag(gains) @ np.linalg.inv(m)
    except np.linalg.LinAlgError:
        return None
    return w.astype(np.float32)


XYZD50_TO_SRGB_PIPE = np.array(
    [
        [3.1338561, -1.6168667, -0.4906146],
        [-0.9787684, 1.9161415, 0.0334540],
        [0.0719453, -0.2289914, 1.4052427],
    ],
    dtype=np.float64,
)


def wb_gains(
    asshot_temperature: float, asshot_tint: float, temperature: float, tint: float
) -> tuple[float, float, float]:
    """Per-channel linear-sRGB gains taking the as-shot white to the user white.

    Twin of wbGains() in static/js/desktop/develop/gl.js — keep identical.
    """
    wa = white_linear_srgb(asshot_temperature, asshot_tint)
    wu = white_linear_srgb(temperature, tint)
    gains = tuple((wa[i] / wa[1]) / (wu[i] / wu[1]) for i in range(3))
    return tuple(min(max(g, 0.125), 8.0) for g in gains)


def _apply_white_balance(
    rgb: np.ndarray,
    settings: Mapping[str, object],
    asshot_temperature: float | None,
    asshot_tint: float | None = None,
    color_profile: Mapping[str, object] | None = None,
) -> np.ndarray:
    temperature = _number(settings, "Temperature")
    white_balance = str(settings.get("WhiteBalance", "")).strip().lower()
    if temperature <= 0.0 or white_balance == "as shot":
        return rgb
    asshot = max(_as_shot_temperature(settings, asshot_temperature), 1.0)
    tint = _number(settings, "Tint")
    if color_profile:
        matrix = wb_matrix(color_profile, asshot, float(asshot_tint or 0.0), max(temperature, 1.0), tint)
        if matrix is not None:
            shape = rgb.shape
            return np.ascontiguousarray((rgb.reshape(-1, 3) @ matrix.T).reshape(shape), dtype=np.float32)
    gains = wb_gains(asshot, float(asshot_tint or 0.0), max(temperature, 1.0), tint)
    result = rgb.copy()
    result[..., 0] *= np.float32(gains[0])
    result[..., 1] *= np.float32(gains[1])
    result[..., 2] *= np.float32(gains[2])
    return result


def _soft_clamp(value: np.ndarray) -> np.ndarray:
    above = value > 1.0
    return np.where(
        value < 0.0,
        0.0,
        np.where(above, 1.0 + (value - 1.0) / (1.0 + C.SOFT_CLAMP_FACTOR * (value - 1.0)), value),
    ).astype(np.float32)


def _region_tone_map(rgb: np.ndarray, settings: Mapping[str, object]) -> np.ndarray:
    y = luma(rgb)
    ev = np.log2(np.maximum(y, C.TONE_EPSILON))
    highlights = _slider(settings, "Highlights2012")
    shadows = _slider(settings, "Shadows2012")
    whites = _slider(settings, "Whites2012")
    blacks = _slider(settings, "Blacks2012")
    wh = _gaussian_ev(ev, C.TONE_EV_HIGHLIGHTS_CENTER)
    ws = _gaussian_ev(ev, C.TONE_EV_SHADOWS_CENTER)
    ww = _gaussian_ev(ev, C.TONE_EV_WHITES_CENTER)
    wb = _gaussian_ev(ev, C.TONE_EV_BLACKS_CENTER)
    hl_scale = np.where(highlights < 0.0, 1.0, C.TONE_HIGHLIGHTS_POS_SCALE)
    delta_ev = (
        C.TONE_HIGHLIGHTS_FACTOR * highlights * hl_scale * wh
        + C.TONE_SHADOWS_FACTOR * shadows * ws
        + C.TONE_WHITES_FACTOR * whites * ww
        + C.TONE_BLACKS_FACTOR * blacks * wb
    )
    rgb = (rgb * np.exp2(delta_ev)[..., None]).astype(np.float32)
    y2 = luma(rgb)
    t = np.power(np.clip(y2, 0.0, 1.0), 1.0 / C.TONE_GAMMA)
    t3 = 0.5 + (t - 0.5) * (1.0 + C.CONTRAST_FACTOR * _slider(settings, "Contrast2012"))
    t3 = _soft_clamp(t3)
    gain = np.power(t3, C.TONE_GAMMA) / np.maximum(y2, C.TONE_EPSILON)
    return (rgb * gain[..., None]).astype(np.float32)


def _curve_points(raw_points: object) -> list[tuple[float, float]]:
    if not isinstance(raw_points, Sequence) or isinstance(raw_points, (str, bytes)):
        return []
    points: list[tuple[float, float]] = []
    for item in raw_points:
        try:
            if isinstance(item, str):
                x_text, y_text = item.split(",", 1)
                x, y = float(x_text), float(y_text)
            else:
                x, y = item  # type: ignore[misc]
                x, y = float(x), float(y)
            points.append((np.clip(x, 0.0, 255.0) / 255.0, np.clip(y, 0.0, 255.0) / 255.0))
        except (TypeError, ValueError):
            continue
    deduped = {x: y for x, y in points}
    return sorted(deduped.items())


def build_monotone_cubic_lut(raw_points: object) -> np.ndarray:
    """Build the 256-entry Fritsch--Carlson curve LUT used by both renderers."""
    points = _curve_points(raw_points)
    samples = np.linspace(0.0, 1.0, C.CURVE_LUT_SIZE, dtype=np.float32)
    if len(points) < 2:
        return samples
    x = np.asarray([point[0] for point in points], dtype=np.float32)
    y = np.asarray([point[1] for point in points], dtype=np.float32)
    dx = np.diff(x)
    slopes = np.diff(y) / dx
    tangents = np.empty_like(x)
    tangents[0], tangents[-1] = slopes[0], slopes[-1]
    for index in range(1, len(x) - 1):
        left, right = slopes[index - 1], slopes[index]
        if left * right <= 0.0:
            tangents[index] = 0.0
        else:
            h_left, h_right = dx[index - 1], dx[index]
            tangents[index] = (h_left + h_right) / (h_right / left + h_left / right)
    for index, slope in enumerate(slopes):
        if slope == 0.0:
            tangents[index] = tangents[index + 1] = 0.0
            continue
        a, b = tangents[index] / slope, tangents[index + 1] / slope
        length = math.hypot(float(a), float(b))
        if length > C.CURVE_MONOTONE_LIMIT:
            scale = C.CURVE_MONOTONE_LIMIT / length
            tangents[index] *= scale
            tangents[index + 1] *= scale
    segment = np.clip(np.searchsorted(x, samples, side="right") - 1, 0, len(x) - 2)
    h = x[segment + 1] - x[segment]
    u = np.clip((samples - x[segment]) / h, 0.0, 1.0)
    h00 = 2.0 * u**3 - 3.0 * u**2 + 1.0
    h10 = u**3 - 2.0 * u**2 + u
    h01 = -2.0 * u**3 + 3.0 * u**2
    h11 = u**3 - u**2
    lut = h00 * y[segment] + h10 * h * tangents[segment] + h01 * y[segment + 1] + h11 * h * tangents[segment + 1]
    return np.clip(lut, 0.0, 1.0).astype(np.float32)


def _apply_lut(channel: np.ndarray, lut: np.ndarray) -> np.ndarray:
    # np.interp is the same linear sampling between the 256 fixed LUT cells,
    # without materializing several full-resolution integer index buffers.
    positions = np.linspace(0.0, 1.0, C.CURVE_LUT_SIZE, dtype=np.float32)
    return np.interp(np.clip(channel, 0.0, 1.0), positions, lut).astype(np.float32)


def _matmul_rows(pixels: np.ndarray, matrix: Sequence[Sequence[float]]) -> np.ndarray:
    m = np.asarray(matrix, dtype=np.float32)
    return pixels @ m.T


def soft_proof_transform(srgb: np.ndarray, profile: str = "srgb") -> tuple[np.ndarray, np.ndarray]:
    """Proof sRGB through a target RGB gamut and return its warning mask.

    This is a matrix/clip preview rather than a full ICC CMM. ``paper`` shares
    sRGB's gamut then maps the displayed linear endpoints to paper white/black.
    The WebGL renderer executes the same matrix sequence.
    """
    profile = str(profile or "srgb").lower()
    targets = {
        "adobe-rgb": (C.SOFT_PROOF_ADOBE_RGB_TO_XYZ, C.SOFT_PROOF_XYZ_TO_ADOBE_RGB),
        "display-p3": (C.SOFT_PROOF_P3_TO_XYZ, C.SOFT_PROOF_XYZ_TO_P3),
    }
    to_xyz, from_xyz = targets.get(profile, (C.SOFT_PROOF_SRGB_TO_XYZ, C.SOFT_PROOF_XYZ_TO_SRGB))
    xyz = _matmul_rows(srgb_to_linear(np.clip(srgb, 0.0, 1.0)), C.SOFT_PROOF_SRGB_TO_XYZ)
    proof = _matmul_rows(xyz, from_xyz)
    warning = np.any((proof < 0.0) | (proof > 1.0), axis=-1)
    displayed = _matmul_rows(_matmul_rows(np.clip(proof, 0.0, 1.0), to_xyz), C.SOFT_PROOF_XYZ_TO_SRGB)
    if profile == "paper":
        displayed = C.SOFT_PROOF_PAPER_BLACK + displayed * (C.SOFT_PROOF_PAPER_WHITE - C.SOFT_PROOF_PAPER_BLACK)
    return linear_to_srgb(np.clip(displayed, 0.0, 1.0)), warning


def linear_to_oklab(linear: np.ndarray) -> np.ndarray:
    lms = _matmul_rows(linear, C.OKLAB_M1)
    lms = np.sign(lms) * np.power(np.abs(lms), 1.0 / 3.0)
    return _matmul_rows(lms, C.OKLAB_M2).astype(np.float32)


def oklab_to_linear(lab: np.ndarray) -> np.ndarray:
    lms = _matmul_rows(lab, C.OKLAB_M2_INV)
    lms = lms * lms * lms
    return _matmul_rows(lms, C.OKLAB_M1_INV).astype(np.float32)


def _gamut_clip_desaturate(linear: np.ndarray) -> np.ndarray:
    """Soft-clip out-of-gamut by desaturating toward OKLab L (not channel clamp)."""
    lab = linear_to_oklab(linear)
    result = linear.astype(np.float32, copy=True)
    outside = np.any((result < 0.0) | (result > 1.0), axis=-1)
    if not np.any(outside):
        return np.clip(result, 0.0, 1.0)

    # Gamut repair is sparse for normal photographs. Preserve the exact math
    # while running the eight-pass search only for pixels that need it.
    selected = lab[outside]
    lightness = selected[:, 0:1]
    chroma_ab = selected[:, 1:3]
    achromatic = oklab_to_linear(
        np.concatenate((lightness, np.zeros_like(chroma_ab)), axis=-1)
    )
    lo = np.zeros(len(selected), dtype=np.float32)
    hi = np.ones(len(selected), dtype=np.float32)
    for _ in range(8):
        mid = 0.5 * (lo + hi)
        candidate_lab = np.concatenate((lightness, chroma_ab * mid[:, None]), axis=-1)
        candidate = oklab_to_linear(candidate_lab)
        ok = np.all((candidate >= 0.0) & (candidate <= 1.0), axis=-1)
        hi = np.where(ok, hi, mid)
        lo = np.where(ok, mid, lo)

    clipped_lab = np.concatenate((lightness, chroma_ab * lo[:, None]), axis=-1)
    clipped = oklab_to_linear(clipped_lab)
    still = np.any((clipped < -1e-5) | (clipped > 1.0 + 1e-5), axis=-1)
    if np.any(still):
        clipped = np.where(still[:, None], achromatic, clipped)
    result[outside] = clipped
    return np.clip(result, 0.0, 1.0).astype(np.float32)


def scale_oklab_chroma(
    srgb: np.ndarray,
    *,
    multiply: float = 1.0,
    saturation: float = 0.0,
    vibrance: float = 0.0,
    dehaze: float = 0.0,
) -> np.ndarray:
    """Scale OKLab chroma at fixed L/hue; soft-clip gamut by desaturation."""
    if (
        abs(multiply - 1.0) < 1e-8
        and abs(saturation) < 1e-8
        and abs(vibrance) < 1e-8
        and abs(dehaze) < 1e-8
    ):
        return srgb
    linear = srgb_to_linear(srgb)
    lab = linear_to_oklab(linear)
    a = lab[..., 1]
    b = lab[..., 2]
    chroma = np.sqrt(a * a + b * b)
    satness = np.clip(chroma / max(C.OKLAB_C_NORM, C.TONE_EPSILON), 0.0, 1.0)
    scale = multiply * (1.0 + saturation + C.DEHAZE_SATURATION_FACTOR * dehaze)
    chroma_new = chroma * scale
    chroma_new = chroma_new + vibrance * (1.0 - satness) * satness * C.VIBRANCE_FACTOR * C.OKLAB_C_NORM
    chroma_new = np.maximum(chroma_new, 0.0)
    safe = np.maximum(chroma, C.TONE_EPSILON)
    lab_new = lab.copy()
    lab_new[..., 1] = a * (chroma_new / safe)
    lab_new[..., 2] = b * (chroma_new / safe)
    zero = chroma <= C.TONE_EPSILON
    lab_new[..., 1] = np.where(zero, 0.0, lab_new[..., 1])
    lab_new[..., 2] = np.where(zero, 0.0, lab_new[..., 2])
    linear_out = _gamut_clip_desaturate(oklab_to_linear(lab_new))
    return linear_to_srgb(linear_out)


def _camera_profile(color_profile: Mapping[str, object] | None) -> Mapping[str, object] | None:
    profile = color_profile.get("camera_profile") if isinstance(color_profile, Mapping) else None
    return profile if isinstance(profile, Mapping) else None


def _profile_curve_points(profile: Mapping[str, object] | None) -> object:
    if not profile:
        return C.BASE_PROFILE_POINTS
    nodes = profile.get("tone_nodes")
    values = profile.get("tone_values")
    if (
        not isinstance(nodes, Sequence)
        or not isinstance(values, Sequence)
        or len(nodes) != C.CAMERA_PROFILE_TONE_NODES
        or len(values) != C.CAMERA_PROFILE_TONE_NODES
    ):
        return C.BASE_PROFILE_POINTS
    try:
        return [(float(node) * 255.0, float(values[index]) * 255.0) for index, node in enumerate(nodes)]
    except (TypeError, ValueError):
        return C.BASE_PROFILE_POINTS


def _apply_tone_curves(
    c: np.ndarray,
    settings: Mapping[str, object],
    camera_profile: Mapping[str, object] | None = None,
    *,
    base_kind: str = "raw",
) -> np.ndarray:
    result = c.copy()
    display_base = base_kind == "display"
    base_lut = build_monotone_cubic_lut(None if display_base else _profile_curve_points(camera_profile))
    imported_look_curve = look_curve(settings)
    if imported_look_curve is not None:
        base_lut = compose_curve_luts(
            base_lut,
            build_monotone_cubic_lut(imported_look_curve),
            look_amount(settings),
        )
    for index in range(3):
        result[..., index] = _apply_lut(c[..., index], base_lut)
    if not display_base and camera_profile is None:
        result = scale_oklab_chroma(result, multiply=C.BASE_PROFILE_SAT)
    main_lut = build_monotone_cubic_lut(settings.get("ToneCurvePV2012"))
    # A main curve is RGB-linked; component curves are applied after it.
    curved = result.copy()
    for index in range(3):
        curved[..., index] = _apply_lut(result[..., index], main_lut)
    for index, suffix in enumerate(("Red", "Green", "Blue")):
        curved[..., index] = _apply_lut(
            curved[..., index], build_monotone_cubic_lut(settings.get(f"ToneCurvePV2012{suffix}"))
        )
    return curved


def apply_camera_profile_ab(srgb: np.ndarray, profile: Mapping[str, object] | None) -> np.ndarray:
    """Apply the fitted circular hue x chroma OKLab residual table."""
    if not profile:
        return srgb
    try:
        table = np.asarray(profile["oklab_ab_delta"], dtype=np.float32)
        edges = np.asarray(profile["chroma_edges"], dtype=np.float32)
    except (KeyError, TypeError, ValueError):
        return srgb
    if table.shape != (C.CAMERA_PROFILE_HUE_BINS, C.CAMERA_PROFILE_CHROMA_BINS, 2) or edges.shape != (4,):
        return srgb
    linear = srgb_to_linear(srgb)
    lab = linear_to_oklab(linear)
    a, b = lab[..., 1], lab[..., 2]
    hue_position = (
        (np.arctan2(b, a) + C.CAMERA_PROFILE_PI)
        / C.CAMERA_PROFILE_TWO_PI
        * C.CAMERA_PROFILE_HUE_BINS
        - C.CAMERA_PROFILE_BIN_CENTER
    )
    hue_floor = np.floor(hue_position)
    hue_mix = (hue_position - hue_floor).astype(np.float32)
    hue0 = np.mod(hue_floor.astype(np.intp), C.CAMERA_PROFILE_HUE_BINS)
    hue1 = (hue0 + 1) % C.CAMERA_PROFILE_HUE_BINS

    chroma = np.hypot(a, b)
    centers = np.asarray(
        [
            edges[0] * C.CAMERA_PROFILE_BIN_CENTER,
            (edges[0] + edges[1]) * C.CAMERA_PROFILE_BIN_CENTER,
            (edges[1] + edges[2]) * C.CAMERA_PROFILE_BIN_CENTER,
        ],
        dtype=np.float32,
    )
    chroma0 = np.where(chroma < centers[1], 0, 1).astype(np.intp)
    chroma1 = np.minimum(chroma0 + 1, C.CAMERA_PROFILE_CHROMA_BINS - 1)
    left, right = centers[chroma0], centers[chroma1]
    chroma_mix = np.divide(
        chroma - left,
        right - left,
        out=np.zeros_like(chroma, dtype=np.float32),
        where=right > left,
    )
    chroma_mix = np.clip(chroma_mix, 0.0, 1.0)
    delta0 = table[hue0, chroma0] * (1.0 - hue_mix[..., None]) + table[hue1, chroma0] * hue_mix[..., None]
    delta1 = table[hue0, chroma1] * (1.0 - hue_mix[..., None]) + table[hue1, chroma1] * hue_mix[..., None]
    delta = delta0 * (1.0 - chroma_mix[..., None]) + delta1 * chroma_mix[..., None]
    adjusted = lab.copy()
    adjusted[..., 1:3] += delta
    return linear_to_srgb(_gamut_clip_desaturate(oklab_to_linear(adjusted)))


def lens_radial_scale(
    radius: np.ndarray | float,
    distortion: Mapping[str, object] | None,
    profile_scale: float = 1.0,
) -> np.ndarray:
    """Map corrected radius to distorted source radius for Lensfun models."""
    r = np.asarray(radius, dtype=np.float32)
    if not distortion:
        return np.ones_like(r)
    try:
        terms = [float(value) for value in distortion.get("terms", ())]
    except (TypeError, ValueError):
        return np.ones_like(r)
    model = str(distortion.get("model") or "").lower()
    r2 = r * r
    if model == "poly3" and terms:
        scale = 1.0 - terms[0] + terms[0] * r2
    elif model == "poly5" and len(terms) >= 2:
        scale = 1.0 + terms[0] * r2 + terms[1] * r2 * r2
    elif model == "ptlens" and len(terms) >= 3:
        a, b, c = terms[:3]
        scale = a * r * r2 + b * r2 + c * r + 1.0 - a - b - c
    else:
        return np.ones_like(r)
    amount = np.clip(profile_scale, C.LENS_PROFILE_SCALE_MIN / 100.0, C.LENS_PROFILE_SCALE_MAX / 100.0)
    return (1.0 + (scale - 1.0) * amount).astype(np.float32)


def lens_vignetting_gain(
    radius: np.ndarray | float,
    vignetting: Mapping[str, object] | None,
    profile_scale: float = 1.0,
) -> np.ndarray:
    """Return Lensfun's PA linear-domain *de-vignetting* gain.

    ``radius`` is normalized so 1 is the image corner, matching Lensfun's PA
    calibration space. Lensfun's normal (non-reverse) modifier divides by the
    polynomial; multiplying by it simulates the lens and darkens the frame.
    """
    r = np.asarray(radius, dtype=np.float32)
    if not vignetting or str(vignetting.get("model") or "").lower() != "pa":
        return np.ones_like(r)
    try:
        k1, k2, k3 = [float(value) for value in vignetting.get("terms", ())[:3]]
    except (TypeError, ValueError):
        return np.ones_like(r)
    r2 = r * r
    polynomial = 1.0 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
    gain = 1.0 / np.maximum(polynomial, C.TONE_EPSILON)
    amount = np.clip(profile_scale, C.LENS_PROFILE_SCALE_MIN / 100.0, C.LENS_PROFILE_SCALE_MAX / 100.0)
    gain = 1.0 + (gain - 1.0) * amount
    return np.clip(gain, C.LENS_VIGNETTE_GAIN_MIN, C.LENS_VIGNETTE_GAIN_MAX).astype(np.float32)


def calibration_matrix(settings: Mapping[str, object]) -> np.ndarray:
    """Return the §28 linear-RGB primary calibration matrix.

    Matrix columns are source primaries. For primary ``i``, saturation expands
    its column about luma and hue mixes that one column toward the adjacent
    RGB primary (positive: R→G→B→R; negative reverses). Consequently a red
    primary adjustment changes red input without moving a blue-only input.
    """
    basis = np.eye(3, dtype=np.float32)
    matrix = basis.copy()
    luma_vector = np.array((C.LUMA_RED, C.LUMA_GREEN, C.LUMA_BLUE), dtype=np.float32)
    for index, name in enumerate(("Red", "Green", "Blue")):
        saturation = _slider(settings, f"Calibration{name}PrimarySaturation") * C.CALIBRATION_SATURATION_SCALE
        column = luma_vector + (1.0 + saturation) * (basis[:, index] - luma_vector)
        hue = _slider(settings, f"Calibration{name}PrimaryHue")
        if hue != 0.0:
            adjacent = (index + (1 if hue > 0.0 else -1)) % 3
            column += abs(hue) * C.CALIBRATION_HUE_MIX * (basis[:, adjacent] - basis[:, index])
        matrix[:, index] = column
    return matrix


def _apply_calibration(rgb: np.ndarray, settings: Mapping[str, object]) -> np.ndarray:
    """Apply primary matrix plus the Adobe-style green↔magenta shadow tint."""
    if not any(_number(settings, f"Calibration{name}Primary{part}") != 0.0 for name in ("Red", "Green", "Blue") for part in ("Hue", "Saturation")) and _number(settings, "CalibrationShadowTint") == 0.0:
        return rgb
    calibrated = np.maximum(rgb @ calibration_matrix(settings).T, 0.0)
    tint = _slider(settings, "CalibrationShadowTint")
    if tint == 0.0:
        return calibrated.astype(np.float32)
    shadow_weight = 1.0 - _smoothstep(C.CALIBRATION_SHADOW_START, C.CALIBRATION_SHADOW_END, luma(calibrated))
    multiplier = 1.0 + tint * C.CALIBRATION_SHADOW_TINT_SCALE * shadow_weight[..., None] * np.array((1.0, -2.0, 1.0), dtype=np.float32)
    return np.maximum(calibrated * multiplier, 0.0).astype(np.float32)


def _full_canvas_source(rgb: np.ndarray, canvas_size: tuple[int, int]) -> np.ndarray | None:
    root = rgb
    while isinstance(getattr(root, "base", None), np.ndarray):
        root = root.base
    width, height = canvas_size
    return root if root.ndim == 3 and root.shape == (height, width, 3) else None


def _bilinear_sample(rgb: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    height, width = rgb.shape[:2]
    valid = (x >= 0.0) & (x <= width - 1) & (y >= 0.0) & (y <= height - 1)
    x0 = np.clip(np.floor(x).astype(np.intp), 0, width - 1)
    y0 = np.clip(np.floor(y).astype(np.intp), 0, height - 1)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    wx, wy = (x - x0)[..., None], (y - y0)[..., None]
    top = rgb[y0, x0] * (1.0 - wx) + rgb[y0, x1] * wx
    bottom = rgb[y1, x0] * (1.0 - wx) + rgb[y1, x1] * wx
    sampled = top * (1.0 - wy) + bottom * wy
    sampled[~valid] = 0.0
    return sampled.astype(np.float32)


def _apply_lens_correction(
    rgb: np.ndarray,
    settings: Mapping[str, object],
    color_profile: Mapping[str, object] | None,
    *,
    pixel_offset: tuple[int, int],
    canvas_size: tuple[int, int] | None,
) -> np.ndarray:
    manual_distortion = _slider(settings, "LensManualDistortionAmount")
    manual_vignette = _slider(settings, "LensManualVignetteAmount")
    profile_enabled = _bool(settings, "LensProfileEnable") and isinstance(color_profile, Mapping) and not _bool(color_profile, "hdr")
    if not profile_enabled and manual_distortion == 0.0 and manual_vignette == 0.0:
        return rgb
    correction = color_profile.get("lens_correction") if profile_enabled and isinstance(color_profile, Mapping) else None
    if not isinstance(correction, Mapping):
        correction = {}
    if not correction and manual_distortion == 0.0 and manual_vignette == 0.0:
        return rgb
    height, width = rgb.shape[:2]
    canvas_width, canvas_height = canvas_size or (width, height)
    offset_x, offset_y = pixel_offset
    crop_ratio = float(correction.get("lens_crop_factor") or 1.0) / max(
        float(correction.get("camera_crop_factor") or 1.0), C.TONE_EPSILON
    )
    # Crop the corrected view before inverse remapping.  This avoids black
    # borders for outward radial models; identity polynomials return scale 1.
    distortion_scale = np.clip(_number(settings, "LensProfileDistortionScale", C.LENS_PROFILE_SCALE_DEFAULT) / 100.0, 0.0, 2.0)
    scaled_distortion = correction.get("distortion")
    if isinstance(scaled_distortion, Mapping) and distortion_scale != 1.0:
        scaled_distortion = {**scaled_distortion, "terms": [float(term) * distortion_scale for term in scaled_distortion.get("terms", ())]}
    auto_crop = distortion_auto_crop_scale(scaled_distortion, canvas_width, canvas_height, crop_ratio)
    x = np.arange(width, dtype=np.float32) + offset_x + C.LENS_IMAGE_CENTER
    y = np.arange(height, dtype=np.float32) + offset_y + C.LENS_IMAGE_CENTER
    x = canvas_width * C.LENS_IMAGE_CENTER + (x - canvas_width * C.LENS_IMAGE_CENTER) * auto_crop
    y = canvas_height * C.LENS_IMAGE_CENTER + (y - canvas_height * C.LENS_IMAGE_CENTER) * auto_crop
    half_min = min(canvas_width, canvas_height) / C.LENS_NORMALIZED_HALF_MIN
    px = (x[None, :] - canvas_width * C.LENS_IMAGE_CENTER) / half_min * crop_ratio
    py = (y[:, None] - canvas_height * C.LENS_IMAGE_CENTER) / half_min * crop_ratio
    radius = np.sqrt(px * px + py * py)
    radial_scale = lens_radial_scale(radius, correction.get("distortion"), distortion_scale)
    radial_scale *= 1.0 + manual_distortion * C.LENS_MANUAL_DISTORTION_FACTOR * radius * radius
    source_x = canvas_width * C.LENS_IMAGE_CENTER + px / crop_ratio * radial_scale * half_min - C.LENS_IMAGE_CENTER
    source_y = canvas_height * C.LENS_IMAGE_CENTER + py / crop_ratio * radial_scale * half_min - C.LENS_IMAGE_CENTER
    full_source = _full_canvas_source(rgb, (canvas_width, canvas_height))
    if full_source is not None:
        result = _bilinear_sample(full_source, source_x, source_y)
    else:
        result = _bilinear_sample(rgb, source_x - offset_x, source_y - offset_y)
    source_radius = radius * radial_scale
    vignette_scale = np.clip(_number(settings, "LensProfileVignettingScale", C.LENS_PROFILE_SCALE_DEFAULT) / 100.0, 0.0, 2.0)
    # Lensfun distortion models use the Hugin short-edge radius, while its PA
    # vignetting calibration defines r=1 at the image corner.
    corner_radius = math.hypot(canvas_width / min(canvas_width, canvas_height), canvas_height / min(canvas_width, canvas_height)) * crop_ratio
    gain = lens_vignetting_gain(source_radius / max(corner_radius, C.TONE_EPSILON), correction.get("vignetting"), vignette_scale)
    midpoint = C.LENS_MANUAL_VIGNETTE_MIDPOINT_MIN + np.clip(_number(settings, "LensManualVignetteMidpoint", 50.0), 0.0, 100.0) / 100.0 * C.LENS_MANUAL_VIGNETTE_MIDPOINT_RANGE
    manual_weight = _smoothstep(midpoint, 1.0, np.clip(source_radius, 0.0, 1.0))
    gain *= np.maximum(0.0, 1.0 + manual_vignette * C.LENS_MANUAL_VIGNETTE_FACTOR * manual_weight)
    return (result * gain[..., None]).astype(np.float32)


def rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maximum = np.maximum(np.maximum(red, green), blue)
    minimum = np.minimum(np.minimum(red, green), blue)
    delta = maximum - minimum
    hue = np.zeros_like(maximum)
    nonzero = delta > 0.0
    red_max = nonzero & (maximum == red)
    green_max = nonzero & (maximum == green)
    blue_max = nonzero & (maximum == blue)
    hue[red_max] = np.mod((green[red_max] - blue[red_max]) / delta[red_max], 6.0)
    hue[green_max] = (blue[green_max] - red[green_max]) / delta[green_max] + 2.0
    hue[blue_max] = (red[blue_max] - green[blue_max]) / delta[blue_max] + 4.0
    hue = np.mod(hue * 60.0, 360.0)
    saturation = np.divide(delta, maximum, out=np.zeros_like(delta), where=maximum > 0.0)
    return hue.astype(np.float32), saturation.astype(np.float32), maximum.astype(np.float32)


def hsv_to_rgb(hue: np.ndarray, saturation: np.ndarray, value: np.ndarray) -> np.ndarray:
    h = np.mod(hue, 360.0) / 60.0
    chroma = value * saturation
    x = chroma * (1.0 - np.abs(np.mod(h, 2.0) - 1.0))
    zeros = np.zeros_like(chroma)
    rgb_prime = np.stack(
        (
            np.select([h < 1, h < 2, h < 3, h < 4, h < 5], [chroma, x, zeros, zeros, x], default=chroma),
            np.select([h < 1, h < 2, h < 3, h < 4, h < 5], [x, chroma, chroma, x, zeros], default=zeros),
            np.select([h < 1, h < 2, h < 3, h < 4, h < 5], [zeros, zeros, x, chroma, chroma], default=x),
        ),
        axis=-1,
    )
    return (rgb_prime + (value - chroma)[..., None]).astype(np.float32)


def hsl_band_weights(hue: np.ndarray) -> np.ndarray:
    """Raised-cosine, circular weights with exactly two active adjacent bands."""
    centers = np.asarray(C.BAND_CENTERS, dtype=np.float32)
    wrapped = np.mod(hue, 360.0)
    extended = np.concatenate((centers, [centers[0] + 360.0]))
    right = np.searchsorted(extended, wrapped, side="right")
    right = np.where(right == len(extended), 1, right)
    left = right - 1
    left = np.where(left == len(centers), len(centers) - 1, left)
    right_center = extended[right]
    left_center = extended[left]
    span = right_center - left_center
    # ``extended`` supplies the 360-degree right edge for the final segment;
    # hue itself is already in that final interval and must not be shifted.
    position = wrapped
    fraction = (position - left_center) / span
    left_weight = 0.5 * (1.0 + np.cos(np.pi * fraction))
    weights = np.zeros(hue.shape + (len(centers),), dtype=np.float32)
    flat = weights.reshape(-1, len(centers))
    indices = np.arange(flat.shape[0])
    flat[indices, left.reshape(-1)] = left_weight.reshape(-1)
    flat[indices, (right % len(centers)).reshape(-1)] += 1.0 - left_weight.reshape(-1)
    return weights


def _hsl_and_black_white(c: np.ndarray, settings: Mapping[str, object], dehaze: float) -> np.ndarray:
    hue, saturation, value = rgb_to_hsv(c)
    source_hue = hue.copy()
    neutral = _smoothstep(C.NEUTRAL_PROTECT_START, C.NEUTRAL_PROTECT_END, saturation)
    centers = np.asarray(C.BAND_CENTERS, dtype=np.float32)

    def segments():
        for index, left_center in enumerate(centers):
            right_index = (index + 1) % len(centers)
            right_center = centers[right_index] + (360.0 if right_index == 0 else 0.0)
            position = np.where(source_hue < left_center, source_hue + 360.0, source_hue)
            mask = (position >= left_center) & (position < right_center)
            yield index, right_index, mask, (position[mask] - left_center) / (right_center - left_center)

    if str(settings.get("ConvertToGrayscale", "False")).strip().lower() == "true":
        gray = luma(c)
        for index, right_index, mask, fraction in segments():
            left_weight = 0.5 * (1.0 + np.cos(np.pi * fraction))
            mixer = (
                left_weight * _slider(settings, f"GrayMixer{C.BAND_NAMES[index]}")
                + (1.0 - left_weight) * _slider(settings, f"GrayMixer{C.BAND_NAMES[right_index]}")
            )
            gray[mask] *= 1.0 + mixer * C.GRAY_MIXER_FACTOR
        return np.repeat(gray[..., None], 3, axis=-1).astype(np.float32)
    for index, right_index, mask, fraction in segments():
        left_weight = 0.5 * (1.0 + np.cos(np.pi * fraction))
        right_weight = 1.0 - left_weight
        hue_adjustment = (
            left_weight * _slider(settings, f"HueAdjustment{C.BAND_NAMES[index]}")
            + right_weight * _slider(settings, f"HueAdjustment{C.BAND_NAMES[right_index]}")
        )
        saturation_adjustment = (
            left_weight * _slider(settings, f"SaturationAdjustment{C.BAND_NAMES[index]}")
            + right_weight * _slider(settings, f"SaturationAdjustment{C.BAND_NAMES[right_index]}")
        )
        luminance_adjustment = (
            left_weight * _slider(settings, f"LuminanceAdjustment{C.BAND_NAMES[index]}")
            + right_weight * _slider(settings, f"LuminanceAdjustment{C.BAND_NAMES[right_index]}")
        )
        hue[mask] += hue_adjustment * neutral[mask] * C.HUE_SHIFT_DEGREES
        saturation[mask] *= 1.0 + saturation_adjustment * neutral[mask]
        value[mask] *= 1.0 + luminance_adjustment * neutral[mask] * C.HSL_LUMINANCE_FACTOR
    # HSL bands stay HSV; global Vibrance/Saturation move to OKLab chroma.
    rgb = hsv_to_rgb(hue, np.clip(saturation, 0.0, 1.0), value)
    return scale_oklab_chroma(
        rgb,
        saturation=_slider(settings, "Saturation"),
        vibrance=_slider(settings, "Vibrance"),
        dehaze=dehaze,
    )


def _color_grade(c: np.ndarray, settings: Mapping[str, object]) -> np.ndarray:
    """§22 four-wheel grade: ab offsets plus luminance-band exposure in OKLab."""
    wheel_names = ("Shadow", "Midtone", "Highlight", "Global")
    if not any(_number(settings, f"ColorGrade{name}{part}") != 0.0 for name in wheel_names for part in ("Hue", "Sat", "Lum")):
        return c
    lightness = luma(c)
    blending = np.clip(_number(settings, "ColorGradeBlending", 50.0), 0.0, 100.0) / 100.0
    balance = _slider(settings, "ColorGradeBalance") * C.COLOR_GRADE_BALANCE_SHIFT
    width = C.COLOR_GRADE_BLEND_MIN + blending * C.COLOR_GRADE_BLEND_RANGE
    shadows = 1.0 - _smoothstep(C.COLOR_GRADE_SHADOW_CENTER + balance - width, C.COLOR_GRADE_SHADOW_CENTER + balance + width, lightness)
    highlights = _smoothstep(C.COLOR_GRADE_HIGHLIGHT_CENTER + balance - width, C.COLOR_GRADE_HIGHLIGHT_CENTER + balance + width, lightness)
    weights = {"Shadow": shadows, "Highlight": highlights, "Midtone": np.clip(1.0 - np.maximum(shadows, highlights), 0.0, 1.0), "Global": np.ones_like(lightness)}
    linear = srgb_to_linear(c)
    lab = linear_to_oklab(linear)
    exposure = np.zeros_like(lightness)
    for name in wheel_names:
        weight = weights[name]
        hue = np.deg2rad(_number(settings, f"ColorGrade{name}Hue"))
        saturation = _slider(settings, f"ColorGrade{name}Sat")
        lab[..., 1] += np.cos(hue) * saturation * C.COLOR_GRADE_AB_SCALE * weight
        lab[..., 2] += np.sin(hue) * saturation * C.COLOR_GRADE_AB_SCALE * weight
        exposure += _slider(settings, f"ColorGrade{name}Lum") * C.COLOR_GRADE_LUMINANCE_EV * weight
    graded = _gamut_clip_desaturate(oklab_to_linear(lab)) * np.exp2(exposure)[..., None]
    return linear_to_srgb(_gamut_clip_desaturate(graded))


def _hue_window(hue: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Inclusive circular 0..360-degree hue interval."""
    lo, hi = np.mod(lo, 360.0), np.mod(hi, 360.0)
    return ((hue >= lo) & (hue <= hi)) if lo <= hi else ((hue >= lo) | (hue <= hi))


def _defringe(c: np.ndarray, settings: Mapping[str, object]) -> np.ndarray:
    """Manual-only purple/green fringe desaturation on sharpen-field edges."""
    purple = np.clip(_number(settings, "DefringePurpleAmount"), 0.0, 100.0) / 100.0
    green = np.clip(_number(settings, "DefringeGreenAmount"), 0.0, 100.0) / 100.0
    if purple == 0.0 and green == 0.0:
        return c
    source_luma = luma(c)
    edge = _smoothstep(C.DEFRINGE_EDGE_LOW, C.DEFRINGE_EDGE_HIGH, np.abs(source_luma - gaussian_blur(source_luma, 1.0)))
    hue, saturation, value = rgb_to_hsv(c)
    amount = np.zeros_like(saturation)
    for prefix, strength in (("Purple", purple), ("Green", green)):
        if strength == 0.0:
            continue
        lo = _number(settings, f"Defringe{prefix}HueLo", 30.0 if prefix == "Purple" else 40.0) * C.DEFRINGE_HUE_SCALE
        hi = _number(settings, f"Defringe{prefix}HueHi", 70.0 if prefix == "Purple" else 60.0) * C.DEFRINGE_HUE_SCALE
        amount = np.maximum(amount, strength * _hue_window(hue, lo, hi))
    saturation *= 1.0 - amount * edge
    return hsv_to_rgb(hue, np.clip(saturation, 0.0, 1.0), value)


def _cross_blur(field: np.ndarray, radius: int = C.NR_COLOR_RADIUS) -> np.ndarray:
    """Reflect-padded center-plus-axes blur, matching the GLSL NR taps."""
    if radius <= 0:
        return field.astype(np.float32, copy=True)
    padded = np.pad(field, ((radius, radius), (radius, radius)), mode="reflect")
    center = padded[radius:radius + field.shape[0], radius:radius + field.shape[1]]
    return (center + padded[:field.shape[0], radius:radius + field.shape[1]]
            + padded[2 * radius:2 * radius + field.shape[0], radius:radius + field.shape[1]]
            + padded[radius:radius + field.shape[0], :field.shape[1]]
            + padded[radius:radius + field.shape[0], 2 * radius:2 * radius + field.shape[1]]) / 5.0


def _noise_reduction(c: np.ndarray, settings: Mapping[str, object]) -> np.ndarray:
    """§22 NR with intentionally lightweight Detail/Contrast approximations."""
    luminance_amount = np.clip(_number(settings, "LuminanceSmoothing"), 0.0, 100.0) / 100.0
    chroma_amount = np.clip(_number(settings, "ColorNoiseReduction"), 0.0, 100.0) / 100.0
    if luminance_amount == 0.0 and chroma_amount == 0.0:
        return c
    result = c.astype(np.float32, copy=True)
    if luminance_amount:
        full_luma = luma(result)
        detail = np.clip(_number(settings, "LuminanceDetail", 50.0), 0.0, 100.0) / 100.0
        contrast = np.clip(_number(settings, "LuminanceContrast"), 0.0, 100.0) / 100.0
        edge = _smoothstep(
            C.NR_DETAIL_EDGE_LOW, C.NR_DETAIL_EDGE_HIGH,
            np.abs(full_luma - gaussian_blur(full_luma, 1.0)),
        )
        # Higher Detail protects existing edges; Contrast restores a bounded
        # portion of the smoothed luma residual rather than inventing detail.
        effective_amount = luminance_amount * (1.0 - edge * detail)
        half = full_luma[::2, ::2]
        padded = np.pad(half, 1, mode="reflect")
        total = np.full_like(half, C.NR_LUMA_SPATIAL_CENTER)
        filtered = half * C.NR_LUMA_SPATIAL_CENTER
        for y, x, spatial in ((0, 1, C.NR_LUMA_SPATIAL_AXIS), (2, 1, C.NR_LUMA_SPATIAL_AXIS), (1, 0, C.NR_LUMA_SPATIAL_AXIS), (1, 2, C.NR_LUMA_SPATIAL_AXIS)):
            neighbor = padded[y:y + half.shape[0], x:x + half.shape[1]]
            weight = spatial * np.exp(-np.square(neighbor - half) / (2.0 * C.NR_LUMA_SIGMA ** 2))
            filtered += neighbor * weight
            total += weight
        smooth = (filtered / total).repeat(2, axis=0).repeat(2, axis=1)[:result.shape[0], :result.shape[1]]
        result += ((smooth - full_luma) * effective_amount)[..., None]
        result += ((full_luma - smooth) * effective_amount * contrast * C.NR_CONTRAST_RESIDUAL)[..., None]
    if chroma_amount:
        lab = linear_to_oklab(srgb_to_linear(np.clip(result, 0.0, 1.0)))
        smooth_ab = np.stack((_cross_blur(lab[..., 1]), _cross_blur(lab[..., 2])), axis=-1)
        lab[..., 1:3] = lab[..., 1:3] * (1.0 - chroma_amount) + smooth_ab * chroma_amount
        result = linear_to_srgb(_gamut_clip_desaturate(oklab_to_linear(lab)))
    return result.astype(np.float32)


def gaussian_blur(field: np.ndarray, sigma: float) -> np.ndarray:
    """Reflect-padded separable Gaussian blur using only NumPy convolution."""
    if sigma <= 0.0:
        return field.astype(np.float32, copy=True)
    radius = max(1, int(math.ceil(C.GAUSSIAN_TRUNCATE * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel /= kernel.sum()
    padded_x = np.pad(field, ((0, 0), (radius, radius)), mode="reflect")
    horizontal = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="valid"), 1, padded_x)
    padded_y = np.pad(horizontal, ((radius, radius), (0, 0)), mode="reflect")
    return np.apply_along_axis(lambda column: np.convolve(column, kernel, mode="valid"), 0, padded_y).astype(np.float32)


def _soft_threshold_residual(residual: np.ndarray, threshold: float) -> np.ndarray:
    magnitude = np.abs(residual)
    gated = np.maximum(magnitude - threshold, 0.0) / max(1.0 - threshold, C.TONE_EPSILON)
    return np.sign(residual) * gated


def _detail(
    c: np.ndarray,
    settings: Mapping[str, object],
    *,
    blur_min_dimension: int | None = None,
    lightness: np.ndarray | None = None,
    blurs: Mapping[str, np.ndarray] | None = None,
) -> np.ndarray:
    lightness = luma(c) if lightness is None else np.asarray(lightness, dtype=np.float32)
    blurs = blurs or {}
    result = c.copy()
    minimum_dimension = blur_min_dimension or min(lightness.shape)
    clarity = _slider(settings, "Clarity2012")
    if clarity != 0.0:
        large = np.asarray(blurs.get("large"), dtype=np.float32) if "large" in blurs else gaussian_blur(lightness, C.BLUR_LARGE_FACTOR * minimum_dimension)
        residual = np.clip(lightness - large, -C.CLARITY_RESIDUAL_MAX, C.CLARITY_RESIDUAL_MAX)
        midtones = np.clip(4.0 * lightness * (1.0 - lightness), 0.0, 1.0)
        result += (residual * C.CLARITY_FACTOR * clarity * midtones)[..., None]
    texture = _slider(settings, "Texture")
    if texture != 0.0:
        small = np.asarray(blurs.get("small"), dtype=np.float32) if "small" in blurs else gaussian_blur(lightness, C.BLUR_SMALL_FACTOR * minimum_dimension)
        residual = np.clip(lightness - small, -C.CLARITY_RESIDUAL_MAX, C.CLARITY_RESIDUAL_MAX)
        result += (residual * C.TEXTURE_FACTOR * texture)[..., None]
    sharpness = np.clip(_number(settings, "Sharpness"), 0.0, 150.0)
    if sharpness != 0.0:
        radius = np.clip(_number(settings, "SharpenRadius", 1.0), 0.5, 3.0)
        sharp = np.asarray(blurs.get("sharp"), dtype=np.float32) if "sharp" in blurs else gaussian_blur(lightness, radius)
        residual = _soft_threshold_residual(lightness - sharp, C.SHARPEN_THRESHOLD)
        masking = np.clip(_number(settings, "SharpenEdgeMasking"), 0.0, 100.0) / 100.0
        edge_mask = _smoothstep(C.SHARPEN_MASK_EDGE_LOW, C.SHARPEN_MASK_EDGE_HIGH, np.abs(lightness - sharp))
        residual *= (1.0 - masking) + masking * edge_mask
        result += (residual * (sharpness / 150.0) * C.SHARPEN_FACTOR)[..., None]
    return result


def _vignette(
    c: np.ndarray,
    settings: Mapping[str, object],
    *,
    pixel_offset: tuple[int, int] = (0, 0),
    canvas_size: tuple[int, int] | None = None,
) -> np.ndarray:
    amount = _slider(settings, "PostCropVignetteAmount")
    if amount == 0.0:
        return c
    height, width = c.shape[:2]
    canvas_width, canvas_height = canvas_size or (width, height)
    offset_x, offset_y = pixel_offset
    left = np.clip(_number(settings, "CropLeft", 0.0), 0.0, 1.0)
    top = np.clip(_number(settings, "CropTop", 0.0), 0.0, 1.0)
    right = np.clip(_number(settings, "CropRight", 1.0), left + C.TONE_EPSILON, 1.0)
    bottom = np.clip(_number(settings, "CropBottom", 1.0), top + C.TONE_EPSILON, 1.0)
    x = (np.arange(width, dtype=np.float32) + offset_x + 0.5) / canvas_width
    y = (np.arange(height, dtype=np.float32) + offset_y + 0.5) / canvas_height
    center_x, center_y = (left + right) * 0.5, (top + bottom) * 0.5
    crop_aspect = (right - left) * canvas_width / max((bottom - top) * canvas_height, 1.0)
    roundness = 1.0 + _slider(settings, "PostCropVignetteRoundness") * C.VIGNETTE_ROUNDNESS_FACTOR
    dx = (x[None, :] - center_x) * 2.0 * crop_aspect / max(roundness, C.TONE_EPSILON)
    dy = (y[:, None] - center_y) * 2.0 * max(roundness, C.TONE_EPSILON)
    rho = np.sqrt(dx * dx + dy * dy)
    midpoint = C.VIGNETTE_MIDPOINT_MIN + np.clip(_number(settings, "PostCropVignetteMidpoint", 50.0), 0.0, 100.0) / 100.0 * C.VIGNETTE_MIDPOINT_RANGE
    feather = C.VIGNETTE_FEATHER_MIN + np.clip(_number(settings, "PostCropVignetteFeather", 50.0), 0.0, 100.0) / 100.0 * C.VIGNETTE_FEATHER_RANGE
    strength = _smoothstep(midpoint, midpoint + feather, rho) * amount * C.VIGNETTE_FACTOR
    if amount < 0.0:
        return c * (1.0 + strength[..., None])
    # Mixing toward white is the screen blend with white and naturally protects highlights.
    return c + (1.0 - c) * strength[..., None]


def grain_hash_u32(x: np.ndarray | int, y: np.ndarray | int, seed: int = C.GRAIN_SEED) -> np.ndarray:
    """The shared unsigned-32-bit grain hash before its 16-bit normalization."""
    x32 = np.asarray(x, dtype=np.uint32)
    y32 = np.asarray(y, dtype=np.uint32)
    with np.errstate(over="ignore"):
        h = (x32 * np.uint32(C.GRAIN_X_MULTIPLIER) + y32 * np.uint32(C.GRAIN_Y_MULTIPLIER)) ^ np.uint32(seed)
        h = (h ^ (h >> np.uint32(C.GRAIN_HASH_SHIFT))) * np.uint32(C.GRAIN_HASH_MULTIPLIER)
    return h


def grain_hash(x: np.ndarray | int, y: np.ndarray | int, seed: int = C.GRAIN_SEED) -> np.ndarray:
    """The shared unsigned-32-bit grain hash, returned as a [0, 1] float."""
    h = grain_hash_u32(x, y, seed)
    return ((h >> np.uint32(C.GRAIN_OUTPUT_SHIFT)) & np.uint32(C.GRAIN_OUTPUT_MASK)).astype(np.float32) / C.GRAIN_OUTPUT_DIVISOR


def _grain(c: np.ndarray, settings: Mapping[str, object], *, pixel_offset: tuple[int, int] = (0, 0)) -> np.ndarray:
    amount = _slider(settings, "GrainAmount")
    if amount == 0.0:
        return c
    cell_size = C.GRAIN_CELL_SIZE_MIN + np.clip(_number(settings, "GrainSize", 25.0), 0.0, 100.0) / 100.0 * C.GRAIN_CELL_SIZE_RANGE
    frequency = C.GRAIN_FREQUENCY_MIN + np.clip(_number(settings, "GrainFrequency", 50.0), 0.0, 100.0) / 100.0 * C.GRAIN_FREQUENCY_RANGE
    height, width = c.shape[:2]
    offset_x, offset_y = pixel_offset
    x = (((np.arange(width) + offset_x) / cell_size) * frequency).astype(np.int64)
    y = (((np.arange(height) + offset_y) / cell_size) * frequency).astype(np.int64)
    noise = grain_hash(x[None, :], y[:, None]) - 0.5
    return c + noise[..., None] * amount * C.GRAIN_FACTOR


def _scene_linear_user_ops(rgb: np.ndarray, settings: Mapping[str, object]) -> tuple[np.ndarray, float]:
    """Apply the scene-linear Basic operations shared by generic and DNG renders.

    For an Adobe-profiled render §30.2 places this block after HueSatMap and
    LookTable, but before the profile tone curve. Film receives the same block
    from the post-BaselineExposure tap and replaces stages 5-7 entirely.
    """
    result = np.asarray(rgb, dtype=np.float32) * np.float32(np.exp2(_number(settings, "Exposure2012")))
    result = _region_tone_map(result, settings)
    dehaze = _slider(settings, "Dehaze")
    if dehaze != 0.0:
        result = (result - C.DEHAZE_AIRLIGHT_FACTOR * dehaze) / (1.0 - C.DEHAZE_AIRLIGHT_FACTOR * dehaze)
        result = np.maximum(result, 0.0)
    return result.astype(np.float32), float(dehaze)


def _resolved_adobe_profile(color_profile: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if not isinstance(color_profile, Mapping):
        return None
    embedded = color_profile.get("adobe_profile")
    if isinstance(embedded, Mapping):
        return dng_pipeline.normalize_adobe_profile(embedded)
    return dng_pipeline.resolve_adobe_profile(color_profile)


def _base_includes_baseline_exposure(color_profile: Mapping[str, object] | None) -> bool:
    """Whether the cached linear-sRGB base already includes DNG BaselineExposure.

    The lossy-DNG decoder records its camera-space ForwardMatrix in ``color``
    after applying BaselineExposure. Native LibRaw bases do not expose that
    field and still require the profile stage here.
    """
    if not isinstance(color_profile, Mapping):
        return False
    color = color_profile.get("color")
    return isinstance(color, Mapping) and color.get("forward_matrix") is not None


def apply_pipeline(
    linear_rgb: np.ndarray,
    settings: Mapping[str, object] | None = None,
    *,
    asshot_temperature: float | None = None,
    asshot_tint: float | None = None,
    color_profile: Mapping[str, object] | None = None,
    pixel_offset: tuple[int, int] = (0, 0),
    canvas_size: tuple[int, int] | None = None,
    blur_min_dimension: int | None = None,
) -> np.ndarray:
    """Apply v1.5 color operations in order and return float32 sRGB."""
    settings = effective_settings(settings)
    rgb = np.asarray(linear_rgb, dtype=np.float32)
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError("linear_rgb must have shape (height, width, 3)")
    rgb = _apply_lens_correction(
        rgb,
        settings,
        color_profile,
        pixel_offset=pixel_offset,
        canvas_size=canvas_size,
    )
    rgb = _apply_white_balance(rgb, settings, asshot_temperature, asshot_tint, color_profile)
    rgb = _apply_calibration(rgb, settings)
    adobe_profile = _resolved_adobe_profile(color_profile)
    cct = max(_as_shot_temperature(settings, asshot_temperature), 1.0)
    if adobe_profile is not None:
        # The cached PABASE1 pixels remain untouched linear-sRGB camera output.
        # Adobe stages 2-4 are intentionally resolved at render time so profile
        # changes never invalidate the expensive linear base cache.
        scene_linear = dng_pipeline.prepare_scene_linear(
            rgb,
            adobe_profile,
            cct=cct,
            input_space="linear_srgb",
            file_baseline_exposure=(
                0.0 if _base_includes_baseline_exposure(color_profile)
                else adobe_profile.get("baseline_exposure")
            ),
        )
    else:
        scene_linear = rgb
    film_stock = str(settings.get("pa_FilmStock") or "").strip()
    if film_stock:
        rgb, dehaze = _scene_linear_user_ops(scene_linear, settings)
        c = apply_film(
            rgb,
            film_stock,
            strength=np.clip(_number(settings, "pa_FilmStrength", 100.0), 0.0, 100.0) / 100.0,
            halation_scale=np.clip(_number(settings, "pa_FilmHalation", 100.0), 0.0, 100.0) / 100.0,
            grain_scale=np.clip(_number(settings, "pa_FilmGrain", 100.0), 0.0, 100.0) / 100.0,
            grain_size_scale=np.clip(_number(settings, "pa_FilmGrainSize", 100.0), 0.0, 100.0) / 100.0,
            min_dimension=blur_min_dimension,
        )
    elif adobe_profile is not None:
        def apply_user_ops(value: np.ndarray) -> np.ndarray:
            return _scene_linear_user_ops(value, settings)[0]

        c = dng_pipeline.apply_adobe_style(
            scene_linear,
            adobe_profile,
            cct=cct,
            user_ops=apply_user_ops,
        )
        dehaze = _slider(settings, "Dehaze")
    else:
        rgb, dehaze = _scene_linear_user_ops(scene_linear, settings)
        c = linear_to_srgb(rgb)
    base_kind = str(color_profile.get("base_kind") or "raw") if isinstance(color_profile, Mapping) else "raw"
    fitted_profile = None if base_kind == "display" or adobe_profile is not None else _camera_profile(color_profile)
    if not film_stock:
        # Adobe's profile curve replaces the generic base curve and base
        # saturation. Imported/user curves remain relative adjustments.
        tone_base_kind = "display" if adobe_profile is not None else base_kind
        c = _apply_tone_curves(c, settings, fitted_profile, base_kind=tone_base_kind)
    c = _hsl_and_black_white(c, settings, dehaze)
    if not _bool(settings, "ConvertToGrayscale"):
        c = apply_camera_profile_ab(c, fitted_profile)
    # §22 global ordering: HSL/vibrance -> grade -> defringe -> NR -> local -> detail.
    c = _color_grade(c, settings)
    c = _defringe(c, settings)
    c = _noise_reduction(c, settings)
    # Local corrections remain after grade and before global detail. Import
    # lazily so masks.py can remain a standalone raster/math twin.
    from .masks import apply_local_corrections, has_local_adjustments

    detail_lightness = None
    detail_blurs = None
    if has_local_adjustments(settings):
        local_luma = luma(c)
        corrections = settings.get("MaskGroupBasedCorrections", ())
        local_clarity = any(
            isinstance(correction, Mapping) and _number(correction, "LocalClarity2012") != 0.0
            for correction in corrections[: C.LOCAL_RENDER_CAP]
        )
        local_texture = any(
            isinstance(correction, Mapping) and _number(correction, "LocalTexture") != 0.0
            for correction in corrections[: C.LOCAL_RENDER_CAP]
        )
        global_clarity = _slider(settings, "Clarity2012") != 0.0
        global_texture = _slider(settings, "Texture") != 0.0
        global_sharpness = np.clip(_number(settings, "Sharpness"), 0.0, 150.0) != 0.0
        minimum_dimension = blur_min_dimension or min(local_luma.shape)
        local_blurs = {
            "large": gaussian_blur(local_luma, C.BLUR_LARGE_FACTOR * minimum_dimension) if local_clarity or global_clarity else local_luma,
            "small": gaussian_blur(local_luma, C.BLUR_SMALL_FACTOR * minimum_dimension) if local_texture or global_texture else local_luma,
        }
        if global_sharpness:
            local_blurs["sharp"] = gaussian_blur(local_luma, np.clip(_number(settings, "SharpenRadius", 1.0), 0.5, 3.0))
        c = apply_local_corrections(
            c,
            settings,
            luma=local_luma,
            blurs=local_blurs,
            pixel_offset=pixel_offset,
            canvas_size=canvas_size,
        )
        detail_lightness = local_luma
        detail_blurs = local_blurs
    c = _detail(c, settings, blur_min_dimension=blur_min_dimension, lightness=detail_lightness, blurs=detail_blurs)
    c = _vignette(c, settings, pixel_offset=pixel_offset, canvas_size=canvas_size)
    c = _grain(c, settings, pixel_offset=pixel_offset)
    # §23 must be the final pixel operation, after locals and global effects.
    from .heal import apply_retouch_spots
    c = apply_retouch_spots(c, settings)
    return np.clip(c, 0.0, 1.0).astype(np.float32)


# Intentional explicit alias for route/render callers.
render_pipeline = apply_pipeline
