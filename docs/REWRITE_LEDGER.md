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
| Verification and fresh boot | Designing | One trustworthy cross-platform check; a fresh catalog opens the real desktop product | The V2 `Library` boundary now opens, attaches, sweeps, browses, tiles, starts/stops, closes, and reopens on Windows; the native desktop does not call it yet, and the inherited broad check remains untrusted |
| Core model and schema | Designing | Photos, copies, drives, decisions, sets, scope, and cache are the whole durable model | Every core file has now passed the first-principles pass: the fresh five-table catalog owns photo/copy truth and append-only decisions, while sets and scopes add no tables or shadow state. Real desktop integration remains before the area is Proven |
| Computation | Designing | Pure decode, metadata, colour, hashing, and rendering functions with no product state | File-kind detection and the shared decode/resize/encode renderer have passed the V2 review; metadata and Develop colour mathematics remain in inherited feature packages |
| Work and cache | Designing | Owed work is a query; one bounded executor; one cache contract | Cache kinds are explicit immutable capabilities and one owned worker has no process-global lifecycle, queue, or registry; legacy executors remain reachable outside this slice |
| Browse, disk, and tiles | Designing | One merged library view, truthful disk state, responsive thumbnails | The owned V2 product boundary now carries a chosen folder through attach, sweep, bounded/composable browse, persisted tile, close, and reopen; decision reindexing updates every row of an identity atomically. Native UI integration and deletion of old route/repository paths remain |
| Organise | Designing | Sets and grouping fall out of the core decisions model | Collections and keywords are validated decision families with composable scopes; opaque saved queries were removed from the core contract, while inherited collection, keyword, saved-view, and stacks surfaces still need replacement |
| Keep and safety | Designing | Trash, backup truth, recovery, and integrity checks use the same copy facts | V2 backup and reclaim now require real regular files and repeat byte proof immediately before deleting one working copy; inherited trash, recovery, integrity, and duplicate paths remain |
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
| `web/model/photos.py` | Rebuilt | Core | `b12f4ff8`, `ca40451c`, `96e985d9`, `7227545d` | Owns admission, full-byte identity, truthful open/state, collision-proof put/move, and version grouping; a verified import now records its photo and copy in the same transaction |
| `web/model/copies.py` | Rebuilt | Core | `96e985d9`, `7227545d` | A complete marker-stable sweep admits new photos, reports changed bytes without replacing identity, retires only hints, and recognizes explicit alternate copy tails instead of duplicating photos |
| `web/model/backup.py` | Rebuilt | Keep and safety | `b12f4ff8`, `7227545d` | Backup only adds byte-proven regular files; reclaim ignores symlinks, honors alternate tails, repeats byte proof at the destructive gate, and removes at most one expendable copy per call |
| `web/model/cache.py` | Rebuilt | Core | `0639ab8d` | Cache kinds are explicit immutable capabilities; recipes refuse undeclared inputs, failures are facts, and eviction receives the exact kinds boot owns rather than discovering imports |
| `web/model/scope.py` | Rebuilt | Core | `d33c655e` | One immutable filter value composes folders, star floors, sets, and explicit selections; large id selections remain one bound JSON argument rather than growing SQL |
| `web/model/sets.py` | Rebuilt | Organise | `d33c655e` | Collections and keywords are named decision families with validated stable ids and atomic hash-membership writes; nonexistent computed-query storage was deleted rather than preserved |
| `web/work.py` | Rebuilt | Work and cache | `0639ab8d`, `bce1178f` | Owed work and debt are queries over explicit kinds; one owned worker starts once and releases its connection without global lifecycle state; 62 focused tests pass |
| `web/tiles.py` | Rebuilt | Browse, disk, and tiles | `0639ab8d` | Replaces V1 adoption, mutable configuration, prefetch, and pregeneration shims with one owned store, atomic no-overwrite publication, exact-row clearing, and a truthful two-parameter recipe |
| `web/library.py` | Rebuilt | Browse, disk, and tiles | this change | One bounded query serves every grid scope and sort; counts, folders, facets, dates, decision reindex, and folder safety derive from core rows without a repository or source visibility rule |
| `web/render.py` | Rebuilt | Computation | this change | One decode, orientation, resize, and JPEG encode path serves tiles; paid RAW/JPEG/memory refusals remain in the focused suite and it imports no product state |
| `web/photo/kind.py` | Rebuilt | Computation | this change | RAW decoding is selected by bytes rather than extension alone, while Develop capability and camera provenance remain separate explicit questions |
| `web/boot.py` | Rebuilt | Verification and fresh boot | `e331be8f` | One owned `Library` is the V2 product boundary: explicit catalog and tile paths, folder attach/refresh, browse, on-demand tile, debt, one worker, and deterministic close; no environment bootstrap or V1 import graph |
| `web/test_core.py` | Rebuilt | Core proof | `284ba3f2`, `da1ccd7e`, `0639ab8d`, `bce1178f`, `7227545d`, `d33c655e`, `e331be8f`, this change | Proves the complete fresh `Library` path plus pagination, duplicate-identity reindex, malformed decisions/dates, decode limits, and persisted tiles; 76 tests and 4 subtests pass on Windows, but native desktop proof is still owed |

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
