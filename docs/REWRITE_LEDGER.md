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
| Verification and fresh boot | Designing | One trustworthy cross-platform check; a fresh catalog opens the real desktop product | An isolated V2 local engine now opens `azimuth-v2.db`, starts the thread-confined product, mounts only V2 routes, and releases its Windows handle on prepare-quit; the packaged server still launches V1 and real subprocess/native-window proof remains |
| Core model and schema | Designing | Photos, copies, drives, decisions, sets, scope, and cache are the whole durable model | Every core file has now passed the first-principles pass: the fresh five-table catalog owns photo/copy truth and append-only decisions, while sets and scopes add no tables or shadow state. Real desktop integration remains before the area is Proven |
| Computation | Designing | Pure decode, metadata, colour, hashing, and rendering functions with no product state | File-kind detection, bounded RAW EXIF, embedded browse metadata, and the shared decode/resize/encode renderer have passed the V2 review; the broad V1 metadata extractor is deleted, while path-based date inference and Develop colour mathematics remain inherited |
| Work and cache | Designing | Owed work is a query; one bounded executor; one cache contract | Cache kinds are explicit immutable capabilities and one owned worker has no process-global lifecycle, queue, or registry; the separate metadata scanner is deleted, while other legacy executors remain reachable outside this slice |
| Browse, disk, and tiles | Designing | One merged library view, truthful disk state, responsive thumbnails | The owned V2 product boundary now carries a chosen folder through attach, sweep, bounded/composable browse, persisted tile, metadata details, close, and reopen; the typed photo routes refuse malformed scope and invalid recipes while keeping SQLite on its intended index. Mounting them in the native app and deleting the old route/repository paths remain |
| Organise | Designing | Sets and grouping fall out of the core decisions model | Collections and keywords are validated decision families with composable scopes; opaque saved queries were removed from the core contract, while inherited collection, keyword, saved-view, and stacks surfaces still need replacement |
| Keep and safety | Designing | Trash, backup truth, recovery, and integrity checks use the same copy facts | V2 backup and reclaim now require real regular files and repeat byte proof immediately before deleting one working copy; inherited trash, recovery, integrity, and duplicate paths remain |
| Import and synchronize | Legacy | One complete intake/synchronize conversation with recoverable file operations | Current staging, registries, journals, routes, and synchronization are inherited |
| Develop | Legacy | Preserve proven pixel mathematics; rebuild the surrounding workflow on V2 | Large V1 workflow remains; embeddings do not block this rewrite |
| Rank, search, and AI | Legacy | Decisions drive rank; enrichment improves results but never gates the product | Parked until the core browse/storage path is excellent |
| Desktop UI | Designing | One fast native-feeling surface with one state and interaction vocabulary | The V2 local transport and route manifest exist independently of the inherited shell; the first-principles visual shell has not landed yet |
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
| `scripts/rewrite_status.py` | Proven | Verification | `be7f7dbc`, `b7665224` | Its Git-derived inventory reports every executable/configuration path; `--check` stays red for Legacy/Rebuilt files, rejects missing active entries, and rejects a Removed path that still exists while accepting one physically deleted |
| `web/model/__init__.py` | Rebuilt | Core | `da1ccd7e` | One constructor opens SQLite, applies the complete core schema, and closes on failure; fresh on-disk proof is in `test_core.py` |
| `web/model/schema.sql` | Rebuilt | Core | `284ba3f2`, `da1ccd7e` | Owns all five core tables and decision provenance without requiring V1 schema first; transitional `vc_of` keeps it below Proven until Develop is rebuilt |
| `web/model/drives.py` | Rebuilt | Core | `284ba3f2` | Explicit attach is the only discovery path; reads no longer probe drive letters or disconnected mappings |
| `web/model/decisions.py` | Rebuilt | Core | `284ba3f2`, `155d450c` | Append-only decisions record provenance, apply one authority rule, preserve provenance when carried, expose safe partial amendment, and now name capture-date corrections explicitly |
| `web/model/photos.py` | Rebuilt | Core | `b12f4ff8`, `ca40451c`, `96e985d9`, `7227545d` | Owns admission, full-byte identity, truthful open/state, collision-proof put/move, and version grouping; a verified import now records its photo and copy in the same transaction |
| `web/model/copies.py` | Rebuilt | Core | `96e985d9`, `7227545d` | A complete marker-stable sweep admits new photos, reports changed bytes without replacing identity, retires only hints, and recognizes explicit alternate copy tails instead of duplicating photos |
| `web/model/backup.py` | Rebuilt | Keep and safety | `b12f4ff8`, `7227545d` | Backup only adds byte-proven regular files; reclaim ignores symlinks, honors alternate tails, repeats byte proof at the destructive gate, and removes at most one expendable copy per call |
| `web/model/cache.py` | Rebuilt | Core | `0639ab8d`, `155d450c` | Cache kinds are explicit immutable capabilities; recipes refuse undeclared inputs, failures are facts, and an optional projection applies under a savepoint so a partial query-index write is rolled back and recorded once |
| `web/model/scope.py` | Rebuilt | Core | `d33c655e` | One immutable filter value composes folders, star floors, sets, and explicit selections; large id selections remain one bound JSON argument rather than growing SQL |
| `web/model/sets.py` | Rebuilt | Organise | `d33c655e` | Collections and keywords are named decision families with validated stable ids and atomic hash-membership writes; nonexistent computed-query storage was deleted rather than preserved |
| `web/work.py` | Rebuilt | Work and cache | `0639ab8d`, `bce1178f`, `155d450c` | Owed work and debt are queries over explicit kinds; one owned worker projects ready answers without a second scanner and releases its connection without global lifecycle state |
| `web/tiles.py` | Rebuilt | Browse, disk, and tiles | `0639ab8d` | Replaces V1 adoption, mutable configuration, prefetch, and pregeneration shims with one owned store, atomic no-overwrite publication, exact-row clearing, and a truthful two-parameter recipe |
| `web/library.py` | Rebuilt | Browse, disk, and tiles | `84d832f2` | One bounded query serves every grid scope and sort; counts, folders, facets, dates, decision reindex, and folder safety derive from core rows without a repository or source visibility rule |
| `web/render.py` | Rebuilt | Computation | `84d832f2`, `155d450c` | One decode, orientation, resize, and JPEG encode path serves tiles; header dimensions now honor display EXIF orientation as the rendered pixels do |
| `web/photo/kind.py` | Rebuilt | Computation | `84d832f2` | RAW decoding is selected by bytes rather than extension alone, while Develop capability and camera provenance remain separate explicit questions |
| `web/photo/__init__.py` | Rebuilt | Computation | `155d450c` | Defines the package boundary as pure format and EXIF reads; identity, location, scheduling, and persistence are explicitly excluded |
| `web/photo/exif.py` | Rebuilt | Computation | `155d450c` | Reads only camera, lens, and capture date from a bounded four-MiB TIFF/RAW header; a synthetic native TIFF proof protects byte order, IFD traversal, and offsets |
| `web/metadata.py` | Rebuilt | Computation | `155d450c` | Embedded browse facts are one content-keyed cache kind; projection honors owner date decisions, updates every identical row, repairs query columns at boot, and excludes path/sidecar inputs that would poison the key |
| `web/features/catalog/metadata.py` | Removed | Work and cache | this change | The pauseable scanner, private thread pool, retry ledger, orientation daemon, and mutable status are replaced by query-derived debt and the one owned V2 chore loop |
| `web/photo_metadata.py` | Removed | Computation | this change | The broad path-based extractor mixed embedded EXIF with filesystem facts, sidecars, global Pillow mutation, and duplicate dimensions; V2 metadata is one bounded content-keyed answer |
| `web/photo/identity.py` | Removed | Core | `b7665224` | Prefix hashing, async backfill state, V1 schema queries, and the HDD governor are replaced by full-byte `model.photos.content_hash` plus the query-driven V2 worker |
| `web/photo/location.py` | Removed | Core | `b7665224` | Drive-letter probing and mutable guessed mappings are replaced by explicit marker-backed drives, safe tails, and verified copy addresses |
| `web/photo/visibility.py` | Removed | Browse, disk, and tiles | `b7665224` | Source joins and `missing_at` filtering contradict the V2 invariant that unplugging a drive never removes a photo from the library; `library.IN_LIBRARY` is the sole rule |
| `web/test_photo_identity.py` | Removed | Core proof | `b7665224` | Exercised the deleted prefix-hash/V1 backfill rather than the complete-byte identity protected in `test_core.py` |
| `web/test_location.py` | Removed | Core proof | `b7665224` | Exercised the deleted drive-letter guesser; marker relocation and same-named-stranger refusals live in `test_core.py` |
| `web/test_visibility_rule_is_one_rule.py` | Removed | Core proof | `b7665224` | Asserted the rejected source/missing visibility rule; the V2 away-versus-lost and library visibility invariants live in `test_core.py` |
| `web/boot.py` | Rebuilt | Verification and fresh boot | `e331be8f`, `155d450c`, this change | One `Library` owns attach/refresh, browse, tile, metadata, debt, worker, and deterministic close; `OwnedLibrary` confines its SQLite connection to one executor thread, drains admitted work before close, and refuses work after close |
| `web/routes/__init__.py` | Rebuilt | Desktop UI transport | this change | Declares the sole V2 route-decorator boundary; it has no product logic or imports |
| `web/routes/library.py` | Rebuilt | Browse, disk, and tiles | `e697d24e`, this change | Typed V2 routes serve bounded photo pages, embedded details, and content-keyed JPEG tiles through the owned product; malformed scope or recipes are 400, unavailable photos are 404, and native app mounting remains before proof |
| `web/routes/system.py` | Rebuilt | Desktop UI transport | this change | Local health, route truth, and prepare-quit are three read-time answers; prepare-quit closes the owned product before acknowledging the desktop shell |
| `web/v2_app.py` | Rebuilt | Verification and fresh boot | this change | One lifespan constructs and starts the isolated V2 product, mounts only V2 routers, exposes no server documentation surface, and deterministically closes on shutdown |
| `web/test_core.py` | Rebuilt | Core proof | `284ba3f2`, `da1ccd7e`, `0639ab8d`, `bce1178f`, `7227545d`, `d33c655e`, `e331be8f`, `84d832f2`, `155d450c` | Proves the fresh `Library`, native EXIF, display orientation, metadata projection/repair, disposable-cache corruption recovery, decision override, projection rollback, pagination, decode limits, and persisted tiles; 80 tests and 4 subtests pass on Windows, but native desktop proof is still owed |
| `web/test_v2_routes.py` | Rebuilt | Desktop UI transport proof | `e697d24e`, this change | Refutes malformed-scope widening, untyped or oversized ids, index loss, cross-thread SQLite access, cancelled-close handle leaks, close races, fake transport data, invalid tile recipes, and missing-photo success; 7 tests and 6 subtests pass against a real fresh V2 catalog |
| `web/test_v2_app.py` | Rebuilt | Verification and fresh boot | this change | Proves the default V2 catalog/cache cannot collide with inherited state, the real route graph mounts, prepare-quit returns only after handle release, and post-close health is unavailable |

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
