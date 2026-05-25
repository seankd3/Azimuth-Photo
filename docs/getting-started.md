# Getting Started

This guide gets photoArchive from a fresh clone to a usable local catalog.

## Install

```bash
git clone https://github.com/Sean-Kenneth-Doherty/photo-archive.git
cd photo-archive/web
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run The App

From the repo root:

```bash
./scripts/photoarchive-server start
```

Open:

```text
http://127.0.0.1:8000
```

The helper also supports:

```bash
./scripts/photoarchive-server status
./scripts/photoarchive-server logs
./scripts/photoarchive-server restart
./scripts/photoarchive-server stop
```

By default it uses `127.0.0.1:8000`. Override the host or port with
`PHOTOARCHIVE_HOST` and `PHOTOARCHIVE_PORT`.

## First Catalog

1. Open the app. Catalog is the first screen.
2. In **Catalog Sources**, choose a folder, use tree browse, or type a path.
3. Select **Add + Scan**.
4. Leave the scan running until photos begin appearing in Library.
5. Rescan a source later when files change on disk.

Removing a source changes catalog/cache state. It does not delete the original
photo folder.

If a removable drive is slow to wake or temporarily offline, photoArchive keeps
included catalog rows active for cached Library, Compare, search, and People
views. Rescan and missing-file checks resume when the source folder is reachable.

## Background Work

Use the **Background Work** panel to start or stop Search, Previews, and People.
Previews also fills the full-size cache when storage permits.

Heavy whole-catalog work stays paused until you start it. Nearby thumbnails,
next-image warmups, and recently viewed full-size images still warm
automatically while you browse.

## First Useful Workflow

1. Scan one photo folder.
2. Open **Library** and make sure the grid loads.
3. Adjust thumbnail size until browsing feels comfortable.
4. Flag obvious picks and rejects with `P`, `X`, and `U`.
5. Open **Compare** and use Mosaic to add quick ranking signal.
6. Return to Library and sort by rating or confidence.
7. Export a filtered result set as JSON or CSV when you want a portable list.

## Optional AI Setup

photoArchive works without AI models. Install the local model from Catalog when
you want semantic image search.

The first model download needs internet access unless the model is already on
disk. After installation, indexing runs locally in the background.

## Troubleshooting

- If the server helper says the virtualenv is missing, rerun the install steps
  in `web/`.
- If the port is busy, stop the old server or run with
  `PHOTOARCHIVE_PORT=8001`.
- If Library feels slow while the app is building, pause any manual Background
  Work job you do not need right now.
- If semantic search is unavailable, check Catalog's AI model and embedding
  index status.
- If People stays empty, make sure preview caching and People scanning are
  enabled and that cached previews have had time to build.
- If previews look stale after changing thumbnail settings, use Catalog's cache
  controls to refresh or clear generated cache.
