# The gates

*Measured 2026-08-16 at `c9f253c9`, on this tree and on the live catalog at
`C:\Azimuth Photo\data\catalog\azimuth.db` (`mode=ro`). Every number below is
followed by the command that produced it. Nothing in the repository was
modified to obtain them; the destructive experiments ran in a throwaway clone.*

CORE.md asks for one gate: *"a line budget that fails the build. One number,
zero maintenance."* A line budget was written, run, and refuted. On this tree,
**deleting blank lines removes 13,601 lines — 11.8% of 115,005 — and every
file still compiles.** A number that a formatter can move by 11.8% cannot price
anything.

So the gates here count something else.

> **Every number in this file goes up when you add code and down when you
> delete it. There is no reference you can add that makes one of them smaller.**

That is the whole design. The line budget went green when you added a
newline. The reachability check went green when you added a keep-alive import.
Both were satisfiable by writing more code, which is the one thing a gate on
this project must never reward. A stranded call site, an upward import, a
hand-made table and a missing script are all *couplings and lies*: writing more
code produces more of them, never fewer.

---

## The gate set

| Gate | Counts | Today | Command |
|---|---|---:|---|
| `seam` | UI `/api/` literals no handler serves | **16** | `python scripts/gates/check.py` |
| `layers` | imports pointing up the stack | **33** | " |
| `tables` | table DDL outside `web/model/schema.sql` | **61** | " |
| `invokes` | paths a workflow names that are not tracked | **4** | " |
| `routes` | a path served twice, stranded, or shadowed | **0** | " |
| `names` | a name that is called and defined nowhere | **0** | " |
| `collects` | test modules that fail to import | **0** | " |

Whole set: **5.6 s** warm, no network, no database. Five are stdlib only.

`names` shells out to **pyflakes**, which must be importable by whichever Python
runs `check.py` (`python -m pip install pyflakes`). That dependency is
deliberate: Python's scoping rules are involved enough that a hand-rolled
undefined-name check would be subtly wrong, and a checker that is subtly wrong
does not merely miss things — it certifies them.

`collects` runs `pytest --collect-only` under **the project's own interpreter**,
found at `web/.venv` rather than assumed to be the one running the gates. A gate
that reported 63 collection errors because it was looking through an interpreter
without fastapi installed would be a false alarm, and a false alarm teaches
people to ignore the gate.

```
$ for i in 1 2 3; do s=$(date +%s%N); python scripts/gates/check.py >/dev/null; \
    e=$(date +%s%N); echo "$(( (e-s)/1000000 )) ms"; done
807 ms
775 ms
785 ms
```

Implementation: `scripts/gates/` — **278 lines of Python and an 8-line budget
file**, six files, one gate each. `ruff check --select E,W,F scripts/gates` →
`All checks passed!`

---

### `seam` — the UI may not call a path the backend does not serve

Served paths come from the 197 route decorators (all 197 carry their path as a
literal on the decorator's own line — verified, so nothing is missed by reading
one line at a time). Called paths come from the `/api/` string literals in
`web/static/**` and `web/templates/**`, with `${...}` read as one wildcard
segment.

**16 called and not served, over 18 sites, measured 2026-08-16.** The count was
derived independently of the architecture document and agrees with it path for
path.

```
/api/auth/key  <- web/templates/setup.html:506
/api/captions/scan/pause  <- web/static/js/desktop/api.js:263
/api/captions/scan/resume  <- web/static/js/desktop/api.js:267
/api/discover  <- web/templates/setup.html:437
/api/lr/connect  <- web/static/js/desktop/api.js:344, :348, :356
/api/lr/status*  <- web/static/js/desktop/api.js:340
/api/pair/connect  <- web/templates/setup.html:465
/api/people/*/ignore  <- web/static/js/desktop/api.js:141
/api/people/*/label  <- web/static/js/desktop/api.js:137
/api/people/merge  <- web/static/js/desktop/api.js:145
/api/people/merge-suggestions/*/reject  <- web/static/js/desktop/api.js:152
/api/people/scan/pause  <- web/static/js/desktop/api.js:327
/api/people/scan/resume  <- web/static/js/desktop/api.js:331
/api/propagation/last  <- web/static/js/desktop/api.js:572
/api/remote-access  <- web/static/js/desktop/api.js:335
/api/remote-access/serve  <- web/static/js/desktop/api.js:372
```

