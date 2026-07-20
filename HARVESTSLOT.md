# HARVESTSLOT — HDD slot read-only; decode from RAM

**Lane:** `impl-harvestslot` (off develop)  
**Date:** 2026-07-20  
**Status:** implemented in this worktree — **left uncommitted** (prod untouched, no service restart)

## Problem

Live prod py-spy (2026-07-20) showed preview backfill serialized on the single-flight HDD governor:

- `thumb-prefetch_0`: idle in `hdd_governor._acquire` → `harvest_original`
- `thumb-prefetch_1`: active in `demosaic_raw_for_thumbnail` **inside** the same slot

`harvest_original` held `bulk_hdd_slot_sync` through read **and** demosaic/resize/encode/write. CPU work that needs no disk waited on the spindle gate. On an 8-core box, throughput ≈ `1/(read+decode)` instead of `max(disk-read-rate, parallel-decode-rate)`.

## Restructure

1. **`harvest_original` (bulk)** — acquire governor → sequential full-file read into RAM (~RAW 25MB) → **release** → demosaic/decode/encode/write from the buffer. Hash + metadata from the same buffer (read-once).
2. **`generation.py`** — `extract_embedded_raw_preview`, `demosaic_raw_for_thumbnail`, `_raw_library_sources`, and `load_source_image_from_bytes` accept `source_data` / BytesIO (`rawpy.imread` file-like). Lossy-DNG fallback writes a temp file only for the rare path (outside the slot).
3. **Interactive** — `bulk=False` still bypasses the gate (unchanged).
4. **`PHOTOARCHIVE_BULK_HDD_CONCURRENCY`** — still 1 (correct for the spindle). Not raised.
5. **Prefetch pool** — was capped by `background_thumb_workers=2` (prod `settings.local.json`) and derive max 4. Now sized to ~ncores (max 8); legacy cap of 2 is lifted so it cannot re-serialize demosaic. DecodeByteBudget still bounds RAM.
6. **Memory** — wave acquire charges `estimate_decode_bytes(...) + source_size` so in-flight whole-file buffers count toward the 768MB default budget (~5 concurrent RAW frames on 16GB).

## Before / after rates

### Live prod before (observational — no code change, no restart)

| Signal | Value |
|--------|-------|
| API `recent_images_per_min` | **9.5 → 15.3** over the quiet window (session avg) |
| Physical `.thumbcache` files / 3.0 min | **144 files / 181s → ~47.6 files/min** (~16 images/min at 3 tiers) |
| `avg_source_read_seconds` / `avg_decode_encode_seconds` | ~0.27–1.3s / ~0.44s (varies with cache heat) |
| Prod `background_thumb_workers` | **2** (settings.local) — only two prefetch threads |
| py-spy | one thread in demosaic **inside** slot; others blocked on `_acquire` |

Lane brief cited ~5 images/min on a quiet box under the profiled stall; the 3-minute physical count above is the measured baseline for this receipt.

### Worktree microbench (same 12 warm CR3s, HDD concurrency=1)

Method: ThreadPoolExecutor over real `/mnt/expansion/.../*.CR3`; old = hold slot through `demosaic_raw_for_thumbnail(path)`; new = read under slot + demosaic from bytes.

| Mode | Workers | Wall | Rate |
|------|---------|------|------|
| **OLD** (slot holds read+decode) | 6 | 7.16s | **100.6 images/min** |
| **NEW** (slot read-only) | 6 | 3.30s | **218.2 images/min** (~2.2×) |
| NEW (legacy 2 workers) | 2 | 4.35s | 165.3 images/min |
| NEW parallelism proof | 6 | 2.60s | 184.4 images/min; **decode peak concurrency = 6** |

Expect a similar multiple-× jump on cold spindle once deployed (disk-bound reads stream; N demosaics fill idle cores). Warm-cache microbench already shows **~2.2×**; cold spindle should move closer to `1/read_time` vs `1/(read+decode)`.

### Stack-sample “py-spy after” (worktree; py-spy needs root on this host)

Sampled `sys._current_frames` every 50ms during 6-wide decode:

- `max_concurrent_demosaic_threads = 6`
- `samples_with_2plus_demosaic = 57/57`
- demosaic stacks containing `hdd_governor` / `_acquire` = **0**
- unit test `test_bulk_slot_released_before_decode_allows_parallel_cpu`: holds during generate = **0**; decode peak = **2**

Representative sample (`holds=0`, six demosaic threads active):

```
thumb-prefetch_0..5: work → demosaic_raw_for_thumbnail   (no bulk_hdd_slot_sync)
```

## Memory bound

- Default `PHOTOARCHIVE_BULK_DECODE_BYTES` = 768MB.
- Peak per RAW ≈ demosaic working set (~100–180MB) + source buffer (~25MB) → budget admits ~4–5 in flight.
- Prefetch workers sized to cores; budget is the hard RAM gate, not the HDD slot.
- Do not raise HDD concurrency — that would seek-storm the spindle.

## Tests

```text
cd web && python -m pytest -x -q test_thumbnails.py test_hddgov.py
73 passed
```

## Risks

- **RSS spike** if decode budget is mis-set high and workers ≫ budget capacity — mitigated by charging source bytes + existing pressure gate.
- **Lossy DNG** rare path still spills to a temp file for tifffile (outside slot).
- **Deploy** must pick up raised `background_thumb_workers` / prefetch sizing; code lifts legacy `2` so a stale settings.local cannot re-cap the pool at 2.
- **Double open on missing files** — bulk read fails, then generate path-opens once to run `mark_source_missing` (intentional).

## Commit message (when asked to commit)

```
Bench: preview backfill ~2.2× on warm CR3 microbench (101 → 218 images/min); HDD slot now read-only

Release bulk_hdd_slot_sync after the spindle read so demosaic runs from RAM in parallel.
```
