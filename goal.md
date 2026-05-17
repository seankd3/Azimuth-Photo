# photoArchive Modularization Goal

## Operating Instruction

A previous agent produced this plan to accomplish the user's task. Treat this file as the source of user intent for the modularization run. Re-read live files as needed, preserve current behavior, and carry work through implementation and verification instead of stopping at analysis.

Parent instructions in `/home/sean/Projects/AGENTS.md` and repo instructions in `AGENTS.md` still apply.

## Summary

Refactor photoArchive into an agent-friendly modular monolith while keeping UX, URLs, API shapes, settings keys, data semantics, and runtime behavior identical. The app remains local-first, no-build frontend, and `uvicorn app:app` continues to work.

The organizing rule is: **features are vertical; shared engines are horizontal**. User workflows live in `features/`; reusable plumbing lives in `core/`, `data/`, `thumbnails/`, and shared frontend modules.

## Target Structure

```text
web/
  app.py                         # compatibility entrypoint exposing app
  core/
    app_factory.py               # create_app(), router/static/template wiring
    background.py                # startup/shutdown task tracking
    requests.py                  # JSON parsing, int coercion, validation helpers
    responses.py                 # image cards, visibility counts, response copies
    query_constraints.py         # q/deep/people/filter image-universe resolver
    cache_events.py              # invalidation/event fanout
    static_assets.py             # static version/template context

  features/
    pages/                       # HTML page routes
    library/                     # rankings, date groups, map markers
    compare/                     # Swiss, Top 50, mosaic, pick/undo
    search/                      # search, similar, duplicates, collections
    people/                      # People routes, service, worker/status contracts
    catalog/                     # scan, sources, folder picker/browse
    settings/                    # app settings, UI settings, reset/save
    cache/                       # cache status, pregen controls, clear
    media/                       # thumbnails, full images, media status, warm
    ai/                          # AI status, model install, embedding pause/resume
    export/                      # JSON/CSV export
    dev/                         # local diagnostics

  data/
    connection.py
    schema.py
    repositories/
      images.py
      catalog.py
      rankings.py
      ratings.py
      embeddings.py
      people.py
      cache_entries.py
      stats.py

  thumbnails/
    config.py
    memory_store.py
    disk_store.py
    generation.py
    full_cache.py
    pregen.py
    status.py

  static/js/
    bootstrap.js                 # exports window.PhotoArchive compatibility API
    api.js
    ui.js
    filters.js
    query_state.js
    media_status.js
    warmup.js
    library/
    compare/
    search/
    people/
    catalog/
    settings/
    cache/
    ai/
    loupe/
```

## Public Contracts To Preserve

- Keep all existing paths: `/`, `/library`, `/rankings`, `/compare`, `/settings`, `/people`, `/catalog`, `/api/*`.
- Keep JSON response shapes stable for rankings, compare, mosaic, search, similar, cache status, AI status, settings, catalog, media status, export, and People.
- Keep settings keys stable, including People keys and thumbnail/cache/AI keys.
- Keep `window.PhotoArchive.*` methods stable so templates and inline handlers continue working.
- Keep source-media safety unchanged: original photos/videos are never mutated or deleted.
- Keep no-build frontend workflow: browser-native ES modules only, no Vite/TypeScript migration in this refactor.

## Backend Refactor Plan

### 1. Add Safety Rails First

- Add route-registry parity tests that assert every current public route still exists.
- Add API-shape tests for People endpoints and People filter composition.
- Add import compatibility tests for `import app`, `import db`, and `import thumbnails`.

### 2. Create Core App Shell

- Move FastAPI construction, middleware, static mounting, templates, startup, and shutdown orchestration into `core/app_factory.py` and `core/background.py`.
- During migration, `web/app.py` may call `create_app_shell()` so existing compatibility aliases and feature dependency wiring stay stable.
- Final cleanup can reduce `web/app.py` to:

```python
from core.app_factory import create_app

app = create_app()
```

