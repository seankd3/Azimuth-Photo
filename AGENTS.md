# Azimuth Photo agent guide

This is the authoritative operating contract for agents working in this
repository. Keep public instructions portable. If `AGENTS.local.md` exists,
read it after this file for deployment-specific paths and live-service truth;
that ignored overlay must never become a product dependency.

## Start here

Before editing or launching anything:

1. Run `git status --short --branch` and `git worktree list`.
2. Read [`docs/TOPOLOGY.md`](docs/TOPOLOGY.md),
   [`docs/development.md`](docs/development.md), and the specification for the
   product surface being changed.
3. Read `AGENTS.local.md` when present.
4. Check the relevant live status before changing runtime behavior.
5. State the intended scope and keep unrelated user or agent changes untouched.

If checkout, branch, runtime, or deployment truth differs from the local
overlay, stop and reconcile the discrepancy before coding.

## Source and runtime boundaries

- GitHub `main` is the released product source of truth. An owner-approved
  rewrite may live on a named branch until its deliberate review and merge; do
  not create additional branches or move production to it implicitly.
- Runtime data never belongs inside a source checkout.
- Do not add personal usernames, home directories, private addresses, mount
  points, catalog paths, or secrets to tracked defaults or documentation.
- Azimuth 2.0 is a laptop-first desktop application. Attached drives are
  working or record/archive storage; being unplugged is normal. The former
  hub/satellite/server design is historical and must not shape new V2 code.
- Product defaults must support a fresh local install without a server,
  network, account, or external service.
- Retired checkouts and worktrees are preservation material, not development
  sources. Do not revive them unless the task explicitly calls for recovery.

## How to work

- Commit owned changes in small, atomic commits on the current owner-approved
  branch. Do not create branches, worktrees, lanes, or parallel editing
  sessions unless the user explicitly requests them.
- Never change a production checkout's branch or restart a long-running service
  as a side effect of development.
- Stage explicit paths. Never use `git add -A` in a shared or dirty tree.
- Preserve unrelated changes.
- Keep code modular and product-owned. Prefer a focused module over a shared
  abstraction with one caller.
- Build real behavior, not mockups or simulations.
- Keep implementation complexity and model plumbing out of the product UI.

## Code ownership

Routes are their own map: `web/harness/goldens/routes.json` is recorded from the
running app, so it cannot drift the way a hand-written list did.

- `web/features/<surface>/` owns backend behavior and routes.
- `web/data/repositories/` owns SQL and persistent queries.
- `web/core/` owns application wiring and cross-cutting runtime code.
- `web/static/js/desktop/` owns focused desktop ES modules.
- `web/static/js/mobile/` owns the PWA.
- `web/thumbnails/` owns preview generation and cache maintenance.
- `desktop/` is the Windows shell; `android/` is the native Android client.
- `scripts/` contains repeatable developer/operator entry points only.

Keep routes thin, SQL in repositories, and UI state close to its owning
surface. Preserve public routes and response shapes unless the task explicitly
changes the contract.

## Data and safety

- Original photos are user data. Do not move, rename, deduplicate, or delete
  them during code cleanup.
- The `Astrophotography/` archive root is out of scope and off limits. It is not
  a library root or a taxonomy destination. No scan, index, import, migration,
  cleanup, dedup, or free-up path may read, move, rename, or delete anything
  under it.
- The archive roots are `Edits/`, `Raws/`, and `Snapshots/`, spelled exactly so.
  Match whatever spelling readdir reports and never create a root that does not
  already exist. Case behaviour is per-volume (an exFAT archive may be
  case-insensitive; an ext4 archive is not) — never normalise or guess casing.
- A V2 photo row's `status` vocabulary is `unflagged`, `picked`, and `trashed`,
  and nothing else. It is a browse projection of the append-only decision log:
  Pick writes `picked`, U clears to `unflagged`, and Reject writes `trashed`.
  The inherited V1 `kept` / `maybe` startup normalizer is historical evidence,
  not a V2 contract. Missing files and excluded sources remain storage facts;
  never overload culling status to represent either one.
- Destructive photo operations require collision-proof destinations,
  full-byte verification, and a recoverable Trash/Undo path.
- Exact duplicate cleanup keeps the oldest filesystem-modified file; the
  earliest catalog record breaks ties.
