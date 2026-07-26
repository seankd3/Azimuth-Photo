# Azimuth Photo — Historical Launch Runbook

Goal: boot each milestone commit, point it at a folder of sample JPGs, trigger a scan so
thumbnails render, and screenshot the UI with a headless browser — **without touching real
data and without downloading multi-GB ML models.**

Repo: `C:/Users/smast/OneDrive/Desktop/Projects/azimuth-devlog/repo/azimuth-photo`
All work is read-only against git history (`git show <commit>:<path>`). Do **not** check anything out.

**Universal facts (true at every commit):**
- The web app lives under `web/`. Modules use flat imports (`import db`, `from core.app_factory import ...`),
  so **you must run uvicorn from inside `web/`** (or `PYTHONPATH=web`). Working dir = `web/`.
- Default server port is **8000** (plain uvicorn default). `AZIMUTH_PORT` only affects a few
  self-URL/display values, not the bind port — always pass `--port 8000` explicitly.
- Supported source extensions include `.jpg/.jpeg/.png` from the start, so a folder of sample JPGs works everywhere.
- Headless screenshot: point Chromium/Playwright at `http://127.0.0.1:8000<route>` after the scan finishes.

**The single most useful env var by era:**
- M1–M2: none — paths are hardcoded (see per-section edits).
- M3–M7: `AZIMUTH_THUMB_CACHE_DIR` (thumb cache) + `AZIMUTH_SMOKE_MODE=1` (skip AI). DB path still hardcoded to `web/azimuth.db`.
- M8–M9: `AZIMUTH_HOME=<scratch>` isolates **everything** (catalog DB, caches, models, backups) under one dir. This is the clean-room switch.

---

## M1 — 60cb19f39 (2026-02-10, first web mosaic ranking)  "PhotoRanker"

**1. Entry point & launch**
- App object: `web/app.py` → `app = FastAPI(title="PhotoRanker")`.
- Command (from `web/`):
  ```bash
  cd web
  python -m uvicorn app:app --host 127.0.0.1 --port 8000
  ```

**2. Data/catalog isolation**
- SQLite catalog: `web/db.py` → `DB_PATH = os.path.join(os.path.dirname(__file__), "photoranker.db")` — **HARDCODED, no env var.** It writes `web/photoranker.db` (+ `-wal`/`-shm`) inside the checked-out tree.
- Thumbnails: **in-memory LRU only** (`web/thumbnails.py`), no disk cache dir — nothing to isolate, nothing persists.
- Source photos: chosen at scan time via the API (below); not a path constant.
- **To isolate:** since git history is read-only and you won't check out, run from a throwaway copy of `web/`, or accept that `photoranker.db` is a fresh isolated file (it is created empty on first boot; it is not shared with any real catalog since the real app uses `azimuth.db`). No real data is at risk.

**3. Scan trigger**
- `POST /api/scan` with JSON body `{"folder": "C:/path/to/sample-jpgs"}` (validated with `os.path.isdir`).
- Status: `GET /api/scan/status` → `{"scanning":bool,"total_found":N,"total_inserted":N,"folder":...,"done":bool}`. Poll until `done=true` (or `scanning=false` with `total_inserted>0`).

**4. AI/embeddings disable**
- No ML at all in this commit — nothing to disable. Requirements are pure image stack.

**5. Key routes to screenshot**
- `/` (index/dashboard — stats + scan control), `/cull`, `/compare`, `/rankings`.
- **Hero:** `/` (mosaic-ranking landing) — this is the "first web mosaic ranking" milestone; `/compare` and `/rankings` show the ranking UI.

**6. Minimal deps** (`web/requirements.txt` — already minimal, install all):
```
fastapi  uvicorn[standard]  aiosqlite  Pillow  rawpy  jinja2  python-multipart
```
(For JPG-only sample folders you can even skip `rawpy`.)

---

## M2 — c0d7f5441 (2026-04-21, AI/Library day — Azimuth Photo is born)

**1. Entry point & launch**
- App object: `web/app.py` → `app = FastAPI(title="Azimuth Photo")`. Same shape as M1.
  ```bash
  cd web
  python -m uvicorn app:app --host 127.0.0.1 --port 8000
  ```

