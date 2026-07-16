# Data And Privacy

Azimuth Photo is built for local personal archives. It does not need a hosted
account or a cloud photo service to catalog, browse, compare, flag, and export
your library.

## Source Photo Safety

Original photo folders remain the source of truth unless a satellite owner
explicitly uses **Free up space** after the hub has accepted the same original.

- Scanning reads source folders and records catalog metadata.
- Normal browsing, scanning, AI work, People, captions, sharing, publishing,
  and export do not edit original image files.
- Trash is the explicit exception: deleting moves originals into a `.trash`
  area on the same source root, and **Empty trash** permanently deletes those
  moved files.
- On a satellite, **Free up space** is a second explicit exception. It asks the
  authenticated hub to confirm each synced original, re-hashes the local file
  immediately before deletion, and skips locally changed or pending photos. The
  catalog row stays in place and the original remains available on demand from
  the hub.
- Removing a source from the catalog changes app catalog/cache state, not the
  source folder itself.
- Generated cache files and database rows can be rebuilt from the original
  folders.
- Temporarily offline source drives do not make catalog rows disappear. Cached
  previews, rankings, search data, and People labels remain usable until the
  drive is reachable again.

## Runtime Data

On a clean install, Azimuth Photo uses platform-native application-data roots.
The SQLite catalog and downloaded models live in the platform data directory;
settings live in its config directory; previews, embedding snapshots, and
Develop intermediates live in its cache directory; process files and logs live
in its state directory. Durable library exports default to
`~/Pictures/Azimuth Photo Exports`.

Older installations are detected from their exact runtime paths and remain
there. In particular, Azimuth Photo does not automatically move or rebuild an
existing catalog, preview cache, model store, settings file, Develop cache, or
backup folder. This protects large established libraries and makes an upgrade
behaviorally identical until the owner explicitly chooses new storage.

All runtime files stay on the machine running the app and remain ignored by
git. `PHOTOARCHIVE_HOME` selects one managed root; granular environment
overrides can select the catalog, settings, previews, models, embedding cache,
Develop cache, exports, backups, run directory, and log directory separately.

## Owner Authentication

Everything the owner can do is protected by a single **owner key**, created by
the setup wizard on first run (only its scrypt hash is stored). Browsers unlock
once at `/unlock` and receive a signed 90-day session cookie; scripts and
satellites send `Authorization: Bearer <owner key>`; paired devices authenticate
with their existing device tokens. Requests from the server's own loopback
address are exempt — sitting at the server keyboard is also the recovery path
if the key is lost (visit Settings from the server machine and set a new key,
which signs out every browser session but keeps paired devices working).

Installs that predate owner authentication stay unlocked until a key is set;
`/api/auth/status` reports `configured: false` so the UI can surface an
"Unsecured" notice. Share links, published client galleries, the unlock page,
static assets, `/api/version`, and pairing-code redemption (single-use,
expiring, rate-limited) are the only routes reachable without a credential.

The after-publish hook (`publish_hook`) executes a shell command, so it is
server-side configuration only: set the `PHOTOARCHIVE_PUBLISH_HOOK` environment
variable or edit the settings file on the server. The settings API rejects
writes to it and never returns the configured command.

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
