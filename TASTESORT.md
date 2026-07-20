# Taste sort speed — `/api/rankings?sort=taste`

**Verdict:** Fixed. Interactive taste sort goes from **~10–16s cold (prod)** to **~500ms first build / ~15ms order-hit / ~1.5ms response-cache**, without changing ranking meaning.

Commit message when shipping: `Bench: taste sort 16s → 497ms cold / 13ms order-hit`

## Profile (before)

Live prod (`:8000`, pid 1694261), 2026-07-20:

| Case | Wall clock |
|------|------------|
| `sort=elo&limit=200` warm | ~1.0s |
| `sort=taste&limit=200` cold (response-cache miss) | **9–16s** (15.9s, 12.1s, 9.2s) |
| same, response-cache hit | **1.5ms** |

`py-spy record` during a cold taste request (noisy — develop/raw work shares the process). Taste-path frames that matter:

| Hot spot | Role |
|----------|------|
| `get_rankings(limit=total≈148k)` + `_annotate_caption_presence` | Full-library row fetch + caption pass |
| `api_rankings_impl` loop @ image_card / per-row `np.dot`+`norm` | Python scoring of every row |
| Python `list.sort` over ~148k cards | Materialize-then-sort |

Component microbench against the same catalog (148 461 images, 46 634×4096 embeds):

| Step (old path) | ms |
|-----------------|----|
| Full `get_rankings(limit=all)` | ~4000 |
| Per-image numpy score + sort | ~900–1500 |
| `image_card` × all rows | ~1400–2700 |
| **Total (no HTTP)** | **~7–10s+** |

Dominant cost was **not** a missing matmul alone — it was scoring + card-building the entire library on every cold request. Matmul itself is ~40ms; `np.linalg.norm(matrix, axis=1)` was ~1s (replaced with einsum ~60ms).

## Fix

1. **Vectorize** taste similarities: one `matrix @ taste_vector` (+ cached einsum row norms) in `taste.taste_similarity_scores`, shared with Elo-blend predictions.
2. **Lite order keys**: `ranking_id_elo` → `(id, elo)` only (sync SQLite in a thread), not full rows + captions.
3. **Page-only cards**: cache ordered id list + taste scores; fetch the page via `IN (...)` (`ranking_rows_by_ids`), build `image_card` for the page only.
4. **Caches** (invalidated with rankings / taste invalidation):
   - similarity / scaled-score prediction cache (existing, extended)
   - row-norm cache by matrix identity
   - taste id↔elo list cache
   - taste ordered-id cache (like blended Elo order)
5. **Unfiltered counts** via `get_visible_pairing_pool_counts` (same pattern as taste-blend).

Blend formula / earned Elo are untouched. Taste sort still exposes raw cosine `taste_score`. Tie-break among equal taste+elo is now **deterministic by id ASC** (documented improvement vs unstable SQLite order among Elo ties).

## Proof (after)

In-process against prod catalog DB + warm embed snapshot (2026-07-20):

| Case | ms |
|------|----|
| First sims compute (norms + matmul + dict) | 337 |
| Cold order build (id_elo miss, sims warm) + page 200 | **497** |
| Order rebuild (id_elo warm, sims warm) + page | **174** |
| Order-cache hit (page only) | **13** |
| Prod response-cache hit (unchanged) | ~1.5 |

Ordering: **top-5 ids match prod old path** `[18643, 18647, 33708, 18640, 18645]`. Deeper ties among equal taste+elo may swap vs the old unstable order; primary ranking meaning unchanged.

Tests: `cd web && python -m pytest -x -q test_library.py -k taste` → **11 passed**.

## Risks

- First request after process start still pays embed matrix load if not snapshot-warm (separate from this fix; snapshot now ~729MB under embed cache dir).
- Id↔elo + ordered-id caches hold ~148k ints — small vs the embed matrix; cleared on rankings/taste invalidation.
- Deterministic id tie-break can reorder equal-score neighbors vs old SQLite-stable order — scores and primary sort key unchanged.