**2. Data/catalog isolation**
- DB: `web/db.py` → `DB_PATH = .../ "azimuth.db"` — **HARDCODED** (note the rename from `photoranker.db`). No env var.
- Thumbnails: still **in-memory only**, no disk cache.
- Source photos: via `POST /api/scan` (unchanged from M1).

**3. Scan trigger**
- `POST /api/scan` body `{"folder": "..."}`; `GET /api/scan/status` (same `scan_state` shape as M1).

**4. AI/embeddings disable — IMPORTANT (this is the "AI is born" commit)**
- New `web/clip_worker.py` (CLIP search). Startup imports it inside a `try/except ImportError` and search endpoints re-import it and return `503 {"error":"CLIP not available"}` if missing.
- **AI is off automatically if you don't install the ML deps.** Install only the light subset (see below) and thumbnails + UI work fine; `/api/search`, `/api/similar`, `/api/duplicates` degrade to 503.

**5. Key routes to screenshot**
- `/` , `/library` (**new** — the "Library" view), `/cull`, `/compare`, `/rankings`.
- **Hero:** `/library` (the new AI/library grid that names the milestone).

**6. Minimal deps** — `requirements.txt` now bundles heavy ML, but **do not install it all.** Boot subset:
```
fastapi  uvicorn[standard]  aiosqlite  Pillow  rawpy  jinja2  python-multipart  numpy
```
**Exclude:** `scikit-learn sentence-transformers transformers bitsandbytes accelerate qwen-vl-utils` (multi-GB, only needed for CLIP search). `numpy` added because search code imports it, but it isn't needed just to boot+scan+thumbnail.

---

## M3 — b28564460 (2026-04-25, mature April UX-polish wave)

**1. Entry point & launch**
- Still monolithic `web/app.py` with top-level `app = FastAPI(...)` and `import ai_models` (which lazy-loads torch — safe, no heavy import at module load).
  ```bash
  cd web
  python -m uvicorn app:app --host 127.0.0.1 --port 8000
  ```

**2. Data/catalog isolation** — first real config surface appears (`web/settings.py`):
- DB: `web/db.py` → `DB_PATH` hardcoded to `web/azimuth.db` (still no env var).
- Thumb cache: `web/thumbnails.py` → `SSD_CACHE_DIR = os.getenv("AZIMUTH_THUMB_CACHE_DIR", <web>/.thumbcache)` — **now a disk cache, env-overridable.**
- Settings persisted to `web/settings.local.json`; embed model dir defaults to `web/.models/...`.
- **To isolate cache to scratch:** `AZIMUTH_THUMB_CACHE_DIR=<scratch>/thumbs`. DB stays in-tree (hardcoded) — run from a throwaway copy of `web/` if you want it elsewhere.

**3. Scan trigger** — scan model shifts to "catalog sources":
- Simple path still works: `POST /api/scan` body `{"folder":"..."}` (adds/restores a source then scans).
- Alternative: `POST /api/catalog/sources` body `{"folder":"..."}`.
- Status: `GET /api/scan/status`.

**4. AI/embeddings disable**
- `ai_models.py` and `embedding_worker.py` **lazy-import** torch/transformers; startup wraps the embedding worker in `try/except`. `ai_models.py` top-level imports are stdlib + `settings` only.
- **Just don't install torch/transformers.** Models never auto-download (install is an explicit `POST /api/ai/model/install`). Thumbnails/UI fully work.

**5. Key routes to screenshot**
- `/` (renders `settings.html`), `/library`, `/compare`, `/rankings`, `/settings`, `/catalog`.
- **Hero:** `/library` (the polished grid). `/settings` shows the new config UI.

**6. Minimal deps** (boot subset of the heavy `requirements.txt`):
```
fastapi  uvicorn[standard]  aiosqlite  Pillow  rawpy  jinja2  python-multipart  numpy
```
Exclude `scikit-learn sentence-transformers transformers huggingface_hub bitsandbytes accelerate qwen-vl-utils`.

---

## M4 — 149d8ba55 (2026-05-18, post-modularization + background work panel)

