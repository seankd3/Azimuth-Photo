# RENAME_PLAN — finish the Azimuth Photo migration (2026-07-12)

Sean approved renaming EVERYTHING. Brand layer (UI strings, docs prose, PWA manifest,
launcher label, FastAPI title), bundle ids (`app.azimuthphoto.mobile`, `app.azimuthphoto.desktop`)
and the GitHub repo (`Sean-Kenneth-Doherty/azimuth-photo`, old URLs redirect) are DONE.
What remains is the plumbing. It is deliberately sequenced — do not start Phase 1 while any
lane/agent is running in `pa-*` worktrees (the import-backend lane on wt-lane-b as of writing).

## Phase 1 — code-level renames, with back-compat (Codex-able, one branch, after lanes land)

Everything ships with a fallback so a half-migrated machine still boots:

1. Env vars: `PHOTOARCHIVE_*` → `AZIMUTH_*`. settings.py reads AZIMUTH_* first, falls back
   to PHOTOARCHIVE_* with a one-line deprecation log. Update all scripts/tests/docs to the
   new names.
2. Scripts: `scripts/photoarchive-*` → `scripts/azimuth-*` (azimuth-check, azimuth-server,
   azimuth-android-build, azimuth-android-e2e). Leave old names as thin exec-forwarding
   shims for one release.
3. Catalog DB: `web/photoarchive.db` → `web/azimuth.db`. On startup, if old exists and new
   doesn't → rename in place (plus -wal/-shm). Same for satellite data dir naming.
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
3. systemd: `photoarchive.service` → `azimuth.service` (new WorkingDirectory/ExecStart
   paths + AZIMUTH_* env). `systemctl --user disable --now photoarchive && enable --now
   azimuth`. One restart, announced — this is the one allowed prod blip.
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
