# LATENCY.md — interactive endpoint latency under bulk load (lane gxlat)

**Verdict:** Cached interactive thumbs were paying an HDD `lstat` on every request
before serving from SSD. Under bulk spindle load that alone blew past snappy
budgets and starved the request path. Fixed: cache-first serving, bounded cold
decode → 204+retry, catalog metadata off the default threadpool, faster grid retry.

## Client definition of "not responding"

`web/static/js/desktop/api.js` → `reportApiFailure()`:

```js
showToast("The library isn't responding.");
```

Fired when a desktop `fetch` fails with **no HTTP status** — i.e. `AbortError` /
`TimeoutError` from `AbortSignal.timeout`:

| Call path | Default timeout |
| --- | ---: |
| `fetchJson` / GET | **10_000 ms** |
| `requestWithStatus` mutations | **20_000 ms** |

Thumb `<img>` loads do **not** use this path (no toast). The toast is JSON API
timeouts — rankings, folders, settings, status — which stall when the event loop
or default threadpool is wedged by media/HDD work.

## Root causes (with evidence)

### 1. Warm thumbs stated the original before SSD cache (primary)

`thumbnail_response` always called `_source_state()` → `inspect_source_file`
(`os.lstat` / `realpath` on the HDD original) **before** memory/disk cache
lookup.

**Before probe** (`scripts/bench_gxlat_load.py`, slow inspect = 250ms, default
threadpool storm):

| Metric | p50 | p95 |
| --- | ---: | ---: |
| rankings | 0.28 ms | 0.37 ms |
| **thumb_sm_cached** | **356 ms** | **358 ms** |
| thumb_md_cold | 356 ms | 461 ms |

Cached sm thumbs paid ~full inspect latency every time. Rankings stayed fast
(already uses `sqlite_timeout(0.25)`).

### 2. Cold decode held the HTTP connection

On cache miss, `await thumbnails.get_thumbnail(...)` ran to completion. Under
HDD seek storms a RAW/TIFF demosaic can exceed the client's 10s JSON budget
(and hold uvicorn capacity). Interactive harvest correctly uses `bulk=False`
(governor bypass) — but raw seek contention still hurts when the request waits.

### 3. Catalog metadata/orientation used `run_in_executor(None, …)`

Orientation + EXIF batches occupied asyncio's **default** pool (anyio limiter
**40**). Interactive `asyncio.to_thread` (source inspect, disk reads) queued
behind bulk header/EXIF work. Orientation also touched the spindle **without**
the HDD governor.

### 4. What was already good

- HDD governor: interactive harvest `bulk=False` (verified in `harvest.py`)
- On-demand decode semaphore (`PHOTOARCHIVE_ON_DEMAND_DECODE_LIMIT`, default 2)
- User-activity classifier (status/health never count as browsing)
- Rankings short SQLite busy timeout (250ms) + stale fallback
- Sync hub work on dedicated pools (`features/sync/executor.py`)

## Fixes (surgical)

1. **`web/features/media/routes.py`** — catalog hints only before cache; serve
   memory/SSD first; filesystem inspect only on miss. When
   `hdd_governor.bulk_hdd_holds() > 0`, skip interactive lstat and try a
   **bounded** decode (`PHOTOARCHIVE_ON_DEMAND_FOREGROUND_TIMEOUT`, default
   1.5s). On timeout → **204 + `Retry-After: 1`** while decode continues via
   inflight. Same pattern for full/lg cold path.
2. **`web/features/catalog/metadata.py`** — dedicated `catalog-meta` threadpool;
   orientation classify now takes `bulk_hdd_slot_sync`.
3. **`web/static/js/desktop/grid.js`** — thumb soft-miss retry at 1s → 5s → 30s
   (was single 30s), so 204 pending fills sharpen quickly.

## Measured after

| Metric | before p50 | after p50 | before p95 | after p95 |
| --- | ---: | ---: | ---: | ---: |
| rankings | 0.28 | 0.18 | 0.37 | 0.26 |
| **thumb_sm_cached** | **356** | **3.6** | **358** | **4.0** |
| thumb_md_cold (bulk gate held) | 356 | **9.8** | 461 | 112 |

Raw JSON: `bench-runs/gxlat-before.json`, `bench-runs/gxlat-after.json`.
Re-run: `PHOTOARCHIVE_SMOKE_MODE=1 web/.venv/bin/python scripts/bench_gxlat_load.py --label after --out bench-runs/gxlat-after.json`

## Tests

```bash
cd web && python -m pytest -x -q test_gxlat_latency.py test_interactive_isolation.py \
  test_hddgov.py test_user_activity.py test_cache_status.py
# 21+ passed in focused media/isolation set
```

New: `web/test_gxlat_latency.py` — cached thumb must not call inspect; cold
over-budget returns 204 within 1s.
