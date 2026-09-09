# Film stock data (Develop §26)

Per-stock constants for the physically modeled film engine. **No pipeline code here** — JSON + this README only.

Units (all stocks):
- `hd_curves` control points are `[logE, density]` with **logE relative to the ISO speed point at 0.0** (density ≈ 0.15 above fog at that point).
- Density is net developed dye/silver density (fog lives in `base.base_fog_density`; C-41 orange mask in `base.orange_mask_rgb`).
- `spectral_crosstalk` rows are **row-normalized** (color: 3×3; B&W: 1×3 panchromatic weights).
- `grain.rms_granularity` is datasheet Diffuse RMS Granularity (×1000 convention as published). **Engine reads flat keys** on the `grain` object (`rms_granularity`, `size_px_at_4k`, `shadow_bias`); nested `r`/`g`/`b`/`pan` blocks are kept as documentation mirrors.
- `grain.size_px_at_4k` is value-noise pitch in pixels at ~4096 px frame width.
- `halation.radius_frac` is blur radius as a fraction of the frame’s min edge.
- Optional `scan_matrix` (3×3) is the §26 stage-6 scanner/print calibration landing the final palette.

Honesty rule: H&D curves and spectral matrices started as **faithful approximations from memory of published datasheet plots**. Halation / grain pitch / `scan_matrix` / paper shoulder are refined against Sean’s lab scans where attributed rolls exist (see validation below).

## Schema

| Field | Color neg | B&W |
|-------|-----------|-----|
| `hd_curves` | `r` / `g` / `b` (~8 points each) | `pan` only |
| `spectral_crosstalk` | 3×3 | 1×3 weights |
| `dir_coupler` | 3×3 (neg off-diagonals) | `[[1.0]]` |
| `grain` | flat keys + optional `r`/`g`/`b` mirrors | flat keys + optional `pan` mirror |
| `halation` | always present | `green_fraction: 0` |
| `scan_matrix` | optional 3×3 | optional (usually identity / unused) |
| `type` | `negative_color` | `negative_bw` |
| `white_point_hint` | `daylight` or `tungsten` | `daylight` |

## Provenance per stock

| File | Stock | Primary datasheet source | Sourced vs approximated | Scan tuning 2026-07-10 |
|------|-------|--------------------------|-------------------------|------------------------|
| `cinestill-800t.json` | CineStill 800T | **Kodak E-4050 / H-1-5219 Vision3 500T** (5219). CineStill = same emulsion, remjet removed, stills C-41 / EI ~800. | **Sourced:** RMS 7, tungsten 3200 K, γ≈0.55. **Approximated:** H&D, spectral, DIR, mask. | Grain flattened for engine. Halation amount/radius nudged using **Cinestill 50** (remjet-removed sibling) + **Aurora 800** San Marcos rolls — **no labeled 800T on disk**. |
| `portra-400.json` | Kodak Portra 400 | **Kodak Portra 400** (**E-4020** / successors). | **Sourced:** RMS 4, daylight. **Approximated:** H&D, crosstalk, DIR, mask. | **Tuned against 15 real San Marcos Filmlab Portra 400 scans.** Mild warm `scan_matrix`, quieter halation, paper shoulder for Noritsu lift, grain size 2.4. |
| `portra-160.json` | Kodak Portra 160 | **Kodak Portra 160** datasheet. | **Sourced:** RMS 3. **Approximated:** H&D, crosstalk, DIR. | Grain flattened only — **no local Portra 160 rolls**. |
| `ektar-100.json` | Kodak Ektar 100 | **Kodak Ektar 100** datasheet. | **Sourced:** RMS ~3.5. **Approximated:** H&D, strong DIR. | Grain flattened only — **no local Ektar rolls**. |
| `kodak-gold-200.json` | Kodak Gold 200 | **Kodak Gold 200** consumer datasheet. | **Sourced:** daylight consumer family. **Approximated:** H&D, warmer mask, weak DIR, RMS 5.5. | **Tuned against 15 real San Marcos Filmlab Gold scans.** Warmer `scan_matrix` + mask, grain size 2.5. |
| `fuji-superia-xtra-400.json` | Fuji Superia X-TRA 400 | **Fujifilm Superia X-TRA 400** technical data. | **Sourced:** daylight Fuji consumer. **Approximated:** cooler/green H&D + crosstalk, RMS ~5. | **Tuned against 15 real San Marcos Filmlab Fuji 400 scans** (nearest Superia X-TRA proxy — folders say “Fuji 400”, not X-TRA). Cool/green `scan_matrix`. |
| `kodak-tri-x-400.json` | Kodak Tri-X 400 | **Kodak Tri-X 400 (400TX)** datasheet. | **Sourced:** RMS **17**. **Approximated:** H&D, weights. | Grain flattened only — **no local Tri-X rolls**. |
| `ilford-hp5-plus.json` | Ilford HP5 Plus | **Ilford HP5 Plus** technical data. | **Sourced:** ISO 400 family. **Approximated:** H&D, RMS ~14. | **Tuned against 5 real San Marcos Filmlab HP5 JPEG scans.** Grain flattened (engine now differentiates vs color); paper slightly brighter mids. JPEG HF may inflate measured grain. |
| `fuji-velvia-50.json` | Fuji Velvia 50 | no provenance recorded when written | treat every number as approximated | none |
| `fujicolor-c200.json` | Fujicolor C200 | no provenance recorded when written | treat every number as approximated | none |
| `kodak-colorplus-200.json` | Kodak ColorPlus 200 | no provenance recorded when written | treat every number as approximated | none — Colorplus scans exist on disk, untuned |
| `kodak-tmax-400.json` | Kodak T-Max 400 | no provenance recorded when written | treat every number as approximated | none |
| `kodak-ultramax-400.json` | Kodak Ultramax 400 | no provenance recorded when written | treat every number as approximated | none — Ultramax scans exist on disk, untuned |
| `portra-800.json` | Kodak Portra 800 | no provenance recorded when written | treat every number as approximated | none — Portra 800 scans exist on disk, untuned |

