# Guided-filter masking (dt-gfilter)

Edge-aware mask refine for Develop local adjustments, per
`darktable-study/SYNTHESIS.md` P1 #1 + #4.

## What landed

- Shared primitive: `web/features/develop/guided_filter.py` (+ JS twin
  `web/static/js/desktop/develop/guided_filter.js`)
  - O(n) integral-image box means
  - Classic He–Sun guided filter with darktable-style ×4 fast path
  - EIGF variant for linear HDR guides
  - `soft_mask(...)` wraps the old gaussian softener when no guide is present
- Feather → `(radius, ε)` uses existing 0–1 Feather sliders:
  `radius = Feather × min_side × 0.04`, `ε = 1 / feathering`
- Wired into `masks.rasterize_correction` / `mask_raster.js` after combine,
  guided by preview luma (radial Feather, range LumRange soft width, AI default)
- `ai_masks` sky path now calls the shared `guided_filter`

## Non-goals honored

No new UI, no filmic/CAT16, no color-science ground-truth edits.

## Before / after renders

High-contrast fixtures from `/mnt/expansion/Photos/RAWS`:

| Scene | Before (geometric feather) | After (guided refine) | Diff ×10 | Masks |
|-------|----------------------------|------------------------|----------|-------|
| R5 `20230218-R5__0447.CR3` | [before](examples/guided-filter/20230218-R5__0447-before.png) | [after](examples/guided-filter/20230218-R5__0447-after.png) | [diff](examples/guided-filter/20230218-R5__0447-diff.png) | [plain](examples/guided-filter/20230218-R5__0447-mask-plain.png) / [guided](examples/guided-filter/20230218-R5__0447-mask-guided.png) |
| DJI `DJI_0125.DNG` | [before](examples/guided-filter/DJI_0125-before.png) | [after](examples/guided-filter/DJI_0125-after.png) | [diff](examples/guided-filter/DJI_0125-diff.png) | [plain](examples/guided-filter/DJI_0125-mask-plain.png) / [guided](examples/guided-filter/DJI_0125-mask-guided.png) |

Local edit: soft radial + strong `LocalShadows2012` lift.

## Perf (24MP-equivalent quarter-res = 1500×1000, radius 20)

From `docs/examples/guided-filter/PERF.txt`:

| Path | ms / apply |
|------|------------|
| guided_filter (fast) | **~60–100** |
| gaussian soft_mask (old) | ~120–180 |

Latest measured run (also in `PERF.txt`): guided **58.5–101 ms**, gaussian **119–178 ms**. Guided stays faster than the softener it wraps. Mask atlas rebuild runs on mask edits, not every tone scrub.

## Tests

```bash
cd web && .venv/bin/python -m unittest test_develop_guided_filter test_develop_masks test_develop_ai_masks
```