**1. Entry point & launch** — **modularized.** `web/app.py` is now just:
```python
from core.app_factory import create_app
app = create_app()
```
- Command is unchanged (target `app:app`, which now calls the factory):
  ```bash
  cd web
  python -m uvicorn app:app --host 127.0.0.1 --port 8000
  ```

**2. Data/catalog isolation**
- DB: `web/db.py` → `DB_PATH` still hardcoded `web/azimuth.db`.
- Thumb cache: `AZIMUTH_THUMB_CACHE_DIR` (default `<web>/.thumbcache`) — same as M3.
- Settings: `web/settings.local.json`.

**3. Scan trigger**
- `POST /api/scan` (in `web/features/catalog/routes.py`) body `{"folder":"..."}`. Same status endpoint `GET /api/scan/status`.

**4. AI/embeddings disable — clean flag now exists**
- `web/core/background.py` → `smoke_mode_enabled()` returns `os.environ.get("AZIMUTH_SMOKE_MODE")=="1"`.
- Set **`AZIMUTH_SMOKE_MODE=1`** to skip all background work (AI embed worker, cache warmers, metadata scan) — startup just warms templates and returns. UI + on-demand thumbnails still work.
- Independently, ML libs lazy-load, so not installing them is also fine. Use both belt-and-suspenders.

**5. Key routes to screenshot**
- `/` (settings landing), `/library`, `/compare`, `/rankings`, `/people`, `/settings`, `/catalog`.
- **Hero:** `/library`. The "background work panel" is a UI element within the library/settings chrome.

**6. Minimal deps** (`requirements.txt` still lists ML; boot subset):
```
fastapi  uvicorn[standard]  aiosqlite  Pillow  rawpy  jinja2  python-multipart  numpy
```

---

## M5 — 9b444ff57 (2026-07-08, the "/d" desktop "one" redesign)

**1. Entry point & launch** — factory pattern (same as M4):
```bash
cd web
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

**2. Data/catalog isolation**
- DB: hardcoded `web/azimuth.db`.
- Thumb cache: `AZIMUTH_THUMB_CACHE_DIR` (default `<web>/.thumbcache`).
- New: `AZIMUTH_PORT` / `AZIMUTH_ACCESS` read for self-URL/access-mode display only.

**3. Scan trigger**
- `POST /api/scan` body `{"folder":"..."}`; status `GET /api/scan/status`. (Also `POST /api/catalog/sources`.)

**4. AI/embeddings disable**
- `AZIMUTH_SMOKE_MODE=1` (via `web/core/background.py`). ML still lazy-loads too.

**5. Key routes to screenshot — the redesign lands**
- **`/d` → `desktop.html`** (the new Lightroom-style "one" desktop surface) — **HERO.**
- `/m` → `mobile.html` (new mobile reskin).
- Legacy still present: `/`, `/library`, `/compare`, `/rankings`, `/people`, `/settings`, `/catalog`.

**6. Minimal deps** (boot subset):
```
fastapi  uvicorn[standard]  aiosqlite  Pillow  rawpy  jinja2  python-multipart  numpy
```

---

## M6 — 874b8a705 (2026-07-10, Develop module WebGL2 editor)

**1. Entry point & launch** — factory pattern:
```bash
cd web
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

**2. Data/catalog isolation — NEW hardcoded Linux path to override on Windows**
- DB: hardcoded `web/azimuth.db`.
- Thumb cache: `AZIMUTH_THUMB_CACHE_DIR` (default `<web>/.thumbcache`).
- **Develop cache:** `web/features/develop/rawproc.py` → `BASE_CACHE_ROOT = Path(os.environ.get("AZIMUTH_DEVELOP_CACHE_DIR", "/mnt/expansion/AzimuthPhotoCache/develop"))`. **The default is a hardcoded omarchy Linux path** — on Windows (or any clean box) you **must** set `AZIMUTH_DEVELOP_CACHE_DIR=<scratch>/develop`, or develop-cache writes fail.
- `/` now defaults to the desktop surface (see routes).

**3. Scan trigger**
- `POST /api/scan` body `{"folder":"..."}`; status `GET /api/scan/status`.

