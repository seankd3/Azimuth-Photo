# Cache Feature

Owns: thumbnail cache status, pregeneration start/stop/status, and thumbnail cache clearing.
Depends on: the `thumbnails` package facade, `features/cache/status.py`, injected DB-path/catalog-count providers, injected `build_ai_status`, and the settings-cache expiry callback configured by `web/app.py`.
Public routes: `/api/cache/status`, `/api/cache/pregen/start`, `/api/cache/pregen/stop`, `/api/cache/pregen/status`, `/api/cache/clear`.
Frontend modules: `static/js/cache/guide.js`, `static/js/cache/status.js`, `static/js/settings/cache_status.js`, plus compatibility exports from `static/js/legacy/app.js`.
Data repositories: Cache status reads browser-original and ahead-window counts through `data/repositories/stats.py` using injected DB-path/catalog-count providers. Routes still call the `thumbnails` package facade directly for cache stats, pregen controls, and cache clearing. Likely later repository owner for cache-entry-only helpers: `data/repositories/cache_entries.py`.
Tests to run: `cd web && .venv/bin/python -m unittest test_thumbnails test_api_shapes test_modular_contracts -q`.
Do not touch: source media, cache status field names, cached-only response behavior, or pregeneration state contracts without parity tests.
