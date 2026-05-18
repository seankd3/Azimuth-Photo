# Library Feature

Owns: rankings endpoint delegation, date groups, map markers, filter options, stats, and library filter/search composition for those payloads.
Depends on: injected DB-backed providers from `web/app.py`, injected `resolve_library_constraints`, injected cache-root provider, and injected rankings handler.
Public routes: `/api/rankings`, `/api/date-groups`, `/api/map/markers`, `/api/filter-options`, `/api/stats`.
Frontend modules: `static/js/library/batch.js`, `static/js/library/date_scrubber.js`, `static/js/library/display.js`, `static/js/library/flags.js`, `static/js/library/map.js`, `static/js/library/navigation.js`, `static/js/library/query.js`, `static/js/library/rank_cards.js`, `static/js/library/search_controls.js`, `static/js/library/search_state.js`, `static/js/library/shell.js`, `static/js/library/sort.js`, plus shared `static/js/filters.js`, `static/js/query_state.js`, and compatibility exports from `static/js/legacy/app.js`.
Data repositories: The service uses app-injected DB-backed providers so current `db.py` cache behavior remains intact while feature code stays independent of the facade. Likely repository owners after data split: `data/repositories/images.py`, `data/repositories/rankings.py`, and `data/repositories/stats.py`.
Tests to run: `cd web && .venv/bin/python -m unittest test_backend test_api_shapes test_modular_contracts -q`.
Do not touch: ranking semantics, visibility count fields, cache-key/filter fields, map/date-group filtering, or thumbnail visibility filtering without parity tests.
