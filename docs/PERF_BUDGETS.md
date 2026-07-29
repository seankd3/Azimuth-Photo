# Performance budgets

Azimuth Photo treats browse speed as a product invariant. `web/test_perf_budgets.py`
runs in the default pytest suite, so `scripts/deploy.sh` cannot ship a regression
that exceeds one of these budgets.

## Interactive latency under load

The number the speed doctrine gets held to: browse stays snappy while bulk work
(pregen / captions) grinds in the background.

```bash
# Against a running server (default http://127.0.0.1:8000) — GET-only, prod-safe
./scripts/bench_interactive.py
./scripts/bench_interactive.py http://127.0.0.1:8000 --loops 10 --warmup 2
./scripts/bench_interactive.py http://127.0.0.1:8000 --with-load --check
# Optional durable evidence; choose a path outside the checkout.
./scripts/bench_interactive.py --history /tmp/azimuth-photo/interactive-history.jsonl
```

Each measured cycle mimics real browsing: one library grid page, 20 `sm`
thumbnails, one `md` preview, one search query, one rankings page. Warm-up
cycles run but are excluded from p50/p95/p99. The default run is fully read-only:
it makes GET requests and writes no evidence. Pass `--history` to keep a JSONL
record outside the checkout when a comparison needs to be retained.

### Load policy (stated choice)

`--with-load` does **not** inject work. Starting pregen/captions is a mutating
POST and is unsafe when this bench is pointed at prod. The second phase runs the
identical mix while polling read-only `GET /api/cache/pregen/status` and
`GET /api/captions/status`, recording whether bulk workers were naturally
active. Findings go in the report; this tool does not fix performance.

### Initial budgets (to be ratified)

| Class | Budget | Notes |
| --- | ---: | --- |
| grid (`/api/rankings` date sort) p95 | **< 150 ms** | LAN, warm |
| sm thumb p95 | **< 80 ms** | LAN, warm |

Mark as initial until product ratifies. `--check` exits non-zero when either
misses. Fixture acceptance runs use the QA `ProbeServer` + catalog (same boot
path as `scripts/bench.py` / desktop QA).

The test builds one temporary, synthetic 2,000-image catalog per module and shares
it across its checks. Every image has an `sm` cache entry, so it covers both the
normal Library first-page route and the cache-first visible-thumbnail query used by
Library sorts. It never opens the real archive or creates source image files.

| Path | Median baseline (2026-07-12) | Budget |
| --- | ---: | ---: |
| `GET /api/rankings?limit=100&sort=elo` | 38.7 ms | 200 ms |
| `GET /api/date-histogram` | 9.7 ms | 50 ms |
| `GET /api/counts` | 8.2 ms | 50 ms |
| `GET /api/filter-options` | 21.9 ms | 125 ms |
| Library thumbnail-visible query | 25.2 ms | 125 ms |
| 2,000 thumbnail signatures | 3.5 ms | 20 ms |
| Collection suggestions, cold | 37.5 ms | 750 ms |
| Collection suggestions, cached | 3.0 ms | 30 ms |

Each check asserts the median of three calls. Route and query caches are cleared
before each sample so the budget continues to cover the real data path; the
synthetic database itself is reused to keep setup representative and fast.

## Proposed daily-workflow SLOs (not ratified)

Targets for the workflows photographers repeat all day. **Covenant** rows are
already product doctrine; **proposed** rows need ratification against measured
cold/warm, local/NAS baselines before any implementation is held to them. A
blank target means measure first — it does not mean "fast enough". Never widen
a budget to mask slowness, and never let a faster number hide an incomplete
result: zero omitted or misordered photos is part of every row.

| Daily workflow | Target | Status |
| --- | --- | --- |
| Cull/Refine/ranking action → visible acknowledgement | < 50 ms perceived, no spinner | covenant |
| Prepared local workflow (server absent, network denied) | zero network dependency | covenant |
| Durable ranking-action append | p95 ≤ 50 ms, p99 < 100 ms | proposed |
| Dual click → both photos replaced | p95 ≤ 50 ms, p99 < 100 ms | proposed |
| Export start → acknowledgement | ≤ 50 ms | proposed |
| Launch → first usable library | measure cold/warm, local/NAS first | proposed |
| Grid first content and sustained scroll | measure first; no omitted photos | proposed |
| Loupe open and next/previous | measure cached/uncached, RAW/raster first | proposed |
| Develop adjustment → preview update | measure first; input never dropped | proposed |

## Re-baselining

Run the focused module on omarchy when the hardware or intentional query shape
changes:

```bash
cd web
AZIMUTH_SMOKE_MODE=1 .venv/bin/python -m pytest -q -s test_perf_budgets.py
```

Use the reported medians to update the table and `BUDGET_MS` together. Set each
budget to roughly five times the measured median, rounded up to a practical
millisecond value. Do not re-baseline to hide an unexplained regression: compare
against the existing budget first and fix or explicitly accept the product tradeoff.

## Live-catalog measurements (139k images, omarchy prod)

Synthetic budgets above ≠ the real system. Dated spot-measurements of live prod
(`curl` total time, hub on omarchy, catalog ~139k images):

| Endpoint | 2026-07-15 (cold) | 2026-07-15 (warm) |
| --- | ---: | ---: |
| `GET /api/rankings?limit=100&sort=elo` | 439 ms | 1.6 ms |
| `GET /api/rankings?limit=100&sort=date_taken` | 809 ms | — |
| `GET /api/date-histogram` | 94 ms | — |
| `GET /api/counts` | 105 ms | — |
| `GET /api/filter-options` | 3 ms | — |
| `GET /api/collections/suggestions` | **11.0 s** | 19 ms |

Method: measure ~40 min after a service restart (route caches cold for the slow
paths, OS page cache warm). Re-measure after any query-shape change and append a
column — do not overwrite history. Top offender: cold collection suggestions
(once per boot; was reported at 60 s on 2026-07-15 pre-merge, now 11 s).
