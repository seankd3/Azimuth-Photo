"""§30.3 corpus acceptance against Adobe-rendered embedded DNG previews.

The smoke suite skips this intentionally expensive, corpus-dependent gate. Run:
    AZIMUTH_RUN_DNG_ACCEPTANCE=1 .venv/bin/python -m pytest -q -s test_dng_acceptance.py
"""

from __future__ import annotations


import io
import os
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageOps

if os.environ.get("AZIMUTH_RUN_DNG_ACCEPTANCE") != "1":
    # Skip before the heavy imports: importer pulls scanner/image_headers,
    # which binds libc at import time and cannot even collect on Windows.
    pytest.skip("AZIMUTH_RUN_DNG_ACCEPTANCE not set", allow_module_level=True)

from features.develop import adobe_profiles, dng_pipeline, importer, lossydng, pipeline, rawproc, xmp_write

pytestmark = pytest.mark.slow

if "AZIMUTH_ACCEPTANCE_DB" not in os.environ:
    pytest.skip("AZIMUTH_ACCEPTANCE_DB must point to a copied acceptance catalog", allow_module_level=True)
PROD_DB = Path(os.environ["AZIMUTH_ACCEPTANCE_DB"])
EVIDENCE_DIR = Path(
    os.environ.get(
        "AZIMUTH_DNG_EVIDENCE_DIR",
        str(Path(tempfile.gettempdir()) / "azimuth-dng-acceptance"),
    )
)
SAMPLE_SIZE = 40
PREVIEW_EDGE = 512
MEAN_L_LIMIT = 0.035
MEAN_AB_LIMIT = 0.025
MODEL_L_LIMIT = 0.08
_TONE_KEYS = {
    "Exposure2012", "Contrast2012", "Highlights2012", "Shadows2012",
    "Whites2012", "Blacks2012", "ToneCurvePV2012", "ToneCurvePV2012Red",
    "ToneCurvePV2012Green", "ToneCurvePV2012Blue",
}
_LINEAR_CURVE = ("0, 0", "255, 255")


def _has_material_tone_edit(value: object) -> bool:
    """Inspect top-level and nested Look/Preset resources from embedded XMP."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _TONE_KEYS:
                if isinstance(child, (int, float)) and abs(float(child)) > 0.01:
                    return True
                if isinstance(child, list) and tuple(child) != _LINEAR_CURVE:
                    return True
            if _has_material_tone_edit(child):
                return True
    elif isinstance(value, list):
        return any(_has_material_tone_edit(child) for child in value)
    return False


def _embedded_settings(path: str) -> tuple[dict, bool]:
    packet = importer.read_embedded_xmp(path)
    if packet is None:
        return {}, True
    try:
        settings = xmp_write.parse_xmp_text(packet)
    except (TypeError, ValueError):
        return {}, False
    if not isinstance(settings, dict):
        return {}, False
    return settings, not _has_material_tone_edit(settings)


def _stratified_dng_sample(limit: int = SAMPLE_SIZE) -> list[dict]:
    connection = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            WITH candidates AS (
                SELECT i.camera_model, i.filepath,
                       row_number() OVER (PARTITION BY i.camera_model ORDER BY i.id) AS model_rank
                  FROM images i
                 WHERE lower(i.file_ext) = '.dng'
                   AND i.missing_at IS NULL
                   AND coalesce(i.camera_model, '') != ''
            )
            SELECT camera_model, filepath
              FROM candidates
             WHERE model_rank <= 12
             ORDER BY camera_model, model_rank
            """
        ).fetchall()
    finally:
        connection.close()
    by_model: dict[str, list[dict]] = defaultdict(list)
    for model, path in rows:
        # §30.3 compares the profile pipeline against an Adobe-rendered preview;
        # native DNGs without embedded Adobe profile tags are not valid inputs.
        if Path(path).is_file() and adobe_profiles.extract_embedded_profile(path) is not None:
            settings, clean = _embedded_settings(str(path))
            by_model[str(model)].append({
                "model": str(model),
                "path": str(path),
                "origin": "embedded-xmp",
                "clean": clean,
                "settings": settings,
            })
    selected: list[dict] = []
    depth = 0
    while len(selected) < limit:
        added = False
        for model in sorted(by_model):
            if depth < len(by_model[model]):
                selected.append(by_model[model][depth])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        depth += 1
    return selected


