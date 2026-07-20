# Taste sort under active pregen — cache invalidation root fix

**Verdict:** Fixed. Live prod under running pregen still pays **7–13s per taste request** (unfixed process). Fixed worktree code under the same catalog + simulated continuous `sm` thumbnail writes: **~30ms repeats** (order cache survives). Commit message when shipping: `Bench: taste sort under pregen 24s → 0.4s`

## Root cause (verified — hypothesis corrected)

**Not** a bumping `_configured_db_signature()`. Wiring injects `db_signature=lambda: db.DB_PATH` — a static path string. Taste/order cache keys include it, but it does not change on writes.

**Actual killer:** every preview-backfill `sm` write calls `cache_events.note_cached_image_ids_added` → `library_service.invalidate_rankings_response_cache()`, which cleared **all** of:

- `_rankings_response_cache` (preview_ready on cards — legitimately stale after new thumbs)
- `_taste_rankings_order_cache` / `_taste_id_elo_cache` / `_blended_rankings_order_cache` (taste/Elo **order** — independent of thumbnails)

So under continuous pregen, taste paid a full order rebuild on every request (prod measured **7–13s** with active pregen on 2026-07-20; earlier lane notes **18–30s** when the embed matrix also had to reload). Elo stayed ~0.4s because SQL order is cheap.

Visible facet/count caches already have their own invalidation on thumb append; they did not need the taste order nuke.

## Fixes

1. **Split rankings invalidation** (`features/library/service.py` + `core/cache_events.py`)
   - `invalidate_rankings_response_cache(*, order_caches=True)`
   - Thumbnail/`sm` path: `order_caches=False` — drop response payloads only
   - Flags, picks, embeds, catalog: default `order_caches=True` (full clear)
   - Taste order still keys on `taste_vector_signature` (comparisons + embeds); Elo/taste changes still rebuild

2. **Embed matrix residency** (`embed_cache.py`)
   - Snapshot load uses file-backed `np.load(..., mmap_mode="r")` (~729MB read-only data)
   - Does **not** re-pin the GPU embedding model
   - `add_vectors()` copies mmap → writable ndarray before mutating
   - Respects memory gate: mmap is file-backed, reclaimable by the OS

## Proof (mandatory — under active / simulated pregen)

### Live prod BEFORE (unfixed process, pregen `state=running`, 2026-07-20)

| Call | Wall |
|------|------|
| `sort=taste&limit=200&offset=0` | 10.2s |
| `offset=200` | 10.9s |
| `offset=400` | 12.6s |
| `offset=0` again | 13.1s |
| `sort=elo&limit=200` | 0.38s |

Identical limit can occasionally hit a lucky response-cache window (~1ms) if no `sm` write lands between requests; changing offset/limit consistently re-pays multi-second rebuilds.

### Fixed worktree in-process (same prod catalog + embed snapshot, simulated `note_cached_image_ids_added` loops)

| Case | ms |
|------|----|
| Embed matrix mmap load | ~500–2500 (SQLite busy under live pregen) |
| Taste cold order build | ~4300–5900 |
| After pregen `sm` bumps (order preserved) | **205** then **~30** |
| Different limit order-hit under pregen | **21** |
| 3× identical repeats under continuous pregen bumps | **30 / 28 / 29** |
| After full elo/taste invalidate (must rebuild) | ~4600 |
| OLD behavior sim (`order_caches=True` each sm) | **481–565**/request |
| NEW thumb-style invalidate | **22–36**/request |

Order cache entry count stays `1` across pregen bumps; response cache drops to `0` (preview_ready refresh only).

### Ordering

Top-10 stable across pregen bumps and limit changes:

`[18643, 18647, 33708, 18640, 18645, 18628, 18623, 18650, 36396, 18655]`

Matches prior tastesort proof top-5. Flag/comparison-style full invalidate clears order caches (tested).

### Tests

```text
cd web && .venv/bin/python -m pytest -x -q test_embed_cache.py test_library.py \
  -k 'taste or snapshot_load or sm_thumbnail or rankings_response_cache_invalidates'
→ 14 passed
```

New coverage: `test_sm_thumbnail_writes_preserve_taste_order`, `test_snapshot_load_uses_file_backed_mmap`.

## Note for ship

Leave this worktree uncommitted until Sean asks. Prod `:8000` was not restarted and was not patched. After merge/restart under live pregen, expect sub-second taste repeats the same way the in-process bench shows.