**4. AI/embeddings disable**
- `AZIMUTH_SMOKE_MODE=1`. ML lazy-loads.
- Note: the Develop module render pipeline needs **numpy** (many `web/features/develop/*.py` do top-level `import numpy as np`, wired at startup) — install numpy even in a light boot. The heavy raw develop uses `rawpy` (already in base). WebGL2 editing itself runs in the browser, not server-side.

**5. Key routes to screenshot**
- `/` → `desktop.html` (**HERO** — default is now the desktop surface), `/d` → `desktop.html`, `/m` → `mobile.html`.
- Develop is a **panel inside `desktop.html`** (open a photo → Develop), not a standalone page. Develop data endpoints live under `/api/develop/{image_id}...`.

**6. Minimal deps** (boot subset — numpy is now mandatory):
```
fastapi  uvicorn[standard]  aiosqlite  Pillow  rawpy  jinja2  python-multipart  numpy
```

---

## M7 — 385db0b7c (2026-07-10, Film panel / dev7)

**1. Entry point & launch** — factory pattern:
```bash
cd web
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

**2. Data/catalog isolation**
- DB: hardcoded `web/azimuth.db`.
- Thumb cache: `AZIMUTH_THUMB_CACHE_DIR`.
- **Develop/HDR/pano caches all default to the hardcoded Linux `/mnt/expansion/AzimuthPhotoCache/develop`** (rawproc, hdr, pano modules). **Set `AZIMUTH_DEVELOP_CACHE_DIR=<scratch>/develop` on Windows.**
- (`AZIMUTH_SMOKE_MODE=1` also `setdefault`-forced inside some test/eval helpers, but **not** in the runtime app path — set it yourself for the server.)

**3. Scan trigger**
- `POST /api/scan` body `{"folder":"..."}`; status `GET /api/scan/status`.

**4. AI/embeddings disable**
- `AZIMUTH_SMOKE_MODE=1`. ML lazy-loads. numpy required (develop/film modules import it at load).

**5. Key routes to screenshot**
- `/` → `desktop.html` (**HERO**), `/d` → desktop, `/m` → mobile.
- Film panel = new tab within the Develop panel inside `desktop.html`; film ops served under `/api/develop/...`.

**6. Minimal deps** (boot subset):
```
fastapi  uvicorn[standard]  aiosqlite  Pillow  rawpy  jinja2  python-multipart  numpy
```

---

## M8 — 49823da21 (2026-07-15, staged import canvas)

**1. Entry point & launch** — factory pattern:
```bash
cd web
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

**2. Data/catalog isolation — MAJOR CHANGE: full runtime-paths system**
- `web/db.py` → `DB_PATH = resolve_runtime_paths().catalog_db` (`web/core/runtime_paths.py`). Paths are now OS-aware (Windows `%LOCALAPPDATA%`/`%APPDATA%`, macOS `~/Library`, Linux XDG).
- **Clean isolation = one var: `AZIMUTH_HOME=<scratch>`** → catalog DB, thumb cache, embed cache, develop cache, models, backups all land under it. Setting `AZIMUTH_HOME` also marks the home "isolated" so it never inherits the shared prod backup dir.
- Granular overrides (all honored): `AZIMUTH_DATA_DIR`, `AZIMUTH_CACHE_DIR`, `AZIMUTH_CONFIG_DIR`, `AZIMUTH_STATE_DIR`, `AZIMUTH_DB_PATH`, `AZIMUTH_THUMB_CACHE_DIR`, `AZIMUTH_DEVELOP_CACHE_DIR`, `AZIMUTH_MODELS_DIR`, `AZIMUTH_EMBED_CACHE_DIR`, `AZIMUTH_SETTINGS_PATH`.
- No more hardcoded Linux develop path leaking on Windows — `AZIMUTH_HOME` covers it. (Legacy `/mnt/expansion` only used if a legacy in-tree install is detected AND that dir exists.)
- Standalone/library env: `AZIMUTH_ORIGINALS_DIR`, `AZIMUTH_LIBRARY_DIR`, `AZIMUTH_MODE` (standalone/hub/satellite) exist for the import/library flow but are optional for a scratch boot.

