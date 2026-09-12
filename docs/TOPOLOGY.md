# Azimuth Photo V2 topology

Azimuth Photo V2 is a laptop-first desktop application. The laptop owns the
catalog, settings, decisions, indexes, previews, and interactive experience.
Attached folders and drives hold photographs. A network service is not part of
the current product architecture.

## Product layout

```text
azimuth-photo/
├── web/              V2 model, product boundary, native edge, and modular UI
├── desktop/          Desktop build guidance and canonical executable icon
├── scripts/          Repeatable development and operating commands
└── docs/             Current guidance and preserved behavior references
```

Runtime databases, caches, models, logs, backups, and photographs stay outside
the source checkout. GitHub `main` is released source; an explicitly approved
rewrite branch may precede a deliberate merge.

## Runtime roles

| Role | Owns | Availability |
| --- | --- | --- |
| Laptop | Catalog, decisions, previews, embeddings, settings, UI | Always |
| Working storage | Recently imported and actively edited originals | Usually attached |
| Record/archive storage | Durable cold originals | Often unplugged |
| Optional share/helper | Files or owed derivative work | Never awaited |

A drive is identified by its marker, not its drive letter. A path is a drive
plus a tail. A drive being absent is a normal state: cached browsing, search,
ranking, organization, and decisions continue without it. Opening uncached
original pixels states plainly that the drive is needed.

## Storage contract

- The catalog and all interactive caches live on SSD.
- A record drive may hold the last durable original; working storage may not.
- Backup adds a verified copy. Reclaim removes only a working copy after a
  fresh full-byte comparison with the record copy.
- Code deployment never moves or reorganizes photographs.
- A share carrying the same drive marker is the same drive at another address.
- SQLite never lives on a network share.
- An optional helper may compute owed cache entries against shared storage, but
  the laptop never asks it a synchronous question.

On Sean's current installation, deployment-specific letters and paths belong
in ignored `AGENTS.local.md`, not this portable document.

## Application boundary

The V2 application is one native process. Its UI calls the Python product
boundary directly; the FastAPI routes, loopback server, Tauri parent, child
engine and the whole V1 application are deleted (09-08).

See [CORE.md](CORE.md) for data and safety invariants,
[ARCHITECTURE.md](ARCHITECTURE.md) for the intended code layers, and
[development.md](development.md) for branch-specific commands.