### What is never “from the datasheet”

- **Halation amounts / radii** — datasheets don’t publish remjet-removed glow parameters. 800T values are product-tuned; other stocks are intentionally subtle.
- **`dir_coupler` matrices** — DIR is described qualitatively; numeric 3×3s are reasoned engine parameters.
- **`orange_mask_rgb` / `print_paper` / `scan_matrix`** — densitometry Status M ≠ scanner RGB. Calibrated against lab scans where possible.
- **`size_px_at_4k` / `shadow_bias`** — derived for the grain model, not printed on datasheets.

## Validation against the owner's film-scan folder (2026-07-10)

Numeric parity with one scan is not the bar (scenes/labs differ). Check **characteristic behavior**.

Scanner: San Marcos Filmlab Noritsu EZ Controller, ~6048×4011 TIFF (HP5 delivered as JPEG).

### Inventory (confident vs unknown)

| Attribution | Confidence | Rolls used | Notes |
|-------------|------------|------------|-------|
| Portra 400 | **high** | `2024-10-01/Portra 400 (1–3)`, `2024-11-18/Portra 400 (1–2)` | Folder + `KodakPortra400-*` filenames |
| Gold 200 | **high** | `2024-10-01/Gold (1–2)`, `2024-12-23/Gold` | `KodakGold200-*` filenames |
| Fuji 400 → Superia X-TRA proxy | **medium** | `2024-11-18/Fuji 400`, `2024-11-25/Fuji 400 (*)`, `2025-05-17/Fuji 400*` | Consumer Fuji 400, not labeled X-TRA |
| HP5 → HP5 Plus | **high** | `2024-12-23/HP5` | JPEG-only delivery |
| Cinestill 50 | brand ref | `2024-12-23/Cinestill 50` | Remjet-removed sibling; **not** 800T |
| Aurora 800 / Portra 800 | high-ISO color refs | `2024-12-23/Aurora 800`, `Portra 800` | **Not** 800T halation GT |
| Ultramax / Colorplus / Metropolis / Fuji 200 | other stocks | present | Ultramax and Colorplus now have stock files, untuned; Metropolis and Fuji 200 have none |
| 1Hour / Self Develop / unlabeled `2025-09-01/{1–6}` | **unknown** | numeric roll IDs only | No stock in path/EXIF |
| Portra 160, Ektar 100, Tri-X, **true 800T** | **missing** | — | No path/EXIF hits |

EXIF/XMP on sampled frames: no film-stock tags (Noritsu geometry/software only). Attribution is folder/filename.

### Measurement summary (sample means)

| Stock | n | OKLab mean a/b | Skin hue° / chroma | Grain HF RMS | Grain pitch@4k | Highlight R−B peak | Tone p1 / p50 / p99 |
|-------|---|----------------|--------------------|--------------|----------------|--------------------|---------------------|
| Portra 400 | 15 | −0.008 / +0.022 | 51° / 0.060 | 0.015 | ~3.1 | 0.16* | 0.12 / 0.52 / 0.87 |
| Gold 200 | 15 | −0.008 / +0.018 | 55° / 0.052 | 0.017 | ~1.4 | 0.11* | 0.16 / 0.49 / 0.81 |
| Fuji 400 | 15 | −0.008 / +0.011 | 48° / 0.057 | 0.020 | ~2.2 | 0.10* | 0.13 / 0.63 / 0.89 |
| HP5 | 5 | ~0 / ~0 (B&W) | — | 0.088† | ~1.3 | ~0 | 0.08 / 0.70 / 0.91 |
| Cinestill 50 | 5 | −0.005 / +0.014 | — | 0.013 | ~2.0 | 0.19* | (family ref) |
| Aurora 800 | 5 | +0.004 / +0.016 | — | 0.015 | ~1.5 | 0.19* | (family ref) |

\*Highlight R−B on real scenes mixes **true halation**, tungsten WB, and warm subject matter — use for relative family behavior, not absolute calibration.  
†HP5 JPEG compression inflates HF RMS.

Engine probes (after 2026-07-10 data fix): mid-gray grain RMS Portra≈0.039, 800T≈0.049, HP5≈0.083, Tri-X≈0.103. Halation probe: 800T keeps elevated R−B out to r≈60–80 px; Portra collapses by ~r40 (long-tail signature).

### Refinement loop (later)

1. Re-digitize H&D from official PDF plots (same logE zeroing).
2. Locate a true labeled **800T** night roll for halation amplitude (point lights / windows).
3. Fit `scan_matrix` on gray cards if any appear in future lab drops.
4. Keep this README’s scan-tuning column updated.
