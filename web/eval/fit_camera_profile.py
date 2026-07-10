"""Fit per-camera rendering profiles from RAW <-> Lightroom-export pairs.

The fitter renders imported Looks, reserves a deterministic 20% of usable
pairs, and trusts a fitted tone curve only when it lowers median luma error on
at least 80% of those held-out photos.  Color residuals are still fitted from
the training pairs when tone does not clear that independent gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as etree
from dataclasses import dataclass
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_ROOT))
os.chdir(WEB_ROOT)
os.environ.setdefault("PHOTOARCHIVE_SMOKE_MODE", "1")

import numpy as np
from PIL import Image

from features.develop import ops_constants as C
from features.develop import pipeline as pipe
from features.develop.importer import parse_xmp_text, read_embedded_xmp
from features.develop.looks import (
    compose_curve_luts,
    effective_settings,
    extract_xmp_look,
    look_amount,
    look_curve,
)
from features.develop.lossydng import decode_lossy_dng
from features.develop.rawproc import estimate_as_shot_white_balance


EXPORT_DIRS = (
    "/mnt/expansion/Photos/Exported Edits/2024/All Selected",
    "/mnt/expansion/Photos/Exported Edits/2025",
)
RAWS = "/mnt/expansion/Photos/RAWS"
EXIFTOOL = "/usr/bin/vendor_perl/exiftool"
DEFAULT_MAX_PAIRS = 48
DEFAULT_FIT_PX = 384
TONE_NODES = np.linspace(0.0, 1.0, C.CAMERA_PROFILE_TONE_NODES)
BASELINE_PROFILE_POINTS = tuple(C.BASE_PROFILE_POINTS)
IDENTITY_LUT = np.linspace(0.0, 1.0, C.CURVE_LUT_SIZE, dtype=np.float32)
LUMA_WEIGHTS = np.float32([C.LUMA_RED, C.LUMA_GREEN, C.LUMA_BLUE])


@dataclass(frozen=True)
class PairSamples:
    raw_name: str
    ours: np.ndarray
    lightroom: np.ndarray
    ours_lab: np.ndarray
    lightroom_lab: np.ndarray


def exif(path: str | Path, tags: list[str]) -> dict[str, object]:
    result = subprocess.run(
        [EXIFTOOL, "-j", "-n", *(f"-{tag}" for tag in tags), str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    try:
        return json.loads(result.stdout)[0]
    except (IndexError, TypeError, ValueError):
        return {}


def gather_pairs() -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for directory in EXPORT_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        result = subprocess.run(
            [EXIFTOOL, "-j", "-n", "-r", "-ext", "jpg", "-DateTimeOriginal", "-Model", str(base)],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        try:
            rows = json.loads(result.stdout)
        except (TypeError, ValueError):
            rows = []
        for metadata in rows:
            jpg = metadata.get("SourceFile")
            capture_time = metadata.get("DateTimeOriginal")
            model = metadata.get("Model")
            match = re.match(
                r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})",
                str(capture_time or ""),
            )
            if not jpg or not model or not match:
                continue
            year, month, day, hour, minute, second = match.groups()
            raw = Path(RAWS) / year / f"{year}-{month}-{day}" / f"{year}{month}{day}-{hour}{minute}{second}.dng"
            if raw.is_file():
                pairs.append((str(raw), str(jpg), str(model)))
    return pairs


def _curve_as_points(lut: np.ndarray) -> list[str]:
    return [f"{index}, {float(value * 255.0):.8f}" for index, value in enumerate(lut)]


def _fit_render_settings(stored: dict[str, object]) -> dict[str, object]:
    """Fold Look into an identity-base render, avoiding future double apply."""

    rendered = effective_settings(stored)
    curve = look_curve(stored)
    if curve is not None:
        look_lut = pipe.build_monotone_cubic_lut(curve)
        amount_lut = compose_curve_luts(IDENTITY_LUT, look_lut, look_amount(stored))
        user_lut = pipe.build_monotone_cubic_lut(stored.get("ToneCurvePV2012"))
        rendered["ToneCurvePV2012"] = _curve_as_points(compose_curve_luts(amount_lut, user_lut))
    rendered.pop("Look", None)
    return rendered


def _parse_fit_xmp(packet: bytes) -> dict[str, object]:
    """Parse global settings and nested Look independently for today's import tree."""

    root = etree.fromstring(packet)
    look = extract_xmp_look(root)
    for parent in root.iter():
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] == "Look":
                parent.remove(child)
    settings = parse_xmp_text(etree.tostring(root, encoding="utf-8"))
    if look:
        settings["Look"] = look
    return settings


