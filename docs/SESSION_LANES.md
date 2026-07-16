# Session lanes — Azimuth Photo (charter, 2026-07-16)

ORG MODEL: Sean = CEO (product vision, decisions). Quality session = CTO
(cross-cutting standards, post-merge verdicts, arbitration between lanes).
Each session below = a MANAGER for its lane: managers own their territory,
decompose work, and delegate execution to subagents/Codex/Grok lanes — managers
orchestrate, subagents do the real work. Thread titles may drift; the LANE is
canonical, not the title.

One session = one lane. Check this file + LANES.md before any fan-out, merge, or
cleanup. Findings outside your lane route to the owner (send_message), not fixed
in place. Program directives go to exactly ONE session.

| Session | Lane | Territory (code) | Branches |
|---|---|---|---|
| **Azimuth Import** | Import & ingest | `web/features/imports/`, staged-import canvas (`import_stage.js`, `import_canvas.js`), card-import engine (CARD_IMPORT_SPEC), satellite field import UX, photo-consolidation program | develop via claimed worktrees |
| **Azimuth bug fixes** | Directed fix campaigns & speed | Sean-directed hunt campaigns (contract classes, perf pushes), sync fix waves (oplog/syncfix lineage), trash/VC campaigns | develop via claimed worktrees |
| **Photo app polish & release readiness** | Release program | RELEASE_PLAN.md, distribution/standalone, releng/CI/CHANGELOG, data-safety drills, scenario coverage gates | develop via claimed worktrees |
| **Azimuth Editing** | Develop module | `web/features/develop/` (color science, controls, render/GL pipeline, LR migration), develop UI | develop via claimed worktrees |
| **Azimuth Android** | Native Android app | `android/` and the `android-app` branch, build/deploy, Pixel flows | android-app (NOT develop) |
| **Azimuth Architecture** | UX architecture & waves | `docs/ui-architecture.md` doctrine, ux-wave campaigns (ux2…ux10 lineage), system lens/drawer design, STORAGE_UX | develop via claimed worktrees |
| **Quality (Fable, head of Quality)** | Standing quality loop | Post-merge sweeps + both-family verdicts, cross-cutting class closures (scope resolution, clocks, preview_ready, worker states), contract guards, perf bench + regressions, visual QA drives | develop via qfix-* worktrees |

## Routing rules

- A sweep finding inside another session's territory: if that session has an
  active campaign there, message it; otherwise Quality may fix it (small,
  tested) and note it in LANES.md.
- Cross-cutting classes (touching 3+ territories) default to Quality.
- The merge bar (north star, AGENTS.md) applies to every lane identically:
  regression proof, reviewer ≠ author, quick-check green.
- Product decisions escalate to Sean; park them in LANES.md under DECISIONS.

## Coordination invariants (from the July audit — non-negotiable)

- Claim in LANES.md BEFORE creating worktrees or launching agents.
- Lane output reaches a named branch before "done"; never `git add -A` in shared trees.
- Prod checkout is main-only. Check develop tip before merging (parallel waves land fast).
- Before big fan-outs: `list_sessions` + `lane status` + reconcile LANES.md.
