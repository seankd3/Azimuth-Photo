# Task hygiene (impl-taskhygiene)

Kill `"Task exception was never retrieved"` on fire-and-forget refresh work, and move blocking catalog-browse filesystem calls off the event loop.

## Shared helper

`core.background.track_background_task` — module-level `BackgroundTaskTracker.track` wrapper. Same done-callback as `AppShell.track_background_task`: retrieves `task.exception()` and logs it so failures are not left unretrieved.

## Sites fixed (A — exception retrieval + in-coro logging)

| Site | Change |
|------|--------|
| `data/repositories/rankings.py` `_refresh_date_histogram` / `_refresh_date_groups` | `track_background_task` + `except Exception: log.exception` before clearing refreshing set |
| `data/repositories/filter_options.py` `_refresh_filter_options` | same |
| `data/repositories/stats.py` SWR path | `_swr_full_stats_refresh` logs+swallows; both SWR and cold path spawn via `track_background_task` |
| `features/catalog/routes.py` `_refresh_folders` + `_run_scan` spawns (`start_scan`, add source, rescan) | `track_background_task`; folders refresh logs failures |
| `features/library/geo_routes.py` backfill / infer | `track_background_task` |
| `features/sync/hub_routes.py` hash backfill | `track_background_task` |
| `features/develop/import_routes.py` develop + lrcat scans | `track_background_task`; lrcat worker now logs failures |

## Sites fixed (B — blocking FS off the loop)

| Site | Change |
|------|--------|
| `api_catalog_browse` | payload built in sync `_browse_dir(current)` via `await asyncio.to_thread(_browse_dir, current)` (includes `quick_browse_roots` + scandir) |
| `start_scan` | `os.path.isdir` via `await asyncio.to_thread(os.path.isdir, folder)` |

## Test evidence

```text
cd web && .venv/bin/python -m pytest -x -q test_taskhygiene.py
# 4 passed

cd web && .venv/bin/python -m pytest -q test_taskhygiene.py test_catalog.py \
  test_backend.py test_settings_status.py test_stacks.py test_quiet_sources.py \
  test_perf_budgets.py --disable-warnings
# 85 passed
```

Proofs in `web/test_taskhygiene.py`:

1. **SWR refresh failure** — stale filter_options return while refresh raises; asserts ERROR log and `_filter_options_refreshing` cleared afterward.
2. **Exception retrieval** — tracker done-callback spy sees the raised `RuntimeError` (no unretrieved leak).
3. **Browse contract** — `api_catalog_browse` source uses `asyncio.to_thread(_browse_dir, …)` and does not call `os.scandir` / `os.path.exists` inline; `_browse_dir` is sync and callable in-thread.

## Risks

- Module-level `_fire_and_forget` tracker is separate from `AppShell.background_task_tracker`, so shutdown `cancel_all` does not cancel these SWR/scan tasks. They are short-lived; failed ones still log. Wiring them into the shell tracker would be a follow-up if shutdown races appear.
- Stats cold path awaiting a failed SWR task (invalidate mid-flight) can still see `None`; rare and pre-existing shape of sharing `_stats_inflight_task`.
- No behavior change to successful cache payloads or browse JSON — only logging and thread offload.
