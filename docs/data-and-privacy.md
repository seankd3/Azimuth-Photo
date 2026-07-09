# Data And Privacy

photoArchive is built for local personal archives. It does not need a hosted
account or a cloud photo service to catalog, browse, compare, flag, and export
your library.

## Source Photo Safety

Original photo folders remain the source of truth.

- Scanning reads source folders and records catalog metadata.
- Normal browsing, scanning, AI work, People, captions, sharing, publishing,
  and export do not edit original image files.
- Trash is the explicit exception: deleting moves originals into a `.trash`
  area on the same source root, and **Empty trash** permanently deletes those
  moved files.
- Removing a source from the catalog changes app catalog/cache state, not the
  source folder itself.
- Generated cache files and database rows can be rebuilt from the original
  folders.
- Temporarily offline source drives do not make catalog rows disappear. Cached
  previews, rankings, search data, and People labels remain usable until the
  drive is reachable again.

## Runtime Data

Runtime data lives under `web/` by default and is ignored by git.

- `photoarchive.db`: SQLite catalog, ratings, comparisons, metadata, People
  data, embeddings, and search caches.
- `.thumbcache/`: generated thumbnails and optional fast local copies of
  original images for browser viewing.
- `.models/`: locally downloaded AI and face-recognition models.
- `settings.local.json`: machine-local settings.
- `.run/server.log`: local server log.

These files are intended to stay on the machine running the app. Do not commit
them to the repo.

## Local AI

The embedding model is installed locally from Hugging Face when you choose to
install it. After download, embedding and search work happens on the local
machine.

The app can still browse, rank, flag, and export without local AI models.

## Presentation Assets

Public repo visuals should not use personal archive screenshots or personal
photo assets. The checked-in preview artwork under `docs/assets/` uses
synthetic demo content only.

Safe presentation assets should avoid:

- Real personal photos.
- Real folder paths, filenames, or drive names.
- Recognizable people.
- GPS coordinates, camera serial numbers, or other private metadata.
- Crops that accidentally reveal private thumbnails in the UI.

Prefer synthetic mockups, generated demo images, or a small dedicated demo
catalog with non-private files.