- Catalogs, previews, models, embeddings, Develop caches, logs, backups, and
  transfer receipts are runtime/generated data. Never commit or silently
  discard them.
- Slow archive drives must not host latency-sensitive catalogs or active
  caches. Serialize bulk reads and prefer SSD/RAM previews.
- Never run tests against a real catalog unless the task explicitly requires a
  read-only live check. Use isolated test homes and fixtures for writes.
- `AZIMUTH_SMOKE_MODE=1` is test-only. Never use it for the real desktop app.

## Simplicity

- Elegant code has the same structure as the problem. Smallness is a
  consequence, not the objective; a shorter implementation that does less or
  remains entangled has not improved the product.
- Judge architectural weight by coupling: how many concepts and files must
  change together to add or repair one behavior. File length and polish are not
  substitutes for that measurement.
- Static reachability is not permission to delete. Before removing a surface,
  inspect operational commands, checks, documentation, and git change history;
  hand-run and externally invoked behavior is invisible to an import graph.
- Reliability comes from having one of each thing, not from more checks. One
  rule for whether a photo is in the library, in one place, used everywhere. A
  rule copied into two hundred queries is two hundred chances to disagree, and
  it cannot be repaired or reasoned about.
- Leave the tree tidy: no merged worktrees, dead branches, or one-off scripts
  left lying around. The next person's speed is set by how much clutter they
  have to read past.
- No test may take longer than 30 seconds. A slow suite does not get run, and a
  suite that does not get run is not protecting anything. Develop against
  `scripts/make_test_library.py` (a few hundred photos on fast local disk),
  never the real archive.

## Verification

Use the smallest check that proves the change, then expand in proportion to
risk:

```bash
./scripts/azimuth-check --quick
./scripts/azimuth-check --list-areas
./scripts/azimuth-check --area <area>
./scripts/azimuth-check --unit
```

- Docs-only: `git diff --check`, link/path review, and instruction-conflict
  search.
- Focused change: quick checks plus the owning test module or named area.
- Desktop behavior: verify the actual workflow in the running app against an
  isolated application home.
- Sync, deletion, imports, recovery, or catalog changes: add focused safety
  tests and verify final persisted state.
- Deployment: verify listener, working directory, health endpoint, catalog,
  cache, and rollback route.

Report exact commands and results. Never claim a UI reproduction or passing
suite that did not occur.

## Documentation hygiene

Each durable fact has one owner:

- `AGENTS.md` — public agent rules and safety boundaries.
- `AGENTS.local.md` — ignored installation-specific deployment truth.
- `MASTER_PLAN.md` — what the user asked for, verbatim, and whether it is done.
- `docs/TOPOLOGY.md` — portable runtime roles and storage architecture.
- `docs/development.md` — setup, edit map, and verification commands.
- `docs/README.md` — documentation index.
- Feature specifications — expensive-to-rediscover product behavior.

`docs/README.md` classifies every document as current V2 guidance, a behavior
reference awaiting V2 adoption, or historical V1 evidence. A historical
document never overrides the four owners above.

Update the owning document instead of adding a new summary. Do not commit
one-off audits, plans, handoff reports, generated receipts, scratch notes, or
dated status files. `MASTER_PLAN.md` is an owner document, not a status file, and
is never deleted under this rule. `CLAUDE.md` is only a compatibility pointer to
this file.

When the user states something he wants from the product, record it in
`MASTER_PLAN.md` section 1 verbatim and dated, in the same session he says it —
one row per ask, before starting the work. A message carrying several asks gets
several rows. Do not paraphrase into a task title, and do not delete a row to
mark it finished; change its status instead.

Before building against a statement, check whether it contradicts an earlier one.
If it does, ask the user which wins rather than picking the newer, the older, or
a blend, and record the answer as a new dated row. Ask the same way when a
statement is too ambiguous to build from. Unresolved contradictions live in
`MASTER_PLAN.md` section 4 and block work on the area they affect.

- A merge to `main` that changes product behavior is not finished until the
  installed Windows desktop app is rebuilt and reinstalled
  (`scripts/build_windows_desktop.ps1`), so the owner is never testing a stale
  bundle. Coordinate across sessions so exactly one rebuild runs per batch.

## Handoff

Finish with the user-visible outcome, files changed, exact verification
commands/results, whether live services or user data changed, and any remaining
deployment boundary. Leave the checkout clean unless asked otherwise.
