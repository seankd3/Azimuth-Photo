# RC cut checklist — Azimuth Photo 1.0.0-rc.2 (release lane, 2026-07-16)

Run top to bottom on the cut day. Every box is a command or an observable, not a vibe.
Current blockers are marked; the cut cannot happen while any BLOCKED item stands.

## 0. Preconditions
- [ ] All lanes merged or explicitly deferred in LANES.md; no dirty claimed worktrees.
- [ ] DECISIONS section in LANES.md empty or explicitly deferred-with-owner.
- [ ] main/develop divergence reconciled (Sean call pending as of 07-16 — see
      azimuth-branch-divergence memory; prod runs main, everything above lands on develop).
- [ ] BLOCKED: GitHub Actions account ban lifted (github.com/settings) — CI + release.yml
      are dead until then; no tag until CI has run green at least once on develop.

## 1. Tree health (Linux, omarchy)
- [ ] cd ~/Projects/pa-develop/web && .venv/bin/python -m pytest -q -k "not playwright"
      -> 0 failed (baseline 07-16: ~1160 passed).
- [ ] ./scripts/photoarchive-check --unit green (the repo gate, includes JS checks).
- [ ] Bench suite: web/perf/bench.py within budgets; no regression vs the 07-16 baseline
      (Quality session holds verdict state — request it).

## 2. Tree health (Windows, XPS runner)
- [ ] cd photography/pa-winqa && git pull; venv per requirements.win-qa.lock;
      pytest -q -> 0 failed (baseline 07-16: 1116 passed incl. playwright).

## 3. Version + changelog
- [ ] VERSION == the tag you are about to cut (currently 1.0.0-rc.1; bump to rc.2 at cut).
- [ ] CHANGELOG.md: move Unreleased -> the new version with date; regenerate the merge-log
      appendix: git log --merges --oneline <last-tag>..develop.
- [ ] /api/version reports the same version from BOTH artifacts (frozen + Docker).

## 4. Fresh-install drills (all three artifacts)
- [ ] Linux frozen: python3.12 scripts/build_server.py -> launch with temp
      PHOTOARCHIVE_HOME -> / 200, /setup wizard, /api/version honest -> clean stop.
- [ ] Windows frozen: same drill on the XPS (proven 07-16; re-run at cut).
- [ ] Docker: docker build -> run with fresh /data volume -> same probes (proven 07-16).
- [ ] Tauri sidecar bundle: NOT YET DRILLED — either drill it or explicitly cut rc.2
      without the desktop shell and say so in the changelog.

## 5. Upgrade path
- [ ] Upgrade-from-main migration test: copy prod-shaped catalog (or the v0 fixture),
      boot the rc artifact against it -> migrations run with backup (v0 backup path is
      enforced), library intact, no data mutation before backup exists.
- [ ] Catalog time-machine restore drill: restore latest snapshot on a scratch copy.

## 6. Data-safety net
- [ ] Drill suite green (07-16 baseline: 109 passed): test_system_backups,
      test_migration_backup, test_trash*, test_sync_oplog/hub/mirror, test_import_staging.

## 7. Cut
- [ ] git tag v1.0.0-rc.2 on the verified develop sha; push tag (release.yml builds
      artifacts once Actions is unbanned).
- [ ] Attach the Windows + Linux onedir zips and compose file to the GitHub release if
      Actions is still down (manual fallback).
- [ ] Smoke the published artifacts one final time from a clean download.
