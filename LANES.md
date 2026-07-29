# LANES.md — live lane claims for the Azimuth Photo release program (untracked)
CHARTER: docs/SESSION_LANES.md defines which SESSION owns which territory. Read it with this file.

## Active claims (2026-07-25)

| Worktree / branch | Work | Owner | Status |
|---|---|---|---|
| pa-sprint-integration / sprint-integration | Controlled sprint integration: reconcile `main` into current `develop`, preserve the Azimuth check shim + nonblocking search fallback + both conflict test classes, fix the three pre-existing Ruff errors separately, then merge completed quality branches in reviewed cohorts with receipts and gates; never merge this lane into `develop` without coordinator review | Codex sprint-integration lane | active — claimed 2026-07-25; integration worktree must start at `52742f941`; production main, runtime data, services, branches, and existing worktrees are read-only |
| pa-deliver-load-resilience / deliver-load-resilience | Owner-side Deliver overlay partial-failure isolation and recovery: independent Private link, Client gallery, and Website loading; truthful per-tab retry; private-link management survives client-picks failure; stale reads ignored after close/tab changes | Codex deliver load-resilience lane | merge-ready at `9e8802643` — focused Node/UI contracts and isolated browser proof green; `web/static/js/desktop/panel.js`, one focused Deliver state/loading module, and focused desktop behavioral tests only; excludes public gallery template/behavior, export backend/UI, publishing job semantics, mobile/Android, sync, recovery, performance, Windows install, runtime data, and service control |
| pa-windows-install / windows-install | Windows install-to-library slice: self-contained Azimuth desktop shell, unsigned NSIS packaging path, and local/mapped-drive/NAS folder onboarding | Codex windows-install lane | implementation + real Windows proof complete through `f8da9949e`; unsigned NSIS install, fresh launch, local folder, mapped `Z:` NAS/UNC, quit, and relaunch all green — ready for controlled integration; excludes client/server sync, signing, public release automation, and frozen identifier renames |
| pa-client-server-hardening / client-server-hardening | Client/server hardening: lightweight sync heartbeat first, then measured workload coordination fixes | Codex root | active |
| pa-health-clarity / health-clarity | Desktop System and Library Health presentation grammar: calm, prioritized, human states for background work, disconnected sources, low storage, and recovery-needed truth | Codex health-clarity lane | READY in `aa7dfffb0` — table-driven Node proof, focused health contracts, and isolated System Health Playwright smoke green; presentation modules and the existing health-smoke assertion only; excludes backend/system recovery, scanner/catalog, thumbnails/cache, sync/client-server, Windows/Tauri, service control, journey tests, and unrelated desktop UI |
| pa-mobile-field-resilience / mobile-field-resilience | Mobile field resilience: contextual load retry, honest queued-write states and recoverable terminal failures, exact offline-cache outcomes, and safe reconnect draining | Codex mobile field-resilience lane | merge-ready at `462997293` — focused queue/offline/browser proofs green; `web/static/js/mobile/write_queue.js`, `offline.js`, `api.js`, narrowly necessary mobile load-state callers, `web/templates/mobile.html`, `web/static/mobile.css`, and focused Node/mobile contract tests only; excludes backend/sync worker/heartbeat, runtime/service control, Android, desktop, public gallery, shared API contracts, and unrelated mobile features |
| pa-recovery-truth / recovery-truth | Catalog recovery truth: honest snapshot/vault status plus verified restore drill | Codex recovery-truth lane | active — system recovery modules and focused tests/docs only; excludes UI, scanner/cache, sync, desktop packaging, and release automation |
| pa-preview-honesty / preview-honesty | Background preview-work truth: honest active/blocked/idle/stalled state, mathematically honest throughput/ETA, preview-first behavior under disk pressure, and read-only interactive evidence | Codex preview-performance lane | active — web/thumbnails/**, web/features/cache/**, web/perf/interactive.py, scripts/bench_interactive.py, focused tests, docs/PERF_BUDGETS.md only; excludes scanner/catalog, UI, desktop, sync, backups/system-health, and shared core |
| pa-perf-filter-options / perf-filter-options | Filter-options performance: measure and remove the real 885 ms live regression without changing UI or hiding correctness work | Codex filter-options performance lane | active — `web/data/repositories/filter_options.py`, strictly necessary cache/routes, focused tests and bench fixtures only; excludes thumbnails/cache, scanner/catalog safety, recovery, desktop/UI, Windows packaging, sync, and shared core |
| pa-scan-safety / scan-safety | Scan interruption safety: preserve catalog truth on disconnected, permission-failed, or interrupted scans; focused scanner/catalog tests | Codex scan-safety lane | active - web/scanner.py, web/data/repositories/catalog.py, and focused scan/catalog tests only; excludes UI, cache/thumbnails, sync, desktop packaging, and recovery/health |
| pa-desktop-journey-proof / desktop-journey-proof | Desktop browser journey proof: real first-library, browse/cull, keyboard/Esc, recovery/empty, inspect/handoff workflows | Codex desktop-proof lane | active — browser/Playwright journey tests and fixture helpers only; excludes desktop UI source, core, Tauri, runtime, scanner/catalog/cache, mobile, sync, and docs |
| pa-perf-people-map / perf-people-map | People and Map query performance: preserve complete results while taking large-catalog warm p95 from ~241/~239 ms to under 100 ms | Codex People/Map performance lane | active — web/data/repositories/people.py, web/data/repositories/rankings.py Map query/cache paths, web/features/people/routes.py, focused tests and benchmark fixtures/docs only; excludes UI, thumbnails/cache, scanner/catalog, filters, desktop/Tauri/Windows, sync, recovery, import, and unrelated route contracts |
| pa-client-gallery-resilience / client-gallery-resilience | Public client gallery resilience and accessibility: honest image/favorite submission failures, retry, lightbox focus return, and behavioral proof | Codex client-gallery resilience lane | active — web/templates/share_gallery.html plus scoped gallery behavior/CSS and focused share/gallery tests only; excludes desktop Deliver owner UI, publishing backend contracts, mobile, exports, docs, and all other lanes |
| pa-first-use-boot / first-use-boot | First-use launch performance: remove client-update bundle housekeeping from boot-to-first-library critical path, with isolated boot benchmark and truthful updater/version regression proof | Codex first-use performance lane | active ? web/app.py, web/features/system/client_bundle.py, focused client-bundle/startup tests, and existing standing benchmark files only; excludes desktop/Develop source, Tauri/Windows packaging, scanner/catalog/cache, sync, recovery, and other perf lanes |

Check this file BEFORE creating a worktree, tasking an agent, or merging to develop.
Add a row when you claim a lane; mark it done when merged; keep scopes disjoint by file
area. Master backlog: docs/RELEASE_PLAN.md. Updated 2026-07-12 ~20:30 by dev17.

| Worktree / branch | Work | Owner (orchestrator) | Status |
|---|---|---|---|
| pa-develop / develop | integration branch | shared — merge only after cross-model review | active |
| pa-lane-b / freeup | Free-up-space satellite verb (hash-gated local original deletion) | Fable session A | READY: branch freeup eb51b3a5 pushed, Fable+Grok reviewed, 3 data-loss fixes, full suite 1134 green, merges CLEAN into develop — merge when develop quiescent |
| pa-releng / releng | release engineering: LICENSE, CI, CHANGELOG, templates (workstream C) | Fable session A | MERGED to develop (b8edefb5, 07-13) — lane free |
| pa-harden / harden | security hardening audit+fixes (workstream D) | Fable session A | MERGED (8efe30bb + harden-scratch ff671f71); 3 CRITICAL findings open in pa-harden/FINDINGS-hardening.md |
| pa-lane-a / wt-lane-a | DISTRIBUTION_SPEC standalone mode + frozen build (orders /tmp/rel13/w-dist.md) | orchestrator rel13 (tmux rel-dist) | running — NOTE: lane-a had an unmerged wizard-era docs commit; reconcile at review |
| pa-lane-c / wt-lane-c | card ingest backend, CARD_IMPORT_SPEC (orders /tmp/rel13/w-card.md) | orchestrator rel13 (tmux rel-card) | running |
| pa-lane-d / wt-lane-d | trust pillar: process-version safety + integrity audit (orders /tmp/rel13/w-trust.md) | orchestrator rel13 (tmux rel-trust) | running — WARNING: lane-d had DIRTY develop-module files before launch; check diff provenance at review |
| pa-audit / audit | standing perf-budget CI, MASTER_PLAN Pillar 7 (orders /tmp/dev17/w-perfci.md) | orchestrator dev17 (tmux dev17-perfci) | running |
| pa-rel-history / rel-history | phone PWA share-target + upload queue, Pillar 5 (orders /tmp/dev17/w-phone.md) | orchestrator dev17 (tmux dev17-phone) | running |
| pa-rel-export / rel-export | (dev17 dist lane killed as duplicate of rel13 dist; worktree clean) | — | free |
| pa-color / color | (dev17 process-version lane killed as duplicate of rel13 trust; worktree clean) | — | free |
| (no worktree, read-only vs pa-develop) | Grok audit sweeps ×6: backend bugs, security, frontend correctness, test gaps, docs-vs-code, perf — outputs /tmp/dev17/out-g-*.md | orchestrator dev17 (tmux dev17-g-*) | perf/tests/sec/front DONE; bugs/docs running |
| pa-lrmigrate / lrmigrate | LR catalog migration backend+UI (orders /tmp/dev16/) | orchestrator dev16 | running |
| pa-datasafety / datasafety | data-safety drills: restore drill, trash retention, corruption recovery (workstream F remainder) | Fable session B (Windows/android) | running (tmux datasafety) |
| pa-phone-folder / sync-folder | phone folder sync | (pre-existing) | check before touching |

| pa-harden / ownerauth | AUTH_SPEC v1 owner authentication (3 open criticals) | Fable session A | MERGED to develop (e340a84c) — Grok-reviewed, device-window fix landed; lane free |

Duplication post-mortem: dev17 and rel13 independently derived the same four lanes 2 min
apart on 2026-07-12; dev17 killed its copies (dist/card/procver/integrity) before any file
was written. Claim here FIRST, then launch.

Rules:
- Never touch ~/Projects/photo-archive (PROD — systemd serves live from it).
- web/.venv is a shared symlink — never pip install into it.
- Rebrand identifier renames are FROZEN until the board clears (docs/RENAME_PLAN.md).
- UI design work is never delegated to Codex/Grok (docs/RELEASE_PLAN.md workstream G).
- Detached codex AND cursor-agent need tmux (die without pty); cursor-agent also needs --force.
- Grok audit findings in /tmp/dev17/out-g-*.md are claims until Fable-verified; perf top-2 verified true.

## dev17 update 2026-07-12 ~20:35
- FIXED + committed to develop (26ce3906): desktop shell dead — brace drops in api.js/drawer.js (P0-1). All web/static/js now node --check clean.
- Verified audit digest at ~/Projects/photo-archive/RELEASE_AUDIT_dev17.md — 3 P0 blockers (desktop-boot FIXED, unauth publish_hook RCE → harden, empty-folder-marks-missing → scanfix), P1 data-loss + perf lists routed to owning lanes. Full Grok reports in /tmp/dev17/out-g-*.md.
- pa-scanfix / scanfix (NEW worktree off develop): Codex sol lane fixing P0-3 empty-folder scan data-loss + regression tests. tmux dev17-scanfix.
- pa-audit: dev17 perf-budget CI lane DONE (uncommitted, 12 perf tests green) — awaiting review. NOTE: pa-audit was also listed for integrity/checksums; perf-budget work landed there instead — reconcile owner.
- pa-rel-history: dev17 phone PWA share-target upload lane DONE (uncommitted, 8 tests green) — awaiting review.
- HEADS UP: full pytest NOT green on develop — pre-existing red test_cache_status.py::...test_search_and_people_start_previews_dependency (mock-ordering leak), hit by multiple lanes. Fix before RC.
- Routing: P1 sync data-loss bugs (oplog cursor / keyword LWW / metadata-push clean) → sync-owning lane ONLY; empty-trash → pa-datasafety; publish-revoke → pa-rel-export/publish; LRCAT-clobbers-user-develop → pa-lrmigrate.

## dev17 heads-up for dev16 LR lanes 2026-07-12 ~20:40
develop moved under you: commit 26ce3906 fixed a build-breaking brace drop in
web/static/js/desktop/drawer.js (renderConnectServer) — desktop shell would not
parse/boot without it. Your uncommitted drawer.js (setup wizard) needs a rebase
onto current develop; the change is a 2-line brace insert, trivial conflict at most.
Also committed to develop: 4a35e572 (empty-scan library-wipe guard, backend).
Full pytest not green on develop — see pre-existing reds noted above.

## Board state 2026-07-15 ~18:00 (verified live by dev17 Fable session)
Supersedes the table above — that table is from 07-12 and every row is stale.

- All rel13/dev16/dev17 lanes MERGED: dist, card, trust, lrmigrate, perfci, phone,
  harden(+ownerauth AUTH_SPEC), releng, datasafety, scanfix. Worktrees removed.
- One Surface fix-wave MERGED to develop: hubhealth, trashux, mobile3s, unify,
  fgmedia, fgclient, handshake, safedates, winqa.
- `bench` and `scenarios1` branches: patch-identical content already in develop
  (git cherry empty) — refs are stale, safe to delete.
- PROD (photo-archive main, systemd :8000): restarted 07-15 10:01 CDT with hubhealth
  fix; bound tailnet-only (100.102.150.104:8000 — hardening live); healthy, 200/13ms,
  no OOM/lock storms in logs. main is 19 commits behind develop (winqa wave).
- Active worktrees now: pa-color(color-science), pa-datasafety(visualfix),
  pa-harden(ownerauth), pa-lane-a(scenarios1), pa-lane-b, pa-lane-c(p0fix),
  pa-lane-d(bench), pa-mobile(mobile-scratch), pa-polish(bugfix), pa-qa(qa-harness),
  pa-develop. All 0-dirty except where noted by their owners.
- Codex weekly cap exhausted until Jul 21 — executor lanes are Grok/cursor-agent
  or Fable-direct until then.
- IN FLIGHT (dev17, 07-15): full pytest sweep on pa-develop to establish the RC
  green/red baseline — results will be appended here.
- CLAIM 07-15 ~18:05 dev17: Q5 coverage gap matrix (grok sweep, read-only vs pa-develop)
  + Q6 audits for publishing/shares, versioning/snapshots, sync/oplog ONLY (imports →
  Azimuth Import session, editing → Azimuth Editing session). Outputs /tmp/q17/.
  Also running: full pytest RC baseline on pa-develop.
- RC BASELINE 07-15 ~18:20 (dev17): full pytest on pa-develop GREEN — 934 passed,
  2 skipped, 389 subtests, 206s. All previously-flagged reds (cache_status mock leak,
  LR collection-schema, People backlog) are fixed. develop→main merge is unblocked
  from the test side; coordinate the prod deploy before pulling it.
- q17 audits DONE 07-15 ~19:00: digest at RELEASE_AUDIT_q17.md (2 P0 VERIFIED, 1 P0
  probable, ~15 P1, coverage matrix 35% uncovered). CLAIM dev17: pa-vcfix worktree
  (NEW, off develop) — VC/trash data-loss P0 + vc_of stack-identity cluster.

## Fable session B (Windows/XPS) update 2026-07-15 ~18:45
- CLAIMED: Q1 winqa execution on the REAL Windows satellite (QUALITY_BAR.md) — fresh XPS clone, qa_windows_smoke + full pytest + qa.ps1 harness; findings will land as fix lanes. Only this session can run the Windows matrix.
- FIXED: docs/WINDOWS_QA.md was committed with unresolved conflict markers (main<->origin/winqa); resolved.
- 07-15 ~19:45: Codex weekly cap RESET (Sean) — Codex lanes available again.
- CLAIM dev17: pa-pubfix (NEW off develop) — publish/share audit fixes (q17 publish
  P0 cache-control + P1 gallery revoke/orphan prune/job gating/zip arcname).
- CLAIM dev17: pa-scenarios (NEW off develop) — Q5 top-25 missing-scenario tests
  from /tmp/q17/out-g-coverage.md.
- CLAIM dev17 (pending vcfix merge): pa-vcid — vc_of identity cluster (stack join
  resolve, version builder VC exclusion, delete_virtual_copy via prepare path).

## UX-elegance wave 2026-07-15 (Fable session, Windows) — claimed ~22:10
| pa-ux-develop / ux-develop | Develop module UX fixes (proof-button bug, undo coalescing, wheel guard, autosave surfacing, WB picker leak, copy-all shortcut, panel persistence) — files: web/static/js/desktop/develop/** only | Fable session UX (Windows) | running (tmux ux-develop) |
| pa-ux-mobile / ux-mobile | Mobile UX fixes (swipe-down dismiss, favorite identity, viewer rating, tab re-tap, refine empty state, copy, person edit, collections-as-scope stretch) — files: web/static/js/mobile/**, mobile.css, mobile.html | Fable session UX (Windows) | running (tmux ux-mobile) |
| pa-ux-desktop / ux-desktop | Desktop shell UX fixes (Ctrl+K/Z lens guards, Escape/d in develop via keyboard.js, flag keeps selection, pick=flag glyph, Elo naming) — files: web/static/js/desktop/* EXCLUDING develop/ | Fable session UX (Windows) | running (tmux ux-desktop) |

## Fable session B (Windows/XPS) — SPEED PROGRAM 2026-07-16 ~00:15
- CLAIMED pa-rel-export / perf-suggestions: kill 38s cold suggestions (persist shoot hints + theme index). Orders /tmp/w-sugg.md. Codex lane (tmux perf-sugg).
- CLAIMED pa-color / perf-people-map: people 27s/3.3s + map 12.7s (profile->index/query fixes). Orders /tmp/w-peoplemap.md. Codex lane (tmux perf-people).
- KPI system live: scripts/bench.py --trend, bench-runs/ committed, nightly user timer azimuth-bench 05:30.

## UX-elegance wave update ~22:15
- pa-ux-develop / ux-develop: MERGED to develop (34eed557) — lane free
- pa-ux-desktop / ux-desktop: MERGED to develop (d3fe0e47, incl. Fable fixup 17ae9c84) — lane free
- pa-ux-mobile / ux-mobile: MERGED to develop (35a97d38) — lane free; stretch (collections-as-scope) blocked on backend ids-scope in /api/rankings
- develop pushed to origin (c4d6fb7a..35a97d38)
- Wave 2 launching: ux2-findability (filters/omnibox/state + filter-options backend), ux2-perf (grid/loupe/events/lenses + mobile timeline/viewer feel layer), ux2-canvas (import/people/trash/duplicates/cull traps), ux2-android (android-app branch: publish-confirm + smalls)
- ux2 lanes claimed ~22:25: pa-ux-desktop/ux2-findability, pa-ux-mobile/ux2-perf, pa-ux-develop/ux2-canvas, pa-ux-android/ux2-android (Fable session UX, Windows) — running (tmux ux2-*)
- 07-15 ~23:15 dev17: MERGED to develop: vcfix (VC family trash, P0-1), scenarios-q5
  (16 coverage gates), pubfix (publish/share audit fixes). Fixed forward on develop:
  POST /api/image/{id}/rating now EXISTS (protective _lr_rating merge + oplog
  append_rating) — mobile star UI (2db3b325) was calling a nonexistent route; also
  restored writeRating in mobile api.js (scenarios lane had removed it as dead code
  concurrent with the mobile session adding the UI).
- KNOWN RED on develop (NOT dev17): test_mobile_contracts test_loupe_swipes_cull_
  without_replacing_navigation expects cullSwipe() which viewer.js does not define
  (has favoriteSwipe) — pre-existing before dev17 merges, belongs to the mobile
  session (e4077311/2db3b325 era). Please reconcile test vs viewer.
- CLAIM dev17: pa-vcid running (vc_of identity cluster, Codex terra high, tmux q17-vcid)
- CLAIM dev17: pa-syncfix running (oplog soft-fail retry ledger, verified sync P0, Codex sol high, tmux q17-syncfix)
- CLAIM quality: Quality program (post-merge review sweeps, bug-class hunts, doctrine audits, merge bar) — owner: Fable session Quality (XPS, 2026-07-15). Read-only vs pa-develop; fixes via fresh claimed worktrees only.
- ux2 web lanes MERGED to develop (eff02e08 findability, 485ada0d perf, 204c6a10 canvas) + pushed; Fable fixups: swipe-settle fallback 2f59f458. ux2-android under review on pa-ux-android (targets android-app, NOT develop). Canvas items 1+7 (import_canvas scan-survival, stage peek) skipped — import_canvas.js not on develop (Azimuth Import session owns it); re-queue after that session merges.
- quality: qfix-back MERGED to develop (696fbf75) — lane free
- quality: qfix-front MERGED to develop (5dd3b404) — lane free
- quality: qfix-mobile MERGED to develop (643b6e8e) — lane free
- 07-16 dev17: syncfix MERGED+PUSHED (oplog pending-retry ledger, sync P0 closed).
  Rating round-trip fixed (GET rating + sheet fetch + click-time target). History
  rail snapshots pinned + fresh. New-UI sweep digest /tmp/q17-keep/out-g-newui.md:
  ROUTING — #1 #2 #6 (staged-import suspect refresh, skip_suspects no-op, poll
  wedges) -> Import session; #3 #5 #8 #9 (write-queue stall/4xx-retry, backup
  spinner, gesture settle race) -> mobile session. All verified-looking, file:line
  in digest. dev17 lanes still running: pa-vcid.
- ux2-android: cherry-picked onto advanced android-app (their harden pass superseded my publish-confirm + honest-toasts — theirs kept); landed: grid density everywhere, search-viewer parity, actionable backup notifications, LoadState outage rows, viewer-overlay scroll preservation (Timeline/Search), MediaStore ContentObserver, env-based keystore path. android-app pushed (bfbd82df). Compile-verified on omarchy.
- ux3-collections lane claimed (pa-ux-mobile, ids-scope backend + mobile collections-as-scope + month covers) — running (tmux ux3-collections)
- 07-16 dev17 wave complete: vcid MERGED (VC stack identity/deletion/XMP baseline),
  mirror develop-guard fix, Law-1 empty-trash fix, rating GET round-trip — all
  pushed. dev17 worktrees removed (vcfix/pubfix/scenarios/vcid/syncfix). Gate on
  develop: 989 passed / 0 failed (excl. playwright + loupe_swipes_cull known red,
  mobile session). One transient flake observed in the first post-merge run —
  Q2 harness workstream should track flake rates.
- PARKED (needs Fable design pass, not urgent while hub+satellite ship in lockstep):
  sync #4 per-family handshake gating — naive push-filtering breaks the single push
  cursor (held ops would be skipped forever or wedge the queue); likely needs
  per-family push watermarks. Design before laning. sync #5 (retire dirty-metadata
  push) is gated on #4. Publish #6 (favorites per-visitor scoping) + #7 (hook-fail
  semantics) are PRODUCT DECISIONS for Sean.
- CLAIM dev17: pa-scen2 (NEW off develop) — coverage ranks 16-25 scenario batch.
- quality: qfix-p2 MERGED to develop — lane free
- ux3-collections MERGED to develop + pushed; Fable fixup: restored collection rename/share/delete via scope-chip sheet
- ux4-sharing lane claimed (pa-ux-desktop, unify client gallery onto share template + og:image + honest downloads + zip + cover + children nav + done flag) — running (tmux ux4-sharing)
- quality: qfix-workers MERGED to develop (16 fixes incl. cross-review round 2) — lane free
- quality: qfix-dataint MERGED — worktree removed
- ux3-regression-fixes MERGED (abbc6f24); ux5-mediums lane claimed (pa-ux-develop) — running (tmux ux5-mediums)
- ux4-sharing + ux5-mediums MERGED to develop + pushed (post-merge suite green)
- quality: qfix-seams MERGED to develop — lane free
- quality: qfix-seams2 MERGED to develop — lane free
- quality: qfix-firstrun MERGED to develop — lane free
- quality: qfix-polish1 MERGED to develop — lane free
- quality: qfix-polish2 MERGED to develop — lane free
- quality: qfix-polish3 MERGED — lane free
- quality: qfix-seams3 MERGED — lane free
- quality: qfix-seams4 MERGED — lane free
- quality: qfix-polish4 MERGED — lane free
- quality: qfix-seams5 MERGED — lane free
- quality: qfix-seams6 MERGED — lane free
- quality: qfix-seams7 MERGED — lane free
- quality: qfix-seams8 MERGED — lane free
- quality: qfix-rev1 MERGED — lane free
- quality: qfix-rev2 MERGED — lane free
- quality: qfix-rev3 MERGED — lane free
- ux6 wave claimed: ux6-export / ux6-deliver / ux6-android-fav — running (tmux ux6-*)
- ux7-system lane claimed (NEW worktree pa-ux-system: drawer→System canvas lens + status peek, save-bar deleted) — running (tmux ux7-system)
- quality: qfix-rev4 MERGED — lane free
- ux6 wave MERGED: export (b35385de) + deliver (153a5b0e) to develop+pushed (141 tests green; Fable fixup: openPublishOverlay compat export); android-fav fast-forwarded android-app (b88fe657). ux7-system still running. NOTE for future specs: lanes must NOT commit REPORT.md (conflicts at merge every time).
- CLAIM dev17: Windows QA runner (QUALITY_BAR Q1/Q2 gap) — fresh clone at XPS
  photography/pa-winqa (develop @ fecc888c), own venv (py3.12). First full suite
  run in progress; Windows-specific reds will be triaged and fixed from dev17.
  NOTE: photography/photoarchive-field on the XPS has 30+ dirty files (WB picker,
  import canvas, sigmoid view WIP) — belongs to an editing/import session; nobody
  stash/pull there.
- quality: qfix-rev5 MERGED — lane free
- quality: qfix-rev6 MERGED — lane free
- ux7-system MERGED to develop + pushed (Fable fixes: lens active-class, peek dual-render, wizard rebrand, About spacing; smoke-verified via Playwright on standalone :8022)
- ux7-system MERGED to develop (drawer.js worker-state helpers reconciled with lens restructure) + pushed; Playwright-verified on standalone smoke server; Fable fixes: lens active-class, peek dual-render, wizard rebrand, About spacing
- quality: qfix-rev7 MERGED — lane free
- ux8-hotfixes MERGED (596e1ca9, migration proven on live-DB copy); ux9-mediums lane running (tmux ux9-mediums)

- ux9-mediums MERGED + pushed. Round-2 review fully closed (4 criticals/highs Fable-fixed, 4 mediums via lane).
- 07-16 dev17 Windows runner: first-ever full suite on the XPS = 95 failed. Three
  classes fixed on branch winqa2 (ae0597d6): with-sqlite3.connect never closes
  (46 test sites, WinError 32 teardown), teardown vs background-task race
  (bounded retry), POSIX-keyed exiftool mock (builders._metadata_key extracted).
  Touched files verified green on Linux. Full Windows re-run in progress; merge
  to develop after it classifies the remainder. Windows suite is ~13x slower
  than Linux (33min vs 2.5min) — worth a Q3 look later.
- quality: qfix-rev8 DROPPED — ux9-mediums shipped the same deliver round-trip in parallel (duplicate; theirs broader). Lesson: sweep findings may be independently fixed by wave owners — check develop tip before merging quality fixes.
- ux10-smart lane running (mobile smart collections as live scopes; design from smart-mechanics investigation)
- ux10-smart MERGED + pushed. Unblocked UX backlog is now EMPTY.
- quality: qfix-rev9 MERGED — lane free
- quality: qfix-rev10 MERGED — lane free
- quality: qfix-rev11 MERGED — lane free
- quality: qfix-eleg2 MERGED (elegance consolidations) — lane free
- quality: qfix-rev12 MERGED — lane free
- quality: qfix-perf1 MERGED (bench-proven) — lane free
- quality: qfix-ux1 MERGED (pixel-proven) — lane free
- quality: qfix-rev13 MERGED — lane free
- quality: qfix-ux2 MERGED — lane free
- quality: qfix-ux3 MERGED — lane free
- 07-16 dev17: winqa2 MERGED+PUSHED to develop (f7591f44) — Windows runner took
  the suite from 95 platform failures to green: card import was 100% broken on
  Windows (read-only fsync), hub upload intake 500ed every chunk, folder scoping
  matched nothing on local rows, republish/hook-kill had no Windows path; plus
  harness conn-tracking + dependency lockfile (requirements.win-qa.lock — fresh
  venvs MUST use it, version drift fakes platform failures). Windows runner
  lives at XPS photography/pa-winqa. Linux gate on merged develop: 1153 passed,
  one PRE-EXISTING red (test_first_run_scan_builds_sm_previews..., preview_ready
  gate era — belongs to the qfix-ux lane, fails at 2bb1771a before winqa2 too).
- ux11-pending lane running (surface hidden_pending_thumbnails); PROD OP LOG: resumed paused previews pregen worker 07-16 ~10:05 via /api/cache/pregen/start (177k backlog, film-scans invisibility root cause)
- quality: qfix-perf2 MERGED (develop-open -53% bench-proven) — lane free
- quality: qfix-perf2 MERGED (develop-open -53% bench-proven) — lane free
- ux11-pending MERGED (452e3bac); ux12-priority lane running (sol high — pregen priority pass fed by browsing signal)
- quality: qfix-eleg3 MERGED — lane free
- ux12-priority MERGED + pushed. Activating on prod requires prod pull + service restart (Sean gate).
- quality: qfix-ux4 MERGED (0 re-packs pixel-proven) — lane free
- 07-16 dev17 WINDOWS RUNNER COMPLETE: full suite on the XPS is GREEN — 1116
  passed / 0 failed incl. playwright + all 8 GL<->Python parity tests on the
  real RTX 3050 Ti. Campaign: 95 -> 56 -> 27 -> 12 -> 0 across five batches,
  all merged to develop (last: 1cbb60da). The QUALITY_BAR matrix now includes
  Sean;s actual platform. Runner: XPS photography/pa-winqa, venv pinned via
  web/requirements.win-qa.lock, run with plain pytest -q (~26min). Watch item:
  test_missing_media_response...writer_is_busy flickers at the 2.0s budget on
  this disk — perf lane datapoint, deliberately NOT widened.
- quality: qfix-ux5 MERGED — lane free
- quality: qfix-ux6 MERGED — lane free
- quality: qfix-rev14 MERGED (clock matrix complete) — lane free
- quality: qfix-rev15 MERGED (both HIGHs closed) — lane free
- CLAIM release-lane (dev17): Windows fresh-install drill — building frozen server artifact on the XPS (pa-winqa), then first-run smoke. Also auditing CI/CHANGELOG freshness.

## DECISIONS / SEAN-ONLY (release lane, 07-16 ~19:30)
- **GitHub Actions is disabled for the ACCOUNT** ("Actions has been disabled for
  this user", HTTP 422 on dispatch; zero runs ever despite active workflows +
  enabled repo Actions + fresh pushes). CI and the v*-tag release builds are
  dead until Sean resolves it at github.com/settings (likely email verification
  or a billing hold — same class as the Netlify suspension). Release lane is
  proceeding with local artifact drills meanwhile.
- Prior parked decisions still open: publish favorites scoping, hook-fail
  semantics, Elo-on-delete, empty-trash VC cascade, taxonomy move journal.
- [Azimuth Architecture lane] ux13-storage claimed (pa-ux-system worktree): STORAGE_UX slices A (archive overview card + /api/storage/overview), B (Storage→Cache rename), C (inline add-flow CTAs) — running (tmux ux13-storage). Slice D (vocabulary Source→Folder) parked under DECISIONS for Sean per docs/STORAGE_UX.md charter note.
- CLAIM import-lane (Fable/Import): taxonomy move journal (crash-safe rename+repoint), import_canvas scan-survival + stage peek, placeholder/aspect integration verify. Worktree pa-import.
- CLAIM develop-lane (Fable/Develop, 07-16 ~16:20): (1) CR3 develop-open perf campaign — LibRaw decode 9.4s / default render 5.76s / EXIF 2.3s / gzip 1.2s per qfix-perf2 attribution, worktree pa-devperf; (2) color-science reconcile+merge to develop (7 known conflicts vs ux6, Fable hand-resolves in pa-color); (3) darktable P1s: highlight-reconstruct wiring (pa-hlrecon) + per-camera noise profiles (pa-noise).
- 07-16 release lane: Windows fresh-install drill PASSED — frozen exe builds,
  boots to first-run wizard, clean data layout, clean shutdown (winrel merged,
  83cd9271). VERSION aligned to 1.0.0-rc.1 (was 0.1.1 in artifacts/handshake);
  CHANGELOG Unreleased documents all waves since rc.1 (f01c5df1). Remaining in
  workstream A: Docker image drill (can run on omarchy), Tauri sidecar bundle;
  CI/release automation BLOCKED on the account-level Actions ban (DECISIONS).
- 07-16 release lane: Docker fresh-install drill PASSED (build -> /setup wizard
  -> honest /api/version -> clean teardown). Found+fixed: image never shipped
  VERSION and /api/version 500ed on missing file — satellites would have read a
  Docker hub as broken (6d8bbbfb). Workstream A remaining: Tauri sidecar bundle
  drill (needs the desktop app repo/context — next), release automation still
  blocked on the Actions account ban.
- CLAIM release-lane 07-16: CTO tasking — (1) product-decision sign-off package, (2) data-safety drill re-run vs current develop, (3) RC cut checklist.
| (done) pa-ux-deliver / ux14-deliver | Deliver grammar cleanup | Fable UX-Architecture | MERGED 13f7cdfbd, pushed; grep proofs (no openPublishOverlay/publish-retry, one esc); Deliver overlay + Shared rename Playwright-verified; doctrine at ui-architecture.md (1ed207cc) |

### DECISIONS — UX Architecture (07-16, Deliver grammar recon)
- Delivery data-model fracture (Sean + backend owners): Deliver private tab creates collection_shares while the Shared lens Private pane creates published-node shares — two objects, two revoke/password/URL semantics for the same intent (panel.js:560 vs shared.js:477). Same split for website: publish job vs snapshot nodes. Recommendation: converge on the published-node tree as the one delivery primitive; Deliver overlay becomes a per-collection view over it; collection_shares becomes a legacy read path. Multi-lane program touching shares/publish backends — needs Sean sign-off + coordination with owning sessions before any lane fires.
- import-lane: freeup MERGED to develop (cc482252 + f36b177a, cherry-picked onto 6d8bbbfb; drawer.js conflict resolved — freeup panel now in system Connectivity section; 36 sync/contract tests green, drawer render verified). Satellite free-up-space verb is live on develop.

## DECISIONS CLOSED (Sean, 07-16 evening — release lane packaged)
- Share favorites: PER-VISITOR (cookie-scoped picks, owner sees merged sets).
- Publish hook-fail: RETRY-TILL-CONFIRMED (Sean said idk -> release lane applied
  its recommendation; honest status + background backoff; override welcome).
- Elo on permanent delete: SACRED — duels are earned evidence, never rewritten.
  Doctrine only, no code change.
- Empty-trash VC cascade: WARN FIRST — Empty Trash must surface "also destroys
  N edited copies" and require confirm when a master in scope has copies with
  real edits.
- CLAIM release-lane: pa-favscope (per-visitor favorites backend, Codex),
  pa-hookretry (hook retry-till-confirmed, Codex), VC-warning (Fable direct:
  backend count + trash UI confirm).
| (done) pa-ux-placeholder / ux15-placeholder | Placeholder-cards polish | Fable UX-Architecture | MERGED 62ef5ba35, pushed; gates green; mobile empty-state Playwright-verified |
- 07-16 release lane, CTO tasking status: (1) DECISIONS 4/4 closed with Sean —
  favorites=per-visitor (lane running), hook-fail=retry-till-confirmed (lane
  running; Sean said idk, release lane applied its recommendation), Elo=sacred
  (doctrine, no code), VC-cascade=warn-first (SHIPPED 3db39d79). (2) Data-safety
  drills re-run vs current tree: 109 passed / 0 failed. (3) RC cut checklist at
  docs/RC_CHECKLIST.md (c2709eec) — blockers named: Actions account ban,
  main/develop reconciliation, Tauri drill.
- DONE fixspeed-lane 07-16: freeup-recover MERGED to develop (3dc2ba1b + 3b291b2d, ff): CTO-routed MED confirmed+fixed — legacy delete_ready lines now resolve source root from catalog, refuse recover when root unknown/offline/remapped; fails-without regression + catalog-lookup pin + remap test; 51 sync tests green. Grok cross-review applied (remap hole closed).
| (done) pa-ux-system / ux13-storage | STORAGE_UX slices A-C | Fable UX-Architecture | MERGED 625d43aa + route-contract fix 5ee0d6a6, pushed; Playwright-verified (archive card lens+peek, Cache rename, inline add-flow); slices D/E still parked in DECISIONS |
- import-lane: import-journal MERGED to develop (9ec22077) — crash-safe move journal for taxonomy reclassify (per-file intent->move->apply txn unit, 4-branch recovery), Codex-built, Fable-reviewed, 103 tests green post-rebase incl. fail-before/pass-after crash proof. Closes the parked taxonomy-move-journal decision.
- 07-16 release lane: ALL FOUR Sean decisions now IMPLEMENTED and merged —
  per-visitor favorites (5c01fb28d), hook retry-till-confirmed (latest push),
  VC-cascade warning (3db39d79), Elo-sacred (doctrine). Gate after both merges:
  1199 passed / 0 failed. Decision worktrees removed. UI wiring for the new
  payload fields (visitors sets on owner review, retrying badge) = Fable/UI
  when surfaces are touched next; payloads are backward compatible meanwhile.
- 07-16 release lane, CEO directive ack: XPS execution wound down — zero release-
  lane processes local (drill server stopped, builds/suites complete). All future
  drills/builds/suites run on omarchy. STRUCTURAL EXCEPTION for CTO awareness:
  Windows-platform verification (pa-winqa suite, frozen Windows server builds/
  drills) cannot execute on omarchy — proposal: batch them, run scheduled/on-
  request on the XPS rather than continuously. photoarchive-field uvicorn on the
  XPS is Sean prod-satellite, not a lane process — left untouched.
- CLAIM fixspeed-lane 07-16: pa-boot-defer / boot-defer — boot campaign step 2 (Codex): defer numpy/rawpy/PIL/imagecodecs off import app; no route/wiring restructure. Owner: Fable Fix-Campaigns-and-Speed.
- CLAIM fixspeed-lane 07-16: pa-profile-index / profile-index — CTO item 2 (Codex): indexed on-demand Adobe profile store; kills 557ms cold load on first develop-open per boot. Owner: Fable Fix-Campaigns-and-Speed.
| (done) grok-rev-ux | Grok cross-review of ux13/14/15 merges | Fable UX-Architecture | CLOSED de051207a: 5 real findings fixed same round (unpublish-undo after close, source-add Esc leak + focus trap + layer registry, paused-hint one-shot); 12 areas verified clean |
| (done) deliver-hookretry-seam | CTO-routed HIGH: hook_retrying states in Deliver | Fable UX-Architecture | FIXED 88825de54 + doctrine: publishJobSettling() drives busy/poll/copy; fails-without contract test added |
- CLAIM fixspeed-lane 07-16: pa-errhonesty / errhonesty — CTO item 3 hunt: 10 confirmed error-path honesty fixes on desktop (export ok-check, preset delete/rename, auto-level distinction, settings rollback, sync-chip mislabel, develop save retry, deliver copy, IPTC button); 2 Grok findings refuted (floating applyFlags/stacks — requestWithStatus never rejects). Owner: Fable Fix-Campaigns-and-Speed.
- develop-lane: color-science MERGED to develop (70c3153b3) — controls waves reconciled vs ux6, S-curve+sigmoid GL twins, WB picker/presets, clip overlays, histogram drag; GPU-parity-proven on XPS + 1187-green Linux suite. Prod deploy still gated on Sean's visual verdict.
- develop-lane FINDINGS routed out: (1) PRE-EXISTING develop reds: test_desktop_correctness publish/export poll trio fails on pure origin/develop (Deliver-grammar wave landed contracts without matching JS?) — belongs to ux13-15/deliver lane; (2) PRE-EXISTING Windows red: test_catalog aspect-at-scan stores no orientation on Windows (1cc58bc POSIX assumption) — spawn-task chip filed on XPS.
- develop-lane: dev-perf-cr3 DONE+reviewed (real-CR3 open 9.6->7.4s, byte-identical, debunked 9.4s decode myth as host contention) — merging next; dev-hlrecon DONE+reviewed (engagement probe-proven on real files; cache v5 bump, old v4 dir = ops cleanup note); dev-noise follow-up lane running (ISO-from-metadata blocker fix).
- import-lane: import-park MERGED to develop (efd2058c, pushed 926a5a03d) — Esc parks the import stage (scan continues in background), peek chip in main chrome, resume/revalidate/expiry-rescan, aspect-at-register for staged imports. Grok-reviewed (3 real bugs fixed), Playwright-proven incl. server-restart expiry drill.
- develop-lane LANDED x3 on develop: color-science reconcile (70c3153b3), dev-perf-cr3 (render −1.9s, byte-identical, ad37831a), dev-noise (camera NR defaults, ISO cached at SOURCE_META_VERSION=3, GPL data flag for Sean, 78d3d287f), dev-hlrecon (opposed highlight reconstruction at decode, RAW cache v5 — old base/v4 dir (136GB) is an ops cleanup candidate once v5 warms; d87f75144). Final full-suite gate running. Prod deploy remains gated on Sean visual verdict.
- develop-lane gate CLOSED: post-landing full suite = 1212 passed; the 6 reds are (a) the PRE-EXISTING Deliver-poll desktop_correctness trio (identical at 926a5a03d pre-hlrecon — deliver/ux13-15 lane owns), (b) test_versioning x3 = box-load flake, passes isolated and after hlrecon modules at tip. develop-lane landings verified clean.

## deslop claim 2026-07-20 ~23:30 (Fable session, XPS)
| (local: photoarchive-field on XPS) / deslop | full-app de-slop sweep: bugs+slop+dead code audit, repo-root doc cleanup, fixes | Fable session (Windows, deslop) | closed 2026-07-25 — no live lane process; branch/worktree output preserved for later review |

## deslop fix wave 2026-07-20 ~23:50 (Fable session, XPS)
| wt-deslop-{feel,algos,sathash,trash,hygiene} / deslop-* | Grok fix lanes off branch deslop: refine responsiveness, strategy algos, satellite hash loop, trash safety, repo hygiene | Fable session (Windows, deslop) | closed 2026-07-25 — no live lane process; named branch/worktree output preserved |

## deslop wave 3 (Fable, XPS) 2026-07-21 ~01:17
| wt-deslop-{localfirst,backfill} / deslop-* | local-first adaptive-RAM hardening + backfill productization (config-agnostic) | Fable session | closed 2026-07-25 — no live lane process; named branch/worktree output preserved |
