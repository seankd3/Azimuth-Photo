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

- GitHub `main` is the product source of truth.
- Runtime data never belongs inside a source checkout.
- Do not add personal usernames, home directories, private addresses, mount
  points, catalog paths, or secrets to tracked defaults or documentation.
- Product defaults must support a fresh standalone install. Hub and satellite
  deployments are selected through environment variables or launcher options.
- Retired checkouts and worktrees are preservation material, not development
  sources. Do not revive them unless the task explicitly calls for recovery.

## How to work

- Commit owned changes directly to `main` in small, atomic commits.
- Do not create branches, worktrees, lanes, or parallel editing sessions unless
  the user explicitly requests them.
- Never change a production checkout's branch or restart a long-running service
  as a side effect of development.
- Stage explicit paths. Never use `git add -A` in a shared or dirty tree.
- Preserve unrelated changes.
- Keep code modular and product-owned. Prefer a focused module over a shared
  abstraction with one caller.
- Build real behavior, not mockups or simulations.
- Keep implementation complexity and model plumbing out of the product UI.

## Code ownership

Use [`docs/CODEBASE_MAP.md`](docs/CODEBASE_MAP.md) for the detailed route map.

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
  already exist. Case behaviour is per-volume (the hub's exFAT archive is
  case-insensitive; an ext4 archive is not) — never normalise or guess casing.
- A photo row's `status` vocabulary is `kept`, `maybe`, and `trashed`, and
  nothing else. Startup rewrites every other value back to `kept`, so a repair
  that parks rows under an invented status silently undoes itself at the next
  restart. To take a row out of the library without deleting it, set
  `missing_at` (the file is not at that path) or exclude its source — both
  survive a restart.
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
- `AZIMUTH_SMOKE_MODE=1` is test-only. Never use it for a real hub or satellite.

## Simplicity

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
- Browser behavior: verify the actual workflow against an isolated server.
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
- `docs/CODEBASE_MAP.md` — module and route ownership.
- `docs/README.md` — documentation index.
- Feature specifications — expensive-to-rediscover product behavior.

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
