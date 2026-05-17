# AI Feature

Owns: AI status, embedding pause/resume controls, model install requests, and model/status payloads used by settings and the bottom bar.
Depends on: `web/ai_models.py`, `web/settings.py`, the `thumbnails` package facade, `web/resource_governor.py`, app-configured `web/embedding_worker.py` DB providers, injected AI status/embedding/deep-search read providers, and injected settings-cache invalidation; imports `web/embedding_worker.py` only inside pause/resume/status handlers.
Public routes: `/api/ai/status`, `/api/ai/embeddings/pause`, `/api/ai/embeddings/resume`, `/api/ai/model/install`.
Frontend modules: `static/js/ai/status.js`, `static/js/settings/ai_status.js`, `static/js/settings/deep_search.js`, plus compatibility exports from `static/js/legacy/app.js`.
Data repositories: AI status reads are injected from `web/app.py`, currently backed by `web/db.py` compatibility facades that preserve stats/embedding cache behavior over `data/repositories/stats.py` and `data/repositories/embeddings.py`. Likely later direct repository owner after cache orchestration moves: `data/repositories/embeddings.py`.
Tests to run: `cd web && .venv/bin/python -m unittest test_embedding_worker test_embed_cache test_api_shapes test_modular_contracts -q`.
Do not touch: AI/settings key names, install role semantics, worker pause/resume strings, or deep-search readiness/cache fields without parity tests.
