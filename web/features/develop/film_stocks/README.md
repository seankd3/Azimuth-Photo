# Film stock data (Develop §26)

Draft per-stock constants for the physically modeled film engine. **No pipeline code here** — JSON + this README only.

Units (all stocks):
- `hd_curves` control points are `[logE, density]` with **logE relative to the ISO speed point at 0.0** (density ≈ 0.15 above fog at that point).
- Density is net developed dye/silver density (fog lives in `base.base_fog_density`; C-41 orange mask in `base.orange_mask_rgb`).
- `spectral_crosstalk` rows are **row-normalized** (color: 3×3; B&W: 1×3 panchromatic weights).
- `grain.rms_granularity` is datasheet Diffuse RMS Granularity (×1000 convention as published).
- `grain.size_px_at_4k` is value-noise pitch in pixels at ~4096 px frame width.
- `halation.radius_frac` is blur radius as a fraction of the frame’s min edge.

Honesty rule: curves and matrices below are **faithful approximations from memory of published datasheet plots**, not point-digitized from scanned PDFs. Treat them as v1 drafts to refine against real datasheets + Sean’s scans.

## Schema

| Field | Color neg | B&W |
|-------|-----------|-----|
| `hd_curves` | `r` / `g` / `b` (~8 points each) | `pan` only |
| `spectral_crosstalk` | 3×3 | 1×3 weights |
| `dir_coupler` | 3×3 (neg off-diagonals) | `[[1.0]]` |
| `grain` | per `r`/`g`/`b` | `pan` |
| `halation` | always present | `green_fraction: 0` |
| `type` | `negative_color` | `negative_bw` |
| `white_point_hint` | `daylight` or `tungsten` | `daylight` |

## Provenance per stock

| File | Stock | Primary datasheet source | Sourced vs approximated |
|------|-------|--------------------------|-------------------------|
| `cinestill-800t.json` | CineStill 800T | **Kodak E-4050 / H-1-5219 Vision3 500T** (5219). CineStill = same emulsion, remjet removed, stills C-41 / EI ~800. | **Sourced:** RMS 7 (Vision3 500T Diffuse RMS), tungsten 3200 K balance, γ≈0.55 aim. **Approximated:** H&D control points (memory of Status M plots), spectral 3×3 from tungsten sensitivity curves, DIR, orange mask RGB, **halation** (engineered strong — remjet absence is product behavior, not a datasheet number). |
| `portra-400.json` | Kodak Portra 400 | **Kodak Portra 400** professional pub (**E-4020** / later Portra successors). | **Sourced:** RMS 4, daylight balance, soft matched-layer intent. **Approximated:** H&D (γ≈0.58), crosstalk, DIR, mask, subtle halation. |
| `portra-160.json` | Kodak Portra 160 | **Kodak Portra 160** professional datasheet. | **Sourced:** RMS 3, daylight. **Approximated:** H&D (γ≈0.56), crosstalk (slightly tighter than 400), DIR, mask, halation. |
| `ektar-100.json` | Kodak Ektar 100 | **Kodak Ektar 100** datasheet. | **Sourced:** RMS ~3.5, daylight, high-sat / fine-grain reputation. **Approximated:** H&D (γ≈0.62), narrow crosstalk, **strongest DIR** in set, mask, minimal halation. |
| `kodak-gold-200.json` | Kodak Gold 200 | **Kodak Gold 200** consumer datasheet (thinner pubs than Portra). | **Sourced:** daylight consumer family. **Approximated:** H&D (γ≈0.58), warmer mask / red build, broader crosstalk, weak DIR, RMS 5.5 (Gold family ~5–6; generation-dependent). |
| `fuji-superia-xtra-400.json` | Fuji Superia X-TRA 400 | **Fujifilm Superia X-TRA 400** technical data. | **Sourced:** daylight consumer Fuji. **Approximated:** H&D with slightly steeper G (γ≈0.59–0.60), G↔B-leaning crosstalk (cool/green bias), weak DIR, RMS ~5. |
| `kodak-tri-x-400.json` | Kodak Tri-X 400 | **Kodak Tri-X 400 (400TX)** B&W datasheet (D-76 / recommended). | **Sourced:** RMS **17**, panchromatic B&W. **Approximated:** H&D (γ≈0.65), 1×3 spectral weights, mild AH halation, paper γ≈2.1. |
| `ilford-hp5-plus.json` | Ilford HP5 Plus | **Ilford HP5 Plus** technical data sheet. | **Sourced:** ISO 400 panchromatic family. **Approximated:** H&D (γ≈0.62, longer toe than Tri-X), weights, RMS ~14, paper γ≈2.05. |

### What is never “from the datasheet”

- **Halation amounts / radii** — datasheets don’t publish remjet-removed glow parameters. 800T values are product-tuned; other stocks are intentionally subtle.
- **`dir_coupler` matrices** — DIR is described qualitatively; numeric 3×3s are reasoned engine parameters (pro C-41 stronger than consumer).
- **`orange_mask_rgb` / `print_paper`** — densitometry Status M ≠ scanner RGB. These are working transforms for the §26 print/scan stage, to be calibrated against scans.
- **`size_px_at_4k` / `shadow_bias`** — derived for the grain model (pitch + negative shadow bias), not printed on datasheets.

## Validation against `/mnt/expansion/Photos/Film Scans`

Numeric parity with one scan is not the bar (scenes/labs differ). Check **characteristic behavior**:

1. **Contact sheets** — same digital scene through each stock vs a real roll of that stock (or nearest neighbor).
2. **Halation** — point tungsten highlights on 800T should show soft red bloom; Portra/Gold/Fuji should not.
3. **Grain at 1:1** — Tri-X / HP5 / 800T coarser than Portra 160 / Ektar; shadow grain bias visible on underexposed negs.
4. **Palette** — Portra skin neutrality; Ektar sky/foliage punch; Gold warmth; Superia cool greens; 800T tungsten neutrals without heavy blue cast.
5. **On-disk anchors already present** (use these first):
   - Portra 400: `San Marcos Filmlab/2024-10-01/Portra 400 (*)`, `2024-11-18/Portra 400 (*)`
   - Gold: `2024-10-01/Gold (*)`, `2024-12-23/Gold`
   - Fuji consumer: `2024-10-01/Fuji 200`, `2024-11-18/Fuji 400`, `2024-11-25/Fuji 400 (*)`, `2025-05-17/Fuji 400*` (nearest to Superia X-TRA 400)
   - HP5: `2024-12-23/HP5`
   - CineStill family: `2024-12-23/Cinestill 50` (related brand; **no labeled 800T folder spotted** — also try Aurora 800 / Portra 800 as high-ISO color references, not 800T halation ground truth)
6. **Missing local ground truth** — Portra 160, Ektar 100, Tri-X, true 800T: validate from datasheet plots + any Tungsten800 / personal 800T rolls when located; don’t fake parity against wrong stocks.

## Refinement loop (later)

1. Re-digitize H&D from official PDF plots (same logE zeroing).
2. Fit `orange_mask_rgb` + optional scanner 3×3 (§26 stage 6) to lab TIFF white/gray patches.
3. Tune 800T halation on real remjet-free highlight frames.
4. Keep this README’s sourced-vs-approximated column updated when a field becomes PDF-digitized.
