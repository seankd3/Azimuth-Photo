# Develop — the other half of the app

**One sentence: an edit is a decision that names Lightroom's own settings, a
render is a cached answer to (bytes, settings), and one Python pipeline is the
only place color math exists — so Lightroom's sidecars are not an import
format, they are the native dialect.**

Sean's directive (08-23, verbatim in MASTER_PLAN 1.18): *"I want it to be
nearly a 1:1 to lightroom classic, (ideally compatible with loading and
editing lightrooms non destructive edits too. (LR timelapse does this well)
this is the other half of the app … lets take our time and do it right, with
elegancy, grace and beautiful simplicity."*

## What 1:1 actually means — measured, not assumed

The census of every `.xmp` Lightroom has written into the real library
(`D:\Pictures`, 2026-08-23):

- **580 sidecars.** 569 carry *only* a crop (CropLeft/Right/Top/Bottom/Angle
  + constraints) — crop is the most-used edit in this library by 50×, and it
  ships first.
- **11 carry full develop settings.** Every one is **ProcessVersion 15.4**,
  **HDREditMode 1** (HDRMaxValue ~+2.3 stops, with the `SDR*` keys driving
  the SDR rendition), CameraProfile "Camera Landscape" (Canon
  camera-matching), and the complete slider surface:
  - Tone: Exposure/Contrast/Highlights/Shadows/Whites/Blacks(2012),
    Texture, Clarity, Dehaze, Vibrance, Saturation
  - WB: Temperature, Tint, WhiteBalance mode
  - Curves: ToneCurvePV2012 (+ per-channel R/G/B), parametric four-zone
    (+ the three split points)
  - Color: HSL ×8 channels, ColorGrade (global/shadow/mid/high × H/S/L +
    blending), SplitToning legacy keys, **PointColors** (PV15+),
    ConvertToGrayscale
  - Detail: Sharpen amount/radius/detail/edge-masking, LuminanceSmoothing,
    ColorNoiseReduction(+detail/smoothness)
  - Lens: LensProfileEnable, AutoLateralCA, Defringe (purple/green),
    manual distortion
  - Geometry: Crop + angle, Perspective* (Upright)
  - Effects: PostCropVignette, Grain, VignetteAmount
  - Calibration: ShadowTint, Red/Green/BlueHue+Saturation
- **Zero masks.** No `MaskGroupBasedCorrections`, no local adjustments,
  no heal/clone in any file. The market's scariest surface is absent from
  the real editing life this app serves. It parks with receipts.

That list — not Adobe's documentation — is the 1:1 target. Anything Sean's
Lightroom writes, we read, render, and write back. Anything it has never
written waits for the day it appears in a sidecar.

## What already exists — harvest, don't rewrite

V1 carries ~11,700 lines under `features/develop/`: a pure-NumPy PV-2012
pipeline (`pipeline.py`), camera/Adobe profile emulation, XMP write
(`xmp_write.py`), `.lrcat` import, DNG pipeline, masks, film emulation, AI
machinery. The acceptance method survives unchanged: **each DNG's own
embedded XMP is ground truth** (`test_dng_acceptance.py`) — render our answer
next to Lightroom's for the same settings and measure.

The 08-16 acceptance verdict stands: the color math is close and **the real
gap is PV15.4 tone** — now sharpened by the census: Sean edits in HDR mode,
so the target is the *SDR rendition of an HDR-mode edit* (what the `SDR*`
keys steer), because every surface this app renders to is SDR.

Per the roadmap (§5): *preserve only color behavior that can be
demonstrated; remove the schedulers, histories, caches and proof machinery
whose questions the core no longer asks.* The pipeline functions move; the
V1 routes, progressive-UI machinery, edit-history tables and preset shelves
do not — V2 already has one worker, one cache, one decision log, and they
are enough:

## The shape — three existing primitives, no new ones

- **The edit is a decision.** Family `develop`, subject = content hash,
  value = the crs-subset JSON (exactly Lightroom's keys and units — our
  storage dialect *is* `crs`, so import/export is transcription, never
  translation). Append-only history gives undo/history for free, the same
  way stars and rotation already work. A virtual copy is a later `develop`
  decision under a version subject — parked until wanted.
- **The render is a cache row.** Kind `develop`, recipe = the settings
  digest — a pure function of inputs, like every other kind. The loupe
  shows the cached rendition; the worker makes them; eviction already
  knows how to keep the disk honest.
- **The pipeline is one module.** Linear in, sRGB out, NumPy, no second
  implementation anywhere — not in JS, not in shaders. Interactivity comes
  from size, not duplication: while a slider scrubs, render at ~512 px
  (tens of milliseconds); on settle, the full loupe; the grid tile
  re-renders behind. Lightroom itself is a proxy-then-full renderer; ours
  is the same shape with sizes as the only knob.

## Sidecar compatibility — the LRTimelapse discipline

LRTimelapse edits Lightroom's XMP as a peer, and survives because it obeys
three rules this module adopts whole:

1. **Read everything, keep everything.** Parse the crs keys we render;
   preserve the ones we don't (verbatim pass-through on write). A sidecar
   that goes through Azimuth and back into Lightroom loses nothing.
2. **Write only what changed, in Lightroom's own spelling.** Same keys,
   same units, same `+0.50` signed formatting, ProcessVersion untouched
   unless we created the edit.
3. **The file on disk is the interchange, the catalog is the truth.**
   Import reads sidecars into decisions (a `by='lightroom'` authority row —
   the owner's own answer outranks it later, the same rule the decision log
   already enforces). Export writes decisions back to sidecars on demand.
   Nothing watches, nothing syncs in the background, nothing touches
   `.lrcat` (the 08-16 ruling: read the photograph, never the catalog).

## Acceptance — before/after photographs, not opinions

The golden set is Sean's own 11 developed sidecars plus their raws. For
each: render at Lightroom's settings, diff against Lightroom's export
(ΔE on a grid + full-frame ΔE percentiles), and publish the pair
side-by-side in the proof output. The bar the roadmap set: *explicit
before/after acceptance photographs before claiming color or renderer
parity.* No slider ships on faith; each pipeline stage lands with its
measured pair.

## Build order

1. **Crop and angle** — DONE 08-23 (`2d58766a`). The sweep's walk hands
   over the sidecars it sees; settings land whole as file-authored
   decisions (spellings kept, arrays kept); the crop projects to
   `images.develop` and renditions key their recipes on it per photograph
   *in SQL* (a `keyed` cache kind — the plain tile survives beside the
   cropped one, because the embedding and the faces read plain pixels).
   The crop surface: C over the loupe, drag draws, edges resize, middle
   moves, Enter applies, Full frame removes; the verb cuts the cropped
   loupe and grid from the plain loupe file synchronously. Proven in the
   window: a sidecar narrowed its tile 1.78 → 0.99, the exact rectangle.
   **Angle parked with its receipt: all 1,138 real CropAngle values are
   exactly 0** — rendering it waits for a measured fixture, not a guessed
   dialect (V1 crops then rotates, which letterboxes corners; Lightroom's
   frame does not).
2. **The read path whole** — adoption already reads every key (done with
   1); the loupe inspector says "Edited" from the projected column. The
   full settings inspector rides the tone phase, when there is something
   to show for each key.
3. **Tone core, measured** — WB, Exposure/Contrast/H/S/W/B, tone curve +
   parametric, on the harvested pipeline; acceptance pairs against the
   golden set; the PV15.4-SDR gap closed here or the miss documented in
   numbers.

   *Probed and measured 08-23:* `features.develop.pipeline` and
   `xmp_write` import clean — the color math and the sidecar writer
   survive as-is. The profile/DNG chains were de-V1'd (rawproc's kind no
   longer registers into a registry that does not exist; the embedded-XMP
   reader moved home to `develop.read_embedded`, where DNG adoption will
   want it anyway), and **the 40-DNG acceptance harness runs again**: 8
   minutes over the working disk's DNGs against Adobe's own embedded
   renders. Baseline: **clean mean |ΔL| = 0.0445 against the 0.035 bar**
   (ab within bounds, lossy-gain gate passing). The worst pairs
   (`D:\azimuth-bench\out\dng-acceptance\accept_*.jpg`) show the
   direction: our render is lighter and flatter — Adobe's camera-profile
   tone digs deeper shadows. That 0.0095 of L, mostly shadow contrast, is
   the tone phase's first target.
4. **Color** — HSL, ColorGrade, calibration, PointColors; grayscale.
5. **Detail + lens + effects** — sharpen/NR (the V1 guided filter
   survives), defringe/CA, vignette/grain.
6. **Write-back + round-trip proof** — edit here, read in Lightroom,
   edit there, read here; the LRTimelapse loop closed.
7. **Export** — the render pipeline at FULL size to JPEG; presets later.

Parked with receipts: **masks/local** (zero occurrences in the library),
**heal/AI** (same), **HDR display path** (every Azimuth surface is SDR;
the SDR rendition is the target), **.lrcat import** (sidecars are the
bridge, per the 08-16 ruling), **virtual copies** (no VC in the census),
**presets/looks** (nothing in the census beyond camera profiles).