**The failure it would have caught: eight times in sixteen days, and the
wreckage is still here.** Replaying the check over the last 400 commits
(2026-07-31 → 2026-08-16) the count moved on 11 commits and rose on 8 — and
every riser is a deletion that removed handlers and left the calls behind:

| Commit | | Subject |
|---|---:|---|
| `412414d9` | 0 → 6 | G1: the sharing quartet is gone |
| `10e29ebd` | 4 → 8 | G3: the AI derivation fleet is gone |
| `963c54a9` | 8 → 10 | Delete the node role, and everything it was gating |
| `3a91a9fc` | 10 → 11 | Stop being a server |
| `571baef5` | 9 → 10 | Delete the boot warmers and the compare stack |
| `a61d81a0` | 10 → 14 | Faces become a cache kind that cannot be made here |
| `13e455f9` | 14 → 18 | Ledger the concurrency correction |
| `fbdc752b` | 14 → 16 | Delete features/sync |

This is the rule CORE.md already states — *a surface is rewritten and its
machinery is deleted in the same commit* — made mechanical. The gate cannot see
whether a surface was rewritten. It can see whether the deletion finished.

**How it can be cheated, priced.** Splitting one literal, `'/api/remote-access'`
→ `'/api/' + 'remote-access'`, leaves a one-segment prefix that matches
everything and the count falls to 15. Measured in the clone. Because the
ratchet is an equality (below), the build still fails — as a *fall* — and going
green needs a second edit that lowers the committed number. The cheat is two
edits and a diff that claims the seam improved. Today the shortest prefix
literal in the tree is two segments (`/api/develop/`, 16 sites), so a bare
`/api/` would stand out. **18 literals over 21 of the 183 sites are prefixes the
gate cannot decide; it errs silent, never loud.** Counting any literal carrying a
wildcard or a `${…}` segment, the blind spot is 63 literals over 74 sites — the
wider number is the honest one to plan against. No false positive today: each of
the 16 was confirmed by grepping the served set for its family.

---

### `layers` — imports point down

`model` is under `data`, `core`, `photo`, `pixels`; those are under `features`;
`features` is under `api` and `app`.

**21 upward imports, measured 2026-08-16** — 19 into `features`, 2 into `api`.
`web/model/` is clean: `grep -hnE '^(from|import) ' web/model/*.py | sort -u`
returns only stdlib and `from model import …`.

**Every one of the 21 is written inside a function body.** A top-of-file grep —
the form the architecture rules are usually written in — reports **0** and
misses all of them. The gate reads any indentation, and also
`importlib.import_module("features…")`; adding that alternative leaves today's
count at 21, so the escape it closes is not one anybody is using yet.

```
web/core/background.py:209: from features.system import backups
… 10 more in background.py, 3 in cache_events.py, 2 in memory_pressure.py …
web/data/schema.py:2197: from features.develop.virtual_copies import ensure_virtual_copies
web/data/schema.py:2212: from features.develop.presets import ensure_develop_presets
web/data/schema.py:2215: from features.quality.scorer import ensure_image_quality
web/features/settings/routes.py:25: import api as core_api
web/features/settings/status.py:15: import api as core_api
```

The three in `schema.py` are the dependency running backwards: the schema
cannot be created without the features it is supposed to be under.

**The failure it would have caught.** Over the same 400 commits the count moved
on 39 and rose on 7. One riser is `5573644a` *"Startup reaches for what it
warms"*, 97 → 102 — the commit that also gave `run_startup` 36 keyword
parameters, and which the function-shape gate was proposed and refuted over. A
signature is a symptom; the upward import is the disease, and it is the one a
grep can see. The other thing the replay shows is the cure: **99 → 19 over the
same 400 commits**, on 36 of them.

