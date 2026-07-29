# Getting Started

This guide gets Azimuth Photo from a fresh clone to a usable local catalog.

## Install

```bash
git clone https://github.com/Sean-Kenneth-Doherty/azimuth-photo.git
cd azimuth-photo
cd web
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
```

## Run The App

From the repo root:

```bash
./scripts/azimuth-server start
```

Open:

```text
http://127.0.0.1:8000
```

The helper also supports:

```bash
./scripts/azimuth-server status
./scripts/azimuth-server logs
./scripts/azimuth-server restart
./scripts/azimuth-server stop
```

By default it uses `127.0.0.1:8000`. Override the host or port with
`AZIMUTH_HOST` and `AZIMUTH_PORT`.

## App Data

A clean install keeps the catalog and generated data outside the source tree:

- Linux follows the XDG data, config, cache, and state directories.
- Windows uses `%LOCALAPPDATA%\Azimuth Photo` and `%APPDATA%\Azimuth Photo`.
- macOS uses `~/Library/Application Support/Azimuth Photo` and
  `~/Library/Caches/Azimuth Photo`.

Startup never moves, copies, or rebuilds existing data, and a source checkout
is never used as runtime storage. If you upgraded from an older version that
kept data inside the repo (`web/azimuth.db`, `web/.thumbcache`, `web/.models`,
`web/.embedcache`), the app starts a fresh catalog at the native location
above; your old data stays untouched on disk. To keep using it, point the app
at it explicitly — set `AZIMUTH_DB_PATH` (plus the matching cache/model
overrides below) to the old files, or move them into the native directories.

Set `AZIMUTH_HOME` to keep a new installation under one chosen root, or
use a granular override such as `AZIMUTH_DB_PATH`,
`AZIMUTH_THUMB_CACHE_DIR`, `AZIMUTH_MODELS_DIR`,
`AZIMUTH_DEVELOP_CACHE_DIR`, or `AZIMUTH_BACKUP_DIR`. Environment
overrides are deployment choices and take precedence over saved settings.

## First Catalog

1. Open the app. It opens on the Grid lens.
2. Open **System** from the top-right settings button.
3. In Sources, choose a folder, use tree browse, or type a path.
4. Select **Add + Scan**.
5. Leave the scan running until photos begin appearing in the grid.
6. Rescan a source later when files change on disk.

Removing a source changes catalog/cache state. It does not delete the original
photo folder.

If a removable drive is slow to wake or temporarily offline, Azimuth Photo keeps
included catalog rows active for cached grid, Refine, search, and People views.
Rescan and missing-file checks resume when the source folder is reachable.

## Background Work

Use **System → Background work** to inspect and pause/resume **AI embeddings**,
**Cache pregeneration**, **People scan**, **Captions**, and **Metadata**.

Heavy whole-catalog work is controllable from the drawer. Nearby thumbnails,
next-image warmups, and recently viewed media still warm automatically while you
browse.

## First Useful Workflow

1. Scan one photo folder.
2. Make sure the **Grid** lens loads.
3. Adjust thumbnail size until browsing feels comfortable.
4. Flag obvious picks and rejects with `P`, `X`, and `U`.
5. Open **Refine** and use Mosaic or Duel to add quick ranking signal.
6. Return to Grid and sort by Rating or Date; use the sort-direction button when
   ascending order is more useful.
7. Save a regular or smart collection, share it privately, publish it to a
   configured website folder, or export a filtered result set as JSON, CSV, or
   ZIP.

## Optional AI Setup

Azimuth Photo works without AI models. Install local models from **System → AI**
or **System → Background work** when you want semantic image search, local
captions/tags, People, and AI-assisted Refine pairing.

The first model download needs internet access unless the model is already on
disk. After installation, indexing runs locally in the background.

## Troubleshooting

- If the server helper says the virtualenv is missing, rerun the install steps
  in `web/`.
- If the port is busy, stop the old server or run with
  `AZIMUTH_PORT=8001`.
- If the grid feels slow while the app is building, pause any Background work
  row you do not need right now.
- If semantic search is unavailable, check System's AI model and embedding
  index status.
- If People stays empty, make sure preview caching and People scanning are
  enabled and that cached previews have had time to build.
- If previews look stale after changing thumbnail settings, use System's cache
  controls to refresh or clear generated cache.
