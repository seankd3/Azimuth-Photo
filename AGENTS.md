# Azimuth Photo agent guide

This is the authoritative operating contract for agents working in this
repository. Keep it short, current, and consistent with the linked docs. Do not
create another competing instruction file.

## Start here

Before editing or launching anything:

1. Run `git status --short --branch` and `git worktree list`.
2. Confirm this is one of the canonical checkouts listed in
   [`docs/TOPOLOGY.md`](docs/TOPOLOGY.md).
3. Read [`docs/development.md`](docs/development.md) and the specification for
   the product surface being changed.
4. Check the relevant live status before changing runtime behavior. The XPS
   satellite uses `http://127.0.0.1:8010`; the Omarchy hub uses port `8000`.
5. State the intended scope and keep unrelated user or agent changes untouched.

If the checkout, branch, runtime, or deployment truth differs from the
documentation, stop and reconcile the discrepancy before coding.

## Canonical truth

- Repository: `Sean-Kenneth-Doherty/azimuth-photo`
- Branch: `main`
- XPS checkout:
  `C:\Users\smast\OneDrive\Desktop\Projects\photography\azimuth-photo`
- Omarchy checkout: `/home/sean/Projects/azimuth-photo`
- XPS runtime: `C:\Azimuth Photo`
- Runtime data never belongs inside a source checkout.

Retired checkouts, worktrees, installations, consolidation staging, and archive
bundles are preservation material—not development sources. Never revive or copy
code from them unless the task explicitly calls for historical recovery.

The current production cutover boundary is documented in
[`docs/TOPOLOGY.md`](docs/TOPOLOGY.md). Do not infer deployment state from a
clean checkout.

## How to work

- Commit owned changes directly to `main` in small, atomic commits.
- Do not create branches, worktrees, lanes, or parallel editing sessions unless
  the user explicitly requests them.
- Never change a production checkout's branch or restart a long-running service
  as a side effect of development.
- Stage explicit paths. Never use `git add -A` in a shared or previously dirty
  tree.
- Preserve unrelated changes. If the requested file overlaps unknown work,
  inspect the diff and work around it or ask before overwriting.
- Keep code modular and product-owned. Prefer a small focused module over a
  shared abstraction with one caller.
- Build real behavior, not mockups or simulations.
- Keep implementation complexity, model choice, and confidence plumbing out of
  the product UI.

## Code ownership

Use [`docs/CODEBASE_MAP.md`](docs/CODEBASE_MAP.md) for the detailed route map.
The short version:

- `web/features/<surface>/` owns backend behavior and routes.
- `web/data/repositories/` owns SQL and persistent queries.
- `web/core/` owns application wiring and genuinely cross-cutting runtime code.
- `web/static/js/desktop/` owns focused desktop ES modules.
- `web/static/js/mobile/` owns the PWA; desktop and mobile share backend writes.
- `web/thumbnails/` owns preview generation and cache maintenance.
- `desktop/` is the Windows shell; `android/` is the native Android client.
- `scripts/` contains repeatable developer/operator entry points only.

Keep routes thin, SQL in repositories, and UI state close to the surface that
owns it. Preserve public routes and response shapes unless the task explicitly
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
- The 20 TB expansion drive is a slow durable tier. Serialize bulk reads and
  keep interactive catalogs and caches on SSD.
- Never run tests against the real catalog unless the task explicitly requires
  a read-only live check. Use isolated test homes and fixtures for writes.
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
- Focused backend/frontend change: quick checks plus the owning test module or
  named area.
- Browser behavior: verify the actual changed workflow against an isolated
  server; a healthy API alone is not UI proof.
- Sync, deletion, imports, recovery, or catalog changes: add focused safety
  tests and verify the final persisted state.
- Deployment: verify the listener, process working directory, health endpoint,
  catalog path, cache path, and rollback route.

Report exact commands and results. Never call the suite green when failures or
errors remain, and never claim a UI reproduction or retest that was not
actually performed.

## Documentation hygiene

Each durable fact has one owner:

- `AGENTS.md` — agent operating rules and safety boundaries.
- `docs/TOPOLOGY.md` — machines, canonical paths, runtime storage, and cutover
  truth.
- `docs/development.md` — setup, edit map, and verification commands.
- `docs/CODEBASE_MAP.md` — module and route ownership.
- `docs/README.md` — documentation index.
- Feature specifications — expensive-to-rediscover product behavior.

Update the owning document instead of adding a new summary. Do not commit
one-off audits, task plans, handoff reports, generated receipts, scratch notes,
or dated status files. Put temporary evidence outside the repository; preserve
only durable conclusions in the appropriate existing doc.

`CLAUDE.md` is a compatibility pointer to this file. It must not duplicate
project rules.

## Handoff

Finish with:

- the user-visible outcome;
- files changed;
- exact verification commands and results;
- whether live services or user data changed;
- any remaining failure, deployment boundary, or deliberately deferred work.

Leave the checkout clean unless the user explicitly asked for an uncommitted
handoff.
