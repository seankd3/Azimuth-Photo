# COUNTS — `/api/counts` unfiltered path (impl-counts)

**Lane:** `impl-counts` · **Date:** 2026-07-20 · **Branch:** `impl-counts` (uncommitted)

## Root cause

Unfiltered `/api/counts` always ran the full aggregate in `rankings.scope_counts`:

```sql
SELECT COUNT(*), SUM(flag='picked'), SUM(flag='rejected')
FROM images i JOIN catalog_sources s ...
WHERE included=1 AND status IN ('kept','maybe') AND missing_at IS NULL AND vc_of IS NULL
```

`EXPLAIN` walked every active row via `idx_images_source_missing_id` (~88–135 ms on ~148k actives). Rankings already short-circuits totals off `SUM(catalog_sources.active_image_count)`; counts did not.

## Chosen approach

**Reuse the maintained counter + flag index seeks** (no new schema / no `ANALYZE`):

| Field | Source |
|-------|--------|
| `total` | `SUM(active_image_count) WHERE included=1` minus active virtual copies (`INDEXED BY idx_images_vc_of`) |
| `picked` / `rejected` | `COUNT(*) … INDEXED BY idx_images_active_flag_elo` + included-source join + `vc_of IS NULL` |

Why this over a covering-index total: the denormalized counter is already kept honest by scan / trash / import / source remove (`update_source_counts_on_conn`). Flag flips do not affect `total`. Filtered scopes (folder, q, stacks=collapsed, exclude_sources, …) keep the existing aggregate path via `has_ranking_count_filters`.

VC subtraction is required for correctness: `active_image_count` historically includes virtual copies; ranking filters hide them with `vc_of IS NULL`. Without `INDEXED BY idx_images_vc_of`, the planner walked all actives for that subquery (~100 ms) — forced index keeps it sub-ms when VC count is 0.

## Before / after

### HTTP `/api/counts` (20 samples, warm)

| Surface | p50 | p95 | Body |
|---------|-----|-----|------|
| Prod baseline (lane brief) | **108.8 ms** | — | — |
| Prod remeasure 2026-07-20 (unchanged code) | **135.3 ms** | 151.1 ms | `148461/7/7` |
| Worktree after (local :59501, bench DB copy) | **6.2 ms** | 7.3 ms | `148461/7/7` |

Bench delta for commit message: **counts 108.8 ms → 6.2 ms p50** (prod-shaped catalog; ~22×). SQL alone is ~0.1 ms; remaining ~6 ms is connect/pragma/HTTP.

### SQL on DB copy (`~/.cache/pa-impl-counts/bench.db`)

| Query | Plan | p50 |
|-------|------|-----|
| **Before** full aggregate | `SEARCH s` + `SEARCH i USING idx_images_source_missing_id` (walk) | **68.6–98.9 ms** |
| **After** fast path | `SCAN catalog_sources` + `idx_images_vc_of` + 2× `idx_images_active_flag_elo` | **0.09 ms** |

## Correctness proof

1. **Live equality on 148k catalog:** fast path `==` full `COUNT(*)` aggregate → `{total:148461, picked:7, rejected:7}`.
2. **Unit test** `test_unfiltered_scope_counts_match_full_aggregate_across_states`:
   - empty filter with an active virtual copy (VC hidden from total/picked)
   - after simulated trash + `_update_source_counts`
   - filtered `orientation=` still matches via aggregate path
3. **Mutation coverage (existing):** trash updates counters (`trash/service.py` → `update_source_counts_on_conn`); `test_trashed_rows_vanish_from_rankings_and_counts` passes.

```text
cd web && python -m pytest -x -q test_library.py -k 'counts' …
→ 9 passed (incl. new correctness test)
```

## Risks

- **Counter drift** if a future mutation path changes active rows without `update_source_counts_on_conn`. Mitigated by existing call sites (scan, trash, import, source remove) + VC index force. Prefer fixing the mutation over re-adding the 100 ms walk.
- **`INDEXED BY`** on flag/VC indexes assumes those indexes stay present (they are schema-owned).
- Filtered/hot scopes unchanged — only the unfiltered global path is accelerated.
