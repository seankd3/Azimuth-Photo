# Development

Azimuth Photo is a FastAPI app with a browser-native frontend. There is no Vite,
TypeScript, or bundled build step.

## Local Setup

```bash
cd web
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
./scripts/azimuth-server start
```

That is the fast core install: library browsing, metadata search, Refine,
Develop, sharing, and publishing do not require the local AI stack. Install
only the inference packs you want, from `web/`:

```bash
python -m pip install -r requirements-ai-search.txt    # semantic search
python -m pip install -r requirements-ai-people.txt    # face recognition
python -m pip install -r requirements-ai-captions.txt  # generated captions/tags
python -m pip install -r requirements-ai-develop.txt   # Develop subject masks
python -m pip install -r requirements-ai-all.txt       # every optional pack
```

The 8B search model and current 4-bit caption preset use bitsandbytes, which the
manifests install only on supported Linux x86-64 systems. Other platforms can
use the compact 2B search model without bitsandbytes; captions stay unavailable
rather than risking an unsafe full-precision model load.

Missing packs are reported in System status and do not hide stored embeddings,
People labels, captions, or tags. Azimuth Photo never installs Python packages
at runtime; install a pack explicitly, then restart the app when convenient.

The app runs at `http://127.0.0.1:8000` by default.

## App Shape

- `web/app.py` creates the FastAPI app through `core.app_factory`.
- `web/core/` holds app wiring, background runtime helpers, request/response
  utilities, static asset handling, and query constraints.
- `web/features/` holds route modules by product surface: access, AI, cache,
  captions, catalog, collections, compare/Refine, dev, export, imports,
  library, media, pages, people, publish, search, settings, share, shared,
  stacks, and trash.
- `web/data/` holds SQLite schema and repositories.
- `web/templates/` contains Jinja templates.
- `web/static/` contains CSS and browser JavaScript modules.
- `web/thumbnails/` contains thumbnail/cache generation and maintenance code.

## Main Routes

- `/` renders the desktop app shell; `/d` is the explicit desktop alias.
- `/m` renders the installable mobile app; `/sw.js` serves its service worker
  at scope `/`.
- `/static/*` serves browser assets.

Feature APIs live under product-specific route modules in `web/features/`.
Public routes and their methods are contract-tested in
`web/test_modular_contracts.py` — register new routes there.

## Mobile App & PWA

The phone experience is a standalone PWA, held to the bars in
[`ui-architecture.md`](ui-architecture.md) (mobile = Google Photos
replacement; desktop = Lightroom Classic replacement).

- Shell: `web/templates/mobile.html`,
  `web/static/mobile.css`, modules under `web/static/js/mobile/`
  (timeline, viewer, refine, search, library, selection, scrubber, flags,
  state, api, toast, bootstrap).
- PWA assets: `web/static/manifest.webmanifest`, `web/static/icons/icon.svg`,
  `web/static/sw.js` (shell precache, stale-while-revalidate thumbnails,
  network-only for other APIs, offline fallback to cached `/m`).
- Service workers require a secure context. Serve the app over HTTPS to use
  `/m` as an installable PWA — for example `tailscale serve --https=8443` →
  `https://<machine>.<tailnet>.ts.net:8443/m`, or any TLS reverse proxy.
  Plain `:8000` works but without the service worker/install flow.
  Machine-specific serving rules (ports already claimed on a given host)
  belong in the untracked `AGENTS.local.md` overlay, not here.
- Timeline endpoints: `/api/date-histogram` (whole-scope month counts driving
  the scrubber and month view) and `/api/counts` (total/picked/rejected per
  scope); `file_type` accepts `raw`/`jpg`/`tif` group aliases.
- All mobile writes go through the same APIs as desktop (flags, mosaic picks,
  collections) — there is no mobile-only write path.

## Where To Edit

Before changing worker controls, scheduling, or status UI, use
[`background-work-behavior.md`](background-work-behavior.md) as the
authoritative product anchor for AI embeddings, cache pregeneration, People
scan, captions, and metadata background work.

