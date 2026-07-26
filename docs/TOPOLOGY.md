# Azimuth Photo topology

This is the canonical map for code, runtime data, generated data, and original
photos. There is one product, one repository, one active branch, and one clean
checkout on each development machine.

## Code

The GitHub repository is `Sean-Kenneth-Doherty/azimuth-photo`. Both machines use
its `main` branch directly:

| Machine | Canonical checkout |
| --- | --- |
| Omarchy | `/home/sean/Projects/azimuth-photo` |
| XPS | `C:\Users\smast\OneDrive\Desktop\Projects\photography\azimuth-photo` |

The repository contains every maintained product surface:

```text
azimuth-photo/
├── web/              FastAPI hub/satellite, desktop web UI, and mobile PWA
├── desktop/          Windows Tauri shell
├── android/          Android client
├── clients/          External integrations, including Lightroom
├── site/             Public website and Field Log output
├── tools/field-log/  Field Log source and capture tooling
├── scripts/          Build, check, run, and deployment commands
├── deploy/           Service and restore-drill units
└── docs/             Current product and operating documentation
```

Do not create integration branches, lane branches, or development worktrees.
Owned changes are committed directly to `main`. The Field Log may create
temporary detached checkouts under its ignored scratch directory while
reproducing historical versions; those are disposable test fixtures, not
development copies.

### Current cutover status

Canonical development is complete on both machines. The XPS shortcut and real
satellite library already run from the canonical checkout and runtime paths.

Omarchy port `8000` still runs the preserved pre-consolidation production
checkout. The clean canonical Omarchy checkout is ready, but the live service
must not be repointed or the preserved checkout retired until a deliberate
service cutover verifies the catalog, caches, intake paths, and rollback path.
This production boundary is the only active code exception.

## Machine roles

| Machine | Role |
| --- | --- |
| Omarchy | Always-on hub, authoritative catalog, background indexing, and durable-original intake |
| XPS | Interactive desktop client, local catalog mirror, SSD caches, imports, and recent-original working set |
| Pixel | Android client and phone-photo source |

The same `web/` application runs as a hub on Omarchy and as a satellite on the
XPS. Catalog changes converge through the sync log; original files upload by
content identity and are byte-verified before the XPS can offer to remove its
local copy.

## Storage tiers

Code and runtime data are deliberately separate. A source checkout must never
be used as a database, cache, model, log, or backup directory.

### Omarchy

Fast SSD storage uses the platform-native Azimuth Photo roots:

| Data | Location |
| --- | --- |
| Catalog and models | `/home/sean/.local/share/azimuth-photo/` |
| Settings | `/home/sean/.config/azimuth-photo/` |
| Preview, embedding, and active Develop caches | `/home/sean/.cache/azimuth-photo/` |
| Logs and process state | `/home/sean/.local/state/azimuth-photo/` |
| Python environment | `/home/sean/.local/share/azimuth-photo/venv/` |

The 20 TB Expansion drive is the durable, slow tier:

| Data | Location |
| --- | --- |
| Originals | `/mnt/expansion/Photos/` |
| Incoming upload staging | `/mnt/expansion/Photos/_intake/` |
| Durable catalog backups | `/mnt/expansion/Azimuth Photo/Omarchy/backups/` |
| Preserved generated-cache history | `/mnt/expansion/Azimuth Photo/Omarchy/cache-archive/` |
| Consolidation and historical archives | `/mnt/expansion/Azimuth Photo/Archive/` |

The hub service explicitly sets the intake, original, and backup paths. The
Expansion drive is not used for the live catalog, models, indexes, thumbnails,
or other latency-sensitive metadata.

### XPS

The Windows satellite uses `C:\Azimuth Photo\` as its portable runtime home:

```text
C:\Azimuth Photo\
├── data\catalog\azimuth.db
├── data\models\
├── config\settings.json
├── thumbs\
├── cache\embeddings\
├── cache\develop\
├── state\logs\
└── state\transfer\
```

The laptop keeps a complete catalog mirror, browsable previews, selected
full-resolution cache entries, and recent originals. Older originals can be
removed only through **Free up space**, after the hub has accepted the upload,
verified the complete file hash, registered the original, and confirmed the
same bytes immediately before local deletion.

Code, tests, and service configuration must be complete and verified before any
existing library migration begins. Migration then runs as resumable,
collision-proof batches with a durable receipt per file; no bulk transfer is
part of a normal code deploy.

## Slow-drive rules

- Bulk HDD work is serialized. It must never create a random-seek storm.
- Interactive browsing reads SSD/RAM previews whenever possible.
- The preview cache owns explicit byte budgets and evicts by recency.
- Recent full-resolution cache entries make recently viewed RAWs available
  without waking the archive drive.
- Models, embeddings, the catalog, and live indexes stay on SSD.
- Originals on the Expansion drive are never reorganized or deleted by a
  development cleanup.
- Generated data may be moved into the named cache archive, but is not silently
  discarded during consolidation.

## Runtime modes

The hub listens on Omarchy port `8000`. The XPS satellite listens on loopback
port `8010`. Never use `AZIMUTH_SMOKE_MODE=1` for either real instance; it is
test-only and disables normal initialization and background work.

Key deployment variables:

```text
AZIMUTH_MODE
AZIMUTH_HOME
AZIMUTH_HUB_URL
AZIMUTH_SYNC_INTAKE_DIR
AZIMUTH_SYNC_RAWS_DIR
AZIMUTH_BACKUP_DIR
AZIMUTH_SSD_CACHE_BYTES
```

See [getting-started.md](getting-started.md) for local setup,
[development.md](development.md) for checks, and [recovery.md](recovery.md)
for catalog restore procedures.
