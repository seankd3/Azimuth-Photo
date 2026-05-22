# photoArchive

photoArchive is a local-first photo library for people with large folders of real
photos: external drives, RAW files, years of exports, slow storage, and a need to
browse, search, rank, and organize without uploading the archive anywhere.

The app runs on your machine. It indexes your source folders, stores its own
catalog and caches, and never edits or deletes your original photo files.

## Current State

photoArchive is a Linux-first desktop web app. The main screens are:

- **Catalog**: add photo folders, scan/rescan sources, manage cache settings,
  install local AI models, tune background work, and review status.
- **Library**: browse the archive in a fast grid or map view, sort and filter
  results, search, flag picks/rejects, open a full-screen loupe, and export.
- **Compare**: rank photos with Mosaic, Swiss A/B, or Top 50 comparison modes.
- **People**: run local face detection, label people, merge suggested matches,
  ignore unwanted groups, and filter the Library by person.

Supported scanned file types are `.jpg`, `.jpeg`, `.png`, `.dng`, `.cr3`,
`.tif`, `.tiff`, and `.webp`.

## Requirements

- Python 3.11+
- Linux, currently the primary target
- Enough local disk for the SQLite catalog, thumbnails, optional original-image
  cache copies, and optional AI models
- An NVIDIA GPU is strongly recommended for semantic search, deep search, and
  large embedding runs
- Internet access for first-time Hugging Face model downloads, unless models are
  already present locally

The app can still catalog and browse images without AI models installed. Semantic
search, similarity, duplicate discovery, deep search, and embedding-backed ranking
helpers become useful after the embedding index is built.

## Install

