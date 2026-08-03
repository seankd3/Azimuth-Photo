# Simplify — running log

Branch `simplify`, off `main`. One cutover when it is done; prod is untouched
until then. This is a retrospective, not a plan: what was cut, what was learned,
and what is known to be broken. Newest first.

## Where it stands

| | main | now |
|---|---|---|
| Non-test Python | 104,185 | 88,747 |
| Test Python | 53,541 | 32,689 |
| Static JS | 38,619 | 37,680 |
| API routes | 305 | 269 |

55 commits. **48,231 deletions against 5,418 insertions.**

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

## Routes this branch removed — 34, and one was wrong

Removed on the rule "no caller in any client tree". That rule has a miss rate:
`POST /api/import/taxonomy/reclassify-personal` has no caller because it is
driven by hand, and MASTER_PLAN carries two open 07-31 directives describing
exactly its job. Restored.

The other 33 stand, but two features lost their whole API surface and that is a
product call, not a cleanup one:

- **`/api/geo/*` — all four gone.** No geotagging endpoints remain, though
  `latitude`, `longitude` and `location_source` are still mirrored columns.
- **`/api/people/faces/{id}/assign` and `/ignore` — gone.** Person-level label,
  merge and ignore survive; per-face correction does not. `db.assign_face` and
  `db.ignore_face` are still there with no door onto them.

Also gone: pano merge (3), HDR detect/status (2), quality scan/status,
`/api/compare/next` (two other compare routes remain), Lightroom preset import,
`/api/publishes`, `/s/gallery/*` (5, with `features/publishing`), and
`/api/ui/settings`.

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
- `web/scripts_stale_refs.py` after any rename. It reads the tests without
  running them and asks the real modules whether each name is still there —
  both `patch.object(mod, "x")` targets and plain `mod._private` access. It
  found four failures on this branch that reading did not, and its exit code is
  the count, so it can gate a commit.
- `pytest --collect-only` after any deletion. It imports all 162 test modules in
  under three seconds and catches references the deleted code left behind —
  including ones inside strings, which no import checker sees.
