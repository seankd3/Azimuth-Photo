# Product vision

Azimuth Photo is the finished desktop home for a photographer's working life:
import, browse, cull, rank, edit, organize, export, and share. It combines
Lightroom Classic's professional instincts with Google Photos' immediacy while
remaining local, private, fast, and understandable.

The first product is Sean's real workflow on the Windows laptop. The design
must generalize cleanly to another photographer without exposing Sean's paths,
hardware, or storage arrangement as product concepts.

## What it should feel like

- Open one installed app and see the complete library immediately.
- Work identically with the archive drive attached or away; only uncached
  original pixels require the drive.
- Import a card, make decisions, develop photographs, and file finished edits
  without translating between disconnected tools.
- Find photographs through folders, dates, people, places, technical facts,
  visual meaning, and remembered context.
- Trust every Pick, Reject, comparison, edit, name, and collection as durable
  work.
- Understand safety and availability without learning about databases, ports,
  workers, models, or storage plumbing.

## Non-negotiables

- **One coherent product.** A feature joins the existing interaction and data
  model; it does not add another miniature application.
- **Laptop first.** The catalog, decisions, derivatives, and interface belong
  on the laptop. Servers and networks are not prerequisites.
- **Offline is normal.** An absent archive is a first-class state, not an error.
- **Originals are sacred.** No automated operation risks the last verified
  copy. Destructive actions are explicit, recoverable, and byte-verified.
- **Decisions are durable; computations are rebuildable.** Picks, edits, names,
  and organization survive. Tiles, embeddings, captions, and inferred scores
  may be recreated.
- **Speed is product behavior.** Interactive work never competes with a chore
  for the resource it needs.
- **Privacy is the default.** Nothing leaves the machine without a deliberate
  destination and action.
- **Elegance is fit.** The code and UI follow the real structure of the problem.
  Smallness and polish are consequences, not substitutes.
- **Weak intelligence does not ship.** A derivative used by another feature
  must be trustworthy enough to support it; otherwise the feature waits.

## Core loop

1. **Import** without overwriting, duplicating, or losing provenance.
2. **Browse** one combined library independent of physical drive layout.
3. **Cull and rank** through durable direct choices; inference helps but never
   impersonates earned judgment.
4. **Develop** non-destructively with faithful color and predictable output.
5. **Organize** through folders and collections as interchangeable views, not
   separate browsers.
6. **Export and share** from finished edits with explicit privacy and no
   duplicated archive structure.

## Product boundary

V2 is not a server product, generic media manager, cloud account, telemetry
platform, or enterprise permission system. Android, remote access, helpers,
publishing automation, and social destinations may return after the installed
desktop product is excellent. They may not distort its core to reserve a place
for hypothetical futures.

## Definition of success

Azimuth is ready when a photographer can install it on a clean Windows machine,
point it at working and archive storage, complete the whole core loop, restart,
disconnect and reconnect drives, recover from ordinary failures, and continue
without lost work, silent errors, stale UI, or technical administration.

The exact product decisions live in [`MASTER_PLAN.md`](../MASTER_PLAN.md). The
rewrite order and release evidence live in [the roadmap](product-roadmap.md).
