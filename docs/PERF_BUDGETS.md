# Performance budgets

Azimuth Photo treats browse speed as a product invariant. `web/test_perf_budgets.py`
runs in the default pytest suite, so `scripts/deploy.sh` cannot ship a regression
that exceeds one of these budgets.

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

## Re-baselining

Run the focused module on omarchy when the hardware or intentional query shape
changes:

```bash
cd web
PHOTOARCHIVE_SMOKE_MODE=1 .venv/bin/python -m pytest -q -s test_perf_budgets.py
```

Use the reported medians to update the table and `BUDGET_MS` together. Set each
budget to roughly five times the measured median, rounded up to a practical
millisecond value. Do not re-baseline to hide an unexplained regression: compare
against the existing budget first and fix or explicitly accept the product tradeoff.