```bash
git clone https://github.com/Sean-Kenneth-Doherty/photo-archive.git
cd photo-archive/web
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

From the repo root:

```bash
./scripts/photoarchive-server start
```

Open:

```text
http://127.0.0.1:8000
```

Useful server commands:

```bash
./scripts/photoarchive-server status
./scripts/photoarchive-server logs
./scripts/photoarchive-server restart
./scripts/photoarchive-server stop
```

The helper uses these environment variables:

- `PHOTOARCHIVE_HOST`, default `127.0.0.1`
- `PHOTOARCHIVE_PORT`, default `8000`
- `PHOTOARCHIVE_MAX_LOG_BYTES`, default `10485760`

You can also run FastAPI directly:

```bash
cd web
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```

## First Run

1. Open the app. The root page opens the Catalog screen.
2. In **Catalog Sources**, choose a folder or use tree browse, then select
   **Add + Scan**.
3. Leave **Computer Work Mode** on **Light Background** for normal use. Switch to
   **Browse** when you want maximum UI responsiveness, or **Max Work** for
   overnight cache/model/index building.
4. Go to **Library** once photos appear. Use the grid first; switch to map view
   when your photos have GPS metadata.
5. Use **Compare** when you want the app to learn which images are better.
6. Install the 2B AI model from Catalog when you want semantic search. Install
   the 8B model only if you want heavier scheduled deep-search results.

## Catalog And Settings

Catalog is the control room for the app:

- Add source folders with **Choose Folder**, **Tree Browse**, or a typed path.
- Rescan a source when files change on disk.
- Remove a source from the active catalog. Source removal is about catalog/cache
  state; it does not delete the original photo folder.
- Pick a background-work mode: **Browse**, **Light Background**, or **Max Work**.
- Install the local 2B daily-search model and optional 8B deep-search model.
- Configure People recognition and review the local face model status.
- Save deep-search terms to precompute on a schedule.
- Tune thumbnail sizes, JPEG quality, RAM cache, SSD cache, cache profile, and
  idle cache warming.
- Clear generated cache files when you want previews rebuilt.

## Library

Library is for everyday browsing and culling:

- Sort by rating, confidence, date taken, date modified, file size, resolution,
  camera, or filename.
- Search with text. When embeddings are ready, searches can use local semantic
  image search; otherwise metadata still works.
- Use **Deep Search** after 8B deep-search terms have been indexed.
- Filter by orientation, ranked/unranked/confident status, flag, minimum stars,
  person, folder, date, file type, camera, and lens.
- Switch between **Grid** and **Map**.
- Use the thumbnail-size slider to make the grid denser or more inspectable.
- Open the loupe with Enter or by selecting a photo. The loupe has a filmstrip,
  progressive image loading, zoom, pan, metadata overlay, and optional cache
  status.
- Flag images as picked, unflagged, or rejected.
- Use **Select** for batch flagging and selected-image JSON/CSV export.
- Export the current ranked/filtered result set as JSON or CSV.

Keyboard shortcuts:

- Library: arrow keys navigate, Enter opens the loupe, `P` picks, `X` rejects,
  `U` clears the flag, Tab jumps to Compare, Esc clears selection.
- Loupe: left/right moves through photos, scroll zooms, Esc closes.

## Compare

Compare builds ranking signal from your choices:

- **Mosaic** shows a grid; pick the best image and the app records one winner
  against the visible alternatives. This is the fastest way to cover a library.
- **Swiss** shows A/B matchups chosen to improve ranking confidence.
- **Top 50** focuses comparison work on the current best images.
- Mosaic strategies control the pool: **Diverse**, **Explore**, **Compete**,
  **Top Cut**, and **Random**.
- Search and filters in Compare limit the comparison pool just like Library.
- **Shuffle** refreshes Mosaic candidates.
- Undo is available from the toolbar or the up-arrow shortcut.
- The bottom bar shows rank-signal count, pool size, coverage, and background
  work status.

## People

People recognition is local:

- Face detection runs from app-generated cached previews.
- The People page groups results into **Most Seen**, **Named People**, **Needs
  Review**, and **Other Faces**.
- Add labels to people you recognize.
- Merge suggested duplicates when two groups are the same person.
- Ignore unwanted groups.
- Use People filters in Library and Compare to narrow browsing/ranking to a
  person.

## Search, Similarity, And AI

photoArchive uses two embedding indexes:

- **Daily Search**: Qwen3-VL-Embedding-2B, intended for normal interactive
  semantic search.
- **Deep Search**: Qwen3-VL-Embedding-8B, intended for scheduled saved-query work
  when the computer has time.

The 2B model is the practical starting point. After installation, photoArchive
builds embeddings in the background and search quality improves as more images
are indexed. Deep Search uses saved terms from Catalog and returns smarter cached
results when the 8B work is ready.

Advanced local API surfaces also exist for similar images, duplicates, EXIF, and
collections:

```text
/api/similar/{image_id}
/api/duplicates
/api/image/{image_id}/exif
/api/collections
```

## Background Work

The bottom **Work** panel appears in Library and Compare. It summarizes active
background jobs such as embedding, deep search, preview cache warming, original
cache warming, and People scanning. Use Catalog's work mode to decide whether the
app should prioritize browsing responsiveness or background throughput.

## Data And Privacy

Runtime data lives under `web/` by default and is ignored by git:

- `photoarchive.db`: SQLite catalog, ratings, comparisons, metadata, People data,
  embeddings, and search caches
- `.thumbcache/`: generated thumbnails and optional fast local copies of original
  images for browser viewing
- `.models/`: locally downloaded AI models
- `settings.local.json`: machine-local settings
- `.run/server.log`: server log

Original photo folders remain the source of truth. Generated thumbnails, cached
original copies, model files, and database rows can be rebuilt.

## Troubleshooting

- If the server helper says the virtualenv is missing, rerun the install steps in
  `web/`.
- If the port is busy, stop the old server or run with `PHOTOARCHIVE_PORT=8001`.
- If semantic search is unavailable, check Catalog's AI model and embedding index
  status.
- If Library feels slow while the app is building, switch **Computer Work Mode**
  to **Browse**.
- If People stays empty, make sure preview caching and People scanning are
  enabled and that cached previews have had time to build.
- If previews look stale after changing thumbnail settings, use Catalog's cache
  controls to refresh or clear generated cache.

Quick health checks:

```bash
curl http://127.0.0.1:8000/api/dev/status
curl http://127.0.0.1:8000/api/ai/status
curl http://127.0.0.1:8000/api/cache/status
```

## Development

The frontend is browser-native HTML, CSS, and JavaScript. There is no Vite,
TypeScript, or bundled build step.

Useful checks:

```bash
cd web
.venv/bin/python -m unittest
cd ..
scripts/photoarchive-browser-smoke --base-url http://127.0.0.1:8000
```

## License

MIT
