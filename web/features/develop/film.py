"""Physically-modeled film emulation (spec §26).

Models the photochemical chain per stock: spectral layer exposure → halation →
H&D characteristic curves → DIR coupler inhibition → per-layer grain →
negative/positive print transform. Operates on LINEAR scene-referred sRGB and
returns display sRGB in [0,1]; when film is enabled the digital tone mapping
(base/profile curve + base saturation) is bypassed by the pipeline.

The per-pixel color math collapses to: crosstalk mat3 → log10 → H&D LUT →
DIR mat3 → print LUT → scan mat3, which is exactly what the GL twin evaluates
(two mat3s + two LUT textures + a log). Halation and grain are the only
spatial stages. Twin: static/js/desktop/develop/film.js — keep identical.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import numpy as np


STOCKS_DIR = Path(__file__).parent / "film_stocks"

# logE domain the H&D LUTs are sampled over (speed point = 0.0).
FILM_LOGE_MIN = -3.0
FILM_LOGE_MAX = 3.0
FILM_LUT_SIZE = 256
# Middle gray in linear scene light exposes the speed point.
FILM_MID_GRAY = 0.18
# Published H&D curves put logE = 0 at the ISO speed point (deep shadow,
# D ~ 0.15 above fog). Scene mid-gray exposes ~one decade above that; without
# this offset the whole scene renders in the toe (flat, milky, desaturated).
FILM_MIDGRAY_LOGE = 1.0
FILM_EPSILON = 1e-6


def list_stocks() -> list[dict[str, Any]]:
    stocks = []
    for path in sorted(STOCKS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stocks.append({
                "slug": data["slug"], "name": data["name"], "iso": data.get("iso"),
                "type": data.get("type"), "white_point_hint": data.get("white_point_hint"),
            })
        except Exception:
            continue
    return stocks


@lru_cache(maxsize=16)
def load_stock(slug: str) -> dict[str, Any] | None:
    path = STOCKS_DIR / f"{slug}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _interp_curve(points: list[list[float]], xs: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    order = np.argsort(pts[:, 0])
    return np.interp(xs, pts[order, 0], pts[order, 1])


def _is_bw(stock: Mapping[str, Any]) -> bool:
    return list(stock.get("hd_curves", {}).keys()) == ["pan"]


def build_film_tables(stock: Mapping[str, Any]) -> dict[str, Any]:
    """Precompute everything the per-pixel path needs. Twin of buildFilmTables in film.js."""
    xs = np.linspace(FILM_LOGE_MIN, FILM_LOGE_MAX, FILM_LUT_SIZE)
    bw = _is_bw(stock)
    curves = stock["hd_curves"]
    if bw:
        d = _interp_curve(curves["pan"], xs)
        hd = np.stack([d, d, d], axis=1)
    else:
        hd = np.stack([_interp_curve(curves[c], xs) for c in ("r", "g", "b")], axis=1)

    crosstalk = np.asarray(stock.get("spectral_crosstalk") or np.eye(3).tolist(), dtype=np.float64)
    if crosstalk.shape == (1, 3):  # B&W panchromatic weighting row
        crosstalk = np.tile(crosstalk, (3, 1))
    dir_coupler = np.asarray(stock.get("dir_coupler") or np.eye(3).tolist(), dtype=np.float64)
    if dir_coupler.shape != (3, 3):
        dir_coupler = np.eye(3)

    negative = str(stock.get("type", "")).startswith("negative")
    base = stock.get("base") or {}
    paper = stock.get("print_paper") or {}
    paper_gamma = float(paper.get("gamma", 2.6))
    paper_shoulder = float(paper.get("shoulder", 0.92))
    _fog = float(base.get("base_fog_density", 0.1))  # intentionally unused; H&D already includes base+fog
    mask = np.asarray(base.get("orange_mask_rgb") or [0.0, 0.0, 0.0], dtype=np.float64)

    # Per-channel print calibration: the printer/scanner neutralizes the orange
    # mask and base fog by exposing each channel so the SPEED-POINT density
    # (mid gray) prints to middle gray. D_ref per channel = density of logE=0
    # through the DIR matrix, plus mask.
    speed_idx = int(round((FILM_MIDGRAY_LOGE - FILM_LOGE_MIN) / (FILM_LOGE_MAX - FILM_LOGE_MIN) * (FILM_LUT_SIZE - 1)))
    d_speed = hd[speed_idx, :] @ dir_coupler.T
    # NOTE: published H&D curves are absolute density and already include
    # base+fog (and, for color negative, the orange mask under Status-M) —
    # do not add fog again or every print goes ~half a stop dark.
    d_ref = d_speed + (mask if negative or bw else 0.0)

    # Print LUT over RELATIVE density (D - D_ref), range ±2.2.
    rel_axis = np.linspace(-2.2, 2.2, FILM_LUT_SIZE)
    if negative or bw:
        # More density than the speed point = more scene light = brighter print.
        # Anchor: rel=0 prints middle gray. Paper white via extended Reinhard
        # (soft(W)=1) so the shoulder rolls instead of clipping; paper_shoulder
        # (<1 = gentler roll) shapes the approach.
        log_print = rel_axis * paper_gamma
        pos = 0.18 * np.power(10.0, log_print)
        paper_white = 2.0
        soft = pos * (1.0 + pos / (paper_white * paper_white)) / (1.0 + pos)
        soft = np.power(np.clip(soft, 0.0, 1.0), max(paper_shoulder, 0.4))
        pos = np.clip(soft, 0.0, 1.0)
        # display-encode the print (sRGB-ish)
        pos = np.where(pos <= 0.0031308, pos * 12.92, 1.055 * np.power(pos, 1.0 / 2.4) - 0.055)
    else:
        pos = np.power(10.0, -(rel_axis + d_ref.mean()))
        pos = np.clip(pos / max(np.power(10.0, -d_ref.mean()) / 0.18, FILM_EPSILON) * 0.18, 0.0, 1.0)
        pos = np.where(pos <= 0.0031308, pos * 12.92, 1.055 * np.power(pos, 1.0 / 2.4) - 0.055)
    print_lut = pos

    scan = np.asarray(stock.get("scan_matrix") or np.eye(3).tolist(), dtype=np.float64)
    if scan.shape != (3, 3):
        scan = np.eye(3)

    halation = stock.get("halation") or {}
    grain = stock.get("grain") or {}
    return {
        "hd_lut": hd.astype(np.float32),                # (256, 3) logE→density
        "crosstalk": crosstalk.astype(np.float32),
        "dir": dir_coupler.astype(np.float32),
        "print_lut": print_lut.astype(np.float32),      # (256,) rel-density→display
        "d_ref": np.asarray(d_ref, dtype=np.float32),
        "mask": mask.astype(np.float32),
        "scan": scan.astype(np.float32),
        "negative": bool(negative or bw),
        "bw": bw,
        "halation": {
            "amount": float(halation.get("amount", 0.0)),
            "threshold": float(halation.get("threshold", 0.7)),
            "radius_frac": float(halation.get("radius_frac", 0.015)),
            "green_fraction": float(halation.get("green_fraction", 0.2)),
        },
        "grain": {
            "rms": float(grain.get("rms_granularity", grain.get("rms", 5.0))),
            "size_px_at_4k": float(grain.get("size_px_at_4k", 3.0)),
            "shadow_bias": float(grain.get("shadow_bias", 0.35)),
        },
    }


def _gaussian_blur(image: np.ndarray, sigma: float) -> np.ndarray:
    """Separable gaussian via numpy convolve (same idiom as pipeline §4b)."""
    if sigma <= 0.5:
        return image
    radius = max(1, int(math.ceil(3.0 * sigma)))
    xs = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-(xs**2) / (2.0 * sigma * sigma))
    kernel /= kernel.sum()
    kernel = kernel.astype(np.float32)
    padded = np.pad(image, ((radius, radius), (0, 0)), mode="edge")
    out = np.zeros_like(image)
    for i, k in enumerate(kernel):
        out += k * padded[i : i + image.shape[0], :]
    padded = np.pad(out, ((0, 0), (radius, radius)), mode="edge")
    out2 = np.zeros_like(image)
    for i, k in enumerate(kernel):
        out2 += k * padded[:, i : i + image.shape[1]]
    return out2


def _value_noise(shape: tuple[int, int], cell: float, seed: int) -> np.ndarray:
    """Smooth per-cell noise (grain clumps, not pixel snow). Deterministic."""
    h, w = shape
    cell = max(cell, 1.0)
    gh, gw = int(h / cell) + 2, int(w / cell) + 2
    rng = np.random.default_rng(seed)
    grid = rng.standard_normal((gh, gw)).astype(np.float32)
    ys = np.arange(h, dtype=np.float32) / cell
    xs = np.arange(w, dtype=np.float32) / cell
    y0 = np.floor(ys).astype(np.int32); x0 = np.floor(xs).astype(np.int32)
    fy = (ys - y0)[:, None]; fx = (xs - x0)[None, :]
    fy = fy * fy * (3 - 2 * fy); fx = fx * fx * (3 - 2 * fx)
    g00 = grid[y0][:, x0]; g01 = grid[y0][:, x0 + 1]
    g10 = grid[y0 + 1][:, x0]; g11 = grid[y0 + 1][:, x0 + 1]
    return (g00 * (1 - fy) * (1 - fx) + g01 * (1 - fy) * fx
            + g10 * fy * (1 - fx) + g11 * fy * fx)


def apply_film(
    linear_rgb: np.ndarray,
    stock_slug: str,
    *,
    strength: float = 1.0,
    halation_scale: float = 1.0,
    grain_scale: float = 1.0,
    grain_size_scale: float = 1.0,
    seed: int = 20260710,
    min_dimension: int | None = None,
) -> np.ndarray:
    """linear scene sRGB (float32, >=0) → display sRGB [0,1] through the stock."""
    stock = load_stock(stock_slug)
    if stock is None:
        raise ValueError(f"Unknown film stock: {stock_slug}")
    t = build_film_tables(stock)
    rgb = np.maximum(np.asarray(linear_rgb, dtype=np.float32), 0.0)
    h, w = rgb.shape[:2]
    dim = float(min_dimension or min(h, w))

    # 1. layer exposures with spectral crosstalk
    layer = rgb.reshape(-1, 3) @ t["crosstalk"].T
    layer = layer.reshape(h, w, 3)

    # 2. halation: bright scene light bounces off the base into the red layer
    hal = t["halation"]
    amount = hal["amount"] * halation_scale
    if amount > 0.0:
        luma = rgb @ np.float32([0.2126, 0.7152, 0.0722])
        excess = np.maximum(luma - hal["threshold"], 0.0)
        sigma = max(hal["radius_frac"] * dim, 1.0)
        glow = _gaussian_blur(excess, sigma)
        layer[..., 0] += amount * glow
        layer[..., 1] += amount * hal["green_fraction"] * glow

    # 3. log exposure relative to the speed point, then H&D curves
    loge = np.log10(np.maximum(layer / FILM_MID_GRAY, FILM_EPSILON)) + FILM_MIDGRAY_LOGE
    idx = (loge - FILM_LOGE_MIN) / (FILM_LOGE_MAX - FILM_LOGE_MIN) * (FILM_LUT_SIZE - 1)
    idx = np.clip(idx, 0.0, FILM_LUT_SIZE - 1.001)
    i0 = idx.astype(np.int32)
    frac = idx - i0
    hd = t["hd_lut"]
    density = np.empty_like(loge)
    for c in range(3):
        col = hd[:, c]
        density[..., c] = col[i0[..., c]] * (1 - frac[..., c]) + col[i0[..., c] + 1] * frac[..., c]

    # 4. DIR coupler inhibition on densities
    density = (density.reshape(-1, 3) @ t["dir"].T).reshape(h, w, 3)

    # 5. grain: density-dependent, clumped, per-layer
    g = t["grain"]
    if grain_scale > 0.0 and g["rms"] > 0.0:
        cell = max(g["size_px_at_4k"] * grain_size_scale * (dim / 4000.0), 1.0)
        # granularity peaks in the mid densities; negatives are grainier where
        # density is LOW (positive shadows) — shadow_bias shifts the response
        d_norm = np.clip(density / 2.2, 0.0, 1.0)
        response = 4.0 * d_norm * (1.0 - d_norm)
        response = response * (1.0 - g["shadow_bias"]) + g["shadow_bias"] * (1.0 - d_norm)
        sigma_d = (g["rms"] / 1000.0) * 9.0 * grain_scale
        for c in range(3):
            noise = _value_noise((h, w), cell, seed + c * 7919)
            density[..., c] += sigma_d * response[..., c] * noise
        if t["bw"]:
            density[..., 1] = density[..., 0]
            density[..., 2] = density[..., 0]

    # 6. orange mask + calibrated print/scan transform (relative density)
    if t["negative"]:
        density = density + t["mask"][None, None, :]
    rel = density - t["d_ref"][None, None, :]
    didx = np.clip((rel + 2.2) / 4.4 * (FILM_LUT_SIZE - 1), 0.0, FILM_LUT_SIZE - 1.001)
    d0 = didx.astype(np.int32)
    dfrac = didx - d0
    plut = t["print_lut"]
    positive = plut[d0] * (1 - dfrac) + plut[d0 + 1] * dfrac
    out = (positive.reshape(-1, 3) @ t["scan"].T).reshape(h, w, 3)
    out = np.clip(out, 0.0, 1.0).astype(np.float32)
    if t["bw"]:
        luma = out @ np.float32([1 / 3, 1 / 3, 1 / 3])
        out = np.stack([luma, luma, luma], axis=-1)

    if strength < 1.0:
        # blend against the neutral digital rendering (plain sRGB encode)
        clipped = np.clip(rgb, 0.0, 1.0)
        neutral = np.where(clipped <= 0.0031308, clipped * 12.92,
                           1.055 * np.power(clipped, 1.0 / 2.4) - 0.055).astype(np.float32)
        out = neutral + (out - neutral) * np.float32(max(strength, 0.0))
    return out