def render_ours(raw_path: str, fit_px: int) -> tuple[np.ndarray | None, dict[str, object] | None]:
    array, metadata = decode_lossy_dng(raw_path, max_px=fit_px * 2)
    linear = array.astype(np.float32) / 65535.0
    packet = read_embedded_xmp(raw_path)
    stored = _parse_fit_xmp(packet) if packet else {}
    settings = _fit_render_settings(stored)
    as_shot = estimate_as_shot_white_balance(
        metadata.get("cam_mul"),
        [],
        as_shot_neutral=metadata.get("as_shot_neutral"),
        color_matrix=metadata.get("color_matrix1"),
        color_matrix2=metadata.get("color_matrix2"),
    )
    color = {
        "as_shot_neutral": metadata.get("as_shot_neutral"),
        "forward_matrix": metadata.get("forward_matrix"),
        "color_matrix1": metadata.get("color_matrix1"),
        "color_matrix2": metadata.get("color_matrix2"),
    }
    output = pipe.apply_pipeline(
        linear,
        settings,
        asshot_temperature=as_shot.get("temperature"),
        asshot_tint=as_shot.get("tint"),
        color_profile=color,
    )
    if abs(float(stored.get("CropAngle", 0) or 0)) > 0.01:
        return None, None
    height, width = output.shape[:2]
    left = int(float(stored.get("CropLeft", 0) or 0) * width)
    right = int(float(stored.get("CropRight", 1) or 1) * width)
    top = int(float(stored.get("CropTop", 0) or 0) * height)
    bottom = int(float(stored.get("CropBottom", 1) or 1) * height)
    output = output[top : max(top + 1, bottom), left : max(left + 1, right)]
    if bool(stored.get("ConvertToGrayscale")) or bool(settings.get("ConvertToGrayscale")):
        return None, None
    return output, stored


def srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    matrix1 = np.asarray(C.OKLAB_M1)
    matrix2 = np.asarray(C.OKLAB_M2)
    lms = linear @ matrix1.T
    return np.cbrt(np.maximum(lms, 0)) @ matrix2.T


def sample_pair(raw: str, jpg: str, fit_px: int) -> PairSamples | None:
    ours, _settings = render_ours(raw, fit_px)
    if ours is None:
        return None
    reference = np.asarray(Image.open(jpg).convert("RGB"), dtype=np.float32) / 255.0
    height, width = ours.shape[:2]
    resized = Image.fromarray((reference * 255).astype(np.uint8)).resize((width, height), Image.Resampling.LANCZOS)
    reference = np.asarray(resized, dtype=np.float32) / 255.0
    ours_flat = ours.reshape(-1, 3)
    reference_flat = reference.reshape(-1, 3)
    correlation = np.corrcoef(ours_flat[:, 1], reference_flat[:, 1])[0, 1]
    if not np.isfinite(correlation) or correlation < 0.85:
        print(f"  skip (corr {correlation:.2f}): {Path(raw).name}", flush=True)
        return None
    indices = np.random.default_rng(1).choice(len(ours_flat), size=min(20_000, len(ours_flat)), replace=False)
    ours_sample = ours_flat[indices]
    reference_sample = reference_flat[indices]
    return PairSamples(
        raw_name=Path(raw).name,
        ours=ours_sample,
        lightroom=reference_sample,
        ours_lab=srgb_to_oklab(ours_sample),
        lightroom_lab=srgb_to_oklab(reference_sample),
    )


