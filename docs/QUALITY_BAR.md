# QUALITY_BAR — quality of what exists beats new features (2026-07-15)

Sean's directive: "still getting basic errors… we need a way to test absolutely everything… benchmarking, profiling, optimizations… really nail the editing."

## Where quality escapes today (evidence-backed)
1. **Environment matrix.** All QA runs on headless Linux; the user lives on a Windows satellite in a WebView2 shell. Today's escapes: "No graphical session available" (reveal took the hub/Linux path for a hub-mirrored source), OSError on pre-1970 `.timestamp()` (Windows-only), `%-d` strftime crash (glibc-only). Every one invisible to the harness.
2. **Source-type matrix.** Features are tested against local sources; hub-mirror and offline-source variants behave differently (reveal, thumbs, trash, export).
3. **Harness trust.** Flakes under load (4-fail → same suite 23/23 minutes later), report.json overwritten per run, no artifacts kept, no flake-rate tracking.
4. **No standing performance truth.** Perf budgets exist in CI but there is no benchmark history, no profiling ritual; suggestions endpoint takes 60s cold on 139k and nobody noticed until today.
5. **Editing correctness coverage.** GL↔Python parity covers the default look; sliders/masks/lens/export paths lack invariant + golden coverage. PV-tone gap work (color-science branch 0e738de3) parked.

## Workstreams
- **Q1 winqa** — Windows + source-type correctness: fix reveal (hub-mirror sources must not shell out on the wrong machine), sweep the posix-assumption list (os.path.commonpath multi-drive, /mnt hardcodes, path joins, shell-outs), suite green on Windows.
- **Q2 harness** — QA harness industrialization: per-run archived reports+artifacts, flake detection (auto-rerun once, tag+count flakes, trend report), scenario isolation, Windows runner so the matrix includes the user's actual platform.
- **Q3 bench** — standing benchmark+profile suite: JSON baselines with history (boot, grid first-paint, thumb p95, search, develop open, suggestions, sync status), regression gates, py-spy ritual, optimize the top offenders (suggestions first).
- **Q4 editqa** — editing correctness: golden renders per camera model, slider-sweep invariants (finite, monotone where expected, no posterization) across every op incl. masks/lens, export==preview parity, then land the parked Contrast2012 tone work behind acceptance.
- **Q5 coverage** — feature-inventory vs scenario gap matrix (grok sweep → ranked gaps → scenario batches until every user-facing verb has a gate scenario).
- **Q6 audits** — rolling cross-model bug hunts per subsystem (publishing/shares, imports, versioning/snapshots, sync/oplog), every finding verified then fixed via lanes.

## Bar
A change ships when: pytest green + QA matrix green (Linux hub + Linux satellite offline/old-hub + Windows satellite) + no benchmark regression + zero new console/server errors in scenarios. "It works on the harness" must mean "it works on Sean's machine."