| Change | Start here | Notes |
| --- | --- | --- |
| Desktop app shell, lenses, panels, Shared, Stacks, Trash, import, settings drawer | `web/templates/desktop.html`, `web/static/desktop.css`, `web/static/js/desktop/` | `/` and `/d` render this shell; keep page behavior in focused desktop modules. |
| Catalog setup, source folders, scans | `web/features/catalog/`, `web/data/repositories/catalog.py`, `web/static/js/desktop/drawer.js` | Keep folder/source SQL in repositories and route parsing in routes. |
| Library rankings, filters, maps, dates | `web/features/library/`, `web/data/repositories/rankings.py`, `web/data/repositories/filter_options.py`, `web/static/js/desktop/` | Preserve cached/offline browsing behavior and visible-count semantics. |
| Refine pair, duel, and mosaic workflows | `web/features/compare/`, `web/elo_propagation.py`, `web/data/repositories/ratings.py`, `web/static/js/desktop/refine.js` | Routes validate requests; services/repositories own candidate pools and rating writes. |
| Collections and smart collections | `web/features/collections/`, `web/data/repositories/collections.py`, `web/static/js/desktop/panel.js`, `web/static/js/desktop/state.js` | Regular collections own image membership; smart collections save a validated query. |
| Stacks | `web/features/stacks/`, `web/data/repositories/stacks.py`, `web/static/js/desktop/duplicates.js` | Builders cover bursts, variants, and cross-source duplicates; UI label is Stacks. |
| Trash and restore | `web/features/trash/`, `web/static/js/desktop/trash.js`, stack/grid callers | Trash moves originals into source-local `.trash`; empty trash is permanent. |
| Private share links | `web/features/share/`, `web/templates/share_gallery.html`, `web/static/js/desktop/panel.js`, `web/static/js/mobile/library.js` | Public gallery routes live at `/s/{token}` and serve resized previews. |
| Website publishing and Shared triage | `web/features/publish/`, `web/features/shared/`, `web/static/js/desktop/shared.js`, `web/static/js/desktop/panel.js`, `docs/publishing.md` | Publishing writes static bundles to `publish_dir`; Shared aggregates links and publishes. |
| Captions and tags | `web/features/captions/`, `web/data/repositories/captions.py`, `web/static/js/desktop/panel_right.js` | Captions are local VLM output; tags participate in filters and search. |
| Imports and import history | `web/features/imports/`, `web/static/js/desktop/importer.js` | Past imports scope the grid through `import_batch`. |
| People review, labels, merges, scan status | `web/features/people/`, `web/data/repositories/people.py`, `web/face_worker.py`, `web/static/js/desktop/people.js` | Source photos are never modified; face crops come from cached previews. |
| Search and AI embedding status | `web/features/search/`, `web/features/ai/`, `web/embed_cache.py`, `web/embedding_worker.py` | Metadata fallback must keep working when AI is cold or deferred. |
| Thumbnail/cache status and pregen | `web/thumbnails/`, `web/features/cache/` | Keep facade exports stable while moving implementation into owning modules. |
| Settings and composed status payloads | `web/features/settings/`, `web/settings.py`, `web/static/js/desktop/drawer.js` | Settings responses are cached defensively and invalidated by named events. |
| Mobile app (timeline, viewer, refine, PWA) | `web/static/js/mobile/`, `web/templates/mobile.html`, `web/static/mobile.css`, `web/static/sw.js` | Check `ui-architecture.md` first; bump the SW cache version when shell assets change. |
| Tests and fixtures | `web/test_support.py`, feature-owned `web/test_*.py` files | Keep shared setup in test support and put behavior tests near their product owner. |

## Checks

Run the repo-root verification script so the project virtualenv is used:

```bash
./scripts/azimuth-check
```

That script runs `git diff --check`, Python compilation with
`web/.venv/bin/python`, JavaScript syntax checks, and the unit suite.

List focused check areas:

```bash
./scripts/azimuth-check --list-areas
```

Use the smallest named area that matches the files you touched:

```bash
./scripts/azimuth-check --area background-work
./scripts/azimuth-check --area ai-search
./scripts/azimuth-check --area previews
./scripts/azimuth-check --area people-work
```

The older aliases still work for compatibility: `frontend`, `search`, `cache`,
and `people`.

Agent verification ladder:

| Change | Check |
| --- | --- |
| One JavaScript file | `./scripts/azimuth-check --quick` plus `node --check web/static/js/path/to/file.js` |
| Desktop/mobile shell UI | `./scripts/azimuth-check --area background-work` |
| Search or AI embedding behavior | `./scripts/azimuth-check --area ai-search` |
| Previews or cache behavior | `./scripts/azimuth-check --area previews` |
| People background work | `./scripts/azimuth-check --area people-work` |
| Narrow handoff | `./scripts/azimuth-check --quick` plus the relevant area |
| Broad handoff or changed browser behavior | `./scripts/azimuth-check --unit` or `./scripts/azimuth-check --full` |

Run unit tests directly from `web/` when you need a narrower loop (always
pytest — unittest discovery silently skips the suite's function-style tests):

```bash
cd web
.venv/bin/python -m pytest -q test_the_module.py
```

Run browser smoke checks against a running server:

```bash
./scripts/azimuth-browser-smoke --base-url http://127.0.0.1:8000
```

For docs-only edits, also run:

```bash
git diff --check
```

## Development Discipline

The app is the priority. Tests, docs, and helper scripts should exist because
they protect real user workflows, not because they make a refactor look more
complete.

- Keep app behavior ahead of scaffolding. A change should not add more test,
  docs, or tooling lines than app lines unless that extra support is explicitly
  requested or protects a high-risk user path.
- Prefer a small regression spine over broad structural contracts. Preserve
  public routes, API shapes, startup behavior, and end-to-end page loading; do
  not add tests that only freeze internal module boundaries or facade ownership.
- Do not add a new script unless it is reusable, documented, and expected to be
  run repeatedly by a developer or user.
- Keep experiments out of main. Prototype folders, one-off audits, generated
  proofs, and agent scratch work should stay untracked or move outside the
  repo.
- Keep `AGENTS.md` as the single tracked agent contract and `CLAUDE.md` as its
  compatibility pointer. Do not add competing instruction files, task plans,
  dated status reports, or agent run logs.
- Before committing, check the public surface with `git ls-files` and the
  pending surface with `git ls-files --others --exclude-standard`.

## Health Endpoints

With the server running:

```bash
curl http://127.0.0.1:8000/api/dev/status
curl http://127.0.0.1:8000/api/ai/status
curl http://127.0.0.1:8000/api/cache/status
```

## Runtime Files

`core.runtime_paths` owns catalog, settings, preview, model, embedding,
Develop, export, backup, transfer, run, and log locations. Its resolver is
read-only; directory creation is explicit and never migrates data. Clean
installs use platform-native roots. Source checkouts are never runtime-storage
fallbacks.

Do not commit runtime data. Use `AZIMUTH_HOME` for a single custom root or
the documented granular `AZIMUTH_*_DIR` / `AZIMUTH_DB_PATH`
overrides for test and deployment isolation.
