# Simplify — running log

Branch `simplify`, off `main`. One cutover when it is done; prod is untouched
until then. This is a retrospective, not a plan: what was cut, what was learned,
and what is known to be broken. Newest first.

## Where it stands

| | main | now |
|---|---|---|
| Non-test Python | 104,185 | 92,569 |
| Test Python | 53,541 | 43,410 |
| Static JS | 38,619 | 37,739 |
| API routes | 305 | 282 |

About 22,600 lines out of 196,000 — 12%. The number was 48,000 before the audit
put back what should not have gone, and the honest figure is the one that
survives checking. What left for good: the injection layer, the Playwright
scenario suite, 424 assertions on literal source text, six duplicate hub
clients, the v1 metadata push, and routes with neither a caller nor an intent.

## Known red

Full suite: **1,256 passed, 8 failed, 6 skipped** in 7m43s. Every remaining
failure fails identically on `main`, checked by extracting `main` with
`git archive` and running it there:

- `test_restore_drill.py` (4), `test_ml_device.py` (2), `test_import_staging.py`
  (2), `test_search_stability.py` (1), `test_row_version_scope.py` (1),
  `test_ai_failure_resilience.py` (1).
- `test_fresh_boot.py` and `test_catalog.py` fail *less* here than on `main`.

The suite is order-dependent: `test_support.py` monkeypatches ~20 module globals
and 42 files import it with `*`. Files pass alone that fail in a run.

## What the branch removed, and the three things it should not have

Removal ran on one rule — "no caller in any client tree" — and that rule has a
miss rate. Auditing against MASTER_PLAN's open rows instead of against callers
found three deletions to undo:

1. **`POST /api/import/taxonomy/reclassify-personal`.** No caller because it is
   driven by hand. Two open 07-31 rows describe its exact job: Sean renames the
   roots himself, and the agent-side job is to "repair catalog paths that still
   point at the old ones."
2. **`POST /api/people/faces/{id}/assign` and `/ignore`.** Open 07-31 row: "ML
   is local-first… Faces and semantic search run on the machine the user is
   sitting at." Person-level label, merge and ignore survived; per-face
   correction did not, leaving `db.assign_face` maintained with no door onto it.
3. **`features/publishing`.** The 08-03 row authorises consolidating the four
   sharing packages *and names what must survive*: layout, theme, cover image,
   download size, per-image sized download. Cutting the package dropped three of
   the five. Restored, along with three helpers a cascade sweep took once they
   looked dead.

The remaining 28 stand, each checked against both callers and open rows:

- `/api/compare/next` — superseded by the mosaic flow; `POST /api/compare`,
  `/api/compare/undo`, `/api/mosaic/next` and `/api/mosaic/pick` all remain.
- `/api/geo/*` — backfill, infer and timeline import are tagging tools with no
  caller. The Map view reads `api_map_markers`, untouched.
- pano merge, HDR detect/status, quality scan/status, Lightroom preset import,
  `/api/publishes`, `/api/ui/settings`.

### And 285 tests that should not have gone

`test_thumbnails.py` (74 tests, 3 mock references), `test_library.py` (109),
`test_compare.py` (67), `test_search.py` (38), `test_settings_status.py` (35)
and `test_hddgov.py` (7) were deleted while every module they cover stayed. The
mandate was to delete tests that "assert implementation details, mock internals,
or duplicate coverage" — these are the opposite: `test_thumbnails` has three
mock references across 3,170 lines and covers the preview pipeline.

Restored. 285 pass. 41 individual tests were then pruned, correctly this time:
each one failed by reaching a private internal that no longer exists
(`db._facet_cache_key`, `compare.service._resolve_*`), which is exactly the
implementation-detail coverage the mandate names. Two failures remain and both
fail on `main`.

Most of the reconciliation was one rename repeated: `data_providers` lost its
underscore prefixes, `configure_data_providers` went with the injection layer,
`preview_priority.clear_scopes` and `hdd_governor.reset_for_tests` were swept as
dead when they are test infrastructure. 48 of `test_thumbnails`'s 49 failures
were a single missing name.

### Everything the caller rule got wrong

One rule drove the carve — "nothing references it" — and it could not tell dead
code from code nothing happens to call. Eight restorations, found by checking
intent instead:

| Restored | Why the rule missed it |
|---|---|
| `reclassify-personal` route | Driven by hand; open 07-31 taxonomy directive |
| `relocate_catalog.py` | Same directive, same shape: a hand-run CLI has no caller |
| Per-face assign/ignore | Open 07-31 row: faces are local-first product |
| `features/publishing` | 08-03 row names layout/theme/cover as must-survive |
| `web/perf` + budgets + `history.jsonl` | Open "Speed is the bar" release gate |
| `scripts/bench.py`, `qa/fixture` | CI's perf-gate job; plan said keep the fixture layer |
| `azimuth-browser-smoke` | Called by `scripts/azimuth-check` itself |
| 285 tests, 3 generators, 11 docs | Cover live modules / feed live data / linked from AGENTS.md |

