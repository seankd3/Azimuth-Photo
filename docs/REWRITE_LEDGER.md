# V2 rewrite ledger

The exhaustive answer to two questions:

1. What has actually been rebuilt from first principles?
2. What inherited code is still capable of reaching the finished product?

This is a release gate, not a percentage-complete report. Every code file
starts as **Legacy**. It leaves that state only through an exact entry in the
file register below. A broad claim about a directory or feature never promotes
the files inside it. Tests, build configuration, and operational scripts count:
they can preserve obsolete architecture or manufacture false confidence just as
easily as product code.

## Statuses

- **Legacy** — inherited code, including code that looks clean or currently
  works. It has not passed the V2 review.
- **Designing** — its purpose and smallest honest shape are being reconsidered.
  Nothing is credited as rewritten yet.
- **Rebuilt** — the V2 implementation has landed and the V1 implementation it
  replaces has been deleted. It still needs real product proof.
- **Proven** — rebuilt, exercised through its actual product path, within its
  performance and safety constraints, with the evidence recorded here.
- **Removed** — deliberately absent from V2 because the underlying question or
  feature was removed. No replacement is owed.

There is no "adopted", "bridged", or "mostly migrated" state. New code calling
old machinery is still **Legacy** as a product area.

## What it takes to become Proven

A product area is Proven only when all of these are true:

1. **Purpose** — the user-visible job is stated without reference to V1.
2. **Shape** — the smallest primitives and ownership boundaries have been
   derived from `CORE.md`, not copied from the old implementation.
3. **Replacement** — the complete behavior lands on the V2 core; no internal
   compatibility layer, dual path, fallback, or shadow implementation remains.
4. **Deletion** — the implementation, tables, routes, workers, settings, tests,
   and documentation made obsolete by that replacement leave in the same
   change.
5. **Review** — every surviving line in the area is read deliberately for
   necessity, naming, coupling, failure behavior, and performance. Moving a
   file is not review.
6. **Proof** — the real product path works with a fresh catalog and real photo
   files. Only tests that protect an expensive invariant are retained.
7. **Record** — exact files, commit, proof, and remaining limitations are added
   to this ledger. Unrecorded means Legacy.

Compatibility is owed to user-owned photographs, decisions, XMP, and other
explicit external formats. It is not owed to V1 modules, tables, routes, jobs,
settings, or network protocols.

## Product-area inventory

| Area | Status | V2 outcome required | Current truth |
|---|---|---|---|
| Verification and fresh boot | Designing | One trustworthy cross-platform check; a fresh catalog opens the real desktop product | Collection works, but the old Bash-only check is broken on this Windows machine and the broad suite still contains ordinary failures and one import-path stall |
| Core model and schema | Designing | Photos, copies, drives, decisions, sets, scope, and cache are the whole durable model | A fresh five-table catalog opens through `model.connect`; decisions own provenance, drive reads are explicit, identities cover every byte, moves are collision-proof, a complete drive sweep admits new photos, and cache kinds are explicit values. Scope and real desktop integration remain before the area is Proven |
| Computation | Legacy | Pure decode, metadata, colour, hashing, and rendering functions with no product state | Pure mathematics is scattered through V1 feature packages |
| Work and cache | Designing | Owed work is a query; one bounded executor; one cache contract | Cache kinds are explicit immutable capabilities and one owned worker has no process-global lifecycle, queue, or registry; legacy executors remain reachable outside this slice |
| Browse, disk, and tiles | Designing | One merged library view, truthful disk state, responsive thumbnails | A fresh folder now becomes a browseable photo and a content-addressed tile through the V2 slice; old routes, repositories, scanners, and thumbnail callers still need replacement and deletion |
| Organise | Legacy | Sets and grouping fall out of the core decisions model | Collections and stacks still use inherited feature and repository shapes |
| Keep and safety | Legacy | Trash, backup truth, recovery, and integrity checks use the same copy facts | Existing implementations predate the V2 core |
| Import and synchronize | Legacy | One complete intake/synchronize conversation with recoverable file operations | Current staging, registries, journals, routes, and synchronization are inherited |
| Develop | Legacy | Preserve proven pixel mathematics; rebuild the surrounding workflow on V2 | Large V1 workflow remains; embeddings do not block this rewrite |
| Rank, search, and AI | Legacy | Decisions drive rank; enrichment improves results but never gates the product | Parked until the core browse/storage path is excellent |
| Desktop UI | Legacy | One fast native-feeling surface with one state and interaction vocabulary | Existing interface contains valuable behavior learning but has not had its V2 line review |
| Desktop packaging | Legacy | A dependable install launches the local product and owns its lifecycle | Tauri packaging exists but has not passed the V2 proof bar |
| Android | Legacy | Explicit product decision after desktop V2 is coherent | No compatibility work is owed during the desktop rewrite |
| V1 remote hub/satellite system | Legacy | Delete it; retain only user-data lessons that inform the new storage design | Rejected as V2 architecture, but its surviving code remains Legacy until physically removed and registered by exact path |