- Do not switch to the final shape until `create_app()` owns both route registration and the dependency/config wiring currently kept in `web/app.py`.
- Keep middleware behavior identical, including static cache headers and selective gzip.

### 3. Extract Shared Contracts

- Move `_json_object`, `_positive_int`, `_clamp_int`, template context, static versioning, image-card response helpers, visibility counts, response-copy helpers, and cache invalidation event hooks into `core/`.
- Create `core/query_constraints.py` as the single resolver for `q`, `deep`, `people`, metadata fallback, search scores, image-id filters, and cache-key fields.

### 4. Move Feature Routes

- Move route groups into `features/<name>/routes.py`, each exposing `router`.
- Keep route function names where practical for test compatibility, but route location becomes feature-owned.
- Each feature gets `service.py` for product orchestration and `contracts.py` for constants/response-shape helpers when needed.

### 5. Split Data Layer

- Move schema and migration checks into `data/schema.py`.
- Move connection setup/WAL handling into `data/connection.py`.
- Move SQL by domain into repositories.
- Keep `web/db.py` as a temporary facade re-exporting old function names until all call sites are migrated.

### 6. Split Thumbnail Engine

- Move thumbnail config/budget math, RAM cache, disk cache/index, generation, full-original caching, pregen worker, and status payload logic into `web/thumbnails/`.
- Keep the old `import thumbnails` path working through the `web/thumbnails/` package facade until all call sites are migrated.

## Frontend Refactor Plan

1. Replace `/static/app.js` with a small compatibility bootstrap that imports native modules and assigns `window.PhotoArchive`.
2. Move shared utilities first: API client, toast/modal UI, filter/query-state handling, media-status cache, warmup queue, preload helpers.
3. Move feature code into matching folders:
   - `library/`: grid, rankings fetch, sorting, batch mode, map entry.
   - `loupe/`: fullscreen image viewer, filmstrip, progressive tier loading.
   - `compare/`: Swiss, Top 50, mosaic, keyboard navigation.
   - `people/`: People page render/actions/filter-to-library.
   - `settings/`: settings form, cache/AI/People settings panels.
4. Keep template inline handlers working through `window.PhotoArchive`; template cleanup can happen later as a separate UX-neutral pass.
5. Keep CSS visually identical. Either preserve one `style.css` with section markers or split into multiple linked CSS files only after screenshot parity is proven.

## Documentation For Future Agents

Add a short `README.md` to each feature folder:

```text
Owns:
Depends on:
Public routes:
Frontend modules:
Data repositories:
Tests to run:
Do not touch:
```

Add or update root navigation docs explaining the feature/shared-engine rule and where new work should land. `AGENTS.md` already contains the modularization rule and must remain aligned with this file.

## Test Plan

- Run baseline before changes: `cd web && .venv/bin/python -m unittest discover -q`.
- After each extraction phase, run the full unittest suite.
- Add focused tests for:
  - route parity
  - API response-shape parity
  - People + search/filter composition
  - query constraint empty-intersection behavior
  - cache-key stability
  - app/db/thumbnails facade imports
- After frontend split, run browser smoke checks for Settings, People, Library, Compare/Mosaic, Loupe, filters, search, export, cache status, and AI status.
- Acceptance criterion: no visible UX change, no route/API regression, and full backend test suite passing.

## Migration Discipline

- Move code in large architectural slices, but keep each slice behavior-preserving.
- Use compatibility facades until all imports are migrated, then remove facades only in a final cleanup phase.
- Avoid opportunistic redesign, naming churn, or UI polish during modularization.
- Commit in coherent chunks: core shell, route extraction, data split, thumbnail split, frontend split, docs/tests cleanup.

## Assumptions

- "Full refactor" means complete modularization of current behavior, not a product rewrite.
- UX and functionality must remain identical.
- Native ES modules are the frontend target.
- This remains an in-place mainline cleanup, not a new sibling product tree.
