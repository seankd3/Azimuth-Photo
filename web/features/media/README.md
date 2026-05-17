# Media Feature

Owns: thumbnail serving, full-image serving, per-image/batch media status, and image warm scheduling.
Depends on: the `thumbnails` package facade, `web/features/media/warm.py`, injected cached-image lookup, injected memory-warm scheduler, and injected DB path for repository reads.
Public routes: `/api/thumb/{size}/{image_id}`, `/api/full/{image_id}`, `/api/image/{image_id}/media-status`, `/api/images/media-status`, `/api/images/warm`.
Frontend modules: `static/js/media_status.js`, `static/js/warmup.js`, `static/js/media_metadata.js`, `static/js/loupe/filmstrip.js`, `static/js/loupe/focus.js`, `static/js/loupe/metadata.js`, `static/js/loupe/navigation.js`, `static/js/loupe/status.js`, `static/js/loupe/tiers.js`, `static/js/loupe/zoom.js`, plus compatibility exports from `static/js/legacy/app.js`.
Data repositories: Image lookups read through `data/repositories/images.py`; cache membership still comes from the injected cached-image helper backed by `data/repositories/cache_entries.py`.
Tests to run: `cd web && .venv/bin/python -m unittest test_thumbnails test_api_shapes test_modular_contracts -q`.
Do not touch: original media files, ETag/cache-header semantics, cached-only 204 behavior, full-image browser-original fallback behavior, or warm-response shapes without parity tests.
