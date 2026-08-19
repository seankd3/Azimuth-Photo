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
| Verification and fresh boot | Designing | One trustworthy cross-platform check; a fresh catalog opens the real desktop product | Source and 72.2 MiB frozen one-process launches now open a fresh `azimuth-v2.db` in the real native window, close with no surviving process, and immediately release the Windows catalog handle. Cross-platform and clean installed-build proof remain |
| Core model and schema | Designing | Photos, copies, drives, decisions, sets, scope, and cache are the whole durable model | Every core file has passed the first-principles pass and now runs behind the direct desktop boundary: the fresh five-table catalog owns photo/copy truth and append-only decisions, while sets and scopes add no tables or shadow state. The transitional Develop relation still keeps the area below Proven |
| Computation | Designing | Pure decode, metadata, colour, hashing, and rendering functions with no product state | File-kind detection, bounded RAW EXIF, embedded browse metadata, and the shared decode/resize/encode renderer have passed the V2 review; the broad V1 metadata extractor is deleted, while path-based date inference and Develop colour mathematics remain inherited |
| Work and cache | Designing | Owed work is a query; one bounded executor; one cache contract | Cache kinds are explicit immutable capabilities and one owned worker has no process-global lifecycle, queue, or registry; the separate metadata scanner is deleted, while other legacy executors remain reachable outside this slice |
| Browse, disk, and tiles | Designing | One merged library view, truthful disk state, responsive thumbnails | The owned V2 product boundary carries a natively chosen folder through attach, progressive sweep, bounded/composable browse, metadata details, close, and reopen; tiles are files the window reads (grid 1024 kept forever, loupe 4096 evictable), made by the worker looked-at-first from one decode per photograph using the camera's embedded preview. Proven on a fresh home: first tile 3.5 s after choosing a folder, every decodable visible tile by 28 s on one lane. With four lanes and a nudge on sweep and look, every decodable visible tile lands by 12 s on a loaded machine. The justified grid, the home-folder first run, and inherited repository deletion remain |
| Organise | Designing | Sets and grouping fall out of the core decisions model | Collections and keywords are validated decision families with composable scopes; opaque saved queries were removed from the core contract, while inherited collection, keyword, saved-view, and stacks surfaces still need replacement |
| Keep and safety | Designing | Trash, backup truth, recovery, and integrity checks use the same copy facts | Pick, Reject, Restore and Undo are one reversible identity decision (`cull.py`), and Trash is the same decision seen from the other side. Empty Trash requires an unchanged typed count, every drive attached, canonical-address discovery beyond copy hints, full-byte proof, record-copy-last deletion, and resumable partial outcomes. The old 2,103-line Trash implementation is deleted; recovery, integrity, and duplicate surfaces remain inherited |
| Import and synchronize | Legacy | One complete intake/synchronize conversation with recoverable file operations | Current staging, registries, journals, routes, and synchronization are inherited |
| Develop | Legacy | Preserve proven pixel mathematics; rebuild the surrounding workflow on V2 | Large V1 workflow remains; embeddings do not block this rewrite |
| Rank, search, and AI | Legacy | Decisions drive rank; enrichment improves results but never gates the product | Parked until the core browse/storage path is excellent |
| Desktop UI | Designing | One fast native-feeling surface with one state and interaction vocabulary | Whole-library virtualization is proven at 47,000 photos. The native Trash lens then proved live counts, selection, Restore, exact Undo, layered Escape, and the disabled typed-count deletion gate against a disposable real-photo catalog; no destructive UI action was automated. Pick/Reject/Clear now run from the keyboard and contextbar in the native window, patch every row of an identity, and undo exactly; the grid follows the worker by itself (cull verbs appear 9 s after attaching 47 photographs with no reload and the selection intact); first-screen tile latency after a sweep (one RAW tile per second on demand) and physical deletion of the remaining inherited browser shell remain |
| Desktop packaging | Designing | A dependable install launches the local product and owns its lifecycle | The 72.2 MiB frozen Windows app is now one process with one direct bridge and one inlined document; the HTTP server, loopback port, Rust/Tauri parent, child engine, and platform icon sets are physically gone. A clean-machine installer, signing, and cross-platform proof remain |
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
| `web/model/schema.sql` | Rebuilt | Core | `284ba3f2`, `da1ccd7e`, `8c3f24a1` | Owns all five core tables and decision provenance without requiring V1 schema first; transitional `vc_of` keeps it below Proven until Develop is rebuilt |
| `web/model/drives.py` | Rebuilt | Core | `284ba3f2` | Explicit attach is the only discovery path; reads no longer probe drive letters or disconnected mappings |
| `web/model/decisions.py` | Rebuilt | Core | `284ba3f2`, `155d450c`, `8c3f24a1` | Append-only decisions record provenance, apply one authority rule, preserve provenance when carried, expose safe partial amendment, and now name capture-date corrections explicitly |
| `web/model/photos.py` | Rebuilt | Core | `b12f4ff8`, `ca40451c`, `96e985d9`, `7227545d` | Owns admission, full-byte identity, truthful open/state, collision-proof put/move, and version grouping; a verified import now records its photo and copy in the same transaction |
| `web/model/copies.py` | Rebuilt | Core | `96e985d9`, `7227545d`, `e3335a42` | A complete marker-stable sweep admits observed photos in bounded committed batches so the grid can fill, reports changed bytes without replacing identity, and retires stale hints only after the complete marked drive stays present |
| `web/model/backup.py` | Rebuilt | Keep and safety | `b12f4ff8`, `7227545d` | Backup only adds byte-proven regular files; reclaim ignores symlinks, honors alternate tails, repeats byte proof at the destructive gate, and removes at most one expendable copy per call |
| `web/model/trash.py` | Rebuilt | Keep and safety | `d0dbf737`, `c78d0c32`, `8c3f24a1`, `220ad680` | Count and Empty only: browsing Trash is `library.trash`. Empty preflights all registered drives, hinted and canonical copies, regular-file identity and full bytes; record copies leave last and interrupted deletion resumes from committed copy truth |
| `web/model/cull.py` | Rebuilt | Keep and safety | `8c3f24a1`, `d2a8357e` | Pick, Clear, Reject, Restore and exact Undo are one append-only status decision per content identity with the column as its browse index; a change reports how many rows it moved, unidentified photographs are refused rather than keyed by row, and the stale-Undo and duplicate-identity refuters live in `test_core.py` |
| `web/model/cache.py` | Rebuilt | Core | `0639ab8d`, `155d450c` | Cache kinds are explicit immutable capabilities; recipes refuse undeclared inputs, failures are facts, and an optional projection applies under a savepoint so a partial query-index write is rolled back and recorded once |
| `web/model/scope.py` | Rebuilt | Core | `d33c655e` | One immutable filter value composes folders, star floors, sets, and explicit selections; large id selections remain one bound JSON argument rather than growing SQL |
| `web/model/sets.py` | Rebuilt | Organise | `d33c655e` | Collections and keywords are named decision families with validated stable ids and atomic hash-membership writes; nonexistent computed-query storage was deleted rather than preserved |
| `web/work.py` | Rebuilt | Work and cache | `0639ab8d`, `bce1178f`, `155d450c`, `39bc64c2`, `220ad680`, `591b4138` | Owed work and debt are queries over explicit kinds; the owner's worker runs in lanes that each step their own share of the library (id modulo lanes, as a scope) on their own connection, doing the most-owed thing for the newest photograph -- on-screen first, then identity, metadata, grid, loupe per photograph -- nudged awake by sweeps and looks, counting what finished for the window's pulse, with no global lifecycle, lock, or claim |
| `web/tiles.py` | Rebuilt | Browse, disk, and tiles | `0639ab8d`, `e757a6fe`, `220ad680` | Two kinds over one store: `grid` (1024, never evicted) and `loupe` (4096, evictable under a ceiling defaulting to half the cache disk's free space). Whichever is asked first decodes the original once at loupe size and publishes both files; the other records its file; a grid tile is cut from an existing loupe without opening the original. Atomic no-overwrite publication, exact-row clearing and one path-presence answer remain; no adoption path, scheduler or mutable directory |
| `web/library.py` | Rebuilt | Browse, disk, and tiles | `84d832f2`, `220ad680` | One bounded page query serves the grid and Trash (the library's complement, ordered from the decision log) and carries cached answers -- tile, loupe -- as columns from one LEFT JOIN each; counts, folders, facets, dates, decision reindex and folder safety derive from core rows without a repository or source visibility rule |
| `web/render.py` | Rebuilt | Computation | `84d832f2`, `155d450c`, `220ad680` | One decode, orientation, resize and JPEG encode path serves tiles and exposes `pixels()` so a store can cut two sizes from one decode; RAWs read the camera's embedded JPEG when it is large enough (oriented by LibRaw's flip, verified on real portrait CR3s) and demosaic otherwise; JPEG drafts are aspect-correct; encoding is baseline (measured 5x cheaper for 5% size) |
| `web/photo/kind.py` | Rebuilt | Computation | `84d832f2` | RAW decoding is selected by bytes rather than extension alone, while Develop capability and camera provenance remain separate explicit questions |
| `web/photo/__init__.py` | Rebuilt | Computation | `155d450c` | Defines the package boundary as pure format and EXIF reads; identity, location, scheduling, and persistence are explicitly excluded |
| `web/photo/exif.py` | Rebuilt | Computation | `155d450c` | Reads only camera, lens, and capture date from a bounded four-MiB TIFF/RAW header; a synthetic native TIFF proof protects byte order, IFD traversal, and offsets |
| `web/metadata.py` | Rebuilt | Computation | `155d450c` | Embedded browse facts are one content-keyed cache kind; projection honors owner date decisions, updates every identical row, repairs query columns at boot, and excludes path/sidecar inputs that would poison the key |
| `web/features/catalog/metadata.py` | Removed | Work and cache | `6ab9c93e` | The pauseable scanner, private thread pool, retry ledger, orientation daemon, and mutable status are replaced by query-derived debt and the one owned V2 chore loop |
| `web/photo_metadata.py` | Removed | Computation | `6ab9c93e` | The broad path-based extractor mixed embedded EXIF with filesystem facts, sidecars, global Pillow mutation, and duplicate dimensions; V2 metadata is one bounded content-keyed answer |
| `web/photo/identity.py` | Removed | Core | `b7665224` | Prefix hashing, async backfill state, V1 schema queries, and the HDD governor are replaced by full-byte `model.photos.content_hash` plus the query-driven V2 worker |
| `web/photo/location.py` | Removed | Core | `b7665224` | Drive-letter probing and mutable guessed mappings are replaced by explicit marker-backed drives, safe tails, and verified copy addresses |
| `web/photo/visibility.py` | Removed | Browse, disk, and tiles | `b7665224` | Source joins and `missing_at` filtering contradict the V2 invariant that unplugging a drive never removes a photo from the library; `library.IN_LIBRARY` is the sole rule |
| `web/test_photo_identity.py` | Removed | Core proof | `b7665224` | Exercised the deleted prefix-hash/V1 backfill rather than the complete-byte identity protected in `test_core.py` |
| `web/test_location.py` | Removed | Core proof | `b7665224` | Exercised the deleted drive-letter guesser; marker relocation and same-named-stranger refusals live in `test_core.py` |
| `web/test_visibility_rule_is_one_rule.py` | Removed | Core proof | `b7665224` | Asserted the rejected source/missing visibility rule; the V2 away-versus-lost and library visibility invariants live in `test_core.py` |
| `web/boot.py` | Rebuilt | Verification and fresh boot | `e331be8f`, `155d450c`, `e697d24e`, `e3335a42`, `b153c471`, `e757a6fe`, `d0dbf737`, `c78d0c32`, `8c3f24a1`, `39bc64c2`, `220ad680`, `591b4138` | One `Library` owns browse, cull, Trash, restore, Undo, empty, look and pulse verbs; rows carry renditions as file URLs so nothing decodes on the interactive lane; the worker reads what the window looks at; `OwnedLibrary` confines interactive SQLite access to one thread, sweeps on one separate bounded connection, drains both lanes before close, and refuses work after close |
| `web/routes/__init__.py` | Removed | Desktop UI transport | `e697d24e`, `e757a6fe` | The route boundary was a migration seam; the desktop now calls the product directly in the same process |
| `web/routes/library.py` | Removed | Browse, disk, and tiles | `e697d24e`, `a3bbd3cd`, `e3335a42`, `b153c471`, `e757a6fe` | HTTP parsing and response translation are unnecessary in the one-person desktop product; direct bridge verbs preserve the product behavior without transport |
| `web/routes/system.py` | Removed | Desktop UI transport | `5c3d24eb`, `b153c471`, `d5460e43`, `e757a6fe` | Health, route discovery, document serving, and prepare-quit existed only to coordinate a loopback child and are gone with it |
| `web/v2_app.py` | Removed | Verification and fresh boot | `5c3d24eb`, `b153c471`, `e757a6fe` | The FastAPI lifespan and mounted static server are replaced by one native process owning the product and inlined document |
| `web/desktop.py` | Rebuilt | Desktop packaging | `e757a6fe`, `d0dbf737`, `c78d0c32`, `8c3f24a1`, `39bc64c2`, `220ad680` | Owns platform paths, one V2 product, one native WebView2 window opened from the document's file URL (so tiles are read straight from the store), direct browse/cull/Trash/look/pulse verbs, the native folder chooser, and idempotent shutdown; real-window and catalog-release proof passed |
| `web/templates/v2.html` | Rebuilt | Desktop UI | `b153c471`, `e757a6fe`, `9ddb91a4`, `a33df983`, `d2a8357e` | The semantic desktop document names library, Trash, native folder setup, browse controls, inspector, loupe, toast, and typed-count deletion without inherited templates or inline behavior |
| `web/static/v2/index.css` | Rebuilt | Desktop UI | `b153c471`, `e757a6fe`, `9ddb91a4`, `a33df983`, `d2a8357e` | One neutral visual vocabulary keeps chrome subordinate to photographs, positions bounded virtual cells, makes destructive intent distinct, preserves full loupe images, and prevents hidden controls leaking through layout rules |
| `web/static/v2/index.js` | Rebuilt | Desktop UI | `b153c471` | The composition root loads registered lenses before the shell without making the shell import a product surface |
| `web/static/v2/net/index.js` | Rebuilt | Desktop UI | `b153c471`, `e757a6fe`, `a33df983`, `8c3f24a1`, `39bc64c2`, `220ad680` | Waits for the native bridge and exposes the sole browse and Trash vocabulary; it contains no fetch, URL, route, or HTTP concept |
| `web/static/v2/store/index.js` | Rebuilt | Desktop UI | `b153c471`, `9ddb91a4`, `a33df983`, `39bc64c2` | Owns the sole mutable UI state, current lens, sparse indexed photo map, selection position, library/Trash counts, the transient notice, and the registry |
| `web/static/v2/kit/virtual-grid.js` | Rebuilt | Desktop UI | `9ddb91a4` | Pure geometry derives columns, total scroll height, bounded visible range, and exact cell positions from width, density, count, and viewport without product state or DOM knowledge |
| `web/static/v2/kit/page-cache.js` | Rebuilt | Desktop UI | `9ddb91a4`, `d2a8357e`, `39bc64c2` | One sparse page owner deduplicates in-flight reads, rejects stale sort generations, jumps to arbitrary offsets, turns one failed page into one visible error rather than a retry storm, patches or removes loaded rows in place by predicate, and re-reads every loaded page under the same generation so the window follows the library without a reset |
| `web/static/v2/lens/library.js` | Rebuilt | Desktop UI | `b153c471`, `e757a6fe`, `9ddb91a4`, `a33df983`, `d2a8357e`, `220ad680` | One lens reconciles only the visible overscan neighborhood for Library or Trash; a cell's picture is the file URL its row carries (or a placeholder until the row changes), the lens tells the shell what is on screen, and it renders the pick flag, accessible selection and an inspector that says *Reading…* until identity has arrived -- no fetch, observer, or retry |
| `web/static/v2/shell/app.js` | Rebuilt | Desktop UI | `b153c471`, `e757a6fe`, `9ddb91a4`, `a33df983`, `d2a8357e`, `39bc64c2`, `220ad680` | The coordinator owns view switching, sparse pages, direct product verbs, native folder choice, sorting, density, race-safe details, layered Escape, grid keys, the P/U/X cull keys and a loupe that shows the best picture its row has (the loupe file, else the grid tile) and swaps as the row changes; it follows the library through one in-place refresh shared by the sweep loop and the worker's pulse, tells the library what is looked at, and renders the status line from state as its one writer |
| `web/static/v2/shell/trash.js` | Rebuilt | Desktop UI | `a33df983`, `d2a8357e`, `39bc64c2` | One focused workflow owns Restore, preflight, the typed-count dialog and permanent outcome copy, handing Undo to the shared toast; its native proof changed 3→2→3 without moving bytes and left the destructive submit disabled |
| `web/static/v2/shell/cull.js` | Rebuilt | Desktop UI | `d2a8357e`, `39bc64c2` | One small workflow turns P/U/X and the contextbar verbs into one product call, edits the loaded window in place by identity, removes a rejected cell and selects what slides into its place, and hands each change to the shared Undo toast; proven in the native window with both duplicate cells flagging, clearing, leaving (45 of 45, Trash 2) and returning on Undo |
| `web/static/v2/shell/undo.js` | Rebuilt | Desktop UI | `d2a8357e`, `39bc64c2` | The one Undo toast: shows a message with the exact changes it can reverse, runs `undoCull` once and reloads; shared by Trash restore and culling instead of living inside the Trash dialog |
| `web/test_v2_virtual_grid.mjs` | Rebuilt | Desktop UI proof | `9ddb91a4` | Refutes DOM growth at 47,000 photos, proves the scroll window follows an arbitrary middle position, and protects last-cell bounds and density changes |
| `web/test_v2_page_cache.mjs` | Rebuilt | Desktop UI proof | `9ddb91a4`, `d2a8357e` | Refutes duplicate page reads, stale-generation repaint, permanent visible-page retry loops at arbitrary large offsets, and a status patch that misses a second row holding the same identity |
| `web/test_core.py` | Rebuilt | Core proof | `284ba3f2`, `da1ccd7e`, `0639ab8d`, `bce1178f`, `7227545d`, `e331be8f`, `84d832f2`, `155d450c`, `e3335a42`, `e757a6fe`, `d0dbf737`, `c78d0c32`, `8c3f24a1`, `d2a8357e`, `220ad680` | Adds Trash refuters for prior-state restore, stale Undo, invalid-member atomicity, 5,000-photo batches, duplicate identities, away drives, stale copy hints, changed bytes, exact counts, record-copy-last interruption and resume; the focused V2 suite passes 99 tests on Windows |
| `web/test_owned_library.py` | Rebuilt | Verification and fresh boot | `e757a6fe` | Preserves only the expensive execution-lane refuters: scans cannot block browsing, cancelled close still releases SQLite, admitted work drains, and the live connection never changes threads |
| `web/test_desktop.py` | Rebuilt | Desktop packaging | `e757a6fe`, `d0dbf737`, `c78d0c32`, `8c3f24a1`, `220ad680` | The real direct bridge crosses attach, refresh, browse, tile, Trash page/count, Undo and destructive dry-run, then closes idempotently and releases the catalog |
| `web/features/trash/__init__.py` | Removed | Keep and safety | `c6b48296` | The legacy feature boundary left with its routes and file-moving service |
| `web/features/trash/routes.py` | Removed | Keep and safety | `c6b48296` | HTTP Trash/restore/empty routes and cache invalidation are replaced by the direct one-process product vocabulary |
| `web/features/trash/service.py` | Removed | Keep and safety | `c6b48296` | The 820-line V1 source-local move engine, retry sleeps, V1 schema joins, virtual-copy families and cross-device sync behavior are replaced by decisions plus one full-copy destructive gate |
| `web/static/js/desktop/trash.js` | Removed | Desktop UI | `c6b48296` | The 473-line inherited modal, fetch, polling, selection and outcome surface is replaced by the 97-line V2 workflow |
| `web/test_trash.py` | Removed | Keep and safety proof | `c6b48296` | The 701-line V1 schema/service suite left with its implementation; expensive safety failures are refuted through the five-table core and direct desktop boundary |
| `web/test_v2_routes.py` | Removed | Desktop UI transport proof | `e697d24e`, `a3bbd3cd`, `e3335a42`, `b153c471`, `e757a6fe` | Route-only assertions left with HTTP; indexed scope moved to core proof and execution ownership moved to `test_owned_library.py` |
| `web/test_v2_app.py` | Removed | Verification and fresh boot | `5c3d24eb`, `b153c471`, `d5460e43`, `e757a6fe` | Server graph and prepare-quit proof are obsolete; `test_desktop.py` exercises the actual one-process boundary and catalog release |
| `web/test_fresh_boot.py` | Removed | Verification and fresh boot | `e757a6fe` | The hub/satellite subprocess server smoke asserted the rejected network product; fresh-home V2 boot moved to `test_desktop.py` and frozen native proof |
| `web/test_windows_desktop_install.py` | Removed | Desktop packaging | `e757a6fe` | Tauri versions, sidecar resources, browser launch, ports, and splash contracts are obsolete; actual one-process build and native proof replace the static assertions |
| `scripts/server_entry.py` | Removed | Desktop packaging | `d5460e43`, `e757a6fe` | The port-bound frozen entry existed only for the deleted child server |
| `scripts/build_server.py` | Removed | Desktop packaging | `d5460e43`, `e757a6fe` | The private server freezer is replaced by the one-process desktop freezer |
| `scripts/build_desktop_ui.py` | Rebuilt | Desktop packaging | `e757a6fe` | Bundles the reviewed modular UI and inlines its CSS and JavaScript into one serverless document, refusing any remaining served-asset reference |
| `scripts/build_desktop.py` | Rebuilt | Desktop packaging | `e757a6fe` | One deterministic PyInstaller recipe includes the native window, V2 product, schema, serverless document, RAW decoder, WebView2 backend, and canonical icon; the 72.2 MiB result passed live proof |
| `scripts/build_windows_desktop.ps1` | Rebuilt | Desktop packaging | `e757a6fe` | One Windows command installs the pinned dependencies, builds the one-process app, and verifies the expected executable without Rust, Tauri, or a server build |
| `scripts/start_azimuth_windows.ps1` | Rebuilt | Desktop packaging | `e757a6fe` | The source launcher now builds the current serverless document and opens `web/desktop.py`; modes, ports, browser discovery, process probing, server reuse, and catalog compatibility switches are deleted |
| `scripts/gates/invokes.py` | Rebuilt | Verification | `e757a6fe` | One small tracked-path gate checks every repository-relative workflow reference; the V2 release is clean while three inherited CI benchmark references remain explicit debt |
| `package.json` | Rebuilt | Desktop packaging | `e757a6fe` | The desktop UI build adds one exact esbuild version to the inherited lint-only development set; the lock resolves a zero-vulnerability dependency graph and no JavaScript package ships in the frozen app |
| `.github/workflows/release.yml` | Rebuilt | Desktop packaging | `e757a6fe` | Tagged releases build and zip only the one-process Windows artifact; inherited Docker publishing, Linux server packaging, and the Tauri signing stub were deleted |
| `desktop/src-tauri/build.rs` | Removed | Desktop packaging | `d5460e43`, `e757a6fe` | Tauri's generated build boundary is unnecessary in the one-process app |
| `desktop/src-tauri/Cargo.toml` | Removed | Desktop packaging | `d5460e43`, `e757a6fe` | The entire Rust dependency graph left with the parent process |
| `desktop/src-tauri/src/engine.rs` | Removed | Desktop packaging | `d5460e43`, `e757a6fe` | There is no child engine, executable resolution, port argument, or developer override |
| `desktop/src-tauri/src/server.rs` | Removed | Desktop packaging | `d5460e43`, `e757a6fe` | Loopback probing, navigation, prepare-quit, and child reaping are deleted rather than hidden behind the new bridge |
| `desktop/src-tauri/src/main.rs` | Removed | Desktop packaging | `d5460e43`, `e757a6fe` | pywebview now owns the sole native window in the same Python process as the product |
| `desktop/src-tauri/src/tray.rs` | Removed | Desktop packaging | `d5460e43`, `e757a6fe` | The transitional tray did not justify retaining a second language and process boundary |
| `desktop/src-tauri/tauri.conf.json` | Removed | Desktop packaging | `d5460e43`, `e757a6fe` | Tauri bundle and window configuration left with Tauri |
| `desktop/src-tauri/tauri.windows.conf.json` | Removed | Desktop packaging | `d5460e43`, `e757a6fe` | There is no frozen server directory to embed as a child resource |
| `desktop/ui/index.html` | Removed | Desktop packaging | `d5460e43`, `e757a6fe` | A waiting splash is unnecessary because the native window owns the inlined V2 document directly |
| `desktop/make_icon.py` | Removed | Desktop packaging | `d5460e43` | An unreferenced import-time icon generator and its stale orange palette were deleted; the bundle retains one canonical desktop icon |

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
