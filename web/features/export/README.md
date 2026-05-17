# Export Feature

Owns: JSON and CSV ranking export surfaces.
Depends on: `core.requests.clamp_int`, injected `resolve_library_constraints`, and injected DB path for repository reads.
Public routes: `/api/export`.
Frontend modules: `static/js/export/actions.js` owns full-ranking and selected-image export URL construction/opening. `static/js/legacy/app.js` and `static/js/library/batch.js` keep compatibility facades for `PhotoArchive.exportRankings` and `PhotoArchive.batchExport`.
Data repositories: Requested-ID export reads through `data/repositories/images.py`; filtered ranking export reads through `data/repositories/rankings.py` with catalog counts from `data/repositories/stats.py`.
Tests to run: `cd web && .venv/bin/python -m unittest test_api_shapes test_modular_contracts -q`.
Do not touch: export field names, CSV column order, filename/header behavior, ID-order export behavior, or filter/search semantics without parity tests.
