# Development

photoArchive is a FastAPI app with a browser-native frontend. There is no Vite,
TypeScript, or bundled build step.

## Local Setup

```bash
cd web
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
./scripts/photoarchive-server start
```

The app runs at `http://127.0.0.1:8000` by default.

## App Shape

- `web/app.py` creates the FastAPI app through `core.app_factory`.
- `web/core/` holds app wiring, background runtime helpers, request/response
  utilities, static asset handling, and query constraints.
- `web/features/` holds route modules by product surface: AI, cache, catalog,
  compare, export, library, media, pages, people, search, and settings.
- `web/data/` holds SQLite schema and repositories.
- `web/templates/` contains Jinja templates.
- `web/static/` contains CSS and browser JavaScript modules.
- `web/thumbnails/` contains thumbnail/cache generation and maintenance code.

## Main Routes

- `/`, `/settings`, and `/catalog` render the Catalog/setup screen.
- `/library` and `/rankings` render Library.
- `/compare` renders Compare.
- `/people` renders People.
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

- Shell: `web/templates/mobile.html` (does not extend `base.html`),
  `web/static/mobile.css`, modules under `web/static/js/mobile/`
  (timeline, viewer, refine, search, library, selection, scrubber, flags,
  state, api, toast, bootstrap).
- PWA assets: `web/static/manifest.webmanifest`, `web/static/icons/icon.svg`,
  `web/static/sw.js` (shell precache, stale-while-revalidate thumbnails,
  network-only for other APIs, offline fallback to cached `/m`).
- Service workers require a secure context. On the tailnet the app is served
  over HTTPS via `tailscale serve --https=8443` →
  `https://omarchy.tail0eeded.ts.net:8443/m`. Plain `:8000` works but without
  the service worker/install flow. Port 443 is a PUBLIC Funnel serving an
  unrelated APK page — never reconfigure it.
- Timeline endpoints: `/api/date-histogram` (whole-scope month counts driving
  the scrubber and month view) and `/api/counts` (total/picked/rejected per
  scope); `file_type` accepts `raw`/`jpg`/`tif` group aliases.
- All mobile writes go through the same APIs as desktop (flags, mosaic picks,
  collections) — there is no mobile-only write path.

## Where To Edit

Before changing worker controls, scheduling, or status UI, use
[`background-work-behavior.md`](background-work-behavior.md) as the
authoritative product anchor for Search, Previews, and People background work.

| Change | Start here | Notes |
| --- | --- | --- |
| Catalog setup, source folders, scans | `web/features/catalog/`, `web/data/repositories/catalog.py` | Keep folder/source SQL in repositories and route parsing in routes. |
| Library rankings, filters, maps, dates | `web/features/library/`, `web/data/repositories/rankings.py`, `web/data/repositories/filter_options.py` | Preserve cached/offline browsing behavior and visible-count semantics. |
| Compare pair and mosaic workflows | `web/features/compare/`, `web/elo_propagation.py`, `web/data/repositories/ratings.py` | Routes validate requests; services/repositories own candidate pools and rating writes. |
| People review, labels, merges, scan status | `web/features/people/`, `web/data/repositories/people.py`, `web/face_worker.py` | Source photos are never modified; face crops come from cached previews. |
| Search and AI embedding status | `web/features/search/`, `web/features/ai/`, `web/embed_cache.py`, `web/embedding_worker.py` | Metadata fallback must keep working when AI is cold or deferred. |
| Thumbnail/cache status and pregen | `web/thumbnails/`, `web/features/cache/` | Keep facade exports stable while moving implementation into owning modules. |
| Settings and composed status payloads | `web/features/settings/`, `web/settings.py` | Settings responses are cached defensively and invalidated by named events. |
| Bottom bar, background work panel, shared browser shell | `web/templates/_bottom_bar_*.html`, `web/static/js/work/`, `web/static/js/ui.js`, `web/static/style.css` | The measured bottom bar height is the shared layout contract. |
| Mobile app (timeline, viewer, refine, PWA) | `web/static/js/mobile/`, `web/templates/mobile.html`, `web/static/mobile.css`, `web/static/sw.js` | Check `ui-architecture.md` first; bump the SW cache version when shell assets change. |
| Legacy browser globals | `web/static/js/legacy/` | Compatibility exports only; put new page behavior in the owning module. |
| Tests and fixtures | `web/test_support.py`, feature-owned `web/test_*.py` files | Keep shared setup in test support and put behavior tests near their product owner. |

## Checks

Run the repo-root verification script so the project virtualenv is used:

```bash
./scripts/photoarchive-check
```

That script runs `git diff --check`, Python compilation with
`web/.venv/bin/python`, JavaScript syntax checks, and the unit suite.

List focused check areas:

```bash
./scripts/photoarchive-check --list-areas
```

Use the smallest named area that matches the files you touched:

```bash
./scripts/photoarchive-check --area background-work
./scripts/photoarchive-check --area ai-search
./scripts/photoarchive-check --area previews
./scripts/photoarchive-check --area people-work
```

The older aliases still work for compatibility: `frontend`, `search`, `cache`,
and `people`.

Agent verification ladder:

| Change | Check |
| --- | --- |
| One JavaScript file | `./scripts/photoarchive-check --quick` plus `node --check web/static/js/path/to/file.js` |
| Background Work UI | `./scripts/photoarchive-check --area background-work` |
| Search or AI embedding behavior | `./scripts/photoarchive-check --area ai-search` |
| Previews or cache behavior | `./scripts/photoarchive-check --area previews` |
| People background work | `./scripts/photoarchive-check --area people-work` |
| Narrow handoff | `./scripts/photoarchive-check --quick` plus the relevant area |
| Broad handoff or changed browser behavior | `./scripts/photoarchive-check --unit` or `./scripts/photoarchive-check --full` |

Run unit tests directly from `web/` when you need a narrower loop:

```bash
cd web
.venv/bin/python -m unittest
```

Run browser smoke checks against a running server:

```bash
./scripts/photoarchive-browser-smoke --base-url http://127.0.0.1:8000
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
- Do not commit public process files such as `AGENTS.md`, `CLAUDE.md`,
  `TODO.md`, `GOAL.md`, or agent run logs.
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

Do not commit runtime data such as `web/photoarchive.db`, `web/.thumbcache/`,
`web/.models/`, `web/settings.local.json`, or `web/.run/server.log`.