def _embedded_preview(path: str) -> Image.Image:
    import rawpy

    with rawpy.imread(path) as raw:
        thumb = raw.extract_thumb()
        flip = int(raw.sizes.flip)
    if thumb.format == rawpy.ThumbFormat.JPEG:
        image = Image.open(io.BytesIO(thumb.data))
        image.load()
    else:
        image = Image.fromarray(np.asarray(thumb.data, dtype=np.uint8))
    exif_orientation = image.getexif().get(274)
    image = ImageOps.exif_transpose(image).convert("RGB")
    # extract_thumb returns stored pixels; postprocess (our base) honors LibRaw
    # flip. Normalize the Adobe side to the same display orientation.
    if exif_orientation:
        return image
    if flip == 3:
        image = image.transpose(Image.Transpose.ROTATE_180)
    elif flip == 5:
        image = image.transpose(Image.Transpose.ROTATE_90)
    elif flip == 6:
        image = image.transpose(Image.Transpose.ROTATE_270)
    return image


def _render_default(path: str, settings: dict) -> tuple[Image.Image, dict]:
    rgb16, meta = rawproc.decode_base(path)
    rgb16 = rawproc._resize_linear_uint16(rgb16, max_edge=PREVIEW_EDGE)
    linear = rgb16.astype(np.float32) / np.float32(65535.0)
    profile = dng_pipeline.resolve_adobe_profile({**meta, "filepath": path})
    if profile is None:
        raise LookupError(f"no Adobe profile for {meta.get('camera_model') or path}")
    as_shot = meta.get("as_shot") or {}
    rendered = pipeline.apply_pipeline(
        linear,
        settings,
        asshot_temperature=as_shot.get("temperature"),
        asshot_tint=as_shot.get("tint"),
        color_profile={**meta, "filepath": path, "adobe_profile": profile},
    )
    pixels = np.asarray(np.clip(rendered * 255.0 + 0.5, 0, 255), dtype=np.uint8)
    return Image.fromarray(pixels, "RGB"), profile


def _matched_arrays(ours: Image.Image, adobe: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    width, height = ours.size
    adobe = ImageOps.fit(adobe, (width, height), method=Image.Resampling.LANCZOS)
    ours_srgb = np.asarray(ours, dtype=np.float32) / 255.0
    adobe_srgb = np.asarray(adobe, dtype=np.float32) / 255.0
    ours_lab = pipeline.linear_to_oklab(pipeline.srgb_to_linear(ours_srgb))
    adobe_lab = pipeline.linear_to_oklab(pipeline.srgb_to_linear(adobe_srgb))
    return ours_lab, adobe_lab


def _side_by_side(path: Path, ours: Image.Image, adobe: Image.Image, label: str) -> None:
    adobe = ImageOps.fit(adobe, ours.size, method=Image.Resampling.LANCZOS)
    header = 34
    canvas = Image.new("RGB", (ours.width * 2, ours.height + header), "#17191d")
    canvas.paste(ours, (0, header))
    canvas.paste(adobe, (ours.width, header))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 9), f"Azimuth Photo | {label}", fill="white")
    draw.text((ours.width + 8, 9), "Embedded Adobe preview", fill="white")
    canvas.save(path, quality=92)


def test_baseline_exposure_includes_active_profile_offset():
    source = np.ones((1, 1, 3), dtype=np.float32)
    profile = {"baseline_exposure": 0.5, "baseline_exposure_offset": -0.25}
    result = dng_pipeline.apply_baseline_exposure(source, profile)
    np.testing.assert_allclose(result, np.exp2(0.25), atol=1e-7)


def test_clean_split_finds_nested_look_tone_edits():
    assert _has_material_tone_edit({"Look": {"Parameters": {"Highlights2012": -10}}})
    assert not _has_material_tone_edit({"ToneCurvePV2012": ["0, 0", "255, 255"]})


