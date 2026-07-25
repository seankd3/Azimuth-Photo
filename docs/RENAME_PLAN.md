# RENAME_PLAN — finish the Azimuth Photo migration (2026-07-12)

Sean approved renaming EVERYTHING. Brand layer (UI strings, docs prose, PWA manifest,
launcher label, FastAPI title), bundle ids (`app.azimuthphoto.mobile`, `app.azimuthphoto.desktop`)
and the GitHub repo (`Sean-Kenneth-Doherty/azimuth-photo`, old URLs redirect) are DONE.
What remains is the plumbing. It is deliberately sequenced — do not start Phase 1 while any
lane/agent is running in `pa-*` worktrees (the import-backend lane on wt-lane-b as of writing).

## Current safe boundary (2026-07-15)

This lane updates only the visible brand: UI labels and titles, user-facing download
names, manifest/about copy, documentation prose, and comments. It deliberately does
not alter any persisted or operational identifier. That separation keeps the live hub,
satellite, and installed desktop app attached to their existing data while the product
is presented as Azimuth Photo.

The following identifiers remain exactly as they are until the corresponding phase
below has a tested migration and a scheduled rollout:

- `PHOTOARCHIVE_*` environment variables and scripts named `photoarchive-*`:
  existing deploy, test, and satellite launch environments depend on them.
- `photoarchive.db`, `photoarchive-*.db.gz`, restore staging names, backup regexes,
  and pre-migration retention: restore safety and backup discovery require the old
  names to remain mutually compatible.
- runtime data/cache locations, including `APP_DIR_NAME="photoArchive"`, XDG/cache
  roots, `C:\PhotoArchiveField`, `Pictures/photoArchive Exports|Imports`, cache
  markers, LocalStorage keys, and the in-repo runtime layout: changing one without
  fallback can strand catalog data or make existing caches appear empty.
- service/process/container/discovery names such as `photoarchive.service`, Docker
  image/container/user names, `_photoarchive._tcp`, worker/thread names, MIME
  boundaries, sync trailer/header names, and the desktop tray/config identifiers:
  these are operational protocols or install-state keys, not visible brand copy.
- database/table/column/setting keys and internal compatibility identifiers including
  `app.state.photoarchive_shell`, `__photoArchiveCullBrief`, `Theme.PhotoArchive`,
  and `PhotoArchiveTheme`: callers, stored state, or platform resources reference them.

## Phase 1 — code-level renames, with back-compat (Codex-able, one branch, after lanes land)

Everything ships with a fallback so a half-migrated machine still boots:

1. Env vars: `PHOTOARCHIVE_*` → `AZIMUTH_*`. Add one centralized resolver so every
   setting reads `AZIMUTH_*` first and falls back to `PHOTOARCHIVE_*`, emitting a
   one-line deprecation log only when the old spelling wins. Keep both spellings
   accepted for at least one release; test precedence, fallback, and no-value behavior
   before updating scripts/tests/docs to prefer the new spelling.
2. Scripts: `scripts/photoarchive-*` → `scripts/azimuth-*` (azimuth-check, azimuth-server,
   azimuth-android-build, azimuth-android-e2e). Leave old names as thin exec-forwarding
   shims for one release.
3. Catalog DB: `web/photoarchive.db` → `web/azimuth.db`. On startup, if old exists and new
   doesn't → rename in place (plus `-wal`/`-shm`), with a rollback-safe fallback to the old
   path if the rename cannot complete. The same migration must cover satellite data-dir
   naming. Do not rename `photoarchive-*.db.gz` backups in place: continue discovering
   their old prefix during the transition, and only create/recognize a new prefix after
   retention, restore, and premigrate checks support both forms.
4. On-disk user dirs: `APP_DIR_NAME "photoArchive"` → "Azimuth Photo";
   `Pictures/photoArchive Exports|Imports` → `Pictures/Azimuth Exports|Imports`. Startup
   migration: rename old dir if present, else fall back to reading old path. NEVER copy —
   rename only; never touch originals trees (RAWS/, Video/ are brand-neutral).
5. Internal identifiers: `__photoArchiveCullBrief`, `Theme.PhotoArchive`, rust worker names,
   Kotlin `ArchiveApi` naming stays semantic — rename only where "photoarchive" literally
   appears; no back-compat needed (invisible).
6. Acceptance: full unit suite green under BOTH env-var spellings; a temp-dir test proving
   db+dirs migrate on first boot and a second boot is a no-op.

## Phase 2 — omarchy infra (careful hands, not a lane; service restart is deliberate)

1. Confirm no agent processes in `~/Projects/photo-archive` or `pa-*` (`pgrep -af codex; tmux ls`).
2. `mv ~/Projects/photo-archive ~/Projects/azimuth-photo`; `mv pa-develop az-develop`;
   `mv pa-lane-{a..d} az-lane-{a..d}`; then from azimuth-photo: `git worktree repair
   ../az-develop ../az-lane-a ../az-lane-b ../az-lane-c ../az-lane-d`. Re-check venv
   symlinks inside lanes (they point into pa-develop — recreate).
3. systemd: add `azimuth.service` with new WorkingDirectory/ExecStart paths + `AZIMUTH_*`
   env aliases, verify it against the existing catalog, then switch from
   `photoarchive.service` in one announced restart. Keep the old unit disabled but
   installed for one release as a rollback target; only then remove it. This is the one
   allowed production blip.
4. mobile-app-toolkit: rename the app entry `photo-archive` → `azimuth-photo` (commands
   become `mobile-app azimuth-photo …`); update its stored paths.
5. `deploy.sh`: paths + service name; verify a deploy end-to-end.
6. tailscale serve: unchanged (port proxy), but verify /m loads after restart.
7. Update TOPOLOGY.md, FIELD_README.md, CLAUDE.md/AGENTS.md banners → replace "frozen"
   notes with the new canonical names.

## Phase 3 — XPS (after Phase 2, since its origin points at the omarchy path)

1. Re-point clone origin: `omarchy:~/Projects/azimuth-photo`. Rename dir
   `photography\photoarchive-field` → `photography\azimuth-field`.
2. Data dir `C:\PhotoArchiveField` → `C:\AzimuthField`; update satellite launch
   scripts/env (AZIMUTH_* spellings) and the Tauri shell's spawned env; rebuild the shell
   (productName/identifier already renamed).
3. Pixel: uninstall old app (`app.photoarchive.mobile` is orphaned), install the new APK.

## Explicitly NOT renamed

Claude auto-memory slugs (internal), git history, `elo_ratings.json`/`blacklist.json`
artifacts, the `photoarchive` tag on old backups. Old GitHub URLs keep redirecting.

## Status 2026-07-23

Phase 1 **started in tree**: `core/env_names.py` (AZIMUTH_* first, PHOTOARCHIVE_* fallback), `core/rebrand_migrate.py` (catalog + Pictures dir renames), runtime_paths defaults use Azimuth Photo / azimuthphoto / azimuth.db with legacy fallbacks, `scripts/azimuth-*` scripts + `photoarchive-*` shims. Phase 2 (omarchy path + systemd) and Phase 3 (XPS field paths) still pending.
