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

On a clean install, photoArchive uses platform-native application-data roots.
The SQLite catalog and downloaded models live in the platform data directory;
settings live in its config directory; previews, embedding snapshots, and
Develop intermediates live in its cache directory; process files and logs live
in its state directory. Durable library exports default to
`~/Pictures/photoArchive Exports`.

Older installations are detected from their exact runtime paths and remain
there. In particular, photoArchive does not automatically move or rebuild an
existing catalog, preview cache, model store, settings file, Develop cache, or
backup folder. This protects large established libraries and makes an upgrade
behaviorally identical until the owner explicitly chooses new storage.

All runtime files stay on the machine running the app and remain ignored by
git. `PHOTOARCHIVE_HOME` selects one managed root; granular environment
overrides can select the catalog, settings, previews, models, embedding cache,
Develop cache, exports, backups, run directory, and log directory separately.

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
