# photoArchive Develop Module — Architecture Spec (v1, frozen)

Branch: `develop`, worktree `/home/sean/Projects/pa-develop`. Goal: a Lightroom-Classic-class
non-destructive RAW develop module inside photoArchive. This spec is the single source of
truth; the WebGL renderer and the Python export renderer MUST implement the same math.
Reference for raw-handling ideas: `/home/sean/Projects/darktable-ref` (read, never copy GPL code verbatim —
learn the approach, write original code).

## 0. Assets & constraints
- RAWs: `/mnt/expansion/Photos/RAWS/<year>/<YYYY-MM-DD>/...` — 82k DNG, 7k CR3, 1.2k CR2.
- 59k `.xmp` sidecars beside the raws with full `crs:*` Lightroom develop settings.
- Root disk 92% full → ALL new caches go under `/mnt/expansion/PhotoArchiveCache/develop/`.
- Dev server: port 8022, `PHOTOARCHIVE_SMOKE_MODE=1` for tests as usual.
- rawpy + numpy are in `web/.venv`. `darktable-cli` exists system-wide (fallback/export experiments only).

## 1. Edit state — canonical JSON
One JSON object per image, stored verbatim with **crs key names** (import fidelity, zero mapping bugs).
Missing key = Lightroom default. Keys v1 renders (everything else is stored+preserved but not rendered —
keep unknown keys on round-trip):

- WB: `Temperature` (K), `Tint` (−150..150), `WhiteBalance` ("As Shot" | "Custom" | presets)
- Tone: `Exposure2012` (−5..+5), `Contrast2012`, `Highlights2012`, `Shadows2012`, `Whites2012`, `Blacks2012` (all −100..100)
- Presence: `Texture`, `Clarity2012`, `Dehaze`, `Vibrance`, `Saturation` (−100..100)
- Curves: `ToneCurvePV2012`, `ToneCurvePV2012Red/Green/Blue` — arrays of "x, y" strings 0..255 (LR format)
- HSL: `HueAdjustment{Red,Orange,Yellow,Green,Aqua,Blue,Purple,Magenta}`, `SaturationAdjustment{...}`, `LuminanceAdjustment{...}` (−100..100)
- B&W: `ConvertToGrayscale` ("True"/"False"), `GrayMixer{Red..Magenta}`
- Detail: `Sharpness` (0..150), `SharpenRadius` (0.5..3), `SharpenDetail`, `SharpenEdgeMasking`
- Effects: `PostCropVignetteAmount` (−100..100), `PostCropVignetteMidpoint`, `PostCropVignetteFeather`, `PostCropVignetteRoundness`, `GrainAmount`, `GrainSize`, `GrainFrequency`
- Geometry: `CropLeft/Top/Right/Bottom` (0..1), `CropAngle` (deg, CCW+), `Orientation` (EXIF 1/3/6/8)

Values arrive from XMP as strings ("+0.50", "True") — normalize to numbers/bools at parse time,
serialize back Adobe-style only in XMP export (later phase; not v1).

**v1 non-goals (be explicit in code comments + UI):** local adjustments/masks, lens corrections,
chromatic aberration, noise reduction, color grading wheels (stored, not rendered), spot removal,
pano/HDR. These are Phase 2+.

## 2. DB — schema v21 (develop branch only)
```sql
CREATE TABLE IF NOT EXISTS develop_settings (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    settings TEXT NOT NULL DEFAULT '{}',      -- canonical JSON
    origin TEXT NOT NULL DEFAULT 'user',      -- 'xmp' | 'lrcat' | 'user'
    xmp_path TEXT, xmp_mtime REAL,            -- provenance for re-sync
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS develop_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    settings TEXT NOT NULL,
    label TEXT,                                -- 'Import from XMP', 'Exposure', ...
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_develop_history_image ON develop_history(image_id, id DESC);
```
`images` gains nothing; raw-ness is detected by extension. Migration bumps schema version with the
existing migration idiom in `web/data/schema.py` (read it first).

## 3. RAW decode + base preview service
Module `web/features/develop/rawproc.py`.
- `decode_base(path) -> (uint16 RGB array, meta)`: rawpy postprocess with
  `use_camera_wb=True, output_bps=16, no_auto_bright=True, gamma=(1,1), output_color=rawpy.ColorSpace.sRGB,
  highlight_mode=rawpy.HighlightMode.Blend, half_size=True` → **linear** sRGB-primary data.
  Resize longest edge → 2048 (LANCZOS via PIL on 16-bit per channel — use numpy resize path if PIL 16-bit RGB is lossy: resize float32 then back).
- meta: as-shot temp/tint estimate. Derive from `raw.camera_whitebalance` R/B ratio → correlated
  temp via standard mired approximation; if unavailable default 5500/0. Also `daylight_whitebalance` for scaling.
- Base cache: `/mnt/expansion/PhotoArchiveCache/develop/base/{image_id}.bin.gz` — gzipped
  little-endian uint16 interleaved RGB + 16-byte header (magic 'PABASE1\0', u32 width, u32 height) and a
  sibling `{image_id}.json` (meta: as-shot WB, dims). Plus `{image_id}.jpg` — fast 8-bit sRGB preview
  (quality 88, gamma-encoded from the same data with a 2.2-ish sRGB curve) for instant paint while the .bin streams.
