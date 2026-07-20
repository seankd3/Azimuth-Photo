# PREGENDIAG — preview backfill stalls after ~9

**Verdict:** Candidate feed was starving, not generation. Bulk preview anti-join OR'd in `full`, and with full tier at 100% util those rows are unactionable. Under priority scan (`scan_batch=2` while idle &lt; 5s — status probes keep this hot), the keyset walks a ~4300-row full-only desert in 8-row bites, returns `-1` (no progress) twelve times, and false-completes while ~52k thumb-pending remain.

## Root cause (exact branch)

**Where:** `_pregen_bulk_candidate_batch` → `candidate_batch(..., missing_sizes=sm|md|lg|full)` then `run_pregen_bulk_batch` collect filter, then worker `generated < 0` → `no_progress_scan_passes` → complete.

**Prod numbers (DB copy + live status 2026-07-20):**

| Metric | Value |
|--------|------:|
| Included online images | 148461 |
| `sm` cached | ~97890 |
| Thumb-pending (any of sm/md/lg) | 52662 |
| Full-missing with thumbs already warm | 55956 |
| Full tier util | **100%** (~11KB room) |
| Gap (full-inclusive order) between early thumb-pending clusters | **max 4304** |

**Stall branch values (instrumented / harness, OLD behavior):**

```
OLD+priority scan_batch=2 sizes=['sm','md','lg','full']
call0: decision=4 pending=4 scanned=8   ← the "~9" burst (a few small waves)
call1..call12: decision=-1 pending=0 scanned=8 empty_pages=4
STALL BRANCH: false-complete after 12 no-progress passes
  (cursor still mid-catalog, thumb work remains)
```

Decision name: `no_progress_scan` / worker treats `generated=-1` until `no_progress_scan_passes >= 12` → state `complete`, then loops and often shows `running` again on the next attempt — net **~0 sustained generation**.

**Hypothesis check:** Cursor-doesn't-rewind is *part* of it (keyset advances through unactionable full-only rows and does not rewind until end). The deeper bug is **polluting the preview anti-join with `full`**, so the cursor's "pending" stream is not O(thumb-pending).

Idle/full-size scans (`scan_batch=1024`) mostly survive the 4304 gap (one painful call) and continue — which matches intermittent slow progress (~6/min) when probes briefly stop. Priority + pollution matches the hard flatline.

## Fix

1. **`_pregen_bulk_candidate_batch`:** anti-join **thumb tiers only** (`sm`/`md`/`lg` with budget). Originals stay on `_pregen_full_candidate_batch` / `run_full_warm_batch` (preview-first).
2. **`run_pregen_bulk_batch`:** empty SQL pages that yield 0 actionable candidates no longer burn the tight 4-page cap alone — allow up to 64 empty pages before `-1` (belt-and-suspenders against future pollution).
3. **Diag:** `PHOTOARCHIVE_PREGEN_DIAG=1` logs `bulk_scan_page` / `bulk_batch` decision fields.

## Before / after persistence

**Before (OLD+priority):** 1 useful call (4 pending) then 12× `-1` → stall.

**After (NEW+priority, thumbs-only):** every call fills `pending=16`.

**90s feed against DB copy → temp `.diag/run/thumbcache`:**

```
batch=1..200: warmed 256/batch, files climb to 52662 (all thumb-pending once)
elapsed=90.1s batches=1157 warmed=294542 files=52662
files_per_min≈35000 (stub writes; proves continuous feed, multi-pass, no stall)
decision=steady_feed
```

Unique physical files reached the full 52 662 thumb-pending set in ~14s and kept feeding on subsequent passes — no post-first-batch idle.

## EXPLAIN QUERY PLAN (thumbs-only anti-join)

```
SEARCH i USING COVERING INDEX idx_images_missing_source_filepath_id (missing_at=? AND source_id>?)
CORRELATED SCALAR SUBQUERY → SEARCH c USING COVERING INDEX sqlite_autoindex_cache_entries_1
  (cache_root=? AND size=? AND image_id=?)   ×3 (sm/md/lg)
SEARCH s USING COVERING INDEX idx_catalog_sources_active
```

Indexed keyset + PK anti-join lookups — O(pending pages), not a full 146k table scan per image.

## Tests

```
cd web && python -m pytest -x -q test_thumbnails.py
→ 69 passed
```

New coverage: bulk excludes full-only rows; full-only desert under priority scan still reaches thumb-pending; full-only work deferred to full phase.

## Risks

- Preview bulk no longer warms `full` in the same wave. Full still runs when bulk returns `<= 0` (`run_full_warm_batch`). During a large thumb backlog, originals wait — intentional preview-first.
- Status/API probes that keep `idle_seconds < 5` still shrink `scan_batch` to 2; with thumbs-only that remains correct, just smaller waves.
- Empty-page budget (64) is a safety net; primary correctness is the thumbs-only anti-join.

## Files touched (uncommitted)

- `web/thumbnails/__init__.py` — thumbs-only bulk missing_sizes
- `web/thumbnails/pregen_worker.py` — empty-scan budget + `PHOTOARCHIVE_PREGEN_DIAG`
- `web/test_thumbnails.py` — regression tests
- `.gitignore` — `.diag/`
- `PREGENDIAG.md` — this report
