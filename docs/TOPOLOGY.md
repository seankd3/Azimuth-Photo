# Azimuth Photo V2 topology

Azimuth Photo V2 is a laptop-first desktop application. The laptop owns the
catalog, settings, decisions, indexes, previews, and interactive experience.
Attached folders and drives hold photographs. A network service is not part of
the current product architecture.

## Product layout

```text
azimuth-photo/
├── web/              Python engine and browser-native product UI
├── desktop/          Desktop shell; subject to replacement during V2
├── android/          Parked client, not a V2 dependency
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

The current implementation still contains FastAPI, Tauri, routes, and other V1
transport residue. They describe code awaiting replacement, not the product's
conceptual architecture. V2's target is one installed desktop application in
which the UI calls the core without exposing servers, modes, ports, pairing, or
sync concepts to the user.

See [CORE.md](CORE.md) for data and safety invariants,
[ARCHITECTURE.md](ARCHITECTURE.md) for the intended code layers, and
[development.md](development.md) for branch-specific commands.