- Endpoints (`web/features/develop/routes.py`):
  - `GET /api/develop/{image_id}/base.bin` (Content-Encoding: gzip, immutable cache headers)
  - `GET /api/develop/{image_id}/base.jpg`
  - `GET /api/develop/{image_id}` → `{settings, origin, meta, history: [last 40]}`
  - `PUT /api/develop/{image_id}` body `{settings, label?}` → saves + appends history (debounced client-side; server just writes)
  - `POST /api/develop/{image_id}/reset` → settings back to origin XMP snapshot (or {})
  - `POST /api/develop/{image_id}/export` body `{format:'jpeg'|'tiff16', quality, max_px?}` →
    full-res render via the Python pipeline (§5), file written to
    `/mnt/expansion/PhotoArchiveCache/develop/exports/`, response streams the file.
- Decode of first-hit is slow (seconds): base generation happens on-demand with a small in-process
  LRU + single-flight lock, and `POST /api/develop/pregen` accepts a list of image_ids for warm-ahead
  (filmstrip neighbors). No new worker daemon in v1.

## 4. The op pipeline (SHARED MATH — implement identically in GLSL and numpy)
Input: linear RGB floats 0..~4 (camera WB applied at decode). `s(v) = v/100` for slider v.
All constants live in ONE place per impl: `web/static/js/desktop/develop/ops_constants.js` and
`web/features/develop/ops_constants.py` — SAME NAMES, SAME VALUES (write a parity test that greps both).

Stage order (linear domain):
1. **WB delta**: `dm = 1e6/T_asshot − 1e6/T_user`; `r *= exp2(dm·K_TEMP)`, `b *= exp2(−dm·K_TEMP)`;
   `g *= exp2(−Tint·K_TINT)`. K_TEMP = 0.0007, K_TINT = 0.0035. WhiteBalance=="As Shot" or missing Temperature → skip.
2. **Exposure**: `rgb *= exp2(Exposure2012)`.
3. **Region tone map** on luminance `Y = dot(rgb, [0.2126,0.7152,0.0722])`, ratio-preserving:
   `t = clamp(Y,0,1)^(1/2.2)` (values >1 pass through the same gain formula, fine).
   Weights: `w_h = smoothstep(0.45,1,t)`, `w_s = 1−smoothstep(0,0.55,t)`,
   `w_w = smoothstep(0.75,1,t)`, `w_b = 1−smoothstep(0,0.25,t)`.
   `t2 = t + 0.28·(s(Highlights)·w_h·(1−t) + s(Shadows)·w_s·(1−t)·t·2) + 0.20·s(Whites)·w_w + 0.16·s(Blacks)·w_b`
   (note Highlights uses (1−t) so −100 recovers without gray mush; Shadows lifts mid-shadows not black point).
   **Contrast**: `t3 = 0.5 + (t2−0.5)·(1+0.85·s(Contrast2012))`, then soft-clamp both ends:
   `t3 = t3<0 ? 0 : (t3>1 ? 1+ (t3-1)/(1+4*(t3-1)) : t3)` (numpy equivalent formula).
   Gain: `g = (t3^2.2) / max(Y, 1e-6)`; `rgb *= g`.
4. **Dehaze** (approx, linear): `d = s(Dehaze)`; if d≠0: `rgb = (rgb − 0.12·d·airlight)/(1−0.12·d)` with
   airlight=(1,1,1), then saturation boost `+0.15·d` folded into stage 8's multiplier. Clamp ≥0.
5. → **gamma encode**: `c = linear_to_srgb(clamp(rgb,0,1))` (proper sRGB curve, both impls).
6. **Tone curve LUT**: main then R/G/B channel curves. Curve = monotone cubic (Fritsch–Carlson)
   through the control points /255; identity if absent. GL: 256×1 RGBA LUT texture built in JS;
   numpy: same LUT array — build the LUT in ONE JS function and ONE python function with identical algorithm.
7. **HSL 8-band + B&W**: convert c→HSL-ish (use HSV: hue h∈[0,360), sat S, val V).
   Band centers deg: R 0, O 30, Y 60, G 120, A 180, B 240, P 280, M 320. Band weight: raised cosine
   to adjacent centers (circular; e.g. between 240 and 280, hue 260 → 0.5/0.5). Neutral protect: `wn = smoothstep(0.04,0.18,S)`.
   `h += Σ w_i·wn·s(HueAdj_i)·30`; `S *= 1 + Σ w_i·wn·s(SatAdj_i)`; luminance: `V *= 1 + Σ w_i·wn·s(LumAdj_i)·0.6` (apply on V after sat).
   If ConvertToGrayscale: output luma `L = dot(rgb_before_hsl, lumw)·(1 + Σ w_i·s(GrayMixer_i)·0.8)` computed with the same band weights from the pre-desaturation hue — then rgb = L, skip HSL/vib/sat.
8. **Vibrance/Saturation** (still HSV): `S *= 1+s(Saturation)`; vibrance: `S += s(Vibrance)·(1−S)·S·1.8`; clamp 0..1; back to RGB.
9. **Clarity/Texture/Sharpen** (gamma luma, needs blurred fields §4b):
   `L = luma(c)`; `c += (L − blurL_large)·0.35·s(Clarity2012)·wm` with midtone mask `wm = 4·L·(1−L)` clamped 0..1 (per-pixel, add to all channels);
   texture: `c += (L − blurL_small)·0.30·s(Texture)`;
   sharpen: `c += (L − blurL_sharp)·(Sharpness/150)·0.9` where blurL_sharp uses σ=SharpenRadius px.
