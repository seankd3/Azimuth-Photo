# Codebase map

Cold-start orientation for coding agents. For setup, route conventions, edit
ownership, and the verification ladder, read [`development.md`](development.md).

## Runtime topology

- Entry point: `web/app.py` imports `core.app_factory.create_app()`; the factory mounts `/static`, templates, feature routers, middleware, and lifecycle hooks.
- Repo launcher: `scripts/photoarchive-server` runs
  `web/.venv/bin/uvicorn app:app --host HOST --port PORT`; default is `127.0.0.1:8000`,
  `PHOTOARCHIVE_ACCESS=tailscale` binds the Tailscale IPv4.
- Service: `/etc/systemd/system/photoarchive.service` runs as `sean` from `web/`,
  waits for Tailscale, binds its IPv4 on `:8000`, restarts on failure; Tailscale
  Serve supplies the phone-facing HTTPS `:8443` URL.
- Runtime paths: `web/core/runtime_paths.py` selects platform-native data,
  config, cache, and state roots for clean installs. Existing in-repo catalog,
  settings, preview, model, embedding, and run paths remain exact legacy local
  state; startup never migrates them.
- Startup warms templates/query caches, then schedules thumbnail prefetch, cleanup, orientation/metadata scans, embedding, People, and caption loops; shutdown stops prefetch and cancels tracked tasks (`core/background.py`).
- Embeddings start paused until Background Work resumes them; People is paused
  by default; captions auto-resume only when `caption_scan_enabled` is true;
  pending rows/IDs survive, but pause, cursor, and retry state reset.

## Backend layout

`web/features/` is route/service ownership; keep SQL in `web/data/repositories/`.

- `access/` — authentication/access helpers and protected-route behavior.
- `ai/` — embedding status, model controls, and AI worker endpoints.
- `cache/` — thumbnail cache status and pregeneration controls.
- `captions/` — caption scan status, pause/resume, and caption edits.
- `catalog/` — source folders, catalog scans, and catalog status.
- `collections/` — regular/smart collections and collection graph operations.
- `compare/` — Refine pair/mosaic interactions and rating writes.
- `dev/` — development/status diagnostics.
- `export/` — catalog/image export endpoints.
- `foreground/` — foreground/manual work coordination endpoints.
- `imports/` — import execution and import-history scoping.
- `library/` — ranked library, filters, dates, maps, and visible-image queries.
- `media/` — thumbnail/full-image/media response endpoints and warming.
- `pages/` — desktop, mobile, and share-gallery page routes.
- `people/` — face scan, labels, merges, and People status.
- `publish/` — published-node snapshots and static website bundle publishing.
- `search/` — metadata/embedding search routes and result shaping.
- `settings/` — settings reads/writes and composed background status.
- `share/` — private share-token/gallery routes and share mutations.
- `shared/` — Shared triage/aggregation across published and private work.
- `stacks/` — burst/variant/cross-source/manual stack operations.
- `trash/` — source-local trash, restore, and permanent empty-trash actions.

`web/data/` — `schema.py` owns SQLite schema/migrations; `connection.py` owns async connections; `repositories/` owns catalog/images, rankings/ratings, collections, cache, captions, embeddings, imports, people, publishing, shares, stacks, stats, filters, and common helpers.

`web/core/` — `app_factory.py` composition/lifecycle; `background.py` tasks; `wiring.py` injection; `query_constraints.py` normalization; `requests.py` / `responses.py` helpers; `static_assets.py` versioning; `cache_events.py` invalidation; `propagation_queue.py` Elo propagation; `work_coordination.py` GPU/manual-turn governance.

## Frontend layout

Templates are `web/templates/desktop.html`, `mobile.html`, and `share_gallery.html`; each app starts from one module bootstrap.

Desktop modules (`web/static/js/desktop/`):