def split_pairs(samples: list[PairSamples], model: str) -> tuple[list[PairSamples], list[PairSamples]]:
    holdout_count = max(1, int(np.ceil(len(samples) * 0.20)))
    seed = 20260710 + sum(model.encode("utf-8"))
    order = np.random.default_rng(seed).permutation(len(samples))
    holdout_indices = set(order[:holdout_count].tolist())
    train = [sample for index, sample in enumerate(samples) if index not in holdout_indices]
    holdout = [sample for index, sample in enumerate(samples) if index in holdout_indices]
    return train, holdout


def fit_tone(samples: list[PairSamples]) -> np.ndarray:
    ours_luma = np.concatenate([sample.ours @ LUMA_WEIGHTS for sample in samples])
    reference_luma = np.concatenate([sample.lightroom @ LUMA_WEIGHTS for sample in samples])
    tone: list[float | None] = []
    for node in TONE_NODES:
        selected = (ours_luma >= node - 0.033) & (ours_luma < node + 0.033)
        tone.append(float(np.median(reference_luma[selected])) if selected.sum() > 400 else None)
    known = [(node, value) for node, value in zip(TONE_NODES, tone) if value is not None]
    if len(known) < 2:
        return TONE_NODES.copy()
    known_x = np.asarray([node for node, _value in known])
    known_y = np.asarray([value for _node, value in known])
    fitted = np.interp(TONE_NODES, known_x, known_y)
    fitted[0] = min(fitted[0], 0.004)
    fitted[-1] = max(fitted[-1], 0.996)
    return np.maximum.accumulate(np.clip(fitted, 0.0, 1.0))


def fit_color_table(samples: list[PairSamples]) -> tuple[np.ndarray, list[float]]:
    ours_lab = np.concatenate([sample.ours_lab for sample in samples])
    ours_ab = ours_lab[:, 1:]
    reference_ab = np.concatenate([sample.lightroom_lab[:, 1:] for sample in samples])
    hue = np.arctan2(ours_ab[:, 1], ours_ab[:, 0])
    chroma = np.hypot(ours_ab[:, 0], ours_ab[:, 1])
    hue_bins = np.linspace(-np.pi, np.pi, C.CAMERA_PROFILE_HUE_BINS + 1)
    chroma_edges = [0.02, 0.06, 0.12, 1.0]
    table = np.zeros((C.CAMERA_PROFILE_HUE_BINS, C.CAMERA_PROFILE_CHROMA_BINS, 2), dtype=float)
    for hue_index in range(C.CAMERA_PROFILE_HUE_BINS):
        for chroma_index in range(C.CAMERA_PROFILE_CHROMA_BINS):
            low = 0.0 if chroma_index == 0 else chroma_edges[chroma_index - 1]
            selected = (
                (hue >= hue_bins[hue_index])
                & (hue < hue_bins[hue_index + 1])
                & (chroma >= low)
                & (chroma < chroma_edges[chroma_index])
            )
            if selected.sum() > 500:
                delta = reference_ab[selected] - ours_ab[selected]
                table[hue_index, chroma_index] = np.median(delta, axis=0)
    return table, chroma_edges


def _curve_rgb(rgb: np.ndarray, lut: np.ndarray) -> np.ndarray:
    positions = np.linspace(0.0, 1.0, len(lut), dtype=np.float32)
    return np.stack([np.interp(rgb[:, channel], positions, lut) for channel in range(3)], axis=1)


def heldout_tone_stats(holdout: list[PairSamples], tone: np.ndarray) -> tuple[list[tuple[str, float, float]], bool]:
    baseline = pipe.build_monotone_cubic_lut(BASELINE_PROFILE_POINTS)
    fitted = np.interp(np.linspace(0.0, 1.0, C.CURVE_LUT_SIZE), TONE_NODES, tone).astype(np.float32)
    rows: list[tuple[str, float, float]] = []
    for sample in holdout:
        target = sample.lightroom @ LUMA_WEIGHTS
        before = float(np.median(np.abs(_curve_rgb(sample.ours, baseline) @ LUMA_WEIGHTS - target)))
        after = float(np.median(np.abs(_curve_rgb(sample.ours, fitted) @ LUMA_WEIGHTS - target)))
        rows.append((sample.raw_name, before, after))
    improved = sum(after < before for _name, before, after in rows)
    return rows, improved / max(1, len(rows)) >= 0.80


