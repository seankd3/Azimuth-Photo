# RELEASE_PLAN — Azimuth Photo 1.0 (2026-07-12)

The bar: **the ultimate self-hosted photo app** — Google Photos effortlessness + Lightroom
Classic instincts, local truth, measurably-improving archive — and *release ready*: a
stranger downloads it, it works, nothing embarrasses us. This is the master backlog; each
workstream ships as a lane with its own work order. Lane claims live in
`~/Projects/photo-archive/LANES.md` (untracked) — check it before tasking a lane.

## Workstreams

### A. Ship vehicle — DISTRIBUTION_SPEC v1 *(in flight — other orchestrator: pa-rel-export/dist lane + wizard already merged)*
Installers, standalone-first, first-run wizard, pairing, guided remote access. Remaining
after current wave: PyInstaller onedir CI builds, Docker image publish, Tauri sidecar bundle.

### B. Import — IMPORT_SPEC v1 *(backend LANDED on wt-lane-b, pending review+merge; canvas-view UI reserved for Fable)*

### C. Release engineering & open-source hygiene *(wave 1, worktree pa-releng / branch releng)*
LICENSE (AGPL-3.0 — the Immich/PhotoPrism convention; protects the open-source ethos; Sean
can veto), CONTRIBUTING.md, SECURITY.md, GitHub Actions CI (unit suite on push/PR, Linux;
JS syntax + compile checks), versioning (VERSION file + CHANGELOG.md, keep-a-changelog),
issue/PR templates, .github/FUNDING off, README release-quality pass (badges, install
pointer, honest screenshots plan).

### D. Hardening & security *(wave 1, worktree pa-harden / branch harden; Grok second-opinion sweeps in parallel, read-only)*
Adversarial audit + fixes with tests: path traversal on every path-taking route (browse,
thumbs, sources, export), share-link auth + token lifecycle, origin guard coverage,
satellite/hub auth posture, SQL injection surface, oversized/malformed uploads, symlink
escapes, zip-slip in any archive handling, DoS-ish (unbounded queries, missing limits),
secrets in logs. Every fix lands with a regression test.

### E. Performance vs the speed covenant *(wave 2)*
Extend PERF_BUDGETS: grid payloads at 47k, scope-switch p95, thumb latency cold/warm,
boot time, worker contention on slow disks. Bench first, fix top offenders, budgets as
standing tests.

### F. Data safety *(partially in flight — other orchestrator: pa-audit integrity/bit-rot lane, pa-rel-history checksum-verified card ingest)*
Remaining: backup restore drill test, trash retention audit, catalog corruption
recovery path documented + tested.

### G. Polish & love *(continuous; UI = Fable only, copy/CSS grind = Grok)*
Empty states, error copy, keyboard affordances everywhere, first-five-minutes experience,
Notion-level detail. Never delegated to Codex.

### H. Azimuth rename plumbing — RENAME_PLAN.md phases 1–3 *(queued behind ALL lanes)*

## Sequencing

Wave 1 (now): C on pa-releng, D on pa-harden, Grok sweeps (read-only, no lane). B backend
review+merge, then Fable builds the import canvas UI. A + F lanes continue under the other
orchestrator (pa-rel-export, pa-audit, pa-rel-history, pa-color).
Wave 2 (as lanes free): E, remaining F, then H once the board is clear, then release
candidate: `--full` green, fresh-install drill on all three artifacts, README screenshots,
tag v1.0.

## Rules for every lane
Scopes are disjoint by file area — a lane must not touch another workstream's files (see
its work order). All work in small atomic commits on the lane branch; acceptance =
`./scripts/photoarchive-check --unit` green plus workstream-specific proof; cross-model
review before merge to develop.
