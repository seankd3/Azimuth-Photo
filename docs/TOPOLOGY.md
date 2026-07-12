# Where Everything Lives

One repo, many faces. This page is the map — if you're ever confused about
where code, data, or a running instance lives, start here.

## The one repo

Everything is a single git repository: `photo-archive`. The canonical remote
lives on the omarchy server (`sean@omarchy:Projects/photo-archive`). There is
no GitHub remote; omarchy **is** the origin, so git is the backup — every
machine's checkout pushes back to it.

```
photo-archive/
├── web/        the app itself — FastAPI server + desktop web UI (/d) + mobile UI (/m)
├── desktop/    Tauri Windows shell (native tray app that runs the satellite for you)
├── android/    Kotlin/Compose phone client
├── docs/       specs and this map
└── scripts/    deploy.sh and friends
```

The same `web/` code runs in two modes:

- **Hub** — the always-on library server (omarchy). Owns the originals and the
  master catalog.
- **Satellite** — the exact same server run on a laptop with
  `PHOTOARCHIVE_MODE=satellite` + `PHOTOARCHIVE_HUB_URL`. Mirrors the whole hub
  catalog locally, imports/culls/edits at local speed, and syncs everything
  back automatically (originals by content hash, edits/flags via the oplog).

## Branch model

| Branch | Meaning |
|---|---|
| `main` | What production runs. Only `scripts/deploy.sh` should move it. |
| `develop` | Integration branch. All work lands here first. |
| `wt-lane-a..d` | Four reusable lane slots for parallel agent work. Reset to `develop` between waves; never long-lived. |

## Checkouts (working copies)

**omarchy** (`ssh omarchy`, Austin — the hub):

| Path | Branch | Role |
|---|---|---|
| `~/Projects/photo-archive` | `main` | **Production.** The `photoarchive` systemd service serves from here on :8000. Don't edit here — deploy into it. |
| `~/Projects/pa-develop` | `develop` | Main dev worktree. Human + primary-agent work happens here. |
| `~/Projects/pa-lane-a..d` | `wt-lane-a..d` | Lane worktrees for parallel agents. Each is isolated; venvs are symlinks into pa-develop. Merged into `develop` when green, then reset. |

**XPS laptop** (Windows — the field machine):

| Path | Role |
|---|---|
| `...\Projects\photography\photoarchive-field` | Clone of the repo (origin = omarchy over ssh). Runs the **satellite** on :8010 — see [FIELD_README.md](../FIELD_README.md). Also where the Tauri shell in `desktop/` gets built. |

**Pixel phone**: no checkout — it's a client. Mobile web UI at `/m` (installable PWA), plus the Android app in `android/`.

## Data (never in git)

| Where | What |
|---|---|
| omarchy `/mnt/expansion/Photos/` | The originals. The archive. |
| omarchy `/mnt/expansion/PhotoArchiveCache/` | Thumbnails, develop base caches, exports. |
| omarchy `~/Projects/photo-archive/web/photoarchive.db` | The master catalog (SQLite). `deploy.sh` backs it up before every deploy (keeps 5). |
| XPS `C:\PhotoArchiveField\` | All satellite data: catalog DB, thumbs, develop cache, exports. |
| XPS `D:\CardOffload\` | Card-offload staging (robocopy + push scripts live there). |

## How to run each face

- **Hub (prod)**: already running — systemd `photoarchive` on omarchy :8000.
- **Satellite (laptop)**: see [FIELD_README.md](../FIELD_README.md) for the
  one command. Or the Tauri tray app (`desktop/`) once built — it launches and
  supervises the satellite for you.
- **Dev server**: from any worktree, `cd web && .venv/bin/python -m uvicorn
  app:app --port 8022` (pick a free port; never :8000).
- **Tests**: `cd web && PHOTOARCHIVE_SMOKE_MODE=1 .venv/bin/python -m pytest -q`
  (smoke mode is for **tests only** — never run a real instance with it; it
  disables the DB init and all background workers).

## How to deploy

One command, from omarchy:

```bash
~/Projects/photo-archive/scripts/deploy.sh
```

It merges `develop` → runs the full test suite (aborts on red) → backs up the
DB → pushes `main` → restarts the service → health-checks → resumes background
workers. That's the only sanctioned path to `main`.

## Docs index

| Doc | What it covers |
|---|---|
| [TOPOLOGY.md](TOPOLOGY.md) | This map. |
| [CODEBASE_MAP.md](CODEBASE_MAP.md) | Inside `web/`: modules, routes, features — the map for agents working in the code. |
| [MASTER_PLAN.md](MASTER_PLAN.md) | Product roadmap and program of work. |
| [DEVELOP_SPEC.md](DEVELOP_SPEC.md) | The Develop (raw editing) module: pipeline math, edit state, GL/numpy twins. |
| [FIELD_SPEC.md](FIELD_SPEC.md) / [FIELD_SPEC_V2.md](FIELD_SPEC_V2.md) | Satellite/hub sync: content-hash upload, catalog mirror, oplog convergence. |
| [FIELD_HTTPS.md](FIELD_HTTPS.md) | Fronting the hub with Tailscale HTTPS (enables PWA/offline on phone). |
| [CARD_IMPORT_SPEC.md](CARD_IMPORT_SPEC.md) | Card import wizard + watched folders. |
