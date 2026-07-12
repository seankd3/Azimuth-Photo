"""§30.3 corpus acceptance against Adobe-rendered embedded DNG previews.

The smoke suite skips this intentionally expensive, corpus-dependent gate. Run:
    PHOTOARCHIVE_RUN_DNG_ACCEPTANCE=1 .venv/bin/python -m pytest -q -s test_dng_acceptance.py
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageOps

from features.develop import adobe_profiles, dng_pipeline, pipeline, rawproc


PROD_DB = Path("/home/sean/Projects/photo-archive/web/photoarchive.db")
EVIDENCE_DIR = Path("/tmp/dev14e")
SAMPLE_SIZE = 40
PREVIEW_EDGE = 512


def _stratified_dng_sample(limit: int = SAMPLE_SIZE) -> list[dict]:
    connection = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            WITH candidates AS (
                SELECT i.camera_model, i.filepath, d.origin, d.settings,
                       row_number() OVER (PARTITION BY i.camera_model ORDER BY i.id) AS model_rank
                  FROM images i
                  LEFT JOIN develop_settings d ON d.image_id = i.id
                 WHERE lower(i.file_ext) = '.dng'
                   AND i.missing_at IS NULL
                   AND coalesce(d.origin, '') != 'user'
                   AND coalesce(i.camera_model, '') != ''
            )
            SELECT camera_model, filepath, origin, settings
              FROM candidates
             WHERE model_rank <= 12
             ORDER BY camera_model, model_rank
            """
        ).fetchall()
    finally:
        connection.close()
    by_model: dict[str, list[dict]] = defaultdict(list)
    for model, path, origin, settings_json in rows:
        # §30.3 compares the profile pipeline against an Adobe-rendered preview;
        # native DNGs without embedded Adobe profile tags are not valid inputs.
        if Path(path).is_file() and adobe_profiles.extract_embedded_profile(path) is not None:
            imported = str(origin or "").casefold() in {"xmp", "imported"}
            try:
                settings = json.loads(settings_json or "{}") if imported else {}
            except (TypeError, ValueError):
                settings = {}
            if not isinstance(settings, dict):
                settings = {}
            by_model[str(model)].append({
                "model": str(model),
                "path": str(path),
                "origin": str(origin or "default"),
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
    draw.text((8, 9), f"Photo Archive | {label}", fill="white")
    draw.text((ours.width + 8, 9), "Embedded Adobe preview", fill="white")
    canvas.save(path, quality=92)


@pytest.mark.skipif(
    os.environ.get("PHOTOARCHIVE_RUN_DNG_ACCEPTANCE") != "1",
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
        results.append({**sample_row, "l": l_delta, "ab": ab_delta, "ours": ours, "adobe": adobe, "profile": profile})

    assert not missing, "Adobe profile coverage missing: " + ", ".join(f"{model}: {path}" for model, path in missing)
    assert len(results) == SAMPLE_SIZE
    per_model: dict[str, list[dict]] = defaultdict(list)
    for result in results:
        per_model[result["model"]].append(result)
    print("\nDNG acceptance by model")
    print("model | n | mean |L| | mean ab | worst |L|")
    for model in sorted(per_model):
        rows = per_model[model]
        imported = sum(row["origin"].casefold() in {"xmp", "imported"} for row in rows)
        print(f"{model} | {len(rows)} | {np.mean([r['l'] for r in rows]):.5f} | {np.mean([r['ab'] for r in rows]):.5f} | {max(r['l'] for r in rows):.5f} | {imported} imported")

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    worst = sorted(results, key=lambda row: row["l"] + row["ab"], reverse=True)[:6]
    for index, row in enumerate(worst, 1):
        _side_by_side(
            EVIDENCE_DIR / f"accept_{index:02d}.jpg",
            row["ours"],
            row["adobe"],
            f"{row['model']}  L={row['l']:.4f} ab={row['ab']:.4f}",
        )

    mean_l = float(np.mean([row["l"] for row in results]))
    mean_ab = float(np.mean([row["ab"] for row in results]))
    print(f"ALL | {len(results)} | {mean_l:.5f} | {mean_ab:.5f}")
    assert mean_l < 0.035
    assert mean_ab < 0.025