**The lesson: "nothing calls it" is evidence about clients, not about intent.**
A hand-run CLI, a CI job, a release gate, a generator and a test all look
identical to dead code under that rule. Before deleting a product surface, read
MASTER_PLAN, `.github/workflows/`, and `scripts/azimuth-check`.

The same rule, applied to what this branch *added*, found two dead functions of
its own: `transport.reachable()` and `photo.kind.is_camera_only()`, both written
because they looked like they completed a set. Neither had a caller, and neither
had a hand-run CLI's excuse for not having one. They are gone. A rule worth
applying to sixteen-year-old code is worth applying to code an hour old.

Peak deletions were 48,429 lines. Putting back what should not have gone cost
about 15,000 of them — that gap is the measure of the overreach.

## What was consolidated

**One role** (`archive/role.py`). Five predicates answered what this node is.
Worked as a truth table over every mode × hub combination, `is_satellite_mode()`
turned out to be exactly `not is_hub_mode()`. The two `is_hub_mode()` copies read
`os.environ` while pairing stores the hub in settings, so a paired laptop kept
announcing itself as a hub over mDNS. Resolving per call also means pairing takes
effect without a restart — `attach_hub()` now re-gates preview generation, which
was decided at import.

**One hub client** (`archive/transport.py`). Six copies of the same ten lines had
drifted into eight timeouts. Three named ones now: `CONTRACT`, `INTERACTIVE`,
`BULK`.

**One way for an edit to travel.** Trashing wrote no oplog entry at all, and the
mirror copied the hub's status back over the local row — so a photo trashed on
the laptop returned on the next refresh. Trash and restore now append a status
entry like every other edit, and the mirror will not un-trash a row this machine
trashed. The hub can still retire a photo; that direction is unchanged and
tested. Proved by disabling the guard and watching the test fail.

**The v1 metadata push is gone.** FIELD_SPEC_V2 said the dirty-table code goes
when the oplog lands green. It landed; the code stayed, pushing every local edit
twice through two mechanisms that disagreed about what counted as changed. 98
lines out of the satellite. The hub route stays — a hub still has to accept a
satellite that has not updated.

**One format rule** (`photo/kind.py`). Six extension-set literals were three
different questions: is this raw, can Develop fit it, did a camera make it.

**One RAW decode** (`pixels/decode.py`). The grid and Develop disagreed on colour
because they were never the same function.

**No injection layer.** `core/wiring.py` was 650 lines and 29 `configure_*`
functions, 90 of whose arguments were `lambda: db.x()`. Deleted; modules import
what they need. 47 `configure()` entry points down to 10 — the survivors take
real parameters or apply live settings.

**One place for JS utilities** (`static/js/lib.js`). 21 copies of `esc`, 9 of
`bytes`, 6 of `fmt`.

## Plan claims that did not survive measurement

The refactor plan was built from an audit that is good at locating code and
unreliable at concluding. Checked and refuted:

- **"Six competing palettes; `--text-2`, `--text-3` and `--danger` have drifted
  between desktop.css and mobile.css."** They are mobile-only tokens, so they
  cannot have drifted between the two. Of the 9 tokens genuinely shared, 7 are
  identical and the other 2 differ only in whitespace inside `rgba()` and
  `cubic-bezier()`. The files are largely disjoint because the surfaces are —
  safe-area insets, tab bars and keyboard offsets have no desktop meaning.
- **"One API client: desktop/api.js and mobile/api.js share 37 duplicated
  wrappers."** The count is right and the conclusion is not: both already import
  `fetchJson` from a shared `../api.js`. The wrappers differ in whether they
  route through desktop's failure reporting, which is how each surface shows an
  error to a person. That is a design decision, not duplication.
- **"Four sharing packages should be merged."** Refuted earlier and withdrawn;
  they are four distinct surfaces, and merging would have invalidated every
  client gallery URL already sent.
- **The dead-code census.** Four of five "dead" modules had a caller.

The claims that did hold were worth the check: `developOpen()` really was
defined three times, and one of the three was a flag nothing ever set.

## Measured, not yet done

**archive/ imports features/, which is backwards.** `pixels/` and `photo/` are
clean; `archive/role.py` and `archive/transport.py` reach into
`features/sync/satellite.py` for the hub URL and device token, behind lazy
imports that hide the direction rather than fix it. That state belongs in
`archive/`. Moving it is 47 call sites through the pairing and auth path, so it
wants its own session; the comments now say so instead of claiming the rule is
being followed.

**Two app-assembly sites, still two.** `web/app.py` (196 lines) and
`core/app_factory.py` (269) both register routers and both run configure calls,
and the order between them is load-bearing but untyped. The injection layer they
threaded is gone — 47 `configure()` entry points down to 17, `core/wiring.py`
deleted — so the merge is now mostly mechanical. It is a boot sequence, so it
wants a session with room to restart the app and walk every surface, not the
tail of one.

**panel.js is two files.** 103 top-level functions in 1,738 lines, and the split
is already visible in the names: the left panel (collections, saved views) and
the deliver overlay (8 `deliver*`, 5 `publish*`, 3 `share*`, plus its own shell,
tabs and draft storage). The second has nothing to do with a left panel.

