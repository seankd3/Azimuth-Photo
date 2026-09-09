# Guided-filter masking (dt-gfilter)

Edge-aware mask refine for Develop local adjustments, adopted from the
darktable study (guided filter as the mask-refine primitive).

## What landed

- Shared primitive: `web/pixels/guided_filter.py`
  - O(n) integral-image box means
  - Classic He–Sun guided filter with darktable-style ×4 fast path
  - EIGF variant for linear HDR guides
  - `soft_mask(...)` wraps the old gaussian softener when no guide is present
- Feather → `(radius, ε)` uses existing 0–1 Feather sliders:
  `radius = Feather × min_side × 0.04`, `ε = 1 / feathering`
- Called by `web/pixels/masks.py` after the mask combine, guided by preview
  luma (radial Feather, range LumRange soft width)

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

```powershell
cd web; .venv\Scripts\python.exe -m pytest -q test_develop_guided_filter.py test_develop_masks.py
```
