# PATHORDER — read previews in filepath order (kill HDD seek storm)

**Verdict:** Bulk candidate selection already walked `(source_id, filepath, id)`. This lane confirms that order, aligns priority scopes to the same key, and measures the read-rate win of path-contiguous RAW vs scattered folders.

**Proposed commit message:**
`pregen: keep path-order candidate sweep (Bench: RAW dense 26.6 vs scatter 11.3 MB/s; live still ~4–5 MB/s)`

## Change

File: `web/thumbnails/pregen.py`

1. **`candidate_batch`** — already `ORDER BY i.source_id ASC, i.filepath ASC, i.id ASC` with a matching keyset cursor. Docstring now states the physical-order rationale (exFAT has no FIEMAP).
2. **`priority_candidate_batch`** — aligned to the same order (`source_id, filepath, id`) so priority folder waves stay directory-contiguous after the user-facing scope jumps the queue. Worker still tries priority scopes first, then falls back to bulk path order.

No schema change. Anti-join / O(pending) wavefix untouched. Vault, burst windows, HDD governor, bulk_scheduler untouched.

## EXPLAIN QUERY PLAN (DB copy `/tmp/pathorder-bench/photoarchive-copy.db`)

Candidate query with cache anti-join + path order (current):

```
SEARCH i USING INDEX idx_images_missing_source_filepath_id (missing_at=? AND source_id>?)
CORRELATED SCALAR SUBQUERY → cache_entries covering PK
SEARCH s USING COVERING INDEX idx_catalog_sources_active
```

No `USE TEMP B-TREE FOR ORDER BY`. Existing index
`idx_images_missing_source_filepath_id (missing_at, source_id, filepath, id)` covers the walk.

Hypothetical `ORDER BY i.id ASC` (brief’s “before”):

```
SEARCH i USING INDEX idx_images_source_missing_id
… 
USE TEMP B-TREE FOR ORDER BY
```

Path order is the better plan. Query time for `LIMIT 500` pending: path ~25ms, id ~28ms — no sort regression.

**Index note (do not add):** keep using `idx_images_missing_source_filepath_id`. No new index needed.

## Read-rate bench (2026-07-20, read-only vs `/mnt/expansion`)

Method: full-file sequential reads of pending images (no cache writes). Live pregen was running concurrently (`prefetch_workers=2`, ~4–5 MB/s reported) — both arms under the same contention.

### JPEG frontier (50 files)

| Arm | Dirs | MB/s | img/min | Notes |
|---|---|---|---|---|
| Path-order frontier | 3 | **29.1** | 109 | Lexically-early pending |
| Id-order frontier | 1 | **39.2** | 142 | Happens to be one dense folder |
| Dense folder path order | 1 | **86.4** | 313 | Contiguous folder streams |

### RAW / DNG / CR2 (40 files) — matches the seek-bound workload

| Arm | Dirs | MB/s | img/min | avg read |
|---|---|---|---|---|
| Path-order RAW frontier | 8 | **16.7** | 63 | 0.95s |
| Id-order RAW frontier | 1 | 12.0 | n/a | Tiny legacy CR2s — not comparable |
| Dense RAW dir, path order | 1 | **26.6** | 78 | 0.77s |
| One-file-per-dir scatter | 40 | **11.3** | 55 | 1.10s |

**Headline delta:** path-contiguous dense RAW **26.6 MB/s** vs scattered folders **11.3 MB/s** (~2.4×). Live pregen still reports ~4.3–5.4 MB/s / ~5–10 img/min — path order is necessary but not sufficient under dual prefetch workers + embedded-preview seeks inside large DNGs.

Locality of current pending head (`LIMIT 500`): path order → 32 dirs / 31 jumps; id order → 14 dirs / 13 jumps. Path order can start in lexically-early sparse leftovers while a dense new folder sits later in path space — expected, and still better than thrashing one-file-per-dir.

## Risks

- **Priority vs bulk:** unchanged product behavior — recent user folders still jump the queue; only within-scope order is path-contiguous.
- **Sparse path head:** finishing old sparse dirs before a dense recent import can look slower than id-order on today’s frontier; physical locality still favors path order once each folder is streamed.
- **Remaining bottleneck (out of scope):** `prefetch_workers=2` dual-reads on one spindle, plus within-file seeks for embedded DNG previews. Do not change here.
- **No prod touch:** bench used a DB copy + read-only opens; thumbcache not modified.

## Tests

`cd web && python -m pytest -x -q test_thumbnails.py` → **67 passed** in 4.09s.
