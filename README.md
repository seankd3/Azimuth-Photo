# photoArchive

photoArchive is a local-first photo archive for browsing, searching, ranking, and organizing large personal photo collections. It is built for real working libraries: external drives, many thousands of images, slow storage, long background jobs, and a need for fast day-to-day interaction.

Everything runs on your machine. Source photos and videos are treated as source-of-truth media and are never modified or deleted by the app.

## Current Features

- **Library**: dense justified grid, map view, Lightroom-style loupe with filmstrip, keyboard navigation, flags, stars, metadata filters, folder filters, People filters, similarity search, and JSON/CSV export.
- **Compare**: Mosaic, Swiss A/B, and Top 50 ranking modes backed by Elo scoring. Mosaic lets one pick record many ranking signals at once.
- **People**: local face scanning, review queues, labels, merges, ignored faces, and People filters that work alongside Library search and filters.
- **Semantic search**: fast daily search with Qwen3-VL-Embedding-2B, plus scheduled deep search with Qwen3-VL-Embedding-8B for saved/cached terms.
- **Search result caching**: repeated semantic searches can return from a persistent SQLite result cache, so common searches stay fast after the first successful embedding pass.
- **Similarity and duplicates**: whole-image embedding search powers "find similar", duplicate discovery, and visual grouping.
- **Ranking intelligence**: Elo propagation nudges visually similar under-ranked images after a comparison, while direct comparisons remain the source of confidence.
- **Cache-aware browsing**: RAM and SSD thumbnail/original caches keep active Library, Loupe, Compare, and Mosaic browsing responsive even when source media lives on slower storage.
- **Background work controls**: Settings and the bottom bar show embedding, deep-search, preview-cache, and original-cache progress with rates, ETAs, and pause/resume controls.
- **No build frontend**: browser-native HTML/CSS/JavaScript modules, no Vite, no TypeScript, no bundling.

## Quick Start

```bash
git clone https://github.com/Sean-Kenneth-Doherty/photo-archive.git
cd photo-archive/web
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd ..
./scripts/photoarchive-server restart
```

Open `http://127.0.0.1:8000`, go to Catalog or Settings, and add the folder that contains your photos.

The server helper respects:

- `PHOTOARCHIVE_HOST` default `127.0.0.1`
- `PHOTOARCHIVE_PORT` default `8000`
- `PHOTOARCHIVE_MAX_LOG_BYTES` for `.run/server.log` rotation

You can also run the app directly:

```bash
cd web
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```

## Requirements

- Python 3.11+
- Linux is the primary development target
- SQLite, Pillow, FastAPI, and the packages in `web/requirements.txt`
- NVIDIA GPU strongly recommended for embedding work
- Local model storage for Qwen embedding models

The app still works without AI models installed, but semantic search, similarity, deep search, duplicate detection, and embedding-backed ranking features will be unavailable or degraded until models are present.

## Main Workflow

1. **Add catalog sources** in Catalog or Settings. The scanner indexes file paths and metadata without touching the original files.
2. **Browse Library** with cache-backed thumbnails, search, filters, People filters, map view, and Loupe.
3. **Flag and filter** images as picked, unflagged, or rejected. Flags are organizational filters, not source-file operations.
4. **Rank in Compare** using Mosaic, Swiss A/B, or Top 50.
5. **Return to Library** to search, inspect ranked results, filter the archive, and export.

## Search And AI

photoArchive uses two embedding surfaces:

- **Daily Search, 2B**: the fast Qwen3-VL-Embedding-2B index used for normal semantic search and interactive work.
- **Deep Search, 8B**: a scheduled Qwen3-VL-Embedding-8B index for smarter saved terms and overnight/max-work runs.

The intended behavior is fast foreground work and heavy background work:

- the 2B model warms on normal app startup;
- the app can show fast 2B results first;
- repeated semantic searches are cached in SQLite;
- deep 8B work can run later and update cached intelligence without blocking normal browsing.

Settings exposes model install/status, embedding progress, deep-search query status, cache health, and background-work mode. The Library and Compare bottom bar includes a compact Work panel with colored progress bars and ETAs.

## Data And Safety

Runtime data lives under `web/` by default:

- `photoarchive.db`: SQLite catalog, metadata, ratings, comparisons, People data, embeddings, and search result caches
- `.thumbcache/`: generated thumbnails and optional browser-readable hot original copies
- `.models/`: local AI models
- `settings.local.json`: machine-local runtime settings
- `.run/server.log`: local server log

These generated paths are gitignored. Source photos/videos are not mutated. Cached originals are copies used for fast browser access, not replacements for archive media.

## Architecture

```text
web/
  app.py                 compatibility entrypoint for uvicorn app:app
  core/                  app factory, request/response helpers, query constraints, cache fanout
  features/              vertical page/API workflows such as library, compare, people, cache, settings
  data/                  schema, SQLite connection helpers, domain repositories
  db.py                  compatibility facade over data repositories
  embedding_worker.py    background embedding and deep-search worker
  embed_cache.py         in-memory embedding matrices for search/similarity paths
  thumbnails/            thumbnail generation, cache budgets, pregen workers, status
  static/js/             no-build browser modules plus the legacy compatibility bridge
  templates/             FastAPI/Jinja page templates and shared partials
```

New feature workflow code should live in `web/features/<feature>/`. Shared app shell and cross-feature helpers belong in `web/core/`. Domain SQL belongs in `web/data/repositories/`, with `web/db.py` kept as the compatibility facade during migration.

The browser remains no-build native JavaScript. Public URLs, JSON response shapes, settings keys, and `window.PhotoArchive.*` compatibility are intentionally preserved while the older frontend drains out of `web/static/js/legacy/app.js`.

## Testing

Common checks:

```bash
cd web
.venv/bin/python -m unittest
cd ..
scripts/photoarchive-browser-smoke --base-url http://127.0.0.1:8000
```

For quick server checks:

```bash
./scripts/photoarchive-server status
curl http://127.0.0.1:8000/api/dev/status
curl http://127.0.0.1:8000/api/ai/status
curl http://127.0.0.1:8000/api/cache/status
```

## License

MIT