**drawer.js is four or five.** 130 functions in 2,186 lines across settings
inputs, the system surface, sources, the Lightroom catalogue scan, cloud backup
and model install — each with its own render/bind/poll trio.

Both are mechanical moves that need a browser pass per surface afterwards, not a
test run. Left for a session that can finish and screenshot them.

## Lessons, paid for

**Automated sweeps for finding, hands for editing.** Seven regex sweeps corrupted
code: matched function names inside longer names, matched parameter names, ate
257 lines of a test file, twice injected stray bytes from backreferences. Every
one was caught by ruff or a test on the next command, which is the only reason
this reads as an anecdote. `_satellite.` matching `satellite.` first and leaving
`_role.` happened *again* during the role work, after this lesson was written.

**"Pre-existing" needs proof, not plausibility.** I recorded
`test_stacks.py::test_collapsed_rankings_apply_search_id_filter_and_stack_exclusion`
as pre-existing on the strength of it failing at the previous commit. It failed
at the previous commit *and* was mine — from a de-injection rename several
commits earlier. Run it against `main`, or say nothing.

**A test outlives the behaviour it was written for.** `test_mirror_reports_
skipped_unhashed_rows` asserted that the mirror drops hub photos without a
content hash. Commit 3a113eb1 removed that on purpose — it was hiding 97,471
photos from the laptop — and deleted the counter with it. The test stayed,
failing on `main`, asserting a bug. It now covers what replaced it, including
the `? <> ''` guard that keeps two unhashed photos from adopting each other.

**A fixture that lies passes for years.** `test_sync_standalone_seeds_hub`
failed on `main` and had three independent faults, each hidden by the one in
front of it. It set the global `db.DB_PATH` to the *satellite* catalog while an
in-process hub app read the same global — so the hub answered the manifest from
the satellite's own rows, said "I already have all three", and uploaded nothing.
Behind that, a path expectation predating the `Raws/Digital/` segment. Behind
that, a flag and a develop edit written with raw SQL, which record no oplog
entry — the v1 push used to carry them regardless of whether anything had
changed, which is exactly the sloppiness deleting it was meant to end. Green
now, and it exercises the real seeding path for the first time.

**A test can fail for a reason that is not in the diff.** Three tests looked
like transport regressions. One was the machine (Steam). One was already failing
on `main` — proved by `git archive main | tar -x` into a scratch directory and
running it there, which needs no worktree and no checkout. Establish the
baseline before believing the diff caused it; two of the three investigations
above started from the wrong assumption.

**`git stash` is not safe here.** The repo carries an old `lane/sync-cursor`
stash. With a clean tree, `git stash` saves nothing and the matching `git stash
pop` restores *that* stash instead, conflicting in files it has never seen. It
happened twice. Check `git stash list` first, or extract with `git archive`.

**Measure back to back or not at all.** A test read 20s and was deleted for
breaking the ten-second rule. Then an untouched test went from 17.8s to 60.6s —
Steam was using 58% of the CPU. Re-measured both versions in the same minute:
73.9s → 14.1s, and the deletion was reinstated as a fix. Cross-run timing
comparisons on this machine are noise.

**The dead-code census was ~80% wrong.** Four of five "dead" modules had a caller
— in a systemd unit, a packaging manifest, or a client tree that grep across
`web/` never sees. Before deleting, also check `scripts/build_server.py`,
`deploy/*.service`, `desktop/src-tauri/`, `clients/`, `android/`, `.github/`.

**A green probe can answer from a process that no longer exists.**
`pkill -f "port 8011"` silently fails on Windows, the replacement never binds,
and every route answers from the old build. `scripts/smoke` now waits for the
port to free and refuses to probe if it does not.

**Guards were hiding failures.** Removing the `_configured()` checks surfaced
silent ones: smart-collection conflicts returned "no conflict" and suggestions
returned an empty list when nobody had wired them. Neither said anything.

## Rules this branch runs under

- Every test and check command finishes in under 10 seconds. `pytest.ini` sets
  `timeout = 10`; there is no opt-out marker.
- `python -m harness --check` stays green. A commit that changes a golden says
  why in the message.
- `./scripts/smoke` exit code is the result — never read through `tail`.
- `web/scripts_js_imports.py` after moving any JS file. It walks every relative
  import in static/js and checks both that the module exists and that the named
  export does. Nothing else covers this: the Python suite never loads a browser
  module and ruff cannot see across languages. It caught gallery_editor.js
  importing ./dom.js after dom.js had become ../lib.js.
- `web/scripts_stale_refs.py` after any rename. It reads the tests without
  running them and asks the real modules whether each name is still there —
  both `patch.object(mod, "x")` targets and plain `mod._private` access. It
  found four failures on this branch that reading did not, and its exit code is
  the count, so it can gate a commit.
- `pytest --collect-only` after any deletion. It imports all 162 test modules in
  under three seconds and catches references the deleted code left behind —
  including ones inside strings, which no import checker sees.
