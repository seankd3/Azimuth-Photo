"""Fit per-camera rendering profiles from RAW <-> Lightroom-export pairs.

Method: decode our linear base (small pyramid level), apply the develop pipeline
with the generic base profile DISABLED at the pair's own XMP settings, apply the
crop so pixels align with the LR export, then fit (a) a monotone 1D tone LUT and
(b) an OKLab hue x chroma delta table minimizing the residual to the LR render.
Output: web/features/develop/profiles/<model-slug>.json
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, "/home/sean/Projects/pa-develop/web")
os.chdir("/home/sean/Projects/pa-develop/web")
os.environ.setdefault("PHOTOARCHIVE_SMOKE_MODE", "1")

import numpy as np
from PIL import Image

from features.develop import pipeline as pipe
from features.develop import ops_constants as C
from features.develop.lossydng import decode_lossy_dng, is_lossy_dng
from features.develop.importer import parse_xmp_text, read_embedded_xmp
from features.develop.rawproc import estimate_as_shot_white_balance

EXPORT_DIRS = [
    "/mnt/expansion/Photos/Exported Edits/2024/All Selected",
    "/mnt/expansion/Photos/Exported Edits/2025",
]
RAWS = "/mnt/expansion/Photos/RAWS"
EXIFTOOL = "/usr/bin/vendor_perl/exiftool"
MAX_PAIRS_PER_CAM = 48
FIT_PX = 384

def exif(path, tags):
    out = subprocess.run([EXIFTOOL, "-j", "-n"] + [f"-{t}" for t in tags] + [str(path)],
                         capture_output=True, text=True, timeout=120)
    try:
        return json.loads(out.stdout)[0]
    except Exception:
        return {}

def gather_pairs():
    pairs = []
    for d in EXPORT_DIRS:
        base = Path(d)
        if not base.is_dir():
            continue
        out = subprocess.run([EXIFTOOL, "-j", "-n", "-r", "-ext", "jpg",
                              "-DateTimeOriginal", "-Model", str(base)],
                             capture_output=True, text=True, timeout=1800)
        try:
            rows = json.loads(out.stdout)
        except Exception:
            rows = []
        for meta in rows:
            jpg = meta.get("SourceFile")
            dt, model = meta.get("DateTimeOriginal"), meta.get("Model")
            if not dt or not model:
                continue
            m = re.match(r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})", str(dt))
            if not m:
                continue
            y, mo, da, h, mi, s = m.groups()
            raw = Path(RAWS) / y / f"{y}-{mo}-{da}" / f"{y}{mo}{da}-{h}{mi}{s}.dng"
            if raw.is_file():
                pairs.append((str(raw), str(jpg), model))
    return pairs

def render_ours(raw_path, fit_px):
    arr, meta = decode_lossy_dng(raw_path, max_px=fit_px * 2)
    lin = arr.astype(np.float32) / 65535.0
    packet = read_embedded_xmp(raw_path)
    settings = parse_xmp_text(packet) if packet else {}
    as_shot = estimate_as_shot_white_balance(
        meta.get("cam_mul"), [], as_shot_neutral=meta.get("as_shot_neutral"),
        color_matrix=meta.get("color_matrix1"), color_matrix2=meta.get("color_matrix2"))
    color = {
        "as_shot_neutral": meta.get("as_shot_neutral"),
        "forward_matrix": meta.get("forward_matrix"),
        "color_matrix1": meta.get("color_matrix1"),
        "color_matrix2": meta.get("color_matrix2"),
    }
    out = pipe.apply_pipeline(lin, settings, asshot_temperature=as_shot.get("temperature"),
                              asshot_tint=as_shot.get("tint"), color_profile=color)
    # apply crop for pixel alignment (geometry only, no angle in v1 fit — skip angled crops)
    if abs(float(settings.get("CropAngle", 0) or 0)) > 0.01:
        return None, None
    h, w = out.shape[:2]
    l = int(float(settings.get("CropLeft", 0) or 0) * w)
    r = int(float(settings.get("CropRight", 1) or 1) * w)
    t = int(float(settings.get("CropTop", 0) or 0) * h)
    b = int(float(settings.get("CropBottom", 1) or 1) * h)
    out = out[t:max(t + 1, b), l:max(l + 1, r)]
    if bool(settings.get("ConvertToGrayscale")):
        return None, None  # B&W pairs don't inform color fitting
    return out, settings

def srgb_to_oklab(rgb):
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    M1 = np.array(C.OKLAB_M1); M2 = np.array(C.OKLAB_M2)
    lms = lin @ M1.T
    lms = np.cbrt(np.maximum(lms, 0))
    return lms @ M2.T

def main():
    identity = tuple((float(x), float(x)) for x in (0, 32, 64, 128, 192, 255))
    C.BASE_PROFILE_POINTS = identity
    C.BASE_PROFILE_SAT = 1.0

    pairs = gather_pairs()
    print(f"pairs found: {len(pairs)}", flush=True)
    by_cam = {}
    for raw, jpg, model in pairs:
        by_cam.setdefault(model, []).append((raw, jpg))

    profiles_dir = Path("features/develop/profiles")
    profiles_dir.mkdir(exist_ok=True)

    for model, items in by_cam.items():
        xs_luma, ys_luma = [], []
        ab_ours, ab_lr, okl_ours = [], [], []
        used = 0
        for raw, jpg in items[:MAX_PAIRS_PER_CAM]:
            t0 = time.time()
            try:
                ours, settings = render_ours(raw, FIT_PX)
                if ours is None:
                    continue
                lr = np.asarray(Image.open(jpg).convert("RGB")).astype(np.float32) / 255.0
                oh, ow = ours.shape[:2]
                lr_im = Image.fromarray((lr * 255).astype(np.uint8)).resize((ow, oh))
                lr = np.asarray(lr_im).astype(np.float32) / 255.0
                x = ours.reshape(-1, 3); y = lr.reshape(-1, 3)
                corr = np.corrcoef(x[:, 1], y[:, 1])[0, 1]
                if not np.isfinite(corr) or corr < 0.85:
                    print(f"  skip (corr {corr:.2f}): {Path(raw).name}", flush=True)
                    continue
                sub = np.random.default_rng(1).choice(len(x), size=min(20000, len(x)), replace=False)
                xs_luma.append((x[sub] @ np.float32([0.2126, 0.7152, 0.0722])))
                ys_luma.append((y[sub] @ np.float32([0.2126, 0.7152, 0.0722])))
                la, lb = srgb_to_oklab(x[sub]), srgb_to_oklab(y[sub])
                okl_ours.append(la)
                ab_ours.append(la[:, 1:]); ab_lr.append(lb[:, 1:])
                used += 1
                print(f"  pair ok ({used}) corr={corr:.3f} {time.time()-t0:.1f}s {Path(raw).name}", flush=True)
            except Exception as e:
                print(f"  pair fail: {Path(raw).name}: {e}", flush=True)
        if used < 6:
            print(f"{model}: only {used} usable pairs — skipping profile", flush=True)
            continue
        X = np.concatenate(xs_luma); Y = np.concatenate(ys_luma)
        # (a) monotone tone LUT, 16 nodes
        nodes = np.linspace(0, 1, 16)
        tone = []
        for i in range(16):
            lo = nodes[i] - 0.033; hi = nodes[i] + 0.033
            m = (X >= lo) & (X < hi)
            tone.append(float(np.median(Y[m])) if m.sum() > 400 else None)
        # fill gaps by interpolation over known nodes
        known = [(n, v) for n, v in zip(nodes, tone) if v is not None]
        kx = np.array([k for k, _ in known]); kv = np.array([v for _, v in known])
        tone = np.interp(nodes, kx, kv)
        tone[0] = min(tone[0], 0.004); tone[-1] = max(tone[-1], 0.996)
        tone = np.maximum.accumulate(np.clip(tone, 0, 1))
        # (b) hue x chroma delta table 12x3 in OKLab
        LA = np.concatenate(okl_ours)
        AB_O = np.concatenate(ab_ours); AB_L = np.concatenate(ab_lr)
        hue = np.arctan2(AB_O[:, 1], AB_O[:, 0])
        chroma = np.hypot(AB_O[:, 0], AB_O[:, 1])
        h_bins = np.linspace(-np.pi, np.pi, 13)
        c_edges = [0.02, 0.06, 0.12, 1.0]
        table = np.zeros((12, 3, 2), dtype=float)
        for i in range(12):
            for j in range(3):
                m = ((hue >= h_bins[i]) & (hue < h_bins[i + 1])
                     & (chroma >= (0.0 if j == 0 else c_edges[j - 1])) & (chroma < c_edges[j]))
                if m.sum() > 500:
                    table[i, j, 0] = float(np.median(AB_L[m][:, 0] - AB_O[m][:, 0]))
                    table[i, j, 1] = float(np.median(AB_L[m][:, 1] - AB_O[m][:, 1]))
        # residuals
        tone_resid = float(np.median(np.abs(np.interp(X, nodes, tone) - Y)))
        slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
        out = {
            "model": model, "pairs": used, "fit_px": FIT_PX,
            "tone_nodes": [float(n) for n in nodes], "tone_values": [float(v) for v in tone],
            "oklab_ab_delta": table.tolist(), "chroma_edges": c_edges,
            "residual_tone_medabs": tone_resid,
        }
        path = profiles_dir / f"{slug}.json"
        path.write_text(json.dumps(out, indent=1))
        print(f"{model}: profile written -> {path} (pairs={used}, tone medabs {tone_resid:.4f})", flush=True)

if __name__ == "__main__":
    main()
