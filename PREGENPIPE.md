# PREGENPIPE — continuous decode pump (no wave starvation)

**Lane:** `pregenpipe` (off develop)  
**Date:** 2026-07-20  
**Status:** implemented in this worktree — **left uncommitted** (prod untouched, no service restart)

## Problem

Live prod py-spy (2026-07-20) showed preview backfill starved by its own orchestration:

- Per-image work ~1s (read ~0.5–1.9s + decode ~0.45s), but sustained rate only ~14–18 images/min
- `thumb-prefetch_0..3` idle in ~4 of 6 samples (~67% idle); usually 0–1 decode active
- 8-worker pool rarely submitted more than ~4 tasks because the async loop never fed faster than that

Root cause in `run_pregen_bulk_batch`:

1. Serial candidate fetch (DB anti-join) with decode threads idle
2. Wave barrier — wait for **all** in-flight before starting the next wave (head-of-line on one slow RAW)
3. Awaited write-queue flush on the critical path every 8 items
4. Blind `PREGENERATE_BATCH_PAUSE_SECONDS = 0.25` between batches

## Redesign

1. **Continuous N-in-flight pump** — as each task completes, immediately submit the next candidate (`asyncio.wait(FIRST_COMPLETED)` + refill). No wave barrier.
2. **Double-buffered candidate fetch** — seed just enough for the first in-flight set, then top up the pending queue in a background task while decode runs.
3. **Drop blind batch pause** — default `PREGENERATE_BATCH_PAUSE_SECONDS` / `pregen_batch_pause_ms` → **0**. Priority yield (`should_pause_for_priority`) still preempts for interactive browsing; isolation layer unchanged.
4. **Non-blocking mid-batch flush** — schedule write-queue flush as a background task; do not await it before the next submit. Final flush still awaited at batch end.
5. **Scan-size = remaining room** — never over-fetch a DB page past the slots left in the batch (avoids skipping unconsumed cursor rows).

Kept: HDD governor single-flight for reads, read-once harvest, decode-byte budget, interactive bypass, priority-scope preemption. HDD read concurrency not raised.

## Before / after

### Live prod before (observational — no code change, no restart)

3.1 min quiet window on running prod (`background_thumb_workers=8`, `pregen_batch_pause_ms=250`):

| Signal | Value |
|--------|-------|
| Physical `.thumbcache` files / 185.6s | **132 files → 42.7 files/min** |
| Session images / same window | **44 → 14.2 images/min** |
| API `recent_images_per_min` (end) | **17.5** |
| API `recent_thumbnails_written_per_min` | **47.7** |
| Disk `sde` read | **20.0 MB/s** (API `recent_read_mbps` 15.7) |
| `avg_source_read_seconds` / `avg_decode_encode_seconds` | 1.87s / 0.44s |
| py-spy (lane brief) | decode threads idle ~67%; usually 0–1 active |

### Worktree microbench (orchestration only — mixed fast/slow items)

```bash
web/.venv/bin/python scripts/bench_pregen_pump.py \
  --items 24 --workers 4 --decode-ms 150 --fetch-ms 100 --pause-ms 250
```

Every 4th item is 3× slower (prod-like head-of-line shape):

| Mode | Wall | Rate | Mean active | Peak | Busy frac |
|------|------|------|-------------|------|-----------|
| **OLD** wave barrier + 0.25s pause + serial fetch | 3.06s | 470.7 img/min | **1.08** | 4 | 0.75 |
| **NEW** continuous pump + overlap fetch + pause 0 | 1.94s | **743.5 img/min (~1.58×)** | **2.13** | 4 | 0.82 |
| **Real** `run_pregen_bulk_batch` pump | 1.72s | 835.2 img/min | 1.71 | **4** | **0.86** |

Stack-sample proof on the real pump: `peak_active = 4` (= configured workers), `busy_frac = 0.86` (decode no longer idle between waves).

### Expected on deploy (cold spindle, harvestslot already in tree)

Recovering the ~67% orchestration idle moves the live ~15–18 img/min toward disk-bound (~80–120 img/min when reads stream continuously under the single-flight HDD governor). Microbench shows **~1.6×** from the pump alone under mixed latencies; removing the 0.25s pause and overlapping DB scans adds the rest on the live path. Re-measure files/min + `sde` MB/s over ≥3 min after deploy — do not claim the live multiple until then.

## Tests

```text
cd web && python -m pytest -x -q test_thumbnails.py test_memory_pressure.py
83 passed
```

New coverage:

- `ContinuousPumpTests.test_slow_item_does_not_block_next_submit` — siblings start before the slow RAW finishes
- `ContinuousPumpTests.test_write_flush_does_not_serialize_decode_submits` — mid-batch flush does not stall the pump
- Existing priority-burst, pressure-abort, and anti-join tests still pass

## Risks

- **RSS / decode budget** still caps true concurrency — pump feeds up to `effective_prefetch_workers`, budget remains the RAM gate.
- **Priority batches** stay exclusive (no bulk-cursor mix-in) when a priority scope engaged mid-batch — same as before.
- **Prod `settings.local.json`** still has `pregen_batch_pause_ms: 250` until reconfigured; code default is 0, and the continuous pump helps even before pause is cleared.
- **Deploy** required for live after numbers — this worktree must not restart prod.

## Commit message (when asked to commit)

```
Bench: preview backfill pump 1.58× (mean decode concurrency 1.1 → 2.1); decode threads saturated, pipeline no longer idle between waves

Continuous N-in-flight refill + overlapped candidate fetch + drop blind 0.25s batch pause.
```
