# Azimuth Photo

Azimuth Photo is a local-first desktop photo library for serious photographers.
It aims to make the complete workflow—import, browse, cull, rank, develop,
organize, export, and share—feel immediate while keeping originals, decisions,
and private data under the photographer's control.

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)

## V2 rewrite status

Azimuth 2.0 is an active ground-up rewrite. V1 accumulated overlapping storage,
sync, worker, cache, and feature machinery and never became a dependable daily
product. V2 keeps the product learning and polished interaction work while
re-deriving the architecture from a small core.

The rewrite is not released. Feature descriptions in historical specifications
are not completion claims.

## The product

- One installed Windows desktop application, with no server administration in
  the normal experience.
- One combined library across working and archive storage.
- Browsing, organization, search, and ranking that continue when an archive
  drive is unplugged.
- Durable direct decisions—picks, comparisons, edits, names, and collections—
  with rebuildable previews, indexes, embeddings, and inferred scores.
- Explicit, recoverable, full-byte-verified handling of destructive actions.
- A keyboard-first grid, loupe, comparison flow, and non-destructive Develop
  surface built for a photographer's full working day.

## V2 shape

The model is described in [the core](docs/CORE.md): drives, photographs, copies,
decisions, and cache entries, with seven operations around them. The surrounding
code shape lives in [the architecture](docs/ARCHITECTURE.md).

The executable is one native process with a direct in-process bridge. The V1
server, routes, workers and browser UI are gone from the tree (2026-09-08);
what remains is the core, the surfaces over it, the colour mathematics in
`web/pixels/`, and one bundled UI. `docs/REWRITE_LEDGER.md` registers every
code file with the evidence that proved it.

## Documentation

Start at the [documentation index](docs/README.md). It separates current V2
guidance, behavior references awaiting adoption, and historical V1 architecture.

- [Product decisions](MASTER_PLAN.md)
- [Core model and invariants](docs/CORE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Product vision](docs/product-vision.md)
- [Rewrite roadmap](docs/product-roadmap.md)
- [Agent guide](AGENTS.md)

## Development

This is prerelease software under active reconstruction. Before running or
editing it, read [AGENTS.md](AGENTS.md), [the topology](docs/TOPOLOGY.md), and
[the development guide](docs/development.md), then verify the commands against
the current branch. Runtime catalogs, caches, models, logs, backups, and photos
never belong in the source checkout.

Azimuth Photo is free and open source under AGPL-3.0.