## Exact code-file register

This register began empty at the start of the audited rewrite. Code written
earlier does not receive credit retroactively because it resembles the target
architecture. We promote files only while reading and proving them in the
current V2 pass.

Run `python scripts/rewrite_status.py` for totals by area or
`python scripts/rewrite_status.py --all` for every remaining path. The final
release gate is `python scripts/rewrite_status.py --check`. The inventory is
derived from Git, so a newly added code file is Legacy automatically
until its exact path earns a register entry.

| File | Status | Product area | Commit | Proof and reason it earns the status |
|---|---|---|---|---|
| `scripts/rewrite_status.py` | Proven | Verification | this ledger change | Its Git-derived inventory reports itself and every other executable/configuration path; normal mode reports, `--all` enumerates, and `--check` remains red while any path is unclassified |
| `web/model/__init__.py` | Rebuilt | Core | `da1ccd7e` | One constructor opens SQLite, applies the complete core schema, and closes on failure; fresh on-disk proof is in `test_core.py` |
| `web/model/schema.sql` | Rebuilt | Core | `284ba3f2`, `da1ccd7e` | Owns all five core tables and decision provenance without requiring V1 schema first; transitional `vc_of` keeps it below Proven until Develop is rebuilt |
| `web/model/drives.py` | Rebuilt | Core | `284ba3f2` | Explicit attach is the only discovery path; reads no longer probe drive letters or disconnected mappings |
| `web/model/decisions.py` | Rebuilt | Core | `284ba3f2` | Append-only decisions now record provenance, apply one authority rule, preserve provenance when carried, and expose one safe partial-amend primitive |
| `web/model/cache.py` | Rebuilt | Core | `0639ab8d` | Cache kinds are explicit immutable capabilities; recipes refuse undeclared inputs, failures are facts, and eviction receives the exact kinds boot owns rather than discovering imports |
| `web/work.py` | Rebuilt | Work and cache | `0639ab8d`, this change | Owed work and debt are queries over explicit kinds; one owned worker starts once and releases its connection without global lifecycle state; 62 focused tests pass |
| `web/tiles.py` | Rebuilt | Browse, disk, and tiles | `0639ab8d` | Replaces V1 adoption, mutable configuration, prefetch, and pregeneration shims with one owned store, atomic no-overwrite publication, exact-row clearing, and a truthful two-parameter recipe |
| `web/test_core.py` | Rebuilt | Core proof | `284ba3f2`, `da1ccd7e`, `0639ab8d`, this change | Opens an empty catalog and proves a real JPEG travels from fresh drive sweep through browse, full identity, and a 400 px persisted tile; 62 tests and 4 subtests pass on Windows, but real desktop proof is still owed |

## How each rewrite change closes

Every rewrite commit updates this file in the same diff:

- change the affected product-area status only when its whole definition is met;
- add every surviving code file touched by the completed V2 surface;
- list the old files and concepts removed in the commit message;
- record the exact verification command and real-product observation;
- leave limitations explicit—an unstated limitation cannot be distinguished
  from forgotten legacy behavior.

The V2 rewrite is complete only when every in-scope code file is either
**Proven** or **Removed**, every product area is **Proven** or **Removed**, and a
fresh install completes the photographer's core loop without invoking an
unregistered path.