10. **Vignette**: `a = s(PostCropVignetteAmount)`; if a≠0: normalized radial dist ρ from crop center (post-crop coords, aspect-corrected with Roundness, Midpoint scales inner radius, Feather softness): `v = 1 + a·smoothstep(mid, mid+feather, ρ)·0.9`; multiply c (darken a<0, lighten a>0 with highlight-priority: mix toward screen blend).
11. **Grain**: `c += (hash(px,py)−0.5)·s(GrainAmount)·0.12` with cell size from GrainSize (hash a fixed integer seed; MUST be identical function in both impls — use a shared integer hash: `h = (x*374761393 + y*668265263) ^ seed; h = (h^(h>>13))*1274126177; frac = ((h>>8)&0xFFFF)/65535`).
12. Output 0..1 gamma sRGB. Crop/rotate/orientation are geometry (GL vertex transform / numpy slice+rotate at export), never pixel ops in the color pipeline.

### 4b. Blur fields
Three Gaussian blurs of gamma luma: σ_large = 0.02·min(w,h), σ_small = 0.004·min(w,h), σ_sharp = SharpenRadius px.
GL: separable two-pass into half-res framebuffers (large/small), full-res for sharpen; numpy: `scipy.ndimage.gaussian_filter` — scipy NOT in deps: implement separable gaussian with numpy convolve (kernel from the same σ formula; truncate 3σ). Downsampled-then-upsampled large blur is fine (matches GL half-res).

### 4c. Parity
`web/test_develop_parity.py`: build a 64×64 synthetic linear gradient+hue image in numpy, run the Python
pipeline with a fixed "torture" settings dict (every slider non-zero, curve with 3 points, HSL bands set),
assert golden checksum/values (store expected array stats, not full dump). The GL side gets a dev-only page hook
`window.__developRenderToPixels(settings)` used by a Playwright test to read back the canvas for the same
synthetic texture (uploaded via a debug endpoint serving the same synthetic .bin) and compares mean/percentile
deltas < 1.5/255. This test is the contract — if it's too strict for GPU float differences, loosen tolerance, never the math.

## 5. Python export renderer
`web/features/develop/render.py`: full-res decode (`half_size=False`), same pipeline in float32 numpy,
crop/angle/orientation applied, writes JPEG (quality param, sRGB) or 16-bit TIFF. Must stream progress-safe
(it can take ~10-20s for 45MP; run in a threadpool executor, endpoint holds until done — v1 acceptable).

## 6. Importer — XMP sidecars (+ lrcat when found)
`web/features/develop/importer.py` + CLI-ish endpoint `POST /api/develop/import/scan {root}`:
- Walk RAWS root; for each raw file (dng/cr3/cr2, case-insensitive): ensure the image exists in the
  library (reuse the existing scanner/source machinery — read `web/scanner.py` and sources model first;
  register a source rooted at the RAWS folder; raws enter `images` like any file; thumbnails: rawpy
  `extract_thumb` embedded JPEG → hand to existing thumbnail pipeline; if no embedded thumb, decode tiny via rawpy half_size + resize).
- Sidecar: same basename `.xmp` → parse with `xml.etree` (attributes on rdf:Description + child elements
  for curves/arrays — handle BOTH attribute and element forms, LR emits both). Extract ALL crs:* keys
  (store everything), plus xmp:Rating → images.rating-equivalent if the schema has one (check; else skip),
  dc:subject keywords → existing keyword/tag machinery if present, else skip silently.