**Cost of the false positive.** 7 rises in 400 commits (1.8%). Two of the seven
are deletion commits reshuffling wiring (`0636236b` *"core/wiring.py is
deleted"*, 60 → 62).

---

### `tables` — a table created once by hand is a table that does not exist

`web/model/schema.sql` holds four `CREATE TABLE` statements. **68 more live
elsewhere, measured 2026-08-16**, 53 of them in `web/data/schema.py`:

```
 53  web/data/schema.py            1  web/features/develop/presets.py
  3  web/features/library/keywords.py   1  web/features/develop/virtual_copies.py
  2  web/features/quality/autocull.py   1  web/features/imports/move_journal.py
  1  web/elo_stars.py                   1  web/features/library/saved_views.py
  1  web/features/catalog/synchronize.py 1 web/features/library/watched_folders.py
  1  web/features/develop/export_presets.py 1 web/features/quality/scorer.py
  1  web/features/system/backups.py
```

**The failure it has already caused.** `features/imports/move_journal.py:10`
declares `taxonomy_move_journal`. The live catalog holds 86 tables and that is
not one of them:

```
$ python -c "import sqlite3; c=sqlite3.connect('file:C:/Azimuth Photo/data/catalog/azimuth.db?mode=ro',uri=True); \
  n=[r[0] for r in c.execute(\"select name from sqlite_master where type='table'\")]; \
  print(len(n), 'taxonomy_move_journal' in n)"
86 False
```

The move journal has never held a row on this machine, because the only code
that creates it is code that has never run here. A table that is created by the
code that uses it exists only where that code has run.

**How it can be cheated, and what it does not see.** The gate counts the words,
not the statement: prose about the DDL counts as DDL. This was not theoretical
— `tables.py`'s own docstring was the 69th hit until it was reworded, which is
how the false positive was found. Today **none of the 68 is prose**; each was
read. Splitting the string (`"CREATE " "TABLE"`) hides a statement. `CREATE
TEMP TABLE` is scratch inside one connection and is not counted; tests build
fixtures and are not counted. Moved on 12 of 400 commits, rose on 5.

---

### `invokes` — a build may not name a file that is not there

Every repository-relative path a workflow names, in a command or in a comment,
must be a tracked file. **4 are not, measured 2026-08-16:**

```
.github/workflows/ci.yml:45: web/perf/standing.py
.github/workflows/ci.yml:48: web/perf/baseline.json
.github/workflows/ci.yml:53: scripts/bench.py
.github/workflows/release.yml:43: scripts/build_server.py
```

`ci.yml` runs `python scripts/bench.py --check` and `release.yml` runs `python
scripts/build_server.py`. `0c259f93` *"Delete 6,251 lines of provably dead
tooling"* deleted both on 2026-08-16 — **66 commits ago** (`git rev-list --count
0c259f93..HEAD`). `9670f11f` deleted `web/perf/` on 2026-08-14. The perf job and
the release build cannot have passed since. **Nothing said so, because the
workflow has never run at all:**

```
$ gh api repos/:owner/:repo/actions/runs --jq .total_count
0
```

That is the standing condition every other gate in this document has to live
with: **run them locally or they do not run.** `python scripts/gates/check.py`
is the whole instrument; the CI job below is a copy of it.

This gate is 34 lines and is the only one whose entire hit list is fixable in a
single commit that touches nothing but YAML.

---

## Today's run

```
$ python scripts/gates/check.py
seam        16 of   16  ok   The UI may not call a path the backend does not serve.
layers      21 of   21  ok   Imports point down.
tables      68 of   68  ok   A table created once by hand is a table that does not exist.
invokes      4 of    4  ok   A build may not name a file that is not there.
gates ok in 0.6s
$ echo $?
0
```

`--list` prints what each number counts. The first run is green by
construction: `scripts/gates/budget.txt` was seeded with today's measurement,
so the gates measure drift from here and claim no credit for the backlog they
name.

---

## Three experiments

**1. A formatter cannot move them.** In a throwaway clone at `c9f253c9`, blank
lines were deleted from every tracked `py/js/css/html/kt/sql` file — 451 files
changed, no token altered, no line reordered.

```
lines before                     115,005
lines after                      101,404      -13,601  (-11.8%)
node --check over web/static     93 files, 0 failures
py_compile over web/*.py        309 files, 0 failures
seam / layers / tables / invokes  16 / 21 / 68 / 4     unchanged, exit 0
```

**A line budget would have paid 13,601 lines for that. These four numbers did
not move by one.**

**2. It goes red on the real commit that caused the damage.** The clone was set
to `963c54a9^`, the budget seeded there by measurement, then moved to
`963c54a9` — an ordinary deletion commit from two days ago:

```
$ git checkout -q 963c54a9 && python scripts/gates/check.py
seam        10 of    8  ROSE   The UI may not call a path the backend does not serve.
    …
    /api/user-collections/*/galleries  <- web/static/js/desktop/gallery_editor.js:7, :52
    /api/user-collections/*/galleries/*  <- web/static/js/desktop/gallery_editor.js:51, :61
layers      35 of   35  ok
tables      82 of   82  ok
invokes      2 of    2  ok
1 GATES FAILED in 0.8s   (exit 1)
```

The commit deleted the gallery handlers. `gallery_editor.js` was still calling
them. The gate names the file and the line.

**3. `--write` will not raise a number.** With that rise in place:

```
$ python scripts/gates/check.py --write ; echo "EXIT=$?"
1 GATES FAILED in 1.2s
EXIT=1
$ grep seam scripts/gates/budget.txt
seam 8
```

---

## The ratchet

**A number that has fallen is a number that must be written down, or the gate
is measuring a tree that is not there.** So the check fails on *any* difference,
in either direction, and the two directions are not equally easy:

| | |
|---|---|
| the count falls | `python scripts/gates/check.py --write` records it |
| the count rises | somebody types the larger number into `budget.txt` |

`--write` refuses to raise. That is the whole ratchet. Growth is a line a human
typed, in a commit whose subject has to say why, and `git log -S` finds it
forever. Shrinkage is a command.

Failing on the fall is what closes the cheats. Every evasion measured above —
splitting a literal, hiding an import, concatenating DDL — makes a number
*smaller*, so it fails the build as a fall, and going green needs a second,
visible edit that claims the improvement out loud.

**The paperwork, measured, not estimated.** Over the last 400 commits, some
shipped number moved on **51 (12.8%)** and rose on **19 (4.8%)**. So one commit
in eight carries an extra `--write`, and one in twenty-one stops to think. The
line budget this replaces needed 61 hand edits in 148 commits (41%) — that
figure is its refuter's simulation, not re-derived here — because line counts
move on nearly every commit and these do not.

```
$ python replay.py 400        # scratch script, git grep at each rev
gate                       moved            rose
seam                       11 (2.8%)         8 (2.0%)
layers · features edge     36 (9.0%)         5 (1.2%)
layers · api edge           3 (0.8%)         2 (0.5%)
tables                     12 (3.0%)         5 (1.2%)
union of the shipped set   51 (12.8%)       19 (4.8%)
```

The budget file is also the census. Reading `budget.txt` tells you the state of
the four seams without running anything, and the four numbers falling to
`0 · 0 · 4 · 0` is what "the architecture landed" looks like as an assertion
rather than a plan.

---

## What CI does not do today

**CI does not run `scripts/lint`. It never has.** `.github/workflows/ci.yml`
has three jobs — pytest, `python scripts/bench.py --check` (deleted), and
`node --check` per JS file. The repository ships a ruff config and an eslint
config that nothing enforces.

They are not clean. Measured 2026-08-16:

```
$ cd web && .venv/Scripts/ruff.exe check .
Found 52 errors.   (23 F821 · 16 F401 · 10 F841 · 1 F811 · 1 E401 · 1 W292)   0.71 s

$ ./node_modules/.bin/eslint web/static/js web/static/sw.js
✖ 12 problems   (10 no-unused-vars · 2 no-undef, 4 files)   4.25 s
```

**23 undefined names in shipping code**, including
`features/develop/routes.py:889` (`cursor`), `features/export/routes.py:205`
(`cache_entries`), `features/imports/staging.py:332,335` (`generation`),
`data/repositories/filter_options.py:67,70,75`, and fifteen in `db.py`. Plus
`web/static/js/desktop/duplicates.js:893,899` — `'groups' is not defined`, a
`ReferenceError` waiting for a user to open the duplicates view.

These are not gate findings. They are 64 defects that the tools already in the
repository already report, and that no build has ever looked at. The lint job
below therefore **fails today**, and that is the correct first commit: fix the
64, then wire it.

## The CI job to add

Do not paste this over the existing file; the perf job must be removed or
repaired in the same edit, because it invokes a script that was deleted 66
commits ago and it is what the new job's credibility is measured against.

```yaml
  gates:
    name: Gates
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      # Four counted seams against scripts/gates/budget.txt. Stdlib only, ~1 s.
      # A number falls by measurement (`--write`) and rises only by a hand edit
      # in its own commit — see docs/GATES.md.
      - run: python scripts/gates/check.py

  lint:
    name: Lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - run: python -m pip install -r web/requirements-dev.txt
      - run: npm ci
      # The repo's own committed configs, enforced for the first time. Red until
      # the 52 ruff findings and 12 eslint findings of 2026-08-16 are cleared —
      # 23 of them are undefined names in code that ships.
      - run: cd web && ruff check .
      - run: ./node_modules/.bin/eslint web/static/js web/static/sw.js
```

---

## Rejected

A rejected gate with its reason is what stops it being proposed again next
quarter. Each of these was written, run against this tree, and refuted with a
count.

**A line budget per area, `budget.toml` + `check_budget.py`.** *Refuted.* Code
lines are not invariant to where newlines go and nothing in this stack is
either: `web/pyproject.toml` ignores E501 and E702, eslint's three rules are all
AST-scope, and CI's JS job is a parser. Its refuter's 12-line compactor, which
glues lone closers onto the previous line, freed **8,468 lines (8.7%) with zero
tokens changed**; deleting blank lines alone — measured here — frees **13,601
(11.8%)**, with `node --check` and `py_compile` green on the result. The gate's entire true-positive haul
across 148 commits was 264 lines — one "formatting" commit buys 32 times that,
invisibly. Second, independently fatal: the replay that priced it suppressed
repeat episodes, so it reported 7 interventions where simulating the actual
raise-to-current policy gives **34 red builds and 61 `budget.toml` edits in the
same 148 commits (41%)**, at an admitted 81% false-positive rate, with the most
churn in `core`, `features`, `legacy` and `tests` — the areas doing the rewrite.
It also prices the derivative while the job is a carve: seeded at 97,374 against
a 40,000 target it is green forever if nobody deletes anything.

**Size ceilings on files and functions, with a committed exception list.**
*Refuted.* The tree's own ruff config ignores E501 and E702, so joining
statements with `;` is repo-legal by written policy. A 45-line mechanical
rewriter cleared **26 of 32** over-ceiling functions — `normalize_settings`
264 → 90, `build_ai_status` 157 → 23 — with ruff still green and every cited
structural problem intact. The cheapest transform uses `ast.unparse`, which
drops comments, so satisfying the gate deletes the "comments explaining why are
not debt" the gate was shaped to protect. The function half was also not a
ratchet: the "now smaller, lower it" rule iterated files only, so all 48
function entries were a permanent allowlist — a 178-line function stubbed to 2
lines passed silently. And the baseline broke on a comment: inserting one blank
comment line above an anonymous unit named `line335` turned CI red on a
byte-identical function.

**`ruff` expanded to B, C4, ERA, FURB, PERF, PIE, RET, SIM plus complexity
ceilings.** *Refuted.* The ceilings were calibrated to the finished core's worst
function with zero headroom — `synchronize.apply` is complexity 16 against a
ceiling of 16, so the first branch added to the best function in the repo fails
the build. The compliant fix for a complexity ceiling is extraction, which adds
lines, against the one objective. 44% of the 194 findings are in tests and
zero-row surfaces that the plan deletes. The tree already carries **99 `# noqa:`
directives across 46 files** and RUF100 was not selected, so the escape hatch is
unpoliced habit. Its one piece of hard evidence — a 16-`NameError` regression —
is caught by `F`, which is already in the committed config. **The half that
survived is the CI job, and it is above.**

**A function-shape gate: C901 + PLR0913 + eslint complexity/max-params.**
*Refuted.* 54 hits, 5.4 s — cost was never the problem. Three evasions were
written and run against the exact gate and all three go green: 12 arguments into
a frozen dataclass; 12 arguments into `**kw`; complexity 18 split into two
helpers threading an accumulator. None removes a line or a coupling. Worse, the
gate's own flagship scalp refutes it: `run_startup`'s 36 parameters were fed by
a 29-field frozen dataclass that **already existed** one file away, so the
one-keystroke green leaves all 137 lines of coupling machinery and hides the
smell a human used to find the cure. And PLR0913 fires twice inside the Develop
maths CORE.md says must survive verbatim, where every argument is a named,
typed, keyword-only mathematical parameter. **What survives is a one-time audit
of the 54 hits, not a build gate.** The upward-import half of that signal is
what `layers` counts instead.

**Reachability at zero tolerance.** *Refuted, and this is the sharpest lesson.*
7 unreached modules / 722 lines, 8 dangling imports, 4.5 s — all real. Then, in
a clone, one appended block of seven imports plus a `_KEEPALIVE` tuple took it
to **0 hits, exit 0, `ruff` clean**, having deleted nothing. The cheapest path
to green was to add a fake reference, which converts "dead module" into "dead
module with a reference" and destroys the signal for every future audit. Its
flagship regression was also already red in the existing pytest job (`cd web &&
AZIMUTH_SMOKE_MODE=1 python -m pytest -q -rs` → 6 failed, 15 passed). 722 lines
is 0.6% of a 75,000-line carve.

**Cross-package imports inside `web/features/` — proposed here, and killed by
its own replay.** 21 today, exactly matching the architecture document. But over
the same 400 commits it **rose on 15 (3.8%)**, and the risers are the good work:
the 08-03 de-injection campaign — *"Search routes ask for what they need"*,
*"Settings routes stop being handed nineteen things"*, *"wiring.py is one
function"* — drove upward imports 99 → 60 while pushing cross-package imports
69 → 92. The campaign traded a bad coupling for a lesser one and this gate would
have failed it fourteen times. **A gate that goes red on the fix is worse than
no gate**, so `layers` counts the vertical edge only, where the same campaign
shows nothing but green.

---

## What could not be determined

**Whether anyone will read a red build.** The workflow has never run
(`total_count` 0). Until Actions is enabled, `scripts/gates/check.py` is a local
command, and a local command is advisory. Doctrine decays.

**Whether `--write` gets run.** The replay measures how often a number moves
(51 of 400), not whether a human records it. If nobody ever runs `--write`, the
numbers stay stale-high and the gates go quiet in exactly the proportion the
tree improves. The failure is visible — a stale number is a failing build the
moment anything moves — but it is a discipline, not a mechanism.

**Nobody ran the application.** Every number here is static measurement, git
replay, and one read-only SQL query. CORE.md rule 7 says to verify by running
it. The 16 stranded paths were proved absent from the route table; they were not
watched failing in a browser.

**The seam gate's silent half.** 21 of 183 call sites are prefixes it cannot
decide — 74 if every wildcard and `${…}` segment counts — and `/api/people/` is
one of them: excused today because `/api/people/status` exists, while
`/api/people/*/label` two lines away is correctly caught. The gate under-reports
by that much. It never over-reports: all 16 hits were confirmed by hand.
