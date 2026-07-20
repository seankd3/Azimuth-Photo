# Lane impl-dngthumbs — cheap DNG library thumbnails

**Verdict:** Library sm/md/lg for DNGs no longer runs Develop+gzip. Full thumb-set drops from **~20.6s → ~1.7s per DNG** (~2.9 → ~35 images/min). Develop still builds `.bin.gz` lazily on open.

Suggested commit message:

```
Stop Develop+gzip during DNG library thumb pregen.

Use embedded JPEG for tiers it covers (sm from ~1024px) and one fast
LibRaw demosaic for the rest. Bench: DNG thumb-set 20.6s → 1.7s.
```

## Path change

| Before | After |
|--------|--------|
| `load_source_image` → embedded only if `long_side >= max_target` (often 3840) | Same gate per tier need, but generators split tiers |
| Miss → `render_display_preview` → `ensure_base_cache` (demosaic + noise + **gzip `.bin.gz`**) | Miss → never call Develop from library thumbs |
| One Develop render fed all tiers | Embedded covers sm (and any tier ≤ embed); **one** `rawpy` half_size+LINEAR demosaic for md/lg |

Files touched (decode path only — not `pregen.py` candidate selection):

- `web/thumbnails/generation.py` — extract / demosaic helpers, tier partition, generators
- `web/thumbnails/__init__.py` — pass `raw_extensions=RAW_EXTENSIONS`
- `web/thumbnails/config.py` — comment: `.dng` stays out of `EMBEDDED_PREVIEW_EXTENSIONS` (budget still charges demosaic for lg waves)
- `web/thumbnails/pregen_worker.py` — comment only

### Who still reads `.bin.gz`

Grep: `ensure_base_cache` / base cache are owned by Develop (`rawproc`, `render_display_preview`, sync hub/readthrough, AI masks). Library thumb generation no longer calls that path.

## Benchmark (15 real DNGs, same set)

Source: catalog DNGs under `/mnt/expansion/Photos/RAWS/2025/2025-11-15/` (ids 154943–154957), ~21MB, sensor ~6252×4168, embedded JPEG ~1024×683. Isolated temp thumb + Develop cache dirs (prod untouched).

### BEFORE (Develop + gzip side effect)

| Metric | Value |
|--------|--------|
| n | 15 |
| mean | **20.596 s/DNG** |
| median | 19.577 s |
| min / max | 15.069 / 29.420 s |
| throughput | **~2.9 images/min** |
| `.bin.gz` written during thumb pass | **15** |

Wall time lived in `read_seconds` (Develop decode + gzip), matching the gpu-offload lane’s finding that the cost was hidden as “disk.”

### AFTER (embedded + single fast demosaic)

| Metric | Value |
|--------|--------|
| n | 15 |
| mean | **1.717 s/DNG** |
| median | 1.744 s |
| min / max | 1.210 / 2.213 s |
| throughput | **~35 images/min** |
| `.bin.gz` written during thumb pass | **0** |
| thumbs written | 45 (sm+md+lg × 15) |
| sm-only (embedded) micro | **~0.020 s** |

Delta: **20.6s → 1.7s** (~12×), **2.9 → 35 img/min**.

## Develop still works (lazy)

After a thumb pass with zero `.bin.gz`, `rawproc.ensure_base_cache(154957, …)` created the binary in ~10s (`size_mb≈11.6`). Opening Develop still builds/uses the accurate base on demand.

## Visual check

PNGs in `receipts/dngthumbs-visual/`:

| Pair | Notes |
|------|--------|
| `before_154957_sm` vs `after_154957_sm` | Same night-sky frame + satellite streak; orientation correct. After (embedded) is darker/cleaner black; before (Develop) has warmer lift + more grain — expected without Develop NR/tone. |
| `before_154957_md` vs `after_154957_md` | Stars stay pinpoint; no obvious wrong color cast or rotation. After is LibRaw LINEAR half_size (not Develop color truth) but looks fine for grid/loupe. |
| `after_*_lg_3840x2560` | Full lg long-side after mild upscale from half demosaic. |

Mean RGB (154957): before sm ≈ (14.6, 6.2, 3.9) → after ≈ (5.3, 2.5, 2.1) — tone differs, subject/geometry match.

## Tests

```text
cd web && python -m pytest -x -q test_thumbnails.py
67 passed in 3.78s

# related
test_interactive_isolation / test_hardening (embedded/dng filters): 4 passed
```

`.dng` remains **outside** `EMBEDDED_PREVIEW_EXTENSIONS` so bulk decode budget still assumes demosaic when lg is in the wave (safe over-count).

## Risks

- Library RAW thumbs are no longer Develop-color-matched (loupe may differ from Develop canvas until the user opens Develop). Intentional for this lane.
- Half_size + LINEAR demosaic + mild upscale to 3840: slightly softer lg than a full AHD demosaic; acceptable for previews.
- Tiny embeds (e.g. some panos at 256px) still demosaic even for sm.
- Existing cached thumbs left alone (no mass invalidate).
- Work left **uncommitted** on branch `impl-dngthumbs` per lane instructions.
