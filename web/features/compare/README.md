# Compare Feature

Owns: compare-next delegation, comparison submit/undo, mosaic-next delegation, mosaic picks, and Elo propagation status/prediction endpoints.
Depends on: app-configured `web/elo_propagation.py` providers, `core.requests.json_object`, `core.requests.positive_int`, injected DB-backed ranking/pairing providers, injected rating write/undo providers, injected pairing-cache callbacks, and injected compare/mosaic next handlers.
Public routes: `/api/compare/next`, `/api/compare`, `/api/compare/undo`, `/api/mosaic/next`, `/api/mosaic/pick`, `/api/propagation/last`, `/api/propagation/predict`.
Frontend modules: `static/js/compare/page_controller.js`, `static/js/compare/images.js`, `static/js/compare/navigation.js`, `static/js/compare/propagation.js`, `static/js/compare/query.js`, `static/js/compare/status.js`, `static/js/compare/view.js`, plus compatibility exports from `static/js/legacy/app.js`.
Data repositories: Compare routes use injected rating write/undo providers from `web/app.py`, currently backed by `web/db.py` compatibility facades that preserve rating/cache invalidation over `data/repositories/ratings.py`. Compare service uses app-injected DB-backed ranking/pairing providers so its feature code stays independent of the facade while preserving current cache behavior. Likely later repository owners after cache orchestration moves: `data/repositories/ratings.py`, `data/repositories/rankings.py`, and `data/repositories/images.py`.
Tests to run: `cd web && .venv/bin/python -m unittest test_api_shapes test_modular_contracts test_backend -q`.
Do not touch: pair/mosaic selection semantics, comparison action IDs, undo behavior, propagation response fields, or existing compare/mosaic JSON shapes without parity tests.