- `api.js` — API calls and URL helpers.
- `bootstrap.js` — desktop startup and module wiring.
- `state.js` — shared state, lenses, events, and preferences.
- `events.js` — DOM/application event delegation.
- `grid.js` / `grid_window.js` — image grid rendering and virtualization window.
- `lenses.js` — lens switching and lens lifecycle.
- `scope_data.js` / `filters.js` — scope/filter data and controls.
- `date_scrubber.js` — date histogram scrubber.
- `panel.js` / `panel_sections.js` / `panel_right.js` — side-panel surfaces.
- `drawer.js` / `folders.js` — settings drawer and catalog folder tree.
- `contextbar.js` / `context_menu.js` — selection/context actions.
- `selection.js` — selection mode and selected IDs.
- `keyboard.js` / `focusTrap.js` — keyboard commands and modal focus.
- `refine.js` — pair/mosaic Refine workflows.
- `loupe.js` — focused image/loupe viewer.
- `people.js` — People review UI.
- `map.js` — map view and markers.
- `omnibox.js` — global search/scope input.
- `suggestions.js` — collection suggestions.
- `shared.js` — Shared triage UI.
- `duplicates.js` / `similar.js` / `stack_cull.js` — duplicate/similarity/stack tools.
- `trash.js` — Trash UI.
- `importer.js` / `export_menu.js` — import and export controls.
- `motion.js` / `toast.js` — motion preferences and notifications.

Mobile: `mobile.html`, `mobile.css`, `sw.js`, manifest, and
`web/static/js/mobile/` (`bootstrap`, `api`, `state`, `timeline`, `viewer`,
`library`, `search`, `refine`, `selection`, `scrubber`, `history`, `flags`,
`toast`, `haptics`, `install`). It shares desktop APIs/writes; `/m` needs HTTPS.

CSS token system: `desktop.css` and `mobile.css` each define `:root` tokens for
surfaces, text, accent/status colors, radii, spacing, motion, typography, and
layout/safe-area dimensions; components consume `var(--token)`. Desktop also
overrides density tokens on `html[data-density]`.

## Data model highlights

- `catalog_sources` owns source path/online/included state; `images` is the
  central catalog row (filepath, metadata, status/flags, Elo/comparison counts,
  GPS, missing/trash state). Metadata FTS mirrors searchable image fields.
- `collections` stores named regular or query-backed smart collections;
  `collection_images` is ordered membership. `collection_links` is the ordered
  parent/child collection graph; graph validation lives in `features/collections`.
- `published_nodes` is an area-scoped (`website`/`private`) hierarchical
  snapshot tree, optionally sourced from a collection; `published_node_images`
  freezes ordered image membership for that snapshot.
- `collection_shares` represents either a collection share or published-node
  share (exactly one owner), with token, expiry/revocation, password and view
  counters; `share_images` freezes share membership and `share_favorites` stores
  visitor favorites.
- `stacks` describes burst/variant/cross-source/manual groups; `stack_members`
  maps images to a stack and stores match scores/order metadata.
- `image_captions` is keyed by `(model_key, image_id)` and stores caption, JSON
  tags, quality, and `user_edited`; triggers maintain `image_tags` and the active
  model's `image_captions_fts`. `caption_scan_images` tracks pending/done/error.

## Tests and checks

- Tests are feature-near `web/test_*.py`; shared setup is `web/test_support.py`.
  Contract/API shape coverage is in `test_modular_contracts.py` and
  `test_api_shapes.py`; browser expectations are `test_ui_contracts.py` and
  `test_browser_smoke.py`.
- Preferred quick check: `./scripts/photoarchive-check --quick` (diff check,
  compile with `web/.venv/bin/python`, and `node --check` for all JS).
- Unit suite: `./scripts/photoarchive-check --unit` or from `web/`,
  `.venv/bin/python -m unittest`; focused areas are listed by
  `./scripts/photoarchive-check --list-areas`.
- Browser smoke: run a server, then
  `./scripts/photoarchive-browser-smoke --base-url http://127.0.0.1:8000`;
  `PHOTOARCHIVE_SMOKE_MODE=1` skips DB initialization and heavyweight workers.

## Gotchas

- Use `web/.venv/bin/python` and its uvicorn, not whichever base `python` or
  globally installed packages happen to be first on `PATH`.
- `PHOTOARCHIVE_SMOKE_MODE=1` is for lightweight startup/smoke tests only; it
  warms templates and skips archive initialization, cache configuration, and
  worker startup.
- After a service restart, explicitly inspect Background Work: embedding and
  People pause state is process-local, while captions follow the setting;
  pending scan rows make work discoverable again, but do not assume every worker
  resumes automatically.
- Thumbnail/cache queries are cache-root scoped. Use `thumbnails.SSD_CACHE_DIR`
  (the configured `cache_root`) consistently; mixing roots causes cache misses,
  stale counts, or incorrect visible-image filtering.
- Do not use plain `:8000` for phone/PWA verification: use the Tailscale HTTPS
  `https://omarchy.tail0eeded.ts.net:8443/m` endpoint.
