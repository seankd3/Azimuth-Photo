# Simplify — running log

Branch `simplify`, off `main`. One cutover when it is done; prod is untouched
until then. This is a retrospective, not a plan: what was cut, what was learned,
and what is known to be broken. Newest first.

## Where it stands

| | main | now |
|---|---|---|
| Non-test Python | 104,185 | 88,794 |
| Test Python | 53,541 | 32,563 |
| Static JS | 38,619 | 37,680 |
| API routes | 305 | 269 |

51 commits. **47,980 deletions against 4,960 insertions.**

## Known red

- `test_sync_mirror.py::test_mirror_reports_skipped_unhashed_rows` — asserts
  `rows_applied == 1`, gets 3. Verified failing at the commit before the
  transport work, so it is not from that. Not yet traced to a commit on this
  branch or confirmed against `main`.
- `test_stacks.py::test_collapsed_rankings_apply_search_id_filter_and_stack_exclusion`
  — pre-existing, verified failing on `main`.
- The suite is order-dependent. `test_support.py` monkeypatches ~20 module
  globals and 42 files import it with `*`. Files pass alone that fail in a run.

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

## Lessons, paid for

**Automated sweeps for finding, hands for editing.** Seven regex sweeps corrupted
code: matched function names inside longer names, matched parameter names, ate
257 lines of a test file, twice injected stray bytes from backreferences. Every
one was caught by ruff or a test on the next command, which is the only reason
this reads as an anecdote. `_satellite.` matching `satellite.` first and leaving
`_role.` happened *again* during the role work, after this lesson was written.

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
