# Azimuth Photo Master Plan

Canonical product and engineering backlog · last evidence refresh: 2026-07-25

This is the coordination source of truth for the gap between Azimuth Photo today
and a polished, local-first, Windows-first professional photo client. It replaces
the older plans as the ordering authority, while preserving their useful detail:

- [Archived product inventory](docs/MASTER_PLAN.md)
- [Azimuth 1.0 release plan](docs/RELEASE_PLAN.md)
- [Quality bar](docs/QUALITY_BAR.md)
- [Release-candidate checklist](docs/RC_CHECKLIST.md)
- [Azimuth migration plan](docs/RENAME_PLAN.md)
- [Live lane ledger](LANES.md)

This file is a backlog, not a claim ledger or lane diary. `LANES.md` remains the
place to reserve files before work starts. When a statement has not been
reverified against the current integration head, it is labeled `unverified`.
Its canonical location is the repository root as `MASTER_PLAN.md`, not under
`docs/`.

## Contents

- [1. Product north star and non-negotiables](#1-product-north-star-and-non-negotiables)
- [2. Current truth snapshot](#2-current-truth-snapshot)
- [3. Product areas and accountable owners](#3-product-areas-and-accountable-owners)
- [4. Master queue](#4-master-queue)
  - [Core Library and Safety](#core-library-and-safety)
  - [Desktop Workflow](#desktop-workflow)
  - [Mobile and Companion](#mobile-and-companion)
  - [Ranking and Local Intelligence](#ranking-and-local-intelligence)
  - [Windows Distribution and Identity](#windows-distribution-and-identity)
  - [Release and Quality Infrastructure](#release-and-quality-infrastructure)
- [5. Benchmarks and quality baseline](#5-benchmarks-and-quality-baseline)
- [6. Integration sequence and yield rules](#6-integration-sequence-and-yield-rules)
- [7. Bug and known-defect register](#7-bug-and-known-defect-register)
- [8. Decisions and assumptions](#8-decisions-and-assumptions)
- [9. Existing lane-output map](#9-existing-lane-output-map)

## Status and priority language

Statuses are intentionally limited:

- `active` — one accountable lane is currently executing.
- `ready after integration` — specified work can start only after the integration
  dependency named by the entry is accepted.
- `parked` — intentionally not executing; starting it would create overlap or
  premature architecture.
- `blocked` — cannot finish without external evidence, authority, or state.
- `done awaiting integration` — committed lane output exists but is not in the
  accepted integration head.
- `unverified` — reported or historically documented, but not re-proven against
  the current integration head.

Priorities are `P0` release/data-safety blocker, `P1` required for the product
promise, `P2` important refinement, and `P3` later leverage.

## 1. Product north star and non-negotiables

Azimuth Photo should feel like the inevitable professional photo client: the
library and editing depth of Lightroom Classic, the calm effortlessness people
expect from modern photo apps, and none of the cloud lock-in or developer
plumbing.

The following are product covenants, not optional backlog ideas:

1. **Client first.** The customer sees a coherent Azimuth Photo application, not
   Python, a server URL, a database, Tailscale, ports, or deployment concepts.
2. **Windows first.** A normal Windows user can download, install, choose local,
   external, mapped-drive, or UNC/NAS folders, and reach their library without a
   terminal.
3. **Local first and offline capable.** Once a device has its catalog, required
   thumbnails, and embeddings, normal browse, search, cull, Refine, ranking,
   organization, and learning continue with no server or network.
4. **Optional server.** Another machine may add protected storage, backup,
   artifact exchange, bulk compute, sync, sharing, or publishing. It never
   becomes a prerequisite for day-to-day local work.
5. **Privacy by default.** Originals, previews, embeddings, faces, captions,
   ranking actions, and catalog facts stay local unless the user enables a
   specific capability with a specific destination.
6. **Personal taste is durable user data.** Direct choices, undo/retractions,
   curation, and edits are preserved locally and portably. Elo, models, indexes,
   and caches are rebuildable projections.
7. **Interactions feel instant.** No click spinner in culling or ranking. A warm
   prepared device should advance immediately while expensive learning happens
   quietly in the background.
8. **Originals and catalog are sacred.** No cleanup, migration, free-space
   operation, scan, restore, or sync path may risk the only verified copy of an
   original or the only durable record of the user's work.
9. **Elegant ownership.** Product behavior lives in focused owner modules.
   Compatibility facades remain thin; large facades are strangled in bounded
   phases, never rewritten wholesale.
10. **Azimuth Photo is the customer identity; internal migration is deliberate.**
    Customer-visible product language, artwork, packages, documentation, and
    normal workflows use **Azimuth Photo** with no prior branding. New identifiers
    follow `AzimuthPhoto` where a platform requires one token, `azimuth-photo`
    for lowercase slugs/executables, and `azimuth_photo` for underscore-style
    code. The approved internal destination is Azimuth naming too. Persisted and
    operational identifiers—including database discovery, stored keys,
    environment variables, services, and installed paths—change only through the
    explicit WIN-06 cutover gate. Permanent compatibility is not the destination,
    but neither is an unproven destructive rename.

Related product doctrine:
[product vision](docs/product-vision.md),
[UI architecture](docs/ui-architecture.md), and
[data and privacy](docs/data-and-privacy.md).

## 2. Current truth snapshot

This snapshot distinguishes locally reverified facts from thread/audit evidence.

### Integration and production

- **Verified 2026-07-25:** production checkout `main` is
  `458675e7797261c6ca658a9d2c16d21dad6206d9`; `develop` is
  `52742f94181d792c869f021ff4374b910fa219ab`.
- **Verified 2026-07-25:** `develop...main` is 5/55 unique commits. Neither side
  can be treated as a fast-forward or discarded history.
- **Verified 2026-07-25:** `pa-sprint-integration / sprint-integration` started
  from the exact `develop` SHA above. The merge of `main` is paused and
  uncommitted after resolving only three audited textual conflicts:
  the pre-migration check-command shim, `web/core/query_constraints.py`, and
  `web/test_desktop_correctness.py`.
- The intended resolutions preserve the Azimuth check shim, `main`'s
  nonblocking committed-search fallback, and both sets of desktop correctness
  tests. The merge must not be committed without coordinator review.
- Production is not a clean source tree. The latest check found 38 status
  entries, including runtime/generated state and an active intelligence task's
  edits. No integration or cleanup task may normalize that checkout.

### Runtime state inside the checkout

- **Verified 2026-07-25:** the active `web/azimuth.db` is about 3.10 GiB, with
  WAL/SHM beside it; a zero-byte pre-migration compatibility database remains.
- **Verified 2026-07-25:** `web/.models` is about 43 GiB and `web/.venv` about
  12 GiB.
- Preview size is moving live state: an earlier audit measured about 171 GiB in
  the source checkout; a later `du` measured about 59 GiB while files were being
  created/removed. Treat the exact size as uncertain until measured during a
  quiet window. The architectural defect is certain: runtime state is mixed
  with source and worktree coordination.
- Multiple multi-gigabyte pre-migration catalog backups also sit under `web/`.
  They must be classified and protected, not deleted by housekeeping.
- Runtime relocation is forbidden until a scratch backup/restore drill proves
  the current catalog and all chosen roots can move and roll back safely.

### Lanes, branches, and worktrees

- **Verified 2026-07-25:** 181 local branches, 63 attached worktrees, and 19
  dirty worktrees exist. The shared lane wrapper reports no Omarchy tmux lane
  and no lane process, but that does not make attached or dirty output stale.
- Completed branch output exists for safety, performance, desktop trust, mobile
  resilience, and Windows onboarding. See the
  [lane-output map](#9-existing-lane-output-map).
- `origin/perf-people-map` now has receipt-only commit `d1f7e4cd0`; its local
  worktree branch remains at `develop`, and the remote is 679/3 divergent from
  current `develop`. Preserve the receipt but never merge the branch wholesale.
  `client-server-hardening` contains older mixed history and requires provenance
  review before any reuse.
- No branch, worktree, bundle, benchmark, or dirty output may be removed until
  an inventory maps it to an accepted commit, archive, or explicit discard
  decision.

### Release and external proof

- `ci.yml` and `release.yml` exist, but no local tags are present and this task
  found no evidence of a successful current public release or current green
  GitHub Actions run. This is an evidence gap, not proof that remote CI is
  absent.
- **Verified 2026-07-25:** `origin/windows-install` is proof-complete at
  `f8da9949e`. Unsigned current-user installer
  `Azimuth Photo_1.0.0-rc.1_x64-setup.exe` is 107,918,017 bytes with SHA-256
  `ABEBF263A502A5A1AC15B1015ED19A67E401E66F44F3A8BEBE83EADA8A500299`.
  Silent install exited 0; a fresh standalone `AZIMUTH_HOME` reported release
  `1.0.0-rc.1`, schema 31, native picker availability, and no hub. The actual
  welcome UI, local-folder browse, mapped `Z:` to UNC/NAS browse in both UI and
  API, Alt+F4 close, and relaunch against the same home all passed.
- This proves install-to-library usability, not release readiness. The artifact
  is unsigned; signing, updater, public publishing, clean-machine identity
  cleanup, codec coverage, upgrade, and rollback remain separate gates.
- No public release is allowed before privacy/history scrub, complete test
  discovery, clean-clone builds, install/upgrade/restore matrices, version
  alignment, signing/update decisions, and real artifact smoke.

### Catalog and intelligence coverage

- **Verified by the 2026-07-25 read-only catalog audit:** about 146,535 active
  photos; only about 44,715 have the current visual embedding.
- About 100,469 active photos had neither an embedding nor a direct/propagated
  ranking signal. Propagation is nominally catalog-wide but cannot reach assets
  without usable evidence or embeddings.
- 75,539 generated comparison-pair rows came from only 7,587 recorded human
  actions in that audit. A mosaic is correlated one-action evidence; treating
  each loser row as an independent action inflates confidence.

## 3. Product areas and accountable owners

Every queue entry has exactly one owning product area. Dependencies may cross
areas, but responsibility does not.

| Product area | Accountable owner | Current authority |
|---|---|---|
| Core Library & Safety | Core Library & Safety owner; integration performed by `pa-sprint-integration` until accepted | Catalog/source truth, scans, previews, recovery, runtime roots, durable identity, originals/catalog protection |
| Desktop Workflow | Desktop Workflow owner; `desktop-journey-proof` is the current proof seam | Desktop library, Loupe, Cull, Deliver, Health, accessibility, keyboard, Develop-shell and omnibox behavior |
| Mobile & Companion | Mobile & Companion owner; `mobile-field-resilience` is the current accepted output | PWA/native companion, offline queue, reconnect, mobile local repository and device workflows |
| Ranking & Local Intelligence | Ranking & Local Intelligence owner; implementation is currently parked | Ranking actions, candidate contract, direct/inferred projections, taste/uncertainty, prepared local intelligence |
| Windows Distribution & Identity | `windows-install` / Windows foundation owner | Bundled engine, installer, first run, folder/NAS onboarding, versions, updater/signing, visible identity |
| Release & Quality Infrastructure | `pa-sprint-integration` until integration handoff | Branch reconciliation, test truth, CI/releases, artifact containment, privacy scrub, docs truth, organization sequence |

## 4. Master queue

### Core Library and Safety

#### CORE-01 — Integrate the verified safety and first-response cohort

- **Status / priority / owner:** `active` · `P0` · Core
  Library & Safety.
- **Desired outcome:** scans never erase catalog truth on interruption; recovery
  reports current snapshots honestly; preview work reports truthful progress;
  filter facets avoid self-contention; the library serves before updater-bundle
  housekeeping.
- **Current evidence:** scan safety, recovery truth, and preview honesty are
  integrated as `b7405c86d`, `119337ad1`, and `22f841466`; filter options is
  integrated as `91b1d66bc`, and first-use boot as `84650c100`. Preview
  integration passed 120 focused tests plus quick. Filter integration passed 16
  focused tests plus quick and moved the controlled 2,000-image uncached median
  from 61.9 ms to 26.9 ms. First-use integration passed 72 focused tests plus
  quick; branch-local QA-5000 evidence moved first HTTP from 3759 ms to
  1415.29 ms, but no production-runtime benchmark was run during integration.
- **Prerequisite:** coordinator-approved `main` reconciliation; manual review of
  `backups.py`, thumbnail paths, and `app.py` overlaps; separate cohort gates.
- **Boundary:** integrate only named commits; no scanner/cache/runtime redesign.
- **Acceptance evidence:** targeted scan/recovery/preview/library/client-bundle
  tests, quick gate after each merge, full non-Playwright pytest after cohort,
  exact benchmark rerun with no correctness or latency regression.

#### CORE-02 — Move runtime state out of the source checkout

- **Status / priority / owner:** `parked` · `P0` · Core Library & Safety.
- **Desired outcome:** catalog, WAL, previews, models, backups, and other runtime
  state live in native Azimuth data/cache roots; source checkouts remain code.
- **Current evidence:** active 3.10 GiB catalog, tens of GiB of models/previews,
  12 GiB venv, and large backup files currently coexist with source.
- **Prerequisite:** verified scratch backup and restore, chosen target roots,
  free-space check, service-window approval, and rollback plan.
- **Boundary:** move/verify runtime state only; do not rename repo, service,
  protocol, or original-photo roots in the same change.
- **Acceptance evidence:** byte/count/catalog-integrity comparison, clean boot
  from new roots, full restore drill, old-path fallback, rollback rehearsal, and
  zero source-checkout runtime writes after cutover.

#### CORE-03 — Preserve before housekeeping

- **Status / priority / owner:** `ready after integration` · `P0` · Core Library
  & Safety.
- **Desired outcome:** every branch, worktree, bundle, backup, and benchmark has
  an explicit disposition before cleanup.
- **Current evidence:** 181 branches, 63 worktrees, 19 dirty worktrees; known
  untracked lane benchmarks and production runtime artifacts.
- **Prerequisite:** accepted integration SHA and owner review of dirty trees.
- **Boundary:** inventory, hash, archive, or mark only; no deletion or worktree
  removal in the classification pass.
- **Acceptance evidence:** machine-readable inventory plus human disposition;
  all kept output reachable by named commit or checksummed archive; no dirty
  tree removed.

#### CORE-04 — Add portable library and asset identity

- **Status / priority / owner:** `parked` · `P1` · Core Library & Safety.
- **Desired outcome:** stable `library_id` and logical asset/rendition identity
  survive copies, restores, and peers while local integer IDs remain fast.
- **Current evidence:** thumbnails, embeddings, mirrors, and many routes still
  depend on device-local image IDs; partial hashes are not sufficient authority
  for destructive or backup decisions.
- **Prerequisite:** integration quiet; schema and migration review; user
  approval of identity semantics.
- **Boundary:** additive identity/source aliases only; no browse/ranking behavior
  change, no mass hashing, no duplicate collapse.
- **Acceptance evidence:** duplicates remain distinct assets, virtual copies
  preserve lineage, clean export/import preserves UUIDs, old peers ignore new
  fields safely, and query results remain identical.

#### CORE-05 — Document the real data-protection model

- **Status / priority / owner:** `ready after integration` · `P1` · Core Library
  & Safety.
- **Desired outcome:** one clear explanation distinguishes originals, catalog,
  sidecars, previews, models, caches, snapshots, backups, and protection
  receipts.
- **Current evidence:** current docs span topology, recovery, privacy, field,
  and rename plans; runtime and backup terms are easy to confuse.
- **Prerequisite:** CORE-02 target roots and the server-role decision may remain
  explicitly pending.
- **Boundary:** truthful documentation only; do not imply caches protect
  originals or that a transfer request equals verified backup.
- **Acceptance evidence:** clean-machine reader can identify what is durable,
  rebuildable, portable, backed up, and required for restore; links pass.

#### CORE-06 — Re-audit destructive and source-alias boundaries

- **Status / priority / owner:** `unverified` · `P1` · Core Library & Safety.
- **Desired outcome:** scans, remapped sources, aliasing, Trash, restore, import,
  and free-space decisions all preserve catalog and byte truth.
- **Current evidence:** prior destructive-order sweeps fixed real issues; new
  scan-safety and recovery branches are pending; portable source identity is
  not complete.
- **Prerequisite:** CORE-01 integration and source-type test matrix.
- **Boundary:** audit first; fix only reproduced classes in bounded owner lanes.
- **Acceptance evidence:** local, offline, remapped, nested, external, mapped,
  and UNC source tests; interruption/failure injection; full digest gates every
  destructive "protected copy" claim.

#### CORE-07 — Close the rename/recovery safety boundary

- **Status / priority / owner:** `active` · `P0` · Core Library
  & Safety.
- **Desired outcome:** every valid current or pre-migration snapshot remains
  discoverable, protected by retention, drillable, and restorable while catalog,
  WAL, owner marker, staging files, and data roots migrate atomically or roll
  back without hiding the user's library.
- **Current evidence:** integration merge `119337ad1` contains reviewed
  `e518de33c`. The combined recovery/system-backup/migration/rebrand/runtime/
  restore-drill matrix now passes 50 tests with one skip, including all six
  prior dual-prefix discovery/retention/drill failures. Exact-name restore of an
  Azimuth snapshot prepares a validated catalog without replacing the live DB.
- **Prerequisite:** close the remaining verified boundaries below before any
  runtime-root or internal-identifier migration.
- **Boundary:** tests and scratch copies only; no live restore, owner-marker
  rewrite, catalog/WAL move, data-root move, backup deletion, or service restart
  during the fix/audit lane.
- **Acceptance evidence:** all current failures pass; both naming generations
  are listed, retained, drilled, and restorable; scratch refusal recognizes both
  live catalog/marker generations; catalog plus WAL/SHM/journal move as one
  recoverable unit; split-root precedence never hides the populated catalog;
  owner identity survives a catalog/path rename; and an installed client applies
  a prepared restore with stop/replace/restart/rollback proof.

| Verified boundary | Exact evidence | Status or unknown | Required acceptance proof |
|---|---|---|---|
| Snapshot discovery and retention | `119337ad1` integrates `e518de33c`; dual-prefix discovery uses the existing validated regex for current `azimuth-*` and frozen `photoarchive-*` names. Focused matrix: 50 passed, 1 skipped; the post-Core complete gate also resolves all six prior failures. | Verified on the integration head in focused and complete-suite order | Preserve mixed old/new behavior: select the correct newest snapshot, protect pre-migration snapshots, prune only eligible snapshots, and restore both exact names. |
| Exact-name restore | Scratch probe created an Azimuth snapshot and `restore_backup()` validated/staged it while leaving the live DB untouched. | Verified preparation; application remains absent | Route and UI select the listed current snapshot, stage it, then installed-client apply preserves the previous live DB, removes stale sidecars safely, restarts, checks schema/catalog counts, and can roll back. |
| Restore-drill scratch refusal | A scratch directory containing only `azimuth.db` was accepted; current guard checks only the prior DB filename and marker. | Verified P0 guard defect | Both catalog filenames, both owner-marker generations, WAL/SHM/journal, and configured live roots hard-refuse before any scratch creation or cleanup; refused bytes remain unchanged. |
| Catalog plus sidecars | Synthetic WAL rename failure moved the main DB to `azimuth.db`, left the old WAL behind, and still returned the new DB path. | Verified failure-path behavior; impact on real uncheckpointed WAL not exercised | Force failure at each main/WAL/SHM/journal step; either all names commit after checkpoint/integrity proof or every name rolls back; reopen and compare rows, `quick_check`, user version, and sidecar state. |
| Split data roots | With a populated prior XDG root and an empty existing Azimuth root, resolver selected the empty Azimuth catalog path and left the populated catalog untouched. | Verified precedence defect in scratch; Windows/macOS impact unknown | Old-only, new-only, both-identical, both-divergent, empty-new/populated-old, cross-volume, low-space, permission-failure, first/second boot, and rollback matrix on Linux/Windows/macOS. Never initialize over an undiscovered catalog. |
| Backup ownership across rename | Live owner marker records the prior catalog path while the active catalog is `web/azimuth.db`; current owner comparison is exact-path equality. No live backup attempt was made. | Verified mismatch evidence; actual scheduler refusal is `unverified` | Read-only warning probe, then scratch rewrite/adoption test that authenticates catalog identity, refuses foreign roots, preserves every snapshot, and resumes scheduled backup after path migration. |
| Schema upgrade and rollback | Existing tests prove populated catalogs refuse schema migration when pre-migration backup fails; schema work uses transactions/local table-rebuild backups. | Backup gate verified; complete downgrade/application path unknown | Old release catalog → current upgrade → injected failure at each phase → restore pre-migration snapshot → old and new app boot checks; catalog counts, direct choices, edits, sources, and user version reconcile. |
| Prepared-restore application | Current and `origin/windows-install` trees stage/status/discard prepared restores; repository search found no apply path, while docs require manual server stop and file moves. | Verified product/recovery gap | One explicit customer action stops the managed engine, preserves the failed DB and sidecars, atomically promotes the staged DB, restarts, verifies health, and offers rollback without terminal instructions. |

### Desktop Workflow

#### DESK-01 — Integrate customer-trust desktop outputs

- **Status / priority / owner:** `done awaiting integration` · `P0` · Desktop
  Workflow.
- **Desired outcome:** Deliver destinations recover independently, public client
  galleries report failures and restore focus, System/Library Health is calm and
  truthful, and Loupe Escape returns to the invoking photo.
- **Current evidence:** `9e8802643`, `de5987071`, `aa7dfffb0`, and
  `569492c17`; focused Node/contracts/browser proof reported by each lane.
- **Prerequisite:** approved main reconciliation; customer-trust cohort review;
  desktop proof merged last inside the cohort.
- **Boundary:** named commits only; no delivery primitive consolidation or UI
  redesign during integration.
- **Acceptance evidence:** focused contracts, isolated gallery/Deliver/Health
  browser proof, focus-return journey, quick gate, then full suite.

#### DESK-02 — Close the remaining desktop journey failures

- **Status / priority / owner:** `unverified` · `P0` · Desktop Workflow.
- **Desired outcome:** high-frequency Cull, Trash, browse, Loupe, and recovery
  journeys run deterministically and leave the user in a usable place.
- **Current evidence:** the desktop-proof task reported Cull second-photo,
  Trash-return, and fixture-I/O failures; only Loupe Escape focus proof was
  committed.
- **Prerequisite:** DESK-01 integration and a quiet isolated QA fixture.
- **Boundary:** reproduce first; separate product defects from harness defects;
  no broad QA rewrite.
- **Acceptance evidence:** fails-before/passes-after scenario for each product
  defect; fixture timings and I/O root cause for harness defects; artifacts kept.

#### DESK-03 — Make Dual a decisive atomic two-photo rhythm

- **Status / priority / owner:** `parked` · `P1` · Desktop Workflow.
- **Desired outcome:** after any successful mouse or keyboard choice, both old
  photos are replaced atomically by two distinct, genuinely new, non-recent
  candidates; failure and undo restore exact state and focus.
- **Current evidence:** current main replaces only the selected card; unmerged
  predecessor behavior exists; boundary contract with Ranking is complete.
- **Prerequisite:** accepted Ranking candidate/action interface and integration
  approval.
- **Boundary:** Desktop owns displayed pair, buffer consumption, races, focus,
  accessibility, rollback, and perceived handoff; Ranking owns eligibility,
  persistence, strategy, and authoritative undo.
- **Acceptance evidence:** atomic A/B to C/D test, no repeat test, rapid-input
  race test, mouse/keyboard parity, failure rollback, exact undo, focus
  continuity, and click-to-new-pair performance budget.

#### DESK-04 — Finish keyboard and accessibility proof

- **Status / priority / owner:** `unverified` · `P1` · Desktop Workflow.
- **Desired outcome:** every daily action has a predictable keyboard path;
  Escape peels one layer; focus returns; labels, contrast, and reduced-motion
  behavior are release quality.
- **Current evidence:** a read-only audit exists and one Loupe focus-return
  scenario is committed; no consolidated implementation was approved.
- **Prerequisite:** DESK-01 and DESK-02; stable UI surfaces.
- **Boundary:** audit and behavior fixes only; no new pages or command system.
- **Acceptance evidence:** keyboard map matrix, screen-reader names, focus-order
  run, reduced-motion proof, desktop journey artifacts on Windows and Linux.

#### DESK-05 — Split the omnibox behind its stable facade

- **Status / priority / owner:** `parked` · `P2` · Desktop Workflow.
- **Desired outcome:** commands, facets/date parsing, suggestion shaping, and
  rendering have clear owners while `initOmnibox`, `focusOmnibox`, and
  `openCommandPalette` remain stable.
- **Current evidence:** modularity audit found a valuable bounded seam; no
  product behavior change is required.
- **Prerequisite:** desktop cohort quiet and journey suite reliable.
- **Boundary:** no new UI, route, bootstrap, state, panel, or lens behavior.
- **Acceptance evidence:** focused pure Node tests, syntax checks, command/facet/
  result/Escape browser smoke, unchanged visuals and API calls.

#### DESK-06 — Reconcile Develop architecture and documentation

- **Status / priority / owner:** `ready after integration` · `P1` · Desktop
  Workflow.
- **Desired outcome:** product doctrine and code map describe the shipped
  Lightroom-like Develop editor, its UI ownership, renderer boundary, and
  parity obligations accurately.
- **Current evidence:** `docs/ui-architecture.md` still contains stale scope
  language while Develop is shipped; large `gl.js`/Develop-shell files warrant
  owner-led modular review, not drive-by splitting.
- **Prerequisite:** integration and Azimuth Editing owner review.
- **Boundary:** docs truth first; renderer extraction only with pixel parity and
  no simultaneous feature work.
- **Acceptance evidence:** no contradictory docs, current module map, GL/Python
  parity suite, golden real-RAW render evidence for any later split.

#### DESK-07 — Reduce large desktop facades in owner-sized slices

- **Status / priority / owner:** `parked` · `P2` · Desktop Workflow.
- **Desired outcome:** `drawer.js`, `panel.js`, and other large composition files
  coordinate focused behavior modules rather than owning unrelated features.
- **Current evidence:** modularity audit measured `drawer.js` around 2,026 lines
  and `panel.js` around 1,579; both are also collision-heavy.
- **Prerequisite:** active delivery/health/Windows integrations complete.
- **Boundary:** one product responsibility per extraction, stable public
  exports, no repo-wide framework.
- **Acceptance evidence:** identical browser behavior, focused tests, smaller
  owner facade, no new circular imports or timing regression.

#### DESK-08 — Cache the complete Map response bytes safely

- **Status / priority / owner:** `ready after integration` · `P1` · Desktop
  Workflow.
- **Desired outcome:** a complete Map opens quickly for large libraries without
  filtering away photos or repeatedly paying full-payload serialization cost.
- **Current evidence:** the 2026-07-25 Map/People performance profile found the
  query layer fast while serializing the complete Map response dominated. No
  accepted absolute time or response-byte count accompanied that profile, so
  this is an attribution, not a numeric baseline.
- **Prerequisite:** accepted integration head, reproducible large Map fixture,
  payload byte/shape measurement, and owner-defined invalidation events.
- **Boundary:** bounded response-byte caching only; do not cap, filter, sample,
  paginate, or change Map semantics to make the metric look faster. People
  pagination remains the separate BUG-PEOPLE-01 decision.
- **Acceptance evidence:** cached and uncached payloads are byte-equivalent;
  create/edit/delete/location/privacy changes invalidate deterministically;
  concurrent requests do not stampede; memory and response-size budgets are
  recorded; cold/warm p50/p95/p99 improve without omitting a photo.

### Mobile and Companion

#### MOB-01 — Integrate field resilience

- **Status / priority / owner:** `done awaiting integration` · `P0` · Mobile &
  Companion.
- **Desired outcome:** local mobile changes survive offline use, retry honestly,
  drain safely after reconnect, and distinguish recoverable from terminal
  failures.
- **Current evidence:** committed output `462997293`; focused queue/offline/
  browser proofs reported green.
- **Prerequisite:** main reconciliation and customer-trust cohort review.
- **Boundary:** named commit only; preserve existing queue semantics and avoid
  backend/sync-worker redesign.
- **Acceptance evidence:** offline write, reload, reconnect, duplicate-delivery,
  terminal-failure, and stale-response tests; mobile browser proof.

#### MOB-02 — Re-audit client/server hardening

- **Status / priority / owner:** `parked` · `P1` · Mobile & Companion.
- **Desired outcome:** companion traffic uses lightweight heartbeat and measured
  workload coordination without making a server the canonical client path.
- **Current evidence:** `client-server-hardening` contains older mixed history
  and is not a clean ready commit against the current split.
- **Prerequisite:** unified integration head and MOB-01.
- **Boundary:** audit/cherry-pick-by-proof; never merge the mixed branch
  wholesale.
- **Acceptance evidence:** exact patch provenance, local-first behavior with
  server absent, bounded network/CPU work, old-peer compatibility.

#### MOB-03 — Preserve and specify the mobile offline queue contract

- **Status / priority / owner:** `ready after integration` · `P1` · Mobile &
  Companion.
- **Desired outcome:** every mobile mutation commits locally before success,
  persists across restart, is idempotent, and never becomes an infinite silent
  retry.
- **Current evidence:** field-resilience implements a stronger queue; native and
  PWA paths still differ.
- **Prerequisite:** MOB-01 and optional-server capability decision.
- **Boundary:** behavior contract first; do not unify PWA/Android storage
  implementations artificially.
- **Acceptance evidence:** airplane-mode session, force-stop/reload, long
  disconnect, retry/backoff, terminal error surfacing, reconnect convergence.

#### MOB-04 — Close native companion gaps

- **Status / priority / owner:** `unverified` · `P1` · Mobile & Companion.
- **Desired outcome:** Android share ingestion, device-token pairing, and
  terminal backup failure handling are complete and visible.
- **Current evidence:** `docs/BUGS.md` records ShareActivity not ingesting shared
  content, no device-token support, and forever-retrying permanent item failures;
  current Android branch parity has not been reverified.
- **Prerequisite:** MOB-03 contract and Windows/server capability decisions.
- **Boundary:** native companion only; no server-authoritative Archive redesign.
- **Acceptance evidence:** real Pixel content-share ingestion, paired secured
  hub, permanent-failure recovery UI, offline Archive unaffected.

#### MOB-05 — Build a genuinely local companion repository

- **Status / priority / owner:** `parked` · `P2` · Mobile & Companion.
- **Desired outcome:** prepared PWA/native clients browse and rank from local
  catalog/artifact state; server adapters are optional inputs.
- **Current evidence:** durable local-first architecture report is complete;
  stable portable identity and event contracts do not yet exist.
- **Prerequisite:** CORE-04 and Ranking action/artifact contracts.
- **Boundary:** one platform slice at a time; no claim that browser storage is
  archival protection.
- **Acceptance evidence:** no-server prepared browse/Refine, restart retention,
  storage-pressure behavior, later event convergence, no implicit media upload.

### Ranking and Local Intelligence

#### RANK-01 — Implement the local ranking action pipeline

- **Status / priority / owner:** `parked` · `P1` · Ranking & Local Intelligence.
- **Desired outcome:** one immutable local human action is acknowledged
  immediately, survives restart, materializes idempotently, and can be undone
  exactly before or after background processing.
- **Current evidence:** design and real-catalog latency baseline are complete;
  an attempted implementation was removed when consolidation was ordered.
- **Prerequisite:** accepted integration head, MOB-01, CORE-04 identity decision,
  and coordinator-approved file boundary.
- **Boundary:** no implementation before approval; action ledger/materializer,
  direct compatibility projection, reservoirs, queue, and acknowledgements only.
- **Acceptance evidence:** action append p95 at or below 50 ms, proposed p99
  below 100 ms, 10,000-action soak, crash cuts, replay/idempotence, exact undo,
  no catalog/model work on acknowledgement path.

#### RANK-02 — Store mosaics as one correlated action

- **Status / priority / owner:** `parked` · `P1` · Ranking & Local Intelligence.
- **Desired outcome:** a mosaic records one action containing winner and complete
  candidate set; pair rows are derived compatibility projections, not
  independent training evidence.
- **Current evidence:** audit found 75,539 pair rows from 7,587 actions; current
  representation can inflate confidence.
- **Prerequisite:** RANK-01 schema and migration review.
- **Boundary:** preserve direct choices and legacy response shapes; do not
  silently reinterpret historical undo.
- **Acceptance evidence:** one durable action per click, exact candidate set,
  deterministic pair projection, grouped training weight, action-level undo,
  migration counts and limitations documented.

#### RANK-03 — Make candidate eligibility complete and honest

- **Status / priority / owner:** `parked` · `P0` · Ranking & Local Intelligence.
- **Desired outcome:** Random means uniform across the complete resolved
  eligible selection; exclusions and quiet sources apply before drawing.
- **Current evidence:** filtered Random currently samples inside an
  Elo-ordered 192-photo window; unfiltered behavior differs.
- **Prerequisite:** accepted candidate-reservoir boundary and RANK-01.
- **Boundary:** candidate mechanics only; no change to model semantics or
  user-visible strategy names.
- **Acceptance evidence:** statistical uniformity over full large scopes,
  filtered/excluded IDs never returned, no deterministic top slice, refill
  remains within interaction budgets.

#### RANK-04 — Replace both Dual photos atomically

- **Status / priority / owner:** `parked` · `P1` · Ranking & Local Intelligence.
- **Desired outcome:** Ranking supplies two unique prepared candidates for each
  successful round and an authoritative action/undo receipt.
- **Current evidence:** warm reservoir p95 is 14.86 ms; current UI keeps one old
  photo; initial uncached loads were hundreds of milliseconds.
- **Prerequisite:** RANK-01 and RANK-03; Desktop DESK-03 implements presentation.
- **Boundary:** Ranking owns candidate and persistence contract, not focus or DOM
  transitions.
- **Acceptance evidence:** two-candidate uniqueness/exclusion tests, no network
  on prepared device, action receipt/undo contract, click-to-new-pair proposed
  p95 at or below 50 ms and p99 below 100 ms.

#### RANK-05 — Separate direct evidence from inferred intelligence

- **Status / priority / owner:** `parked` · `P1` · Ranking & Local Intelligence.
- **Desired outcome:** explicit choices remain dominant and inspectable;
  uncertainty, provenance, model version, and inferred score never masquerade as
  direct user judgment.
- **Current evidence:** current Elo, taste, and similarity propagation are
  separate projections with limited confidence semantics.
- **Prerequisite:** RANK-01/RANK-02 durable actions and shadow-evaluation design.
- **Boundary:** preserve current ranking output until shadow results pass; no
  model cutover on the click path.
- **Acceptance evidence:** provenance per score, direct/inferred fields,
  retraction consumption, deterministic rebuild, calibration and held-out choice
  evaluation, explicit cutover approval.

#### RANK-06 — Propagate learning across every eligible asset

- **Status / priority / owner:** `parked` · `P1` · Ranking & Local Intelligence.
- **Desired outcome:** catalog learning is not restricted to the current Refine
  scope or embedded minority; all eligible assets can gain useful projection
  with uncertainty.
- **Current evidence:** about 100,469 of 146,535 active photos had neither
  embedding nor ranking signal; similarity propagation reaches only prepared
  neighbors.
- **Prerequisite:** coverage plan, stable identities, RANK-05 provenance, and
  shadow evaluation.
- **Boundary:** typed graph/model may use visual, time, camera, person, collection,
  and lineage edges only with versioned semantics; never overwrite direct intent.
- **Acceptance evidence:** catalog coverage report, uncertainty calibration,
  cold/unembedded behavior, no leakage across inappropriate edge types,
  reversible versioned projections, no interaction regression.

#### RANK-07 — Make prepared intelligence portable and server optional

- **Status / priority / owner:** `parked` · `P1` · Ranking & Local Intelligence.
- **Desired outcome:** thumbnails, embeddings, and model artifacts carry asset
  identity, input fingerprint, recipe/model revision, size, and checksum; adopted
  local copies remain usable after producer loss.
- **Current evidence:** caches are largely local-ID-addressed; server-optional
  architecture contract is complete but unimplemented.
- **Prerequisite:** CORE-04 identity and explicit server capability/consent
  decision.
- **Boundary:** manifests and local adoption first; compute exchange and backup
  later; no implicit upload.
- **Acceptance evidence:** import artifact pack into different local IDs, reject
  wrong model/recipe/corruption, server disappears after compute and local
  ranking continues, network capture shows no unapproved upload.

#### RANK-08 — Establish ranking and taste performance truth

- **Status / priority / owner:** `ready after integration` · `P0` · Ranking &
  Local Intelligence.
- **Desired outcome:** standing real-catalog and synthetic gates cover direct
  submit, next candidates, combined click, cold/warm projections, worst-case
  contention, and prepared no-network behavior.
- **Current evidence:** direct p95 30.87 ms but 6.4 s worst; mosaic submit p95
  73.64 ms; next p95 182.80 ms; combined p95 230.77 ms; live Elo response
  1558.7 ms; cold Taste 18873.6 ms.
- **Prerequisite:** integration and a non-production catalog clone/fixture.
- **Boundary:** benchmark before optimization; never widen a budget to hide work.
- **Acceptance evidence:** versioned JSON history, fixture/catalog shape,
  p50/p95/p99/worst, profiler stacks, CI regression thresholds, no production
  writes.

### Windows Distribution and Identity

#### WIN-01 — Finish real Windows installer proof

- **Status / priority / owner:** `done awaiting integration` · `P0` · Windows
  Distribution & Identity.
  Identity.
- **Desired outcome:** a normal Windows user installs Azimuth Photo and opens a
  working first library from local, mapped-drive, and UNC/NAS folders.
- **Current evidence:** proof-complete `origin/windows-install` at `f8da9949e`;
  the 107,918,017-byte installer and SHA-256 are recorded above. Unsigned
  current-user install, fresh standalone home, release/schema, native picker,
  actual welcome UI, local browse, mapped `Z:`/UNC NAS UI and API browse,
  Alt+F4, and relaunch passed. Omarchy reran
  `test_windows_desktop_install`: 7/7 passed; the branch range passed
  `git diff --check`.
- **Prerequisite:** controlled Windows integration cohort after the earlier
  cohorts; preserve the artifact/proof receipt.
- **Boundary:** integrate only the four named commits; this proof does not
  authorize signing, updater, publishing, identifier migration, or format claims.
- **Acceptance evidence:** achieved for unsigned install-to-library. Re-run the
  same matrix on the integrated SHA; add uninstall/reinstall, signed download,
  update, upgrade, and rollback only in their owning release gates.

#### WIN-02 — Integrate bundled engine and folder onboarding

- **Status / priority / owner:** `done awaiting integration` · `P0` · Windows
  Distribution & Identity.
- **Desired outcome:** Tauri supervises a self-contained local Azimuth engine and
  onboarding hides hub/server mechanics.
- **Current evidence:** `0ee6004db`, `ca651c201`, `8fd62ab98`, and `f8da9949e`;
  real Windows proof above; Linux-side Cargo and focused Python proof reported
  green.
- **Prerequisite:** final controlled integration cohort.
- **Boundary:** named commits only; legacy identifiers remain compatible during
  the migration window, produce no new non-Azimuth output, and remote sync
  remains unchanged.
- **Acceptance evidence:** Windows proof plus Linux packaged-engine smoke,
  package/resource contract, clean first-home behavior, no developer runtime.

#### WIN-03 — Remove customer-visible plumbing

- **Status / priority / owner:** `ready after integration` · `P1` · Windows
  Distribution & Identity.
- **Desired outcome:** no customer-facing Python, server, port, URL, hub,
  satellite, Tailscale, or developer terminology in normal installation/use.
- **Current evidence:** onboarding branch removes some plumbing; repository docs
  and older visible surfaces still contain technical language.
- **Prerequisite:** WIN-02 and optional-server product vocabulary.
- **Boundary:** visible copy/workflow only; technical identifier migration stays
  in WIN-06 rather than being hidden inside copy changes.
- **Acceptance evidence:** fresh-user content audit, no manual URL entry for
  normal local use, optional storage/compute introduced as product capabilities.

#### WIN-04 — Align versions, signing, updates, and release artifacts

- **Status / priority / owner:** `blocked` · `P0` · Windows Distribution &
  Identity.
- **Desired outcome:** installer, engine, API, About, update manifest, tag, and
  changelog report one version; signed updates install safely.
- **Current evidence:** the unsigned RC installer now proves current-user
  installation and relaunch. The accepted integration head still reports
  `0.1.0` in both Tauri and Cargo while the proved Windows branch artifact is
  `1.0.0-rc.1`; no signing, updater, tag, public download, or release-channel
  proof exists.
- **Prerequisite:** signing/public-release decisions, green CI, WIN-01/WIN-02,
  clean upgrade drill.
- **Boundary:** no public artifact before release gates; secrets never enter git.
- **Acceptance evidence:** signed installer verification, version matrix,
  previous-version upgrade/rollback, update signature failure test, clean
  download smoke.

#### WIN-05 — Apply the Light Meridian identity consistently

- **Status / priority / owner:** `parked` · `P1` · Windows Distribution &
  Identity.
- **Desired outcome:** installer, icon, shell, PWA, Android, About, screenshots,
  and public surfaces share one Azimuth Photo asset system; old visible marks are
  gone.
- **Current evidence:** rebrand audit proposed the system; broad asset replacement
  has not been integrated or approved as a release artifact.
- **Prerequisite:** Windows and mobile integration, asset review, clean package
  proof.
- **Boundary:** visible identity only; never couple asset work to persisted
  identifier migration.
- **Acceptance evidence:** platform asset inventory, high-DPI/Windows shell
  proof, PWA/Android parity, no customer-visible old mark, accessibility contrast.

#### WIN-06 — Choose the cutover and complete internal Azimuth naming

- **Status / priority / owner:** `blocked` · `P0` · Windows Distribution &
  Identity.
- **Desired outcome:** customer-visible Azimuth Photo is complete and all owned
  repository/remotes, application paths, services, scripts, environment
  variables, databases, data directories, packages, stored keys, and internal
  identifiers reach their platform-appropriate Azimuth names without stranding
  installs or data.
- **Current evidence:** Phase 1 compatibility code is on `main`; legacy wrappers
  and names remain intentionally. The installed app displays Azimuth Photo, but
  its OS-level executable is still `photoarchive-desktop.exe`. This is verified
  owned-name migration inventory, not authority for an immediate binary rename.
  Repo/service/XPS phases remain incomplete.
- **Prerequisite:** complete identifier inventory, runtime extraction, no active
  feature lanes, backup/restore drill, collision-proof path map, accepted
  integration, impact estimate, and explicit user selection of one path below.
- **Boundary:** the destination is approved, but no persisted/internal rename
  starts before the cutover-path decision; no sweeping search/replace; no
  permanent compatibility promise; customer-visible prior branding remains a
  defect regardless of the selected internal path.
- **Acceptance evidence:** the decision packet maps every identifier and
  installation population, compares data-loss/rollback/support cost, records the
  approved path, and then proves that path's full matrix.

| Evaluated path | Behavior | Required proof before selection | Completion evidence |
|---|---|---|---|
| **A — Tested compatibility bridge for one stable release** | New installs and writes use Azimuth identifiers; narrowly scoped readers discover both identifier generations for exactly one stable release | New/old/mixed precedence, first/second boot, DB/WAL/backup discovery, upgrade/rollback, pre-migration client behavior, bridge-removal design | Stable-release support window completes with migration counts and no stranded install; bridge is then removed and the Azimuth-only state passes the release matrix |
| **B — Backed-up zero-legacy cutover** | A scheduled migration converts all selected persisted and operational identifiers; normal operation retains no compatibility bridge after successful cutover | Verified backups, scratch restore, collision-proof move map, disk sizing, service/remotes/toolkit coordination, atomic failure and rollback drills | Announced cutover succeeds; restored catalog and originals reconcile; services/restarts/upgrade pass; selected active tree, artifacts, paths, databases, and runtime output meet the approved zero-legacy inventory |

### Release and Quality Infrastructure

#### REL-01 — Reconcile `main` and `develop`

- **Status / priority / owner:** `active` · `P0` · Release & Quality
  Infrastructure.
- **Desired outcome:** one reviewable integration history contains production
  fixes and develop Collections/search work without losing either contract.
- **Current evidence:** 5/55 divergence; merge paused after exactly three
  conflict resolutions.
- **Prerequisite:** coordinator review of the product-section map and merge diff.
- **Boundary:** no further merge work and no commit until approval; production
  remains untouched.
- **Acceptance evidence:** exact parent SHAs, conflict receipt, shim/fallback/both
  test sets present, targeted search/desktop tests, quick and full gates.

#### REL-02 — Fix the three pre-existing Ruff errors separately

- **Status / priority / owner:** `ready after integration` · `P1` · Release &
  Quality Infrastructure.
- **Desired outcome:** quick lint gate is green without hiding the integration
  diff.
- **Current evidence:** unused `shutil` in `web/core/rebrand_migrate.py`, unused
  `_MIB` in `web/test_host_profile.py`, and unused `os` in
  `web/test_rebrand_migrate.py`.
- **Prerequisite:** approved REL-01 merge commit.
- **Boundary:** remove only the three unused imports in one atomic commit.
- **Acceptance evidence:** Ruff green, focused rebrand/host tests, commit contains
  only those paths.

#### REL-03 — Merge completed cohorts serially

- **Status / priority / owner:** `parked` · `P0` · Release & Quality
  Infrastructure.
- **Desired outcome:** completed work lands in dependency order with manual
  overlap review and exact receipts.
- **Current evidence:** named lane outputs are ready; several touch files changed
  on the opposite history. REL-01/REL-02 and the five-item initial Core cohort
  are integrated with focused and quick gates green. The post-Core complete
  non-Playwright gate improved from 24 failures to 18 by closing exactly the six
  recorded recovery failures; all 18 remaining identities were already
  classified and no new failure appeared.
- **Prerequisite:** REL-01 and REL-02 plus an accepted disposition and green
  rerun for every active full-gate defect.
- **Boundary:** Core trust/performance, customer trust, desktop proof, then
  Windows after live proof; never batch unrelated commits.
- **Acceptance evidence:** SHA/conflict/test/perf receipt after every cohort and
  separate coordinator approval before merge into `develop`.

#### REL-04 — Make pytest the complete default test truth

- **Status / priority / owner:** `parked` · `P0` · Release & Quality
  Infrastructure.
- **Desired outcome:** the default unit gate collects unittest and pytest-native
  tests, excluding only explicitly marked slow/bench/Playwright work.
- **Current evidence:** the current paused head collects 1,425 tests through
  unittest versus 1,539 selected by pytest from 1,541 collected (two bench
  deselections). The non-Playwright selection is 1,533 of 1,535 collected.
  Therefore 114 selected pytest tests can be missed by the current default.
  The first post-reconciliation complete non-Playwright run selected 1,533 tests
  and finished with 24 failed, 1,507 passed, 3 skipped, 2 deselected, and 399
  passing subtests in 560.64 seconds. Seven failures map to existing plan items;
  17 are newly registered below. No deterministic failure was introduced by the
  three manual conflict resolutions: 12 reproduce on exact `main`, one on exact
  `develop`, and the remaining 11 pass in an isolated failing-node rerun. After
  recovery merge `119337ad1`, a targeted rerun of those original 24 nodes
  improved from 13 failed/11 passed to 7 failed/17 passed. The complete
  non-Playwright rerun on post-Core head `d90d89f4a` finished with 18 failed,
  1,519 passed, 3 skipped, 2 deselected, and 399 passing subtests in 509.80
  seconds. Exact set comparison found the six recovery failures resolved, all
  other 18 unchanged, and zero new failure identities. The two deterministic
  host-profile failures were then traced to stale test contracts rather than
  runtime regressions: exact `main` contains both contradictions, while exact
  `develop` predates the adaptive profile and does not contain the new AI
  contract. Contract-only commit `50f1299a` now injects deterministic host
  budgets and a 16-GiB profile; the two nodes plus profile/model/memory/runtime
  coverage passed 51 tests with one mounted-corpus skip, and quick is green.
  Deterministic P0 `BUG-AI-OWNER-01` was then isolated to exact `main`: its
  mid-load race guard correctly stopped generic unload from stealing an active
  loader's leases, but the now-terminal failed-load path relied on that same
  guarded unload and retained both manual and GPU ownership. Exact `develop`
  passes the node. Commit `22946846` releases only the failed attempt's owners
  after forced cleanup; the exact node, 28 cross-worker lifecycle/resource
  tests, the complete 29-test embedding-worker file, targeted Ruff, and quick
  are green. The post-repair complete non-Playwright checkpoint on `2b818a8b3`
  selected 1,539 of 1,541 collected tests but could not complete: two attempts
  aborted with signal 11 / exit 139. The captured attempt reached 253 progress
  events, including the repaired model-budget node passing in full order and
  the same known caption-cancellation and first-run order failures, then
  segfaulted while a media-warm thread used the persistent thumbnail SQLite
  connection as `BackendTestCase.asyncTearDown` closed it. The demosaic
  host-profile and AI-owner nodes at collection positions 285 and 570 were not
  reached. A preliminary harness drain then exposed the second ownership layer:
  media warm's wrapper had completed, but thumbnail prefetch had handed a probe
  to an unretained `asyncio.to_thread` task that was still inside
  `cache_entries._get_disk_entry` when teardown closed SQLite. Final
  harness-only commit `3c6b0fae` preserves the explicit media-warm drain, then
  drains work handed off on the test loop before closing the per-test
  connection; production scheduling is unchanged. The focused ordering
  contract passed twice, `test_compare.py` completed 10/10 runs (510 tests),
  the cache/media/thumbnail lifecycle matrix passed 45 tests, and Ruff plus
  quick are green. Two frozen-source complete gates then finished without a
  native abort: the first had 18 failed, 1,520 passed, 3 skipped, 2 deselected,
  and 399 passing subtests in 566.71 seconds; the second had 15 failed, 1,523
  passed, 3 skipped, 2 deselected, and 399 passing subtests in 500.27 seconds.
  The first failure set exactly matches the post-Core 18-identity baseline; the
  second omits only the three known performance-budget failures. Neither adds a
  new identity, and both confirm the host-profile and AI-owner identities
  closed in full order.
- **Prerequisite:** integration complete; quality-foundation lane reapproved.
- **Boundary:** `scripts/azimuth-check`, pytest config, focused target mapping,
  and docs; retain the pre-migration command reader only during the protected
  cutover, then remove it under WIN-06.
- **Acceptance evidence:** before/after collect counts, all unittest cases still
  collected, pytest-only hardening/restore/perf/develop contracts included,
  quick and unit green.

#### REL-05 — Contain future artifacts prospectively

- **Status / priority / owner:** `parked` · `P1` · Release & Quality
  Infrastructure.
- **Desired outcome:** DB accidents, timestamped bench output, `.bak-*`, backups,
  status JSON, and generated client bundles stop appearing as new source debris.
- **Current evidence:** the current tree tracks 74 paths under
  `web/.pytest-tmp/`, 13 database-like files, 15 `bench-runs/` paths, and three
  receipt logs. Existing ignores do not contain `web/.pytest-tmp/` or
  `bench-runs/`; ignoring `receipts/` does not untrack existing files.
- **Prerequisite:** preservation inventory; quality-foundation approval.
- **Boundary:** prospective ignore rules only; no deletion, `git rm`, or cleanup
  in this task.
- **Acceptance evidence:** ignored-state demonstration, unchanged tracked
  artifact set, no runtime path accidentally hidden if it should be source.

#### REL-06 — Make docs, routes, and code map truthful

- **Status / priority / owner:** `parked` · `P1` · Release & Quality
  Infrastructure.
- **Desired outcome:** contributor docs describe current feature families,
  composition, route inventory, Develop, auth/backup, and verification commands.
- **Current evidence:** code-map omissions and stale importer/module claims were
  audited; UI architecture still understates Develop; route/client drift exists.
- **Prerequisite:** integration head and owner confirmation for retirement
  candidates.
- **Boundary:** document reality; do not delete `importer.js` or move routes
  without journey proof.
- **Acceptance evidence:** generated/current route contract, module inventory,
  no dead doc links, no contradictory Develop scope, owner-confirmed retirement
  list.

#### REL-07 — Scrub public and privacy risk from tree and history

- **Status / priority / owner:** `unverified` · `P0` · Release & Quality
  Infrastructure.
- **Desired outcome:** public source, docs, artifacts, and history contain no
  personal absolute paths, private IP/default servers, credentials, catalog
  facts, personal website-export paths, or recoverable private media.
- **Current evidence:** a bounded current-tree scan found `/home/sean/` in 35
  files/125 lines, Windows user-path forms in three files/seven lines,
  `/mnt/expansion/` in 76 files/119 lines, and private `100.x` HTTP(S) endpoints
  in 15 files/18 lines. These are review candidates, not all confirmed leaks.
  Tailnet-hostname coverage and full-history scanning were not completed.
- **Prerequisite:** preserved backup, agreed redaction/history policy, clean
  release candidate.
- **Boundary:** audit first; no history rewrite without explicit user approval
  and remote coordination.
- **Acceptance evidence:** secret/path/IP/catalog/media scanners over current
  tree and full history, manual high-risk review, clean-clone verification, no
  credentials or private data in release artifacts.

#### REL-08 — Prove CI, tags, and public release automation

- **Status / priority / owner:** `blocked` · `P0` · Release & Quality
  Infrastructure.
- **Desired outcome:** Linux and Windows gates run on the accepted SHA; tags
  produce reproducible artifacts and release notes.
- **Current evidence:** CI runs pytest only on Ubuntu/Python 3.12 plus a Node 22
  syntax job; it does not build or test Windows, macOS, native desktop, Android,
  install, upgrade, or restore. The tag workflow still publishes a legacy-named
  Docker package and Linux frozen server; its signed Windows job is hard-disabled
  with `if: false`. There are zero local tags and no accepted green remote run,
  published release, SBOM, provenance, attestation, or signing proof.
- **Prerequisite:** GitHub access/status, complete test truth, versions/signing,
  release-candidate SHA.
- **Boundary:** no tag or public release as a substitute for missing CI.
- **Acceptance evidence:** green workflow URLs/IDs, immutable tag, artifact
  checksums/SBOM as chosen, clean-download smoke, release rollback procedure.

#### REL-09 — Run the release matrix from clean environments

- **Status / priority / owner:** `parked` · `P0` · Release & Quality
  Infrastructure.
- **Desired outcome:** clean clone/build/install/first-run, local/NAS library,
  backup/restore, upgrade, rollback, offline, and mixed-version paths are proven.
- **Current evidence:** the Windows RC proves a fresh installed artifact, not a
  reproducible clean-clone build. CI intends a clean Ubuntu checkout/install,
  but no accepted remote run or clean-clone receipt was found, and no current
  clean upgrade/rollback/restore matrix exists.
- **Prerequisite:** REL-03/REL-04/REL-08 and WIN-04.
- **Boundary:** disposable homes/catalog copies only; never production originals
  or catalog.
- **Acceptance evidence:** Linux/Windows artifact matrix, local/mapped/UNC roots,
  no-server normal use, upgrade with pre-migration backup, capsule/catalog
  restore, old-client compatibility, exact receipts.

#### REL-10 — Consolidate repository organization methodically

- **Status / priority / owner:** `parked` · `P2` · Release & Quality
  Infrastructure.
- **Desired outcome:** an understandable monorepo contains application,
  desktop/mobile clients, public website history, docs, and tooling without
  scattered product identity.
- **Current evidence:** organization audit recommends a monorepo target; current
  checkout mixes runtime, many worktrees, legacy docs, and related website
  history.
- **Prerequisite:** preservation inventory, runtime extraction, quiet feature
  lanes, public/privacy scrub, migration plan.
- **Boundary:** import website with history; no flattening, sweeping rename,
  branch deletion, or source removal in the first phase.
- **Acceptance evidence:** collision-proof path map, retained histories, build/
  deploy ownership, clean clone, no runtime/data movement hidden inside source
  reorganization.

## 5. Benchmarks and quality baseline

Targets labeled **covenant** are already product doctrine. Targets labeled
**proposed** require ratification in the implementing lane. A blank numeric
target means measure/profile before choosing one; it does not mean "fast enough."

| Date / source | Metric | Current | Target | Status |
|---|---|---:|---|---|
| 2026-07-25 first-use lane and integration | First library HTTP response during boot | Branch-local QA-5000: 3759 ms before, 1415.29 ms after; integration `84650c100`: 72 focused tests and quick green, no new runtime timing | Preserve or improve in a disposable integration benchmark; exact release budget to ratify | `active` |
| 2026-07-25 filter-options lane | Filter facet read | 37.8 ms before; 29.2 ms after | No correctness loss or integration regression | `active` |
| 2026-07-25 filter integration, controlled 2,000-image catalog | Uncached `/api/filter-options` median of three route calls | 61.9 ms on parent `4170f7bf1`; 26.9 ms on staged `a9e11f4ed` composition (56.5% lower) | At or below the standing 125 ms synthetic budget with exact scoped facets | `active` |
| 2026-07-25 real-catalog ranking clone | Dual/direct durable submit p95 | 30.87 ms; 6.4 s worst outlier | Action acknowledgment at or below 50 ms p95 **proposed**; below 100 ms p99 **proposed** | `parked` |
| 2026-07-25 real-catalog ranking clone | 12-photo mosaic submit p95 | 73.64 ms | At or below 50 ms action acknowledgment **proposed** | `parked` |
| 2026-07-25 real-catalog ranking clone | Next mosaic candidates p95 | 182.80 ms | Prepared candidate handoff within click covenant; exact backend split to ratify | `parked` |
| 2026-07-25 real-catalog ranking clone | Mosaic submit plus next p95 | 230.77 ms | Click-to-new-pair at or below 50 ms p95 and below 100 ms p99 **proposed** | `parked` |
| 2026-07-25 real-catalog ranking clone | Warm 24-photo reservoir p95 | 14.86 ms | Remain below click-to-new-pair budget | `parked` |
| 2026-07-25 ranking audit | Live Elo response | 1558.7 ms | Profile and remove from interactive path; numeric target not yet ratified | `parked` |
| 2026-07-25 ranking audit | Cold Taste response | 18873.6 ms | Background/precomputed only; numeric build budget not yet ratified | `parked` |
| 2026-07-25 ranking audit | Prepared-device network dependency | Interactive path still has remote/server-oriented seams | Zero network dependency for prepared browse/ranking **proposed** and product-required | `parked` |
| 2026-07-25 catalog audit | Active photos with current embeddings | 44,715 of about 146,535 | Coverage plan with truthful per-device readiness; percentage target not yet approved | `parked` |
| 2026-07-25 Windows install proof | Unsigned current-user installer and install-to-library journey | 107,918,017 bytes; SHA-256 `ABEBF263…A500299`; install exit 0; local + mapped `Z:`/UNC browse; close/relaunch passed | Preserve on integrated SHA; signing/updater/public release remain separate gates | `done awaiting integration` |
| 2026-07-25 Omarchy Windows-branch check | `test_windows_desktop_install` | 7/7 passed; range `git diff --check` passed | Same proof on controlled integration SHA | `done awaiting integration` |
| 2026-07-25 Map/People profile | Complete Map response generation | Query layer fast; full-payload serialization dominates; absolute latency/bytes not accepted | Bounded byte-cache with exact-payload and invalidation proof; no feature filtering | `ready after integration` |
| 2026-07-25 recovery integration | Recovery/system-backup/migration/rebrand/runtime/restore-drill matrix | 50 passed, 1 skipped on `119337ad1`; all six prior naming-generation failures pass | Preserve zero discovery/retention/drill failures in the complete gate; retain all safety passes | `active` |
| 2026-07-25 preview integration | Preview correctness and performance-honesty contracts | 120 passed on `22f841466`; quick green; ETA uses current image-completion rate; full-original background caching preserves 2 GiB free space; benchmark history is opt-in | Preserve focused proof in full-suite order; collect a read-only live latency comparison before claiming a speed delta | `active` |
| UI architecture covenant | Cull/Refine perceived response | Existing paths can poll or exceed 50 ms | Below 50 ms perceived; no spinner **covenant** | `active` |
| 2026-07-25 test-discovery audit | Default selected automated coverage | 1,425 unittest; pytest selects 1,539/1,541 after two bench deselections; 114-test gap; non-Playwright selects 1,533/1,535 | Complete intended pytest unit selection | `parked` |
| 2026-07-25 release audit | Application release version | Current integration head: Tauri/Cargo `0.1.0`; proved Windows branch artifact: `1.0.0-rc.1` | One authoritative version across tag, manifest, installer, engine, API, About, and updater | `blocked` |
| 2026-07-25 release audit | Clean-source release evidence | Fresh Windows install passed; no accepted clean-clone build, upgrade/rollback/restore matrix, tag, or remote green run | Reproducible clean clone through signed clean-download smoke and recovery matrix | `blocked` |
| 2026-07-25 REL-01/REL-02 gate | Complete non-Playwright pytest | 1,533 selected: 24 failed, 1,507 passed, 3 skipped, 2 deselected, 399 subtests passed in 560.64 s | Zero failures before any cohort merge | `blocked` |
| 2026-07-25 post-Core gate on `d90d89f4a` | Complete non-Playwright pytest | 18 failed, 1,519 passed, 3 skipped, 2 deselected, 399 subtests passed in 509.80 s; exactly six recovery failures resolved, zero new identities | Zero failures before customer-facing cohorts; preserve all six recovery closures | `blocked` |
| 2026-07-25 host-profile contract remediation on `50f1299a` | Two deterministic host-profile failures plus related profile/model/memory/runtime coverage | Before: exact integrated nodes failed 2/2 on live Omarchy because tests asserted static 6,815,744,000-byte VRAM and one RAW worker; after: 51 passed, 1 mounted-corpus skip; Ruff and quick green; neither identity failed in either complete gate on `3c6b0fae` | Preserve adaptive host authority across deterministic 8-/16-/64-GiB and known-VRAM fixtures | `done awaiting integration` |
| 2026-07-25 AI owner remediation on `22946846` | Failed search-model load ownership lifecycle | Exact `main` returns false with both manual and GPU owner still `embeddings`; exact `develop` passes. After: exact node 1 passed; cross-worker lifecycle/resource matrix 28 passed; complete embedding-worker file 29 passed; targeted Ruff and quick green; the identity did not fail in either complete gate on `3c6b0fae` | Preserve the full-order closure; real packaged Linux/Windows model-deserialization failure remains to be exercised without delaying integration | `done awaiting integration` |
| 2026-07-25 post-repair checkpoint on `2b818a8b3` | Complete non-Playwright pytest | Current collect: 1,539 selected of 1,541, two deselected. Two full-command attempts aborted with signal 11 / exit 139; captured run stopped after 253 progress events with one repaired host-budget node passed, two known order failures, and no new assertion identity before a native SQLite/thread teardown crash. Demosaic and AI-owner nodes were not reached | Zero native aborts; both remaining repaired identities pass in full order; exact failure-set comparison then completes against the 18-failure baseline | `blocked` |
| 2026-07-25 BUG-TEST-CRASH-01 on `3c6b0fae` | Complete non-Playwright pytest after test-harness lifecycle repair | Focused contract 2 passed; `test_compare.py` completed 10/10 runs (510 tests) without a crash; cache/media/thumbnail lifecycle matrix passed 45 tests; Ruff and quick green. Full gate 1: 18 failed, 1,520 passed, 3 skipped, 2 deselected, 399 subtests passed in 566.71 s. Full gate 2: 15 failed, 1,523 passed, 3 skipped, 2 deselected, 399 subtests passed in 500.27 s. Both completed without a native abort; gate 1 exactly reproduced the 18-identity baseline and gate 2 omitted only the three known performance-budget identities | Preserve zero native aborts and no new failure identity; remaining assertions stay owned by their existing bug entries | `done awaiting integration` |

Every future performance receipt records catalog/fixture shape, machine, commit,
warm/cold state, p50, p95, p99, worst, profiler attribution, and correctness
checks. A faster incomplete query is a regression.

## 6. Integration sequence and yield rules

### Required sequence

1. **Freeze and review the current merge.** `main` is merged into the isolated
   sprint worktree with no commit. Review every automatically overlapping path
   and the three manual resolutions. Do not commit until coordinator approval.
2. **Commit reconciliation only after approval.** Record both parent SHAs,
   conflicts, resolution rationale, targeted tests, and quick/full gate.
3. **Fix Ruff separately.** Remove exactly the three unused imports in REL-02.
4. **Core trust/performance cohort.** Integrate scan safety, recovery truth,
   preview honesty, filter options, and first-use boot serially. Run targeted and
   quick gates after each; benchmark and full gate after cohort.
5. **Customer-trust cohort.** Integrate mobile field resilience, Deliver loading
   recovery, client-gallery resilience, and Health clarity with manual UI
   conflict review.
6. **Desktop proof.** Integrate the Loupe focus-return proof after product UI
   commits so the journey asserts the accepted surface.
7. **Windows last.** Integrate Windows bundle/onboarding only after terminal
   NSIS install, first-launch, local, mapped, and UNC/NAS evidence.
8. **Coordinator review before `develop`.** The integration branch is a review
   artifact, not authority to merge or deploy.

### Strict do-not-start rules

- Do not start Ranking implementation, Dual implementation, quality-foundation,
  facade refactors, monorepo reorganization, runtime relocation, identity asset
  rollout, or internal identifier migration before their decision and
  prerequisites above.
- Do not modify production `main`, runtime data, services, original roots, or
  existing dirty worktrees during integration.
- Do not delete, reset, stash, clean, detach, rename, or remove any branch,
  worktree, bundle, backup, benchmark, database, cache, or `.bak-*` file without
  its explicit preservation disposition.
- Do not merge a mixed-history branch wholesale. Extract only a reviewed commit
  or patch with provenance.
- Do not combine visible rebrand work with persisted/service identifier
  migration.
- Do not call a server mandatory merely because the current implementation uses
  one. Prepared local behavior is the target authority.
- Do not make a public release before REL-07 through REL-09 pass.

### Yield immediately when

- An active owner touches the same file or product responsibility.
- A worktree is dirty and provenance is unclear.
- A test fails outside the named change or a benchmark regresses.
- Windows proof, backup/restore proof, privacy scrub, or user decision is a hard
  prerequisite.
- A migration cannot prove mixed-version fallback and rollback.
- A destructive action depends on an unverified copy, unresolved path, wildcard,
  or broad cleanup target.

## 7. Bug and known-defect register

This register is for reproducible defects and evidence gaps, not broad
initiatives. Items marked `unverified` must be reproduced before a fix lane.

| ID | Status / priority | Owner | Known defect or gap | Required closure evidence |
|---|---|---|---|---|
| BUG-DESK-01 | `unverified` / P0 | Desktop | Cull journey fails on the second-photo step according to desktop-proof task | Isolated fails-before trace; focused product or harness fix; passes-after artifact |
| BUG-DESK-02 | `unverified` / P0 | Desktop | Trash journey does not return to the expected usable library state | Exact navigation/state trace and deterministic passes-after journey |
| BUG-QA-01 | `unverified` / P1 | Release | QA fixture I/O caused journey instability/latency in desktop-proof work | I/O attribution, reusable fixture timing, no hidden production diagnosis |
| BUG-RANK-01 | `parked` / P0 | Ranking | Filtered Random draws from an Elo-ordered 192-photo window rather than full eligible selection | Statistical full-scope proof with filters/exclusions |
| BUG-RANK-02 | `parked` / P0 | Ranking | Direct submit has a 6.4 s worst outlier despite 30.87 ms p95 | Contention/crash profile and proposed p99 below 100 ms gate |
| BUG-RANK-03 | `parked` / P0 | Ranking | Mosaic submit/next path is 73.64/182.80 ms p95; combined 230.77 ms | Durable local append plus prepared reservoir benchmarks |
| BUG-RANK-04 | `parked` / P1 | Ranking | Dual leaves one stale survivor after a successful choice | Atomic two-new-photo behavioral and timing proof |
| BUG-RANK-05 | `parked` / P1 | Ranking | Pair rows can be treated as independent evidence despite shared human action | One-action migration/projection and grouped-training proof |
| BUG-RANK-06 | `parked` / P1 | Ranking | 100,469 active photos lacked embeddings and ranking signal in audit | Coverage/provenance report and full-catalog projection evaluation |
| BUG-TEST-01 | `parked` / P0 | Release | Default unittest gate selects 1,425 tests while pytest selects 1,539; 114 selected tests are omitted | Before/after collection receipt and green complete unit gate |
| BUG-TEST-02 | `active` / P0 | Release | The post-Core complete gate confirms the same eleven tests fail only in suite order but pass together in isolation: caption owner release, first-run preview/ranking visibility, decode-budget cancellation, manual People work, and seven thumbnail bulk/priority/manual-work contracts | Reproduce with order/bisection receipt, eliminate leaked global/env/resource state without weakening product assertions, then pass isolated nodes and two complete non-Playwright runs |
| BUG-TEST-CRASH-01 | `done awaiting integration` / P0 | Release | The pre-existing test-harness race had two ownership layers: media warm itself could still use the per-test persistent thumbnail SQLite connection, and its prefetch wrapper could finish after handing a probe to an unretained `asyncio.to_thread` task. A preliminary direct drain still crashed at 35% with the child in `cache_entries._get_disk_entry`; final harness-only commit `3c6b0fae` explicitly drains media-warm tasks and then handed-off test-loop work before closing the connection, without changing production async behavior. Focused ordering 2 passed, compare stress completed 10/10 (510 tests), lifecycle coverage passed 45, Ruff and quick are green, and two complete gates finished without native aborts. Gate 1 exactly reproduced the known 18 failures; gate 2 reproduced 15, omitting only three known performance budgets; neither introduced a new identity | Preserve the ordering contract and both complete-gate receipts when this lane is reviewed for `develop`; do not weaken the bounded fail-before-close behavior |
| BUG-ARTIFACT-01 | `parked` / P1 | Release | Source tracks 74 pytest-temp paths, 13 database-like files, 15 benchmark-run paths, and three receipt logs; ignores do not prospectively contain all classes | Preserve/disposition inventory, no deletion in containment lane, and clean proof that new generated files remain untracked |
| BUG-VERSION-01 | `blocked` / P0 | Release | Current integration manifests report `0.1.0` while the proved Windows artifact reports `1.0.0-rc.1` | One-version matrix across source, tag, installer, engine, API, About, updater, and changelog |
| BUG-CI-01 | `blocked` / P0 | Release | CI is Ubuntu/Python plus Node syntax only; signed Windows release is disabled and no accepted current remote green run exists | Accepted workflow run on release SHA with Windows build/install gates and immutable artifact receipts |
| BUG-CLEANCLONE-01 | `parked` / P0 | Release | Fresh Windows install is proved, but no reproducible clean-clone build or upgrade/rollback/restore matrix is accepted | Disposable clean-source build and cross-platform install/recovery matrix with exact receipts |
| BUG-HOSTPROFILE-01 | `done awaiting integration` / P1 | Core | Root cause is two stale exact-`main` test contracts: unset model budgets were changed to adaptive host values while the test retained static fallback constants, and adaptive cgroup-aware RAW sizing replaced the raw-physical-memory one-worker threshold without updating that assertion. Exact `develop` predates the adaptive profile and lacks the new AI contract. Contract-only commit `50f1299a` injects deterministic host budgets and a 16-GiB profile; 51 related tests passed, 1 mounted-corpus test skipped, quick is green, and neither identity failed in two complete gates on `3c6b0fae` | Retain deterministic 8-/16-/64-GiB and known-VRAM policy coverage. Physical Windows profiles, the 64-GiB class, and host-probe fallback behavior remain unverified in this slice |
| BUG-AI-OWNER-01 | `done awaiting integration` / P0 | Core | Commit `a28a7efa5` correctly made generic unload preserve leases while a model may still be loading, but a terminal load exception then called only that guarded unload. Exact `main` returns false with both manual and GPU owner still `embeddings`, potentially blocking captions or other work until the 15-minute lease expires; exact `develop` passes. Commit `22946846` explicitly releases only the terminal failed attempt after forced cleanup, and the regression proves captions can immediately claim both lanes; the identity stayed closed in both complete gates on `3c6b0fae` | Preserve the focused lifecycle proof and full-order closure. A packaged real-model deserialization failure on Linux/Windows remains unverified |
| BUG-PREGEN-WATCHDOG-01 | `active` / P0 | Core | Deterministic exact-`main` stall-watchdog test resets the executor twice despite fresh progress heartbeats | Slow-progress fixture completes with no cancellation/reset while a truly stalled fixture still recovers |
| BUG-RUNTIME-02 | `active` / P0 | Core | Deterministic exact-`main` Develop-root test sends HDR, panorama, and RAW caches to `/home/sean/.cache/photoarchive/develop` while the selected disposable application root is elsewhere | All direct Develop modules resolve one selected Azimuth data root across new/legacy env precedence and never leak to a host-global cache |
| BUG-SHORTCUT-01 | `active` / P1 | Desktop | Exact `develop` advertises `C / O / M / H` in the Library shortcut sheet while its binding-proof map still expects `O / M / Y / H` | Product-approved key set, matching visible sheet and live handlers, keyboard/accessibility proof |
| BUG-TEST-SHIM-01 | `active` / P1 | Release | Deterministic exact-`main` UI contract reads deprecated `photoarchive-browser-smoke` wrapper contents instead of the canonical Azimuth smoke implementation | Contract follows the canonical script while a separate compatibility test proves the wrapper delegates correctly |
| BUG-LINT-01 | `ready after integration` / P1 | Release | Three current Ruff F401 errors block quick lint | Exact three-import atomic commit and Ruff green |
| BUG-ROUTE-01 | `unverified` / P1 | Mobile | Mobile `writeRating()` targets nonexistent `/api/image/{id}/rating`; manual stars require product decision | Route/client decision, contract test, no conflict with Taste/Elo |
| BUG-ROUTE-02 | `unverified` / P2 | Desktop | `/api/quality/scan` exists without a customer entry point for old stacks | Product decision and end-to-end scoring/recovery proof if built |
| BUG-DOC-01 | `ready after integration` / P1 | Desktop | UI architecture understates/outdates shipped Develop scope | Owner-approved doctrine and code-map correction |
| BUG-DOC-02 | `unverified` / P1 | Release | Code map omits current families/modules and lists `importer.js` despite no known inbound import | Live inventory; import journey before any retirement |
| BUG-ID-01 | `blocked` / P0 | Windows | Customer-visible pre-Azimuth branding may remain across shipped surfaces, while internal/persisted identifiers have not completed the approved Azimuth migration; the installed binary is verified as `photoarchive-desktop.exe` | Visible-brand audit reaches zero customer-facing occurrences; every internal finding is classified and then closed by the selected WIN-06 path; integrated installer exposes the approved executable name |
| BUG-WIN-ID-01 | `unverified` / P1 | Windows | OS app discovery exposed both `app.azimuthphoto.desktop` and older `com.seankennethdoherty.photoarchive` identities for the same running window; this may be a stale install or identifier-migration collision | Clean-machine install with old app removed; enumerate registry/app identity before and after; prove one running identity, one uninstall entry, one data home, and upgrade continuity |
| BUG-WIN-CODEC-01 | `unverified` / P1 | Windows | Frozen-engine build emitted unresolved optional `imagecodecs` DLL warnings for JPEG-XS, JetRaw, and HEIF; no supported-format decode failure was reproduced | Declare supported Windows formats, inspect frozen imports/DLLs, then decode representative JPEG-XS/JetRaw/HEIF or explicitly classify unsupported formats; clean packaging log for supported set |
| BUG-MAP-PERF-01 | `ready after integration` / P1 | Desktop | Full Map payload serialization, not its query, dominates the reported large-library profile | Reproducible cold/warm profiler plus bounded response-byte cache with byte equality, deterministic invalidation, memory bound, and no filtering |
| BUG-RECOVERY-02 | `ready after integration` / P0 | Core | Restore-drill scratch guard accepts a directory containing an Azimuth-named live catalog | Dual-generation catalog/marker/sidecar refusal with byte-preservation failure injection |
| BUG-RECOVERY-03 | `ready after integration` / P0 | Core | Catalog rename can commit the main DB, fail a WAL sidecar rename, and still select the new DB | Atomic or fully reversible main/WAL/SHM/journal migration with row/integrity comparison at every injected failure |
| BUG-RECOVERY-04 | `ready after integration` / P0 | Core | An existing empty Azimuth data root wins over a populated prior root, hiding the populated catalog from normal resolution | Cross-platform split-root matrix with explicit conflict UI and no new catalog initialization while an unadopted catalog exists |
| BUG-RECOVERY-05 | `ready after integration` / P0 | Core | Recovery can prepare a valid restore but current and Windows-branch code have no managed apply path; documentation requires terminal file moves | Installed-client stop/promote/restart/health/rollback journey using disposable catalogs and no terminal |
| BUG-RECOVERY-06 | `unverified` / P0 | Core | Live backup owner marker names the prior catalog path while the active DB uses the Azimuth filename; exact-path ownership may refuse the next backup after rename | Read-only warning confirmation, scratch identity migration, and successful scheduled snapshot without weakening foreign-root refusal |
| BUG-PRIV-01 | `unverified` / P0 | Release | Bounded current-tree scan found 35 files with `/home/sean/`, three with Windows user paths, 76 with `/mnt/expansion/`, and 15 with private `100.x` HTTP(S) endpoints; full-history and Tailnet-hostname coverage remain unknown | Classify every hit, complete current/history scan, and inspect clean release artifacts |
| BUG-RUNTIME-01 | `active` / P0 | Core | Runtime databases, previews, models, backups, and venv coexist with source | Verified relocation/restore and source checkout stays runtime-clean |
| BUG-REL-01 | `blocked` / P0 | Release | No accepted proof of current public CI, tag, or release | Green CI evidence, immutable tag, clean-download artifact smoke |
| BUG-MAP-01 | `unverified` / P2 | Desktop | Map may ignore the active Similar scope | Product decision plus scope-consistency test |
| BUG-PEOPLE-01 | `unverified` / P2 | Desktop | People results cap at 100 per section and can silently omit people | Pagination/cap decision, complete result behavior and perf proof |
| BUG-GALLERY-01 | `unverified` / P2 | Desktop | Public client gallery may ignore its configured custom cover | Public page contract and browser proof |
| BUG-CULL-01 | `unverified` / P2 | Desktop | Cull-brief zoom has no backend subject/focus box despite frontend fields | Box contract or deliberate removal, real-photo proof |
| BUG-SETTING-01 | `unverified` / P3 | Desktop | `show_loupe_cache_status` is documented as persisted but unread | Wire or remove after usage audit and migration-safe test |
| BUG-MOB-01 | `unverified` / P1 | Mobile | ShareActivity may fail to ingest shared content URIs | Real Pixel share-in ingestion proof |
| BUG-MOB-02 | `unverified` / P1 | Mobile | Secured hub device-token pairing is absent in native client | Paired secured-hub backup/browse proof |
| BUG-MOB-03 | `unverified` / P1 | Mobile | Permanent backup item failures may retry forever without recovery UI | Terminal classification, user recovery, queue-drain test |

Resolved historical defects stay in [the detailed bug log](docs/BUGS.md); they
should not be re-added here unless reproduced on the accepted integration head.

## 8. Decisions and assumptions

### Approved product direction

- Customer-visible product name is **Azimuth Photo**.
- Customer-visible language, artwork, package identity, documentation, and
  workflows must contain no prior branding.
- Owned technical, persisted, and operational identifiers will also reach the
  platform-appropriate Azimuth form. Existing installations do not change until
  WIN-06 selects and proves either the one-stable-release bridge or the backed-up
  zero-legacy cutover.
- Windows desktop is the primary distribution/client surface; mobile is a
  first-class companion.
- Local/NAS folders must be effortless.
- Prepared browse/ranking/intelligence must work without a server.
- A server is optional and capability-scoped: storage, protection, exchange,
  compute, sharing, or publishing.
- Every ranking click should feel instant.
- Dual replaces both photos after each successful rating.
- Learning/projection should cover the full eligible catalog, not only the
  current refinement scope.
- Originals and durable user evidence take precedence over caches and cleanup.
- Existing installs remain protected during the chosen migration. Permanent
  compatibility is not the destination; the remaining user decision is which
  proven cutover path and service window to use.

### User approval required before significant change

| Decision | Why approval is required | Safe work allowed beforehand |
|---|---|---|
| Ranking model and authoritative cutover | Changes how taste is interpreted and ordered | Action/provenance design, shadow evaluation, benchmarks |
| Mosaic evidence weighting and migration tolerance | Historical pair rows cannot perfectly reconstruct all intent | Count/report legacy actions and limitations |
| Manual 0–5 star behavior | May conflict with Taste/Elo and Lightroom semantics | Route/client audit only |
| Sidecar/XMP automatic-write policy | Touches user-adjacent files and Lightroom interoperability | Read-only inventory, isolated round-trip proof |
| Runtime data relocation | Moves the live catalog/cache and affects service recovery | Scratch backup/restore and target sizing |
| Internal Azimuth identifier migration cutover | Internal Azimuth naming is approved, but persisted names affect systemd, toolkit, remotes, live data, scripts, restore behavior, and mixed-version clients | Choose **A:** one-stable-release tested bridge followed by removal, or **B:** backed-up zero-legacy cutover; first complete the inventory, preservation map, precedence/rollback tests, and impact comparison |
| Cloud/server capability defaults | Determines what data leaves devices and why | Capability model and no-upload tests |
| Public license/release/signing policy | Creates external obligations and update trust | CI/reproducibility/signing research without secrets |
| Light Meridian final assets | Changes the durable public identity | Inventory and platform rendering proofs |
| Map Similar-scope and People pagination behavior | Changes user expectations and query cost | Reproduce and measure current behavior |
| Full-history redaction/rewrite | Rewrites shared Git history | Read-only scan and redaction plan |

### Working assumptions to verify

- Omarchy remains an excellent optional storage/compute/protection node, but not
  the normal local UI authority.
- The bundled local engine can remain internal implementation if it is invisible
  and fully managed by the Windows client.
- Existing transitional readers and stored names are migration inputs. They
  retire through the selected WIN-06 path rather than becoming permanent
  architecture.
- Existing sidecars/XMP are an interoperability layer, not a complete backup of
  ranking actions or catalog structure.
- Earlier lane test reports are credible evidence for review, not substitutes
  for rerunning gates on the accepted integration SHA.
- Existing workflow files are intent, not proof of public CI or release.

## 9. Existing lane-output map

This appendix prevents duplicate work. A commit listed here is preserved output,
not permission to merge it.

| Queue entry | Branch / commit | Output | Integration condition |
|---|---|---|---|
| CORE-01 | `scan-safety` / `a5dacedd9` | Interrupted/offline scan catalog safety | Core cohort after main reconciliation |
| CORE-01/CORE-07 | `recovery-truth` / `e518de33c` → merge `119337ad1` | Dual-prefix snapshot discovery, retention, destination guard, and restore-drill truth | Integrated without conflicts; focused recovery matrix 50 passed, 1 skipped; quick green |
| CORE-01 | `preview-honesty` / `e6c98e937` → merge `22f841466` | Honest background preview state/ETA and preview-first disk reserve | Integrated after manual heartbeat/watchdog/bulk-selection overlap review; 120 focused tests and quick green; no live latency claim |
| CORE-01 | `perf-filter-options` / `a9e11f4ed` → merge `91b1d66bc` | Consolidated exact facet reads from one materialized eligible-image set plus scoped People query | Integrated without conflicts; 16 focused tests and quick green; controlled 2,000-image median 61.9 → 26.9 ms; large-catalog live proof remains |
| CORE-01 | `first-use-boot` / `fce783a7a` → merge `84650c100` | Defer updater bundle prep after library readiness while advertising no unready update | Integrated without conflicts; startup/fresh-home/desktop/rollback matrix 72 passed and quick green; untracked QA-5000 receipts preserved in the source lane; disposable integration timing remains |
| MOB-01 | `mobile-field-resilience` / `462997293` | Offline/reconnect/terminal mobile write behavior | Customer-trust cohort |
| DESK-01 | `deliver-load-resilience` / `9e8802643` | Independent Deliver destination loading/retry | Manual `panel.js` review |
| DESK-01 | `client-gallery-resilience` / `de5987071` | Honest gallery failure/retry and focus | Isolated gallery browser proof |
| DESK-01 | `health-clarity` / `aa7dfffb0` | Calm System/Library Health states | Backend payload compatibility review |
| DESK-01/DESK-02 | `desktop-journey-proof` / `569492c17` | Loupe Escape focus-return scenario | Merge after desktop product cohort |
| WIN-01/WIN-02 | `windows-install` / `0ee6004db` | Bundled local engine and desktop shell | Proof complete; controlled Windows cohort |
| WIN-01/WIN-02 | `windows-install` / `ca651c201` | Local/mapped/UNC onboarding | Proof complete; controlled Windows cohort |
| WIN-01/WIN-02 | `windows-install` / `8fd62ab98` | Bootstrap pinned Tauri CLI on Windows | Proof complete; controlled Windows cohort |
| WIN-01/WIN-02 | `windows-install` / `f8da9949e` | PowerShell-safe Tauri CLI probe; branch proof tip | Proof complete; rerun integrated install matrix |
| REL-01 | `main` / `458675e77` | Production fixes, Phase 1 rebrand compatibility, host/runtime work | Audited merge into isolated integration |
| REL-01 | `develop` / `52742f941` | Collections/search/intelligence integration line | First parent of sprint integration |
| BUG-HOSTPROFILE-01 | `sprint-integration` / `50f1299a` | Replaced two inherited machine-specific assertions with deterministic adaptive budget and 16-GiB RAW-worker contracts; no product code changed | Focused profile/model/memory/runtime matrix 51 passed, 1 mounted-corpus skip; Ruff and quick green; identity closed in both complete gates on `3c6b0fae` |
| BUG-AI-OWNER-01 | `sprint-integration` / `22946846` | Terminal search-model load failures release both embedding leases without weakening the mid-load unload race guard; captions can immediately take ownership | Exact node 1 passed; cross-worker lifecycle/resource matrix 28 passed; complete embedding-worker file 29 passed; targeted Ruff and quick green; identity closed in both complete gates on `3c6b0fae` |
| BUG-TEST-CRASH-01 | `sprint-integration` / `3c6b0fae` | Test teardown drains media-warm wrappers and nested thumbnail-prefetch handoffs before closing the per-test persistent SQLite connection | Compare 10/10 (510 tests), lifecycle 45 passed, Ruff/quick green; two full gates completed with zero native aborts and no new failure identity |
| RANK research | No implementation branch | Real-catalog latency, action/intelligence boundary, local-first contract | Preserve report; one future owner |
| Quality foundation | No accepted commit | Complete pytest discovery, prospective ignores, docs/code-map truth | Start only after integration |
| DESK-08 / `perf-people-map` | `origin/perf-people-map` / `d1f7e4cd0` | QA-5000 KPI receipt only; reported Map serialization attribution is not a code fix | Preserve receipt; remote is 679/3 divergent from `develop`, so never merge wholesale |
| `client-server-hardening` | Mixed older history | Potential heartbeat/workload ideas | Re-audit; never merge wholesale |

Research/audit tasks for modularity, organization, rebrand, flows, Health,
ranking design, local-first architecture, keyboard access, and sprint
coordination should close after their findings are represented here. New work
starts from these queue IDs, not from old thread titles.

---

Maintenance rule: update evidence and status, not prose history. When an item is
accepted, record its commit and receipt in the appendix, mark the queue entry,
and keep the acceptance evidence. When the product direction changes, update
the north star or decision table explicitly so implementation does not drift.