def write_profile(
    model: str,
    samples: list[PairSamples],
    fit_px: int,
    output_dir: Path,
    *,
    dry_run: bool,
) -> dict[str, object] | None:
    if len(samples) < 6:
        print(f"{model}: only {len(samples)} usable pairs - skipping profile", flush=True)
        return None
    train, holdout = split_pairs(samples, model)
    tone = fit_tone(train)
    table, chroma_edges = fit_color_table(train)
    heldout, trusted = heldout_tone_stats(holdout, tone)
    train_x = np.concatenate([sample.ours @ LUMA_WEIGHTS for sample in train])
    train_y = np.concatenate([sample.lightroom @ LUMA_WEIGHTS for sample in train])
    residual = float(np.median(np.abs(np.interp(train_x, TONE_NODES, tone) - train_y)))
    before = float(np.median([row[1] for row in heldout]))
    after = float(np.median([row[2] for row in heldout]))
    improved = sum(row[2] < row[1] for row in heldout)
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    payload = {
        "model": model,
        "pairs": len(samples),
        "fit_pairs": len(train),
        "holdout_pairs": len(holdout),
        "fit_px": fit_px,
        "tone_nodes": [float(node) for node in TONE_NODES],
        "tone_values": [float(value) for value in tone],
        "tone_trusted": trusted,
        "oklab_ab_delta": table.tolist(),
        "chroma_edges": chroma_edges,
        "residual_tone_medabs": residual,
        "heldout_luma_medabs_before": before,
        "heldout_luma_medabs_after": after,
        "heldout_improved_pairs": improved,
    }
    path = output_dir / f"{slug}.json"
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(
        f"| {model} | {len(train)} | {len(holdout)} | {before:.5f} | {after:.5f} | "
        f"{improved}/{len(holdout)} | {'yes' if trusted else 'no'} |",
        flush=True,
    )
    for raw_name, pair_before, pair_after in heldout:
        print(f"  holdout {raw_name}: {pair_before:.5f} -> {pair_after:.5f}", flush=True)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=WEB_ROOT / "features" / "develop" / "profiles",
        help="Profile destination (use a temporary directory for evidence-only refits).",
    )
    parser.add_argument("--max-pairs-per-camera", type=int, default=DEFAULT_MAX_PAIRS)
    parser.add_argument("--fit-px", type=int, default=DEFAULT_FIT_PX)
    parser.add_argument("--model", action="append", help="Exact camera model to fit (repeatable).")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    C.BASE_PROFILE_POINTS = tuple((float(value), float(value)) for value in (0, 32, 64, 128, 192, 255))
    C.BASE_PROFILE_SAT = 1.0
    pairs = gather_pairs()
    print(f"pairs found: {len(pairs)}", flush=True)
    by_camera: dict[str, list[tuple[str, str]]] = {}
    for raw, jpg, model in pairs:
        by_camera.setdefault(model, []).append((raw, jpg))

    print("| camera | fit | holdout | median before | median after | improved | trusted |", flush=True)
    print("|---|---:|---:|---:|---:|---:|:---:|", flush=True)
    selected_models = set(args.model or ())
    for model, items in sorted(by_camera.items()):
        if selected_models and model not in selected_models:
            continue
        samples: list[PairSamples] = []
        for raw, jpg in items[: max(1, args.max_pairs_per_camera)]:
            started = time.time()
            try:
                sample = sample_pair(raw, jpg, max(32, args.fit_px))
                if sample is None:
                    continue
                samples.append(sample)
                print(
                    f"  pair ok ({len(samples)}) {time.time() - started:.1f}s {Path(raw).name}",
                    flush=True,
                )
            except Exception as error:
                print(f"  pair fail: {Path(raw).name}: {error}", flush=True)
        write_profile(
            model,
            samples,
            max(32, args.fit_px),
            args.output_dir,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