@pytest.mark.skipif(
    os.environ.get("AZIMUTH_RUN_DNG_ACCEPTANCE") != "1",
    reason="40-DNG Adobe-preview acceptance is opt-in and excluded from smoke",
)
def test_40_dng_adobe_preview_acceptance():
    sample = _stratified_dng_sample()
    assert len(sample) == SAMPLE_SIZE, f"only {len(sample)} eligible DNGs were available"
    results = []
    missing = []
    for sample_row in sample:
        model = sample_row["model"]
        path = sample_row["path"]
        try:
            ours, profile = _render_default(path, sample_row["settings"])
        except LookupError:
            missing.append((model, path))
            continue
        adobe = _embedded_preview(path)
        ours_lab, adobe_lab = _matched_arrays(ours, adobe)
        l_delta = float(np.mean(np.abs(ours_lab[..., 0] - adobe_lab[..., 0])))
        ab_delta = float(np.mean(np.linalg.norm(ours_lab[..., 1:3] - adobe_lab[..., 1:3], axis=-1)))
        adobe_fit = ImageOps.fit(adobe, ours.size, method=Image.Resampling.LANCZOS)
        ours_linear = pipeline.srgb_to_linear(np.asarray(ours, dtype=np.float32) / 255.0)
        adobe_linear = pipeline.srgb_to_linear(np.asarray(adobe_fit, dtype=np.float32) / 255.0)
        luma = np.asarray([0.2126, 0.7152, 0.0722], dtype=np.float32)
        results.append({
            **sample_row,
            "l": l_delta,
            "signed_l": float(np.mean(ours_lab[..., 0] - adobe_lab[..., 0])),
            "ab": ab_delta,
            "ours_luma": float(np.mean(ours_linear @ luma)),
            "adobe_luma": float(np.mean(adobe_linear @ luma)),
            "lossy": lossydng.is_lossy_dng(path),
            "pixel": "pixel" in model.casefold(),
            "ours": ours,
            "adobe": adobe,
            "profile": profile,
        })

    assert not missing, "Adobe profile coverage missing: " + ", ".join(f"{model}: {path}" for model, path in missing)
    assert len(results) == SAMPLE_SIZE
    print("\nDNG acceptance by model and honest embedded-XMP split")
    print("split | model | n | mean |L| | signed L | mean ab | luma gain | lossy")
    for split_name, split_rows in (
        ("clean", [row for row in results if row["clean"] and not row["pixel"]]),
        ("edited", [row for row in results if not row["clean"] and not row["pixel"]]),
        ("pixel-ungated", [row for row in results if row["pixel"]]),
    ):
        per_model: dict[str, list[dict]] = defaultdict(list)
        for row in split_rows:
            per_model[row["model"]].append(row)
        for model in sorted(per_model):
            rows = per_model[model]
            gain = sum(row["ours_luma"] for row in rows) / sum(row["adobe_luma"] for row in rows)
            print(
                f"{split_name} | {model} | {len(rows)} | "
                f"{np.mean([r['l'] for r in rows]):.5f} | {np.mean([r['signed_l'] for r in rows]):+.5f} | "
                f"{np.mean([r['ab'] for r in rows]):.5f} | {gain:.4f} | {sum(r['lossy'] for r in rows)}"
            )

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    worst = sorted(results, key=lambda row: row["l"] + row["ab"], reverse=True)[:6]
    for index, row in enumerate(worst, 1):
        _side_by_side(
            EVIDENCE_DIR / f"accept_{index:02d}.jpg",
            row["ours"],
            row["adobe"],
            f"{row['model']}  L={row['l']:.4f} ab={row['ab']:.4f}",
        )

    gated = [row for row in results if row["clean"] and not row["pixel"]]
    assert gated, "no clean non-Pixel DNGs were available for the acceptance gate"
    mean_l = float(np.mean([row["l"] for row in gated]))
    mean_ab = float(np.mean([row["ab"] for row in gated]))
    print(f"GATED CLEAN NON-PIXEL | {len(gated)} | {mean_l:.5f} | {mean_ab:.5f}")
    per_model: dict[str, list[dict]] = defaultdict(list)
    for row in gated:
        per_model[row["model"]].append(row)
    model_l = {
        model: float(np.mean([row["l"] for row in rows]))
        for model, rows in per_model.items()
    }
    failed_models = {model: value for model, value in model_l.items() if value > MODEL_L_LIMIT}
    lossy_gated = [row for row in gated if row["lossy"]]
    if lossy_gated:
        lossy_gain = sum(row["ours_luma"] for row in lossy_gated) / sum(row["adobe_luma"] for row in lossy_gated)
        print(f"LOSSY CLEAN GAIN | {len(lossy_gated)} | {lossy_gain:.5f}")
        assert abs(lossy_gain - 1.0) <= 0.02, f"clean lossy luma gain {lossy_gain:.5f} outside 1.0 +/- 0.02"
    assert mean_l <= MEAN_L_LIMIT, f"clean non-Pixel mean |L| {mean_l:.5f} > {MEAN_L_LIMIT:.3f}"
    assert mean_ab <= MEAN_AB_LIMIT, f"clean non-Pixel mean ab {mean_ab:.5f} > {MEAN_AB_LIMIT:.3f}"
    assert not failed_models, f"per-model mean |L| gate failed: {failed_models}"
