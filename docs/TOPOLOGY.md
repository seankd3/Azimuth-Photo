# Azimuth Photo topology

Azimuth Photo is one product and one repository with three runtime roles. A
single machine can run standalone, or several devices can form a private photo
cloud without changing the source code.

## Product layout

```text
azimuth-photo/
├── web/              FastAPI engine, desktop web UI, and mobile PWA
├── desktop/          Windows Tauri shell
├── android/          Native Android client
├── clients/          External integrations
├── site/             Public website
├── tools/field-log/  Product-history source and capture tooling
├── scripts/          Build, check, run, and deployment commands
├── deploy/           Configurable Linux service templates
└── docs/             Product and operating documentation
```

Runtime databases, caches, models, logs, backups, and photos stay outside this
tree. GitHub `main` is the source of truth; deployment-specific facts belong in
the ignored `AGENTS.local.md` overlay.

## Runtime roles

| Role | Purpose |
| --- | --- |
| Standalone | Complete local library with no server dependency |
| Hub | Always-on authoritative catalog, indexing, sync intake, and durable originals |
| Satellite | Interactive catalog mirror, fast local caches, imports, and a recent-original working set |
| Client | Android or browser UI connected to a configured hub |

`AZIMUTH_MODE` selects `standalone`, `hub`, or `satellite`. Android asks for the
hub URL during onboarding rather than shipping a maintainer-specific address.

## Storage tiers

Keep these concerns separate:

| Tier | Typical contents | Placement |
| --- | --- | --- |
| Source | Git checkout and virtual environment | Developer-selected project directory |
| Fast state | Catalog, indexes, models, settings, logs | Platform-native application data on SSD |
| Fast cache | Previews, embeddings, active Develop results | SSD with explicit byte budgets |
| Durable originals | RAWs, images, videos, intake | User-selected library root, often NAS/HDD |
| Recovery | Verified catalog backups and transfer receipts | Separate durable location |

Without overrides, Azimuth uses platform-native application data directories.
`AZIMUTH_HOME` provides a portable all-in-one runtime root. Fine-grained
variables such as `AZIMUTH_DATA_DIR`, `AZIMUTH_CACHE_DIR`,
`AZIMUTH_THUMB_CACHE_DIR`, and `AZIMUTH_BACKUP_DIR` support tiered servers.

For a slow archive disk:

- keep catalog, indexes, models, previews, and active Develop cache on SSD;
- serialize bulk reads with `AZIMUTH_BULK_HDD_CONCURRENCY=1`;
- serve interactive browsing from SSD/RAM previews;
- cache recently viewed full-resolution files;
- never reorganize originals during a code deployment.

## Hub and satellite safety

Catalog changes converge through the sync log. Original files upload by content
identity and are byte-verified before a satellite may offer to remove its local
copy. **Free up space** must confirm the same complete bytes immediately before
local deletion.

Library migrations are separate, resumable operations with collision-proof
destinations and durable per-file receipts. A code update never implies a bulk
photo transfer.

## Configuration

The Windows PowerShell launcher defaults to a fresh standalone library in the
current user's local application-data directory. Existing or portable
libraries can be pinned explicitly:

```powershell
.\scripts\start_azimuth_windows.ps1 `
  -DataRoot "D:\Azimuth Photo" `
  -Mode satellite `
  -RequireExistingCatalog
```

Linux deployments start from `deploy/azimuth-photo.service` and
`deploy/azimuth-photo.env.example`. Copy the environment example outside the
repository and adapt addresses, users, photo roots, and cache budgets to the
installation.

See [getting-started.md](getting-started.md) for local setup,
[development.md](development.md) for checks, and [recovery.md](recovery.md) for
catalog restoration.
