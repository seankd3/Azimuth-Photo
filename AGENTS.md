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
- `docs/TOPOLOGY.md` — portable runtime roles and storage architecture.
- `docs/development.md` — setup, edit map, and verification commands.
- `docs/CODEBASE_MAP.md` — module and route ownership.
- `docs/README.md` — documentation index.
- Feature specifications — expensive-to-rediscover product behavior.

Update the owning document instead of adding a new summary. Do not commit
one-off audits, plans, handoff reports, generated receipts, scratch notes, or
dated status files. `CLAUDE.md` is only a compatibility pointer to this file.

## Handoff

Finish with the user-visible outcome, files changed, exact verification
commands/results, whether live services or user data changed, and any remaining
deployment boundary. Leave the checkout clean unless asked otherwise.