**3. Scan trigger** — two options:
- Simple (still works): `POST /api/scan` body `{"folder":"..."}`; status `GET /api/scan/status`.
- New staged import canvas: `POST /api/import/scan` (scan a card/folder) → `GET /api/import/scan/{scan_id}` (poll) → `POST /api/import/commit`. For a quick thumbnail screenshot, `POST /api/scan` is simpler.

**4. AI/embeddings disable — base install is now ML-free**
- `web/requirements.txt` was **split**: base has **no torch/transformers**. AI deps moved to `requirements-ai-{search,people,captions,develop,all}.txt`. Installing base = AI already absent.
- Also still supports `AZIMUTH_SMOKE_MODE=1` to skip background workers.

**5. Key routes to screenshot**
- `/` → `desktop.html` (**HERO**), `/d` → desktop, `/m` → mobile, **`/setup` → `setup.html`** (new onboarding/import wizard).
- Staged import canvas is a surface within the desktop UI, backed by `/api/import/*`.

**6. Minimal deps** — install base `requirements.txt` as-is (already ML-free):
```
fastapi  uvicorn[standard]  aiosqlite  Pillow  rawpy  jinja2  python-multipart
numpy  opencv-python-headless  scikit-learn  tifffile  imagecodecs  zeroconf
```
(All light; boots + scans + thumbnails + develop with no model downloads.)

---

## M9 — b9a701bac (HEAD, current Azimuth)

**1. Entry point & launch** — factory pattern (unchanged):
```bash
cd web
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```
- Also shippable via `Dockerfile` / `docker-compose.yml` / Tauri desktop, but the uvicorn command above is the direct path.

**2. Data/catalog isolation** — same runtime-paths system as M8:
- `DB_PATH = resolve_runtime_paths().catalog_db`.
- **`AZIMUTH_HOME=<scratch>`** isolates everything (data/cache/catalog/models/develop/backups). Same granular overrides as M8 (`AZIMUTH_DATA_DIR`, `AZIMUTH_CACHE_DIR`, `AZIMUTH_DB_PATH`, `AZIMUTH_THUMB_CACHE_DIR`, `AZIMUTH_DEVELOP_CACHE_DIR`, `AZIMUTH_MODELS_DIR`, `AZIMUTH_EMBED_CACHE_DIR`, `AZIMUTH_SETTINGS_PATH`, …).

**3. Scan trigger**
- `POST /api/scan` body `{"folder":"..."}` (validated `os.path.isdir`, `try_begin_scan()` guard); status `GET /api/scan/status`.
- Staged import: `POST /api/import/scan` → `GET /api/import/scan/{scan_id}` → `POST /api/import/commit`.

**4. AI/embeddings disable**
- Base `requirements.txt` is ML-free (AI in `requirements-ai-*.txt`). `AZIMUTH_SMOKE_MODE=1` skips background workers. Models only download on explicit install.

**5. Key routes to screenshot**
- `/` → `desktop.html` (**HERO**), `/d` → desktop, `/m` → mobile, `/setup` → setup wizard.

**6. Minimal deps** — install base `requirements.txt` (`web/pyproject.toml` also present):
```
fastapi  uvicorn[standard]  aiosqlite  Pillow  rawpy  jinja2  python-multipart
numpy  opencv-python-headless  scikit-learn  tifffile  imagecodecs  zeroconf
```

---

## Copy-paste boot recipes

### M1 / M2 (monolith, no env config)
```bash
# from repo root, after obtaining a working copy of web/ at the commit
cd web
python -m uvicorn app:app --host 127.0.0.1 --port 8000
# then:
curl -X POST http://127.0.0.1:8000/api/scan -H 'Content-Type: application/json' \
     -d '{"folder":"C:/scratch/sample-jpgs"}'
curl http://127.0.0.1:8000/api/scan/status      # poll until done:true
# screenshot:  M1 -> /   |   M2 -> /library
```

