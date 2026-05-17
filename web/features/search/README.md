# Search Feature

Owns: text search endpoint, similar images, duplicate detection, EXIF extraction/cache/update, collections clustering, and query-result visibility shaping.
Depends on: `web/helpers.py`, `web/photo_metadata.py`, injected rankings/embedding/cache helpers, injected DB-backed active-source/image/metadata providers, and optional runtime imports of `web/embed_cache.py`, `numpy`, and `sklearn`.
Public routes: `/api/search`, `/api/similar/{image_id}`, `/api/duplicates`, `/api/image/{image_id}/exif`, `/api/collections`.
Frontend modules: `static/js/search/query.js`, `static/js/library/search_controls.js`, `static/js/library/search_state.js`, `static/js/media_metadata.js`, plus compatibility exports from `static/js/legacy/app.js`.
Data repositories: Routes use injected providers from `web/app.py`, currently backed by `web/db.py` compatibility facades to preserve model-key, active-source, image lookup, and metadata-update cache behavior. `service.visible_embedding_page` reads via `data/repositories/cache_entries.py` and `data/repositories/images.py`. Likely remaining direct repository owners after cache orchestration moves: `data/repositories/embeddings.py`, `images.py`, and `rankings.py`.
Tests to run: `cd web && .venv/bin/python -m unittest test_api_shapes test_embed_cache test_embedding_worker test_modular_contracts -q`.
Do not touch: metadata fallback behavior, deep-search/text-query behavior, similarity score formatting, duplicate/collection cache keys, or EXIF response shape without parity tests.