- Write develop_settings origin='xmp' with xmp_path/mtime. Re-running scan updates only when mtime changed
  and origin!='user' (never clobber user edits — that's sacred).
- EXIF (camera, lens, ISO, shutter, aperture, date) comes from the existing metadata extraction if it
  handles raw containers; verify with exiftool fallback (`exiftool -j`) only if the existing path fails on DNG/CR3.
- Import must be resumable + idempotent; log progress every 500 files; expose `GET /api/develop/import/status`.
- lrcat (if a catalog file is found later): Phase-1.5 — collections + picks import; NOT in this lane.

## 7. UI — Develop view
New lens tab "Develop" (icon: sliders) + `D` opens the current/selected photo in Develop.
Files: `web/static/js/desktop/develop/{develop.js, gl.js, ops_constants.js, curve_lut.js, panels.js, histogram.js}`,
CSS appended to desktop.css ("Develop" section). Layout (full-canvas, LR idiom, existing tokens):
- Center: GL canvas (fit letterboxed; 1:1 zoom toggle on click/Z; spacebar temporary 1:1). 8-bit jpg placeholder
  paints first, swaps to GL when base.bin arrives (streamed fetch → Uint16Array → RGB16UI texture or
  float conversion; WebGL2 required, use `texStorage2D(RGB16F)` path: convert u16→f16/f32 in JS or upload as
  R16UI×3? Simplest robust: Float32Array upload to RGBA32F if memory allows (2048·1365·4·4 ≈ 45MB — acceptable), else RGBA16F).
- Right panel 320px, collapsible sections in LR order: Histogram (top, computed via downsampled readback
  every edit, RGB overlay + clipping triangles), Basic (WB temp/tint w/ As-Shot reset, Auto? — skip Auto v1),
  Tone (exposure→blacks), Presence (texture/clarity/dehaze/vibrance/saturation), Tone Curve (interactive
  spline editor with draggable points, channel selector), HSL (three tabs H/S/L, 8 sliders each),
  B&W toggle + Gray mixer (swaps HSL section), Detail (sharpening 3 sliders), Effects (vignette 4 + grain 3), Crop (§7b).
- Sliders: custom scrubby slider component — label left, value right (editable on click), drag anywhere on row,
  double-click = reset to default, shift-drag = fine. Sub-pixel smooth, 60fps — render loop only re-renders on
  dirty flag via rAF. data-tip on every control.
- Left: reuse the existing filmstrip along the bottom (like loupe) for prev/next (arrow keys), edits auto-save
  (debounced 400ms PUT) — switching photos never loses state. Undo/redo: Ctrl+Z/Ctrl+Shift+Z walks develop_history in-session.
- Before/After: `\` hold shows origin-XMP (or import-time) settings render; button too.
- Copy/Paste settings: Ctrl+Shift+C/V with a small checklist popover (v1: all-or-nothing OK).
- Reset button (bottom of panel): POST reset.
- Export button (bottom): quality dialog popover → POST export → download/toast.
- Only raws get Develop (v1): non-raw images show Develop tab disabled state with tooltip "RAW editing only (for now)".

### 7b. Crop tool
Overlay mode on the canvas: rule-of-thirds grid, drag handles + edges, angle slider ±45 with live grid,
aspect presets (Original/1:1/4:5/16:9/Free), straighten by dragging a line. Writes CropLeft/… + CropAngle.
GL applies crop+rotation in the vertex/UV transform live.

## 8. Performance budgets
- Slider drag → frame: < 8ms render on RTX-less iGPU-class (the XPS has RTX 3050 Ti but assume less); pipeline is one
  fragment pass + cheap blur passes only when Presence/Detail sliders are non-zero (skip passes when all zero).
- Base.bin fetch: show jpg within 150ms; bin swap silent.
- History writes: fire-and-forget, never block UI.

## 9. Lane boundaries (file ownership — no overlaps)
- Lane RAWPROC: `web/features/develop/{__init__,rawproc,routes}.py`, `web/data/schema.py` (v21 block),
  wiring in `web/core/wiring.py`/`app.py` (grep how features register), `web/test_develop_backend.py`.
- Lane IMPORT: `web/features/develop/importer.py`, importer routes appended into a SEPARATE router file
  `web/features/develop/import_routes.py`, `web/test_develop_import.py`. Touches scanner/sources READ-ONLY —
  if a scanner change is unavoidable, emit a "SCANNER PATCH" block in the report instead of editing.
- Lane RENDER: `web/features/develop/{render,ops_constants}.py` (+ pure `pipeline.py` shared with rawproc read-only), `web/test_develop_parity.py` (python half).
- Lane UI: everything under `web/static/js/desktop/develop/`, `web/templates/desktop.html` (Develop section + lens tab),
  desktop.css appended Develop section, `web/static/js/desktop/{app.js,keyboard.js,views.js}` minimal wiring —
  emit "KEYBOARD PATCH" if keyboard.js conflicts (orchestrator applies).
- Integration (orchestrator): merges, parity Playwright test, polish.

## 10. Definition of done (v1 tonight)
Import scan over 2024 RAWS subset runs clean; open a DNG in Develop; every §7 panel functional;
edits imported from XMP appear exactly as LR left them (spot-check known-edited photo); export JPEG
matches canvas within tolerance; suite green; screenshots captured. Honest notes on any approximation gaps.

## 11. Field notes (measured 2026-07-10, overrides §3 cache paths)
- rawpy 0.27 decode of a 38MB DNG: **1.2s warm**, but cold file read off /mnt/expansion is **17–45s**
  (HDD at ~2MB/s under caption/embedding worker contention). Decode cost is I/O, not CPU.
- Therefore: base cache (**.bin.gz + .jpg + .json**) lives on the root SSD at
  `/home/sean/.cache/photoarchive-develop/base/` with LRU eviction capped at 12GB (evict by atime/mtime,
  check on each write). Exports stay on `/mnt/expansion/PhotoArchiveCache/develop/exports/`.
- UI: first-open of an uncached raw takes ~20–60s — show an honest staged progress state
  ("Reading RAW from disk…" → "Developing preview…"), never a dead spinner. Filmstrip warm-ahead
  (pregen ±2 neighbors) is mandatory, fire it on every photo switch.
- `raw.extract_thumb()` returned BITMAP (tiny PPM) on these LR-converted DNGs — importer thumbnails must
  handle the bitmap form (encode to JPEG via PIL) and fall back to half-size decode when thumb is unusable.
- Sample as-shot WB multipliers: cam_wb [2.207, 1.0, 1.506, 0.0] — R/B gains relative to G; derive temp/tint
  estimate from the R/B ratio vs daylight_whitebalance as speced.

---

# PHASE 2 — Local adjustments (masks), AI masks, HDR, presets, camera profiles, lens corrections
Appended 2026-07-10. Same rules as v1: this spec is frozen; the GL and numpy implementations MUST share
math; constants live only in the twin ops_constants files; darktable (~/Projects/darktable-ref) is
reference-only, never copied.

## 12. Mask model (canonical, Adobe schema verbatim)
Settings JSON gains `MaskGroupBasedCorrections`: a list of *corrections*. Each correction:
- `CorrectionActive` (bool), `CorrectionAmount` (0..2, scales all its local settings), `CorrectionID`
- Local adjustment keys, Adobe names, fraction-scaled (−1..+1 unless noted): `LocalExposure2012` (EV/4? NO —
  Adobe stores EV·0.25? measured: LocalExposure2012 = EV × 0.25 is WRONG; it is EV × 1/4? — VERIFY against a
  known catalog value once and normalize; whatever the finding, STORE Adobe-native and convert in ONE shared
  helper `localToSlider()` with the mapping documented there): `LocalExposure2012, LocalContrast2012,
  LocalHighlights2012, LocalShadows2012, LocalWhites2012, LocalBlacks2012, LocalClarity2012, LocalDehaze,
  LocalTexture, LocalTemperature, LocalTint, LocalSaturation, LocalHue, LocalSharpness, LocalGrain` (v1 renders
  the first 13; Sharpness/Grain stored only).
- `CorrectionMasks`: list of masks combined per `MaskBlendMode` (0=add, 1=intersect via multiply) and
  `MaskInverted`, `MaskValue` (opacity 0..1). Mask kinds (`Masks[i]` entries or `What` field):
  - **Gradient** (linear): `ZeroX, ZeroY, FullX, FullY` (normalized coords; value 0 at Zero line → 1 at Full line,
    smoothstep between, constant beyond).
  - **CircularGradient** (radial): `Top,Left,Bottom,Right` (ellipse bbox), `Angle`, `Feather` (0..1),
    `Flipped`/inside-out via MaskInverted. Value 1 inside, feathered falloff to 0 at edge·(1+feather).
  - **Paint** (brush): `Dabs` list of "d x y" / "r radius" strings (normalized to the LONG edge? — Adobe dab
    coords are normalized to width for x and height for y; VERIFY against a known brush position on one photo),
    plus `Flow`, `CenterWeight`. Rasterize: stamp gaussian-soft circles (hardness from CenterWeight) at dab
    positions with radius r, accumulate with flow, clamp 0..1.
  - **RangeMask / luminance** (`CorrectionRangeMask` with `Type=1`): `LumRange` "lo/hiSoft lo hi hiSoft" style
    quad + `LuminanceDepthSampleInfo`; v1: smoothstep window on gamma luma with the 4 params.
  - **RangeMask / color** (`Type=2`): `ColorAmount`, sampled point colors → v1: gaussian distance in OKLab ab
    plane around sampled hue, width from ColorAmount.
  - **AI masks** (ours, non-Adobe): `{ "What": "Mask/Image", "MaskSubType": "1"(subject)|"2"(sky),
    "ReferencePoint": ..., "pa_cache_key": "<sha>" }` — the raster comes from the AI mask cache (§14). When an
    Adobe AI mask is imported (MaskSubType present without our cache key), regenerate with OUR segmenter lazily.

### 12b. Rendering contract (BOTH renderers)
1. After the GLOBAL pipeline stages up to and including HSL/vibrance (i.e. on gamma-domain sRGB), each active
   correction is applied sequentially: compute mask value m∈[0,1] per pixel (combine its CorrectionMasks:
   start 0, add-mode masks max-accumulate scaled by MaskValue, intersect-mode multiply; apply MaskInverted per
   mask; final m *= CorrectionAmount clamp 0..2).
2. Local adjustment application = run a REDUCED version of the global ops parameterized by the local values,
   blended by m: exposure (linear-domain exp2 → approximate in gamma domain via pow(2, ev·m)^(1/2.2) factor —
   define exactly: convert pixel to linear (srgb_to_linear), apply exp2(localEV·m·4)·? — SEE §12c scaling —
   then WB temp/tint via the SAME wb math at reduced strength m, region tones with the log-EV weights,
   clarity/texture reuse the existing blur fields, saturation via OKLab chroma. Implement as ONE shared
   function `applyLocalCorrection(rgbLinear, luma, blurs, local, m)` mirrored in GLSL/numpy.
3. Performance: GL evaluates masks from PRE-RASTERIZED single-channel textures (one per correction, quarter
   resolution, generated in JS — analytic gradients could be shader-evaluated but rasterizing keeps ONE code
   path with numpy). numpy rasterizes identically (shared constants; brush stamp = same gaussian).
   Cap: 16 corrections rendered (log a warning beyond; store unlimited).
4. Mask rasters cache-key on (image_id, correction hash, base dims).

### 12c. Local value scaling
Adobe local fractions: LocalExposure2012 ±4EV ↔ ±1.0 (so EV = value·4); most others ±100 ↔ ±1.0
(slider = value·100); LocalTemperature/Tint ±100 slider mapping to a REDUCED WB shift (temp delta = value·? —
use mired delta = value·30 mired as v1 approximation, documented constant LOCAL_WB_MIRED_SCALE). Put every
scale constant in ops_constants twins.

### 12d. UI (Masking panel — LR idiom, Notion-level polish)
Toolbar button + panel section "Masking": list of corrections (auto-named "Mask 1…", rename inline, eye toggle,
delete, duplicate), each expands to: mask chips (its CorrectionMasks with kind icon, add/subtract menu,
invert), "+ Add Mask" menu (Subject, Sky, Brush, Linear Gradient, Radial Gradient, Luminance Range, Color
Range), and the local sliders (Light: exposure→blacks; Color: temp/tint/saturation/hue; Effects:
clarity/texture/dehaze/sharpness). Canvas interactions: drag-to-create for gradients (live preview),
brush with [ ] size keys + soft cursor ring, O toggles red overlay (rubylith, 50% red where m>0), overlay
auto-shows while dragging. Esc exits mask mode. Every control has data-tip. Undo integrates with the existing
history stack (masks are part of settings JSON — free).

## 13. lrcat full develop settings (Lua table parser)
`Adobe_imageDevelopSettings.text` is a Lua table literal (`s = { ... }`). Write `parse_lua_table(text) → dict`
(tokenizer: strings, numbers, booleans, nested {}, ["quoted"] keys, bare keys, lists; no Lua exec). Import maps
it to canonical settings for pre-2024 images (catalog wins over embedded XMP when both exist and catalog
timestamp newer — actually: catalog wins unless origin='user'). Masks come along for free. Also capture
`Look` (profile name + its Parameters curve — apply Look.Parameters.ToneCurvePV2012 as base curve when
present) and lens profile fields (stored for §17).

## 14. AI masks service
`web/features/develop/ai_masks.py`: onnxruntime (CPU) with u2net (subject; use rembg's u2net.onnx, download
once to /mnt/expansion/PhotoArchiveCache/develop/models/) and skyseg (use u2net trained variant or
semantic-segmentation ONNX for sky; if no good off-the-shelf sky model, v1 sky = gradient+color heuristic:
luminance/position/blue prior refined by guided filter — honest about it in code).
Endpoints: `POST /api/develop/{id}/ai-mask {kind: "subject"|"sky"}` → runs on the base preview (jpg),
saves single-channel PNG at base resolution to the develop cache, returns `{cache_key, url}`;
`GET /api/develop/ai-mask/{cache_key}.png`. Client adds a mask entry referencing pa_cache_key; renderer loads
the PNG raster like any other mask texture. Idempotent by content hash.

## 15. Presets
Table `develop_presets(id, name, folder, settings TEXT, created_at)` (v22 migration). API: list/create
(from current image's settings minus geometry/WB? — LR asks; we store FULL and apply selectively)/apply/
delete/rename. Import: scan for LR preset .xmp files if present on expansion (report what's found, don't block).
UI: "Presets" section in the LEFT side of Develop (new slim panel): folders, hover = live preview on the GL
canvas (apply settings non-destructively in preview flag, revert on mouseout — cheap, it's just uniforms),
click = apply (history entry "Preset: name").

## 16. Camera profile fitting (eat-their-lunch color)
Offline fitting script `web/eval/fit_camera_profile.py`: for each camera model (R5, R7), gather RAW↔LR-export
pairs (capture-time matching like tonight), decode our linear base, run our pipeline WITHOUT base profile at
the pair's XMP settings, downsample both to ~128px pixel-matched sets, then fit:
(a) a 1D tone LUT (monotone, 16 control points) minimizing luma error,
(b) a hue×sat 2D delta table (6×3 nodes in OKLab hue×chroma, bilinear) minimizing ab error.
Output `web/features/develop/profiles/<model>.json`. Pipeline hook (§4 step 5.5): apply camera profile LUT +
hue/sat table (twins) when profile file exists for the image's camera; falls back to BASE_PROFILE_POINTS.
Report residual stats per camera honestly.

## 17. Lens corrections (after masks land)
lensfunpy (python) + GL uv-distortion twin. v1: distortion (poly3/ptlens from lensfun DB by camera+lens EXIF)
+ vignetting; honor `LensProfileEnable` imported from LR. GL applies inverse distortion in orientedUv();
numpy warps with the same polynomial (bilinear sample). CA later.

## 18. HDR merge
`web/features/develop/hdr.py`: detect brackets (same lens/focal, ≤2s apart, ≥3 frames, distinct
ExposureBiasValue/shutter). `POST /api/develop/hdr/merge {image_ids}`: decode linears (our decoders),
align (phase correlation on downsampled luma, translation-only v1), merge = radiance-weighted average in
linear (weights hat-function on exposure validity, normalized by relative EV from shutter·ISO·aperture),
write float32 EXR (imagecodecs) + a 16-bit "HDR DNG-like" base into the develop cache, register a new library
image (source: virtual "HDR Merges" folder path on expansion cache) whose base decode short-circuits to the
merged data, Develop works on it with extended headroom (values >1 allowed pre-tonemap; Highlights slider
recovers). Grid gets "Merge to HDR" in the selection context when a detected bracket is selected (v1: also
allow manual multi-select merge).

## 19. Definition of done, Phase 2 wave 1
Masks: create/edit/render all five manual kinds + AI subject; LR-imported masks from a 2023 catalog image
render recognizably (spot-check vs LR export where a pair exists); presets save/apply with live hover preview;
lrcat parser round-trips the sampled blob; HDR merges one real bracket; suite green; screenshots of masking UI,
before/after of an AI subject mask, HDR result.

---

# PHASE 3 — close remaining Lightroom parity gaps (appended 2026-07-10)

## 20. RAW <-> Edit version stacks (product design, decided)
Ratified roadmap item: "link Exported Edits files to their originals, so the loupe can toggle
edited/original and duplicates never pit an edit against its own raw."
- New stack kind "version" (extends STACK_KINDS): members = one RAW + its edited exports.
- Matching (builder `build_version_groups`): (a) exact basename tail match (raw stem == export stem);
  (b) capture-time + camera-model match (exports are renamed like SKD-Starbase-…): same
  DateTimeOriginal second + same Model ⇒ version pair (EXIF via images metadata; exiftool fallback).
  Idempotent, incremental, auto=1.
- Representative = the newest EDIT (the finished photo wins the grid); RAW is a member. Grid badge for
  version stacks shows "RAW+N" (distinct from burst count pill).
- Loupe/Develop: version stacks expose members in the existing stack strip; pressing V (or the strip)
  toggles edit <-> raw; "Edit RAW" affordance opens Develop on the raw member.
- Develop export endpoint: when exporting a render of a RAW that belongs to (or gains) a version stack,
  the exported file (if registered into the library later) joins the same stack — v1: exports are
  download-only, so instead add "Save to library" option on export that registers the JPEG under
  /mnt/expansion/PhotoArchiveCache/develop/library-exports/ as an image AND stacks it with its raw.
- Refine/duplicates: version-stack members are excluded from pairing against each other (same idiom
  as existing stack exclusions — verify how burst stacks are excluded and mirror it).
- Flags: existing stack flag semantics apply unchanged.

## 21. Looks rendering + trusted tone refit
Imported settings may carry Look (name + Parameters incl. ToneCurvePV2012, Clarity2012, ConvertToGrayscale,
Amount). Render: fold Look.Parameters.ToneCurvePV2012 into the per-image base LUT (compose AFTER camera
profile/base curve: LUT_total = LookCurve ∘ BaseOrProfileCurve), scale by Look.Amount; apply Look's simple
params (Clarity/grayscale) by merging them into effective settings AT READ TIME (never mutating stored
settings). LookTable 3D LUTs are unavailable (catalog table empty) — parameters-only, documented.
Then REFIT camera profiles (eval/fit_camera_profile.py) with Looks rendered in the "ours" pipeline; if the
new tone fit improves median luma on ≥80% of held-out pairs per camera, write profiles with tone_trusted=true.

## 22. Color grading wheels, noise reduction, CA/defringe, distortion auto-crop
- ColorGrade{Shadow,Midtone,Highlight}{Hue,Sat,Lum} + Global + Blending + Balance: standard 3-way lift
  gamma gain in OKLab-ish (hue+sat per wheel as ab offset scaled by luma-band weight from Blending/Balance;
  Lum as band exposure) — twins, constants shared. UI: three wheels + global, LR-style, in a "Color Grading"
  panel section (canvas-drawn wheels, drag pucks, double-click reset).
- NR: LuminanceSmoothing (edge-preserving smooth — GL: bilateral-lite on luma at half res mixed by amount;
  numpy: same kernel) + ColorNoiseReduction (chroma median/blur in OKLab ab). Approximation documented.
- CA/Defringe: DefringePurpleAmount/GreenAmount with Hue ranges (desaturate offending hue band near
  high-contrast edges — edge mask from the sharpen blur field); auto lateral CA (AutoLateralCA) via
  per-channel radial scale fitted from... v1: honor manual defringe only, note auto-CA as later.
- Distortion auto-crop: after lensfun distortion remap, scale uv to the maximal inscribed axis-aligned
  rect (compute from the polynomial at the frame edges) so no dark borders — twins.

## 23. Healing / spot removal
Adobe RetouchAreas / PaintBasedCorrections with clone/heal: v1 = circular clone-stamp spots
{{src x,y, dst x,y, radius, feather, opacity, mode: clone|heal}} stored under our key "pa_RetouchSpots"
(import Adobe RetouchInfo when parseable, else skip honestly). Render twins: clone = copy source disc
(feathered) at dest; heal = clone + mean-color match of dest ring. GL: second source sample in the main
pass loop (cap 32 spots); numpy identical. UI: Heal tool in the toolbar — click sets dest, drag picks
source, handles to adjust, list in a panel section with delete.

## 24. Export dialog + batch settings sync
- Export popover grows: format (JPEG/TIFF16), quality, long-edge resize, output sharpening
  (screen/print low/std/high — post-resize unsharp), filename pattern, and "Save to library" (§20).
  Batch: export selection from grid (queue endpoint, sequential, toast progress).
- Batch sync: in Develop, "Sync…" button — copies chosen setting groups (checkbox popover: WB/Tone/
  Presence/Curve/HSL/Grade/Detail/Effects/Masks) from current photo to the grid selection via one API
  call (POST /api/develop/sync {source_id, target_ids, groups}); history entries on each target.

## 25. Panorama merge
Mirror the HDR pattern: detect candidate pano sequences (same lens/focal, ≤10s apart, 2-8 frames,
overlapping content), POST /api/develop/pano/merge {image_ids} — OpenCV (opencv-python-headless) stitcher
on our decoded linears (downscale to 3000px for v1), result registered like HDR merges (EXR + base cache,
kind tag pano). Honest failure states (stitch confidence).

---

# PHASE 4 — Film emulation engine, Renditions model, final parity closure (appended 2026-07-10)

## 26. Film emulation — physically modeled, not preset LUTs
Goal: the most accurate film emulation shipping anywhere. Not a color preset: a model of the
photochemical chain, per stock, operating on LINEAR scene-referred data. When Film is enabled it
REPLACES the digital tone mapping (base/profile curve + BASE_PROFILE_SAT are bypassed; user Tone
sliders still work — they modify scene exposure BEFORE film, like printing/pushing).

Stage order (both twins; after WB/exposure/region-tone in linear, replacing stage 5-6 tone path):
1. **Layer exposures**: logE_i = log10(dot(S_i, rgb_linear)) per emulsion layer i∈{R,G,B}-sensitive,
   with 3×3 spectral sensitivity crosstalk matrix S (layers overlap — from datasheet sensitivity curves).
2. **Halation** (optical, pre-development): h = blur_wide(max(scene_luma − H_thresh, 0));
   logE_red += log10(1 + H_amount·h) (dominantly red layer, small green fraction H_GREEN_FRACTION).
   Kernel: exponential-tailed blur, radius H_RADIUS as fraction of frame min-edge (reuse the blur-field
   machinery with one extra large-σ field when Film is on). CineStill 800T (remjet removed): H_amount
   high, threshold low, radius large — the signature red glow around tungsten highlights. Normal C-41
   stocks: subtle values. All per-stock constants.
3. **Characteristic curves** (H&D) per layer: density D_i = hd_curve_i(logE_i) — monotone control
   points encoding toe / straight-line (gamma ~0.55–0.65 neg, ~1.6–1.9 slide) / shoulder, digitized
   from published Kodak/Fuji datasheets per stock.
4. **DIR coupler inhibition**: D = M_dir · D (3×3, small negative off-diagonals) — inter-layer
   development inhibition that creates film's color separation "pop".
5. **Grain**: per-layer stochastic density noise σ_i(D) peaking at mid densities (RMS granularity from
   datasheets), generated at stock grain pitch (value-noise at G_SIZE px at base resolution, NOT white
   pixel noise), monochromatic per layer (layers uncorrelated). Exposure-dependent: negative film is
   grainier in shadows (low density on neg = shadows of positive).
6. **Print/scan transform**: negative: subtract base+fog and orange mask, invert through a print-paper
   curve (paper gamma ~2.6 with soft shoulder — this is where blacks roll); slide: direct positive.
   Output → display sRGB. A per-stock 3×3 "scanner calibration" matrix lands the final palette.
7. UI: "Film" panel section (after Effects): stock picker (elegant cards: name, ISO, format vibe),
   Strength (blend film vs digital output — 100% default), Halation, Grain, Grain Size overrides,
   plus per-stock defaults. Data-tips explain each stock in one line.
Stocks v1 (data files web/features/develop/film_stocks/<slug>.json — curves as control points +
matrices + grain/halation params): CineStill 800T, Portra 400, Portra 160, Ektar 100, Kodak Gold 200,
Fuji Superia X-TRA 400, Kodak Tri-X 400 (B&W path: single layer, panchromatic weighting), Ilford HP5+.
**Validation, honest**: Sean's real film scans live at /mnt/expansion/Photos/Film Scans (lab scans incl.
800T-era rolls; the Tungsten800 project implies 800T familiarity). Numeric parity with a specific scan
isn't the bar (scenes differ) — the bar is characteristic behavior: halation on point highlights,
grain structure at 1:1, palette on skin/sky/tungsten. Produce comparison contact sheets.

## 27. Renditions — one photo, many faces (RAW↔edit unification; the elegant version)
Concept shift: the library's unit is the PHOTO (a capture moment), not the file. Files are RENDITIONS:
- the RAW (source of truth, always kept),
- baked exports (LR-era JPEGs found on disk, or our §24 saved exports),
- and LIVE renditions = develop settings states (virtual copies — no file at all, rendered on demand).
Storage: version stacks (§20, kind='version') group RAW+exports; virtual copies are lightweight image
rows sharing the RAW's filepath with their own develop_settings (vc_of column, v23 migration) so every
existing surface (grid, elo, flags, collections) works on them unchanged.
Behavior contract:
- Grid shows ONE cell per photo: representative = newest edit rendition (live edit beats baked export
  beats raw). Badge: small film-frame glyph + count. Expand like any stack.
- Loupe/Develop: V cycles renditions; the strip labels them ("RAW", "Export · 2024", "Virtual Copy 2").
- Opening Develop on a baked export offers "Edit original RAW" — and because we import the XMP/lrcat
  settings, the RAW opens LOOKING LIKE the export: the baked file becomes just a frozen proof of a
  state we can keep editing. That's the unification: old exports aren't dead ends, they're bookmarks.
- "Create Virtual Copy" in Develop (Ctrl+') — new live rendition starting from current settings.
- Snapshots panel (named states within one rendition) rides develop_history with labels.
- Culling/refine/search/dedup operate on photos; renditions never compete against each other.

## 28. Remaining parity closure (after §20-25 lanes land)
- **Transform/Upright panel**: perspective (vertical/horizontal/rotate/scale/aspect + offset), auto
  level (horizon detect), guided upright (2/4 line picks). Twins: 3×3 homography in uv (GL vertex/uv,
  numpy warp).
- **Lens Corrections UI panel** (backend §17 exists): Profile tab (enable, maker/model override,
  amount sliders) + Manual tab (distortion, vignette amount/midpoint).
- **Calibration panel**: shadows tint + per-primary hue/sat (legacy but users expect it) — small twin math.
- **Detail panel completion**: sharpen Masking slider (edge mask a-la LR, from existing blur fields),
  NR Detail/Contrast sub-sliders; render NR masking honestly.
- **Before/After side-by-side** (Y key: split view left/right + top/bottom) and Reference view (R held:
  pick any photo as reference beside canvas).
- **Soft proofing**: sRGB/AdobeRGB/P3 + a paper profile sim with gamut warning overlay.
- **Quick Develop** in grid: batch relative adjustments on selection (uses §24 sync machinery).
- **Watched folder auto-import** (scanner already rescans; add an inotify/poll watcher toggle per source).
- **Red-eye**: click-fix (desaturate+darken pupil disc) — low priority, tiny.
- **Panel layout parity pass**: final Develop right-panel order mirrors LR Classic exactly:
  Histogram · Basic · Tone Curve · HSL/Color · Color Grading · Detail · Lens Corrections · Transform ·
  Effects · Film (ours) · Calibration; left rail: Presets · Snapshots · History · Collections.
- Explicit non-goals: Print/Book/Slideshow modules, tethered capture (hardware), Adobe cloud sync.