### M3–M7 (modular, hardcoded DB, smoke flag)
```bash
# Windows PowerShell env (bash shown for portability):
export AZIMUTH_SMOKE_MODE=1
export AZIMUTH_THUMB_CACHE_DIR=C:/scratch/thumbs
export AZIMUTH_DEVELOP_CACHE_DIR=C:/scratch/develop   # REQUIRED for M6/M7 on Windows
cd web
python -m uvicorn app:app --host 127.0.0.1 --port 8000
curl -X POST http://127.0.0.1:8000/api/scan -H 'Content-Type: application/json' \
     -d '{"folder":"C:/scratch/sample-jpgs"}'
# screenshot:  M3/M4 -> /library   |   M5 -> /d   |   M6/M7 -> /  (desktop.html)
```
> Note SMOKE_MODE=1 skips *all* background work including idle thumbnail pregeneration.
> Thumbnails are still generated **on demand** when the grid requests them, so the UI renders.
> If a milestone's grid looks empty in a headless shot, hit a thumb URL first
> (`/api/thumb/sm/<id>`) or briefly run **without** SMOKE_MODE to let pregen warm the cache.

### M8 / M9 (runtime-paths, ML-free base)
```bash
export AZIMUTH_HOME=C:/scratch/azimuth-home     # isolates catalog+cache+develop+backups
export AZIMUTH_SMOKE_MODE=1                       # optional: skip background workers
cd web
python -m uvicorn app:app --host 127.0.0.1 --port 8000
curl -X POST http://127.0.0.1:8000/api/scan -H 'Content-Type: application/json' \
     -d '{"folder":"C:/scratch/sample-jpgs"}'
# screenshot:  / (desktop.html)   |  also /setup for the onboarding/import canvas
```

---

## Cross-era notes

**Consistent across all eras**
- FastAPI app object is always reachable as `app:app` from inside `web/`; launch is always `python -m uvicorn app:app --port 8000`.
- The `POST /api/scan {"folder": "..."}` + `GET /api/scan/status` pair is the reliable scan trigger from **M1 all the way to M9** — even after the richer "sources" (M3) and "staged import" (M8) flows were added, the simple endpoint survives. Use it everywhere.
- JPG sources supported from day one; port 8000 default.
- AI never auto-downloads models — heavy models are always an explicit opt-in (missing-import → 503, or explicit `POST /api/ai/model/install`). So "AI off" is the default posture; you just avoid installing ML libs and/or set smoke mode.

**What shifts**
- **Entry point:** monolithic `web/app.py` with an inline `FastAPI(...)` (M1–M3) → thin `app.py` calling `create_app()` from `core/app_factory` (M4→M9). Launch command is identical either way.
- **App title / brand:** `PhotoRanker` (M1) → `Azimuth Photo` (M2+) → Azimuth Photo branding layer (late).
- **Catalog DB location:** hardcoded `web/photoranker.db` (M1) → hardcoded `web/azimuth.db` (M2–M7) → OS-aware `resolve_runtime_paths().catalog_db`, overridable via `AZIMUTH_HOME`/`AZIMUTH_DB_PATH` (M8–M9). **The DB path is only env-configurable from M8 onward.**
- **Thumbnail cache:** in-memory only (M1–M2) → on-disk `.thumbcache`, env `AZIMUTH_THUMB_CACHE_DIR` (M3–M7) → under the runtime cache dir / `AZIMUTH_HOME` (M8–M9).
- **AI-off mechanism:** simply-not-installed (M1–M2) → lazy-import + `AZIMUTH_SMOKE_MODE=1` (M4–M7) → **requirements.txt split so the base install is ML-free** (M8–M9).
- **Requirements:** minimal image stack (M1) → base file bloated with torch/transformers/bitsandbytes that you must *not* fully install (M2–M7) → base file cleaned to a light stack with AI carved into `requirements-ai-*.txt` (M8–M9).
- **Develop cache trap:** M6–M7 hardcode a Linux default `/mnt/expansion/AzimuthPhotoCache/develop` for the develop/HDR/pano caches — **must** set `AZIMUTH_DEVELOP_CACHE_DIR` on Windows. M8+ folds this under the runtime cache dir, so `AZIMUTH_HOME` covers it.
- **Primary UI surface / hero route:** `/` dashboard (M1) → `/library` grid (M2–M4) → `/d` desktop redesign (M5) → `/` == `desktop.html` becomes the default surface (M6→M9), with Develop/Film/Import as panels inside it and `/setup` added for onboarding (M8+).
- **numpy** becomes a hard boot dependency once the Develop module lands (M6+, top-level imports); before that it's only needed by search code paths.
