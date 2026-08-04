# Simplify — running log

**Merged to `main` 2026-08-04** — 149 commits, a clean fast-forward, and the
branch is gone. Work continues on `main`; the shrink is not finished, it just
stopped needing a branch. Prod on omarchy follows `main` from here.

This is a retrospective, not a plan: what was cut, what was learned, and what is
known to be broken. Newest first.

## The blind spot in hunting frozen switches (08-04)

**A fourth false positive, with a different cause.** Refine's `strategy="top"`
was reported as a mode no shipped client can select. True, and irrelevant:
`features/compare/routes.py:91` takes `strategy: str = "explore"` as a **query
parameter**, so anyone can send `?strategy=top` over HTTP. It is not a frozen
switch, it is a public contract with an unpopular value — and `AGENTS.md` says
to preserve route contracts unless the task is explicitly to change them. "No
client sends it" is a product question about whether the mode should exist, not
evidence that the code is unreachable.

Noted in passing, and it is the real defect here: **`strategy` has no allowlist.**
`service.py` branches on `== "top"` and `!= "top"`, so any unrecognised value
silently behaves as not-top. That is the same shape as
`RANKING_SORTS.get(sort, "elo DESC")` — a user-supplied key with a plausible
default and no rejection. Worth fixing as its own change, in both places at once.



Two automated passes hunted "a parameter that has one value at every call site,
and the code only the other value would reach". It found four real ones worth
about 350 lines. It also produced three false positives that share one shape, and
the shape is worth naming because the technique cannot see it:

**A switch frozen on purpose is indistinguishable from a switch frozen by
accident.** The difference is never in the call sites. It is in a comment, a
dated directive, or a spec section — exactly the places a call-site census does
not look.

- `_pregen_should_pause_for_priority()` returns `False` at every site and is
  checked at fourteen places in `pregen_worker.py`. It is not a disabled
  safeguard: the two lines above the `return` cite an owner directive of
  2026-07-20 — the hub's bulk backfill never throttles for activity, because
  interactivity is protected by the satellite's local cache and by on-demand
  requests that bypass the bulk gate. `test_thumbnails.py:2892-2959` monkeypatches
  it to `True`, so the yielding path is tested and can be switched back on.
- `dng_pipeline` `input_space="camera"` is unreachable, and
  `camera_rgb_to_prophoto` implements a **numbered stage of the frozen
  DEVELOP_SPEC** (§30.2). Two of the three functions called dead have a passing
  test asserting dual-illuminant interpolation.
- `sigmoid_view`'s solver "runs on every HDR render" — measured, 35 us against a
  125 ms to 7.75 s transform. Its surviving test catches only 6 of 8 injected
  corruptions of the constants it supposedly replaces.

**And a hazard the same pass surfaced about itself.** The `input_space` switch
froze 22 hours before it was reported, in commit `faf02083` ("Delete 43 functions
nothing calls"), which removed `render_dng_profile` — the entry point that
forwarded `input_space=`. The reviewer's phrase: *the prior pass manufactured this
pass's evidence*. Deletion passes ratchet. Each one makes the next one's census
look more damning, and nothing in the method notices. Check `git log -S` on the
switch before believing it was always frozen.

## Thirteen unused JS exports, and why they were not deleted (08-04)

Of 826 exported names under `static/js/`, **13 appear nowhere else** — not in
another module, not in a template, not in Python, Kotlin or Rust. The count is
solid; the conclusion "therefore delete" is not, and checking three of them is
why:

- **`openSystemLens`** is not dead code, it is an unwired entry point. The System
  lens itself is live — `lenses.js:13` imports `mountSystemLens`, and `'system'`
  appears throughout the chrome logic. What nothing calls is the deep-link
  variant taking a `section` argument. Deleting it removes an intended API, not
  weight.
- **`disposeThumbRenderer`** destroys a shared WebGL renderer. Nothing calls it
  because `thumbShared` is a deliberate singleton, created lazily and reused for
  the app's lifetime. Deleting it removes the only way to free that context, on a
  codebase whose notes already record a WebGL context-limit hazard. An uncalled
  `dispose` is a question, not debris.
- **`EXPORT_SHARPEN_OPTIONS` / `EXPORT_SYNC_GROUPS`** are option tables. An
  options list with no consumer is usually a half-wired feature.

The rest: `resetSettings`, `getImportOptions`, `listImports` (api.js wrappers for
routes that exist), `softProofTransform`, `renderSyntheticPixels`, `armKeyword`,
`keywordPainterState`, `stepZoom`, `peekQueue`.

**The rule this reinforces:** an export with no importer tells you about wiring,
not about intent — the same lesson that made the earlier dead-code census ~80%
wrong. Each of these needs a product decision (wire it or drop both halves), and
that is not a sweep. Left in place, listed here so the next pass starts from the
analysis instead of the count.

## Duplicates: verified live (08-04)

The 229-line cut landed with "NOT yet visually verified" in its commit message.
It is now verified, against the real 142k-photo catalog on :8010 — the page
renders 10,718 identical groups and 538 GB of candidates, all five filter pills
switch, close and reopen re-renders 51 cards, and the console is silent
throughout. That exercises `reloadStacks`, `restartStacks`, the stack observer
and the mount/unmount teardown, which is where the edits were. No screenshot:
the browser pane was not compositing, so this is DOM and console evidence rather
than pixels.

One thing that looked like damage and was not: `#stacks-rescan` renders
`hidden`. It is authored that way in the markup and is byte-identical before and
after the change.

**Recorded because the tool was wrong first.** A reachability pass matching
`name(` reported `rescanStacks`, `startIdenticalVerification` and
`cleanupVerifiedIdenticals` as unreachable. All three are live and registered as
`addEventListener('click', fn)` — a bare reference, never a call — so the pattern
that found them "dead" was the pattern that could not see how they are used.
Matching any reference took the claim from 404 lines to 144. **A dead-code
detector that only understands calls will delete every event handler you have.**

## One derivation system (08-04, in progress)

Thumbnails, Develop bases, embeddings, captions and face vectors are the same
operation under five names, and the tree implements it six times: `readthrough`,
`preview_mirror`, `prefetch`, the on-miss task in `media/routes`,
`embedding_sync`, and the ZIP path in `publishing/downloads`. Five separate
cursor tables, three AI workers that are ~52% the same 1,328 lines, and of the
four derived AI products exactly one (embeddings) reaches the laptop at all.

The design is one content-addressed key `(content_hash, kind, recipe)` over one
table, and its law is Sean's: **a derivation is enrichment, never a
precondition.** Once nothing blocks on one, who computes stops being
architecture and becomes a single default-off boolean.

**Not the oplog, and that is worth writing down.** It looks like the natural
carrier — append-only, content-hash keyed, already replicated. It is wrong for
this: `pull_entries` runs `SELECT … FROM oplog ORDER BY seq` with no LIMIT or
WHERE and filters in Python (`oplog.py:998-1010`), so every pull is O(total
rows); there is no `DELETE FROM oplog` anywhere in the tree; and LWW per
`(content_hash, family)` allows one live value per identity. Wants are ~900k
high-churn rows that are meaningless once satisfied. The mechanic to generalise
is `mirror_export.py:114-160` instead — snapshot `MAX(row_version)`, page on a
version boundary so a version's rows never split. It is the one transport here
that was built correctly.

**Done: every photo gets an identity on purpose.** The content hash is what
every derived artifact will be keyed on, and nothing in the scan path wrote one
— `thumbnails/harvest.py` set it as a side effect of making a thumbnail, and the
only deliberate filler was a route with no caller, oldest-first behind an
`id > mark` cursor. `photo/identity.py` replaces it: newest first, cursor-free,
every role. Empty-string hashes are normalised to NULL at startup, so "no
identity" has one spelling instead of the two that a dozen queries carry
`IS NOT NULL AND trim(…) != ''` to dodge.

*Measured, because the comment claimed it before it was true:* naming
`idx_images_needs_identity` in the query is load-bearing. Left to the planner on
150k rows with 4,546 owed an identity, SQLite picks
`idx_images_missing_date_source` plus a temp B-tree for the sort — 25.1 ms
against 1.7 ms — until someone runs `ANALYZE`, and nothing in this app ever
does. The first draft of that comment asserted the index would be used; the
`EXPLAIN QUERY PLAN` said otherwise. **Write the plan check before the comment,
not after.**

**Verified open, not yet fixed: an edit never refreshes its preview.**
`purge_image_cache` has exactly one caller (`features/catalog/routes.py:467`),
and the Develop save path is not it — `features/develop/routes.py:275-293`
writes `develop_settings`, appends history, appends an oplog entry, and touches
no preview. So an edited photo shows its unedited camera JPEG indefinitely. The
fix is not another invalidator call: it is the recipe belonging to the key, so
an edit changes the key. The signature chain funnels through two functions
(`thumbnails/__init__.py:335` and `:352`), which is where a `recipe` term goes;
unedited photos keep an empty recipe, so nothing already cached is invalidated.

**Known cost of this step:** +58 net production lines. H1 and H2 add the
foundation and cannot pay for themselves; the deletions are H3–H5, and the wave
is scored net at the end. Two named exceptions, not a waived rule.

## Where it stands

| | main | now |
|---|---|---|
| Non-test Python | 104,185 | 92,667 |
| Test Python | 53,541 | 43,402 |
| Static JS | 38,619 | 37,744 |
| API routes | 305 | 282 |

About 22,600 lines out of 196,000 — 12%. The number was 48,000 before the audit
put back what should not have gone, and the honest figure is the one that
survives checking. What left for good: the injection layer, the Playwright
scenario suite, 424 assertions on literal source text, six duplicate hub
clients, the v1 metadata push, and routes with neither a caller nor an intent.

## Four conditions that can never come true (08-03)

Four branches, one commit each, all off `99bdfc1d`, all merging clean onto
`simplify` at `8625f95d` — checked with `git merge-tree`, not assumed. Net
**-103 lines**, which is not what they are worth.

| Branch | Commit | Net | What it removes |
|---|---|---|---|
| `elegance/ask-the-module` | `d0d34ce9` | -85 | 23 "not configured" guards across ten feature modules |
| `elegance/bench-pregen-wiring` | `974fea6a` | -14 | a `configure_data_providers` call that stopped the pregen benchmark importing |
| `elegance/cull-inspect-centres` | `62c89a5b` | -16 | a subject-focus computation whose inputs the payload never carries |
| `elegance/hub-update-one-surface` | `45f57f6e` | +12 | a rolling-upgrade banner reading two fields no server emits |

All four are the same shape, and it is a better shape than the one the earlier
sweeps hunted. Not **"nothing calls this"** — the rule that was ~80% wrong and
cost eight restorations — but **"nothing can make this true."** Intent rescues a
caller-less CLI; nothing rescues a branch whose predicate is constant. A
hand-run tool has an excuse for having no caller. A subject box the server never
sends has no excuse for never arriving.

**The guards were the tail of a thread already open here.** This log records that
removing the `_configured()` checks surfaced silent failures — smart-collection
conflicts returning "no conflict", suggestions returning empty. The 23 that
remained were the other half: after the injection layer went, every one of them
was an assertion that cannot fail, and two were worse than noise, because a
guard that never fires makes its `else` unreachable. Two alternate
implementations sat behind them, maintained and dead. Ten feature modules,
`catalog`, `library`, `compare`, `publish`, `share`, `stacks`, `trash`, `export`
and `people` among them; 282 routes before and after, 1,609 tests collected,
smoke 17×200.

**The pregen benchmark could not import, and this branch broke it.** `web/perf`
is on the restored list above — put back because the "Speed is the bar" release
gate depends on it. It was restored and never run: `perf/bench.py` still called
`thumbnails.configure_data_providers`, which left with `wiring.py`. Proved both
ways on a copied fixture catalog, back to back: before, `AttributeError: module
'thumbnails' has no attribute 'configure_data_providers'`, exit 1; after, a real
table (`embedded_sm_md` 40.5 items/min, `small_preview_lg` 45.8) and — the part
a green table would not prove — three new `cache_entries` rows and the matching
files on disk. Restoring a file is not the same as checking it runs. Nothing in
the suite imports `perf/bench.py`, so nothing said.

**The other two never reached a person.** The cull brief computed a focus point
from `subject_box`/`focus_box`; the autocull payload carries neither, so
fourteen lines resolved to centre every time. Measured in a live browser rather
than argued: a click on a 300×200 figure holding a 900×600 image lands at
`-300px / -200px`, exactly what `frame/2 - width*0.5` gave. And the "your server
needs an update" banner read two fields no server has ever emitted, so the
rolling-upgrade warning could not fire. That one is the only branch that adds
lines: the vocabulary the banner was guessing at now lives in
`docs/RELEASING.md`, where `test_versioning.py` already asserts it.

### The refuted one, and what it says about the survey

**"Has this photo earned ranking evidence" is written eight times.** The census
is right and the fix is wrong, at the site the claim named as its main win.

The eight are real: `helpers.py`, `ratings.py`, two in `rankings.py`, four in
`stats.py`, same three-part decision, same `1200.0`, same `0.0001`,
byte-identical modulo the `i.` alias. Rule 1 was honoured — no reference in
`android/`, `clients/`, `deploy/`, `scripts/`, `.github/` or
`desktop/src-tauri/`.

The proposed rewrite of the hand-negated eighth to `NOT ({cond})` fails twice,
independently:

- **It is not sargable.** `i.comparisons = 0` is an equality SQLite can seek on;
  `NOT (COALESCE(i.comparisons, 0) > 0 OR …)` gives the planner nothing.
  Measured on 150,000 rows against the real `SCHEMA`, both forms alternated in
  one process, min of six: at 10% uncompared the count goes 16.65 → 137.66 ms
  and the page 34.57 → 144.26 ms, and the plan drops from
  `idx_images_status_comps_elo` to `idx_images_source_missing_id`. Slower in
  every regime tested. That is exactly what `photo/visibility.py`'s own
  docstring warns about, and it lands on the Refine working set.
- **It changes the row set.** `comparisons` is `INTEGER DEFAULT 0` — nullable
  (`data/schema.py:47`, checked). Under three-valued logic a NULL row is
  excluded by the AND-chain and included by `NOT(COALESCE(...))`. Verified
  directly.

The claim's own acceptance test — "diff the emitted SQL character for
character" — was therefore unsatisfiable by construction, at two of its own
sites.

**Where the survey is weak is now stated three times in this file, so it is a
rule.** The palette claim, the API-client claim, the visibility-rule claim and
now this one all held as counts and failed as conclusions. **A survey's count is
evidence; its conclusion is a hypothesis.** A text census sees eight identical
strings and infers one rule. It cannot see the query planner, and it cannot see
that SQL's `NOT` is not Python's — so it cannot tell a copy from a spelling
chosen on purpose. Consolidation claims that touch SQL need a plan check and a
NULL check before they are claims at all.

What survives: seven of the eight consolidate, and the eighth keeps its sargable
spelling with the comment `photo/visibility.py` already models. "Eight becomes
one" was never available; seven and a documented exception is. The refutation
also turned up something real and small on its own — `rated_images` is
maintained by two routes, recomputed by SQL in `stats.py` and incremented in
`cache_events.py`, bounded to the 30-second `FULL_STATS_CACHE_TTL_SECONDS`
window because undo takes the full-invalidate path. Worth knowing, not worth a
branch.

A refuted finding cost one investigation and prevented an 8× regression on the
compare surface. That is the cheapest outcome available this round.

### What is next

**Merge the four onto `simplify` and delete the four worktrees.** They are
verified, they merge clean, and `simplify` is one commit ahead of their shared
base with no overlap — this merge is the cheapest it will ever be. Verified work
sitting in a worktree does not reach the cutover, and this branch has a cutover
coming; four unmerged heads are four things the cutover has to reconcile for no
gain. The seven-site consolidation is the next piece of work after that, not
before it.

## Deployed and measured on the real archive (08-03)

`simplify` runs on omarchy at `192177e3`, against the live 153,891-row catalog.
A `.backup` snapshot was taken first (`azimuth.db.pre-simplify-20260803-203743`,
3.65 GB, `quick_check ok`); rollback is `main@327602ab`. No dependency and no
schema changes cross the branch, so nothing migrated.

**Counts are identical to the pre-deploy baseline** — 147,334 active, 153,891
catalog, 11 picked, 19 rejected, 2,592,316 comparisons. That is the check that
matters for the visibility-rule work: any change to what counts as "in the
library" moves those numbers.

Warm timings on 147k photos:

| Route | Warm |
|---|---|
| `rankings?sort=taste` | 1.5 ms |
| `rankings?sort=elo` | 1.5 ms |
| `rankings?stacks=collapsed` | 1.4 ms |
| `stats` | 1.6 ms |
| `stacks` | 274 ms |
| `search?q=sunset` | 801 ms |

First call after a restart is 4–29 s while the caches populate, matching the
known cold-start behaviour rather than adding to it. Sixteen routes, all 200.

**Two things the deploy found that the scratch catalog could not.** The OOM
killer took the service during startup when 20 requests arrived while it was
still doing boot work — 8.7 GB peak, 6 GB swap on a 16 GB box. And
`/api/quality/status` now 422s, because that route was one of the 28 removed and
the path falls through to `/api/quality/{image_id}`; deliberate, and listed
above, but it reads as a break from outside.

The Windows desktop installer builds from this branch: 108 MB NSIS bundle, and
the bundled engine boots clean with no import errors — the check that `archive`,
`photo` and `pixels` reach the PyInstaller bundle at all.

## Known red

Full suite: **1,580 passed, 18 failed, 8 skipped** in 10m29s. Every failure was
checked against `main` by extracting it with `git archive` and running it there.
The eighteen, and what each is:

| Failures | File | Against `main` |
|---|---|---|
| 4 | `test_restore_drill` | identical |
| 2 | `test_ml_device` | identical |
| 2 | `test_import_staging` | identical |
| 2 | `test_fresh_boot` | **better here** — `main` fails 3 |
| 2 | `test_catalog` | 1 identical; the 2nd only appears in a full run |
| 1 | `test_settings_status` | identical |
| 1 | `test_search_stability` | identical |
| 1 | `test_row_version_scope` | identical |
| 1 | `test_hddgov` | identical (a Linux `ionice` path) |
| 1 | `test_ai_failure_resilience` | identical |
| 1 | `test_optional_ai` | **the only one that was ours — fixed since** |

So seventeen stand and one was fixed: a TTL cache in `caption_status_payload`
that made two files depend on their order.

**A slow test, four hypotheses, and no anomaly.**
`test_catalog::test_add_source_then_keep_remove_preserves_rows_and_originals`
read 9.06s against the ten-second cap. Chasing it:

1. *The harness is slow* — no: `BackendTestCase` setup is 0.09s, not the ~4s I
   had written here without measuring.
2. *The routes are slow* — no: the four steps behind `POST /api/catalog/sources`
   total 0.07s.
3. *The per-request TestClient lifecycle* — real (`_request` rebuilds the app for
   every call) but only 1.31s cold, 0.12s after.
4. *A one-second git timeout firing three times* — a profile showed
   `WaitForSingleObject` at 1.004s per call against a `timeout=1`, which looked
   conclusive. Caching the commit changed nothing: 5.5s with, 5.75s without,
   measured back to back. The cache was reverted; `StaticAssetContext` is built
   once per app anyway.

On a quiet machine the test runs **~5.5s**, comfortably inside the cap. The 9.06s
and 7.75s readings were machine load, as were the "1.004s" waits — the profiler's
own overhead. There was nothing to find. The cost of not checking that first was
four hypotheses and two published claims that had to be withdrawn.

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
`BULK`, plus `open_stream` for originals too large to hold in memory.

One hub call still sits outside it: `features/trash/remote.py`, the empty-trash
forwarding left for E4 because permanently deleting an original wants the laptop
paired to omarchy before anyone touches it. A commit message here once called
`_open_hub_stream` "the last raw urlopen outside the transport" — it was not,
and that contradicted the commit that deliberately left this one. The other five
`urlopen` calls in the tree are not hub traffic: a model download, a local port
probe, a benchmark, and two QA readiness checks.

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

## The big Python files are long, not tangled

Having a cohesion measure, it was worth pointing at the six largest non-test
modules. Module variables touched by five or more functions:

| Module | lines | functions | shared vars |
|---|---|---|---|
| `data/repositories/rankings.py` | 2,454 | 47 | **0** |
| `features/compare/service.py` | 1,733 | 53 | 1 |
| `features/library/service.py` | 1,661 | 51 | **0** |
| `features/collections/suggestions.py` | 1,249 | 48 | 2 |
| `features/develop/pipeline.py` | 1,243 | 54 | **0** (one module variable in total) |
| `data/repositories/catalog.py` | 1,343 | 58 | **0** |

Compare `drawer.js`: 17 of 50. These files are long lists of largely independent
functions, not state machines. Length is not what made this codebase hard to
move around in.

Two things were, and only one is fixed. Jump-to-definition no longer lands on a
`lambda: db.x()` — `wiring.py` is deleted and 47 `configure()` entry points are
down to 17. But **484 function-local imports remain**, against 552 on `main`.
`core/background.py` alone has 44 of them against 7 at module scope, because
`core/` importing `features/` at module scope would cycle. That is the plan's
fourth root cause, it is Wave C, and this branch has not done it: a reader
following a call into `run_startup` still has to read the body to learn what it
touches.

**They cannot simply be hoisted, and that was tested rather than assumed.** A
static pass over the import graph said all 299 were safe — which was nonsense,
because the graph is acyclic *because* those imports are local. Hoisting all 44
in `core/background.py` and importing the app failed on the first try:
`data/repositories/filter_options.py` imports `core.background
.track_background_task` at module scope, so the moment `core.background` reaches
a feature at module scope, the cycle closes. Reverted.

So the local imports are load-bearing, and the only way to remove them is the
inversion the plan describes: move what `core/` and `data/` reach for down out
of `features/`, then hoist. Not a mechanical change.

**Mapping that inversion made it much smaller than it looks.** 35 feature
modules are reached from `core/` or `data/`, but **22 of them are
`core/app_factory.py` registering routers** — assembly, not a violation, and it
travels with the app-factory merge. Of the rest, three were plainly misplaced:
`features/search/fusion.py`, `features/search/planning.py` and
`features/people/clustering.py` each had **exactly one importer, in the layer
below**, and imported nothing from `features/` themselves. They are now
`core/search_fusion.py`, `core/search_planning.py` and
`data/people_clustering.py`. Three top-level inversions gone; what remains is
`data/schema.py`'s three `ensure_*` calls and the lazy fan-out in
`core/background.py` and `core/cache_events.py`.

The three `ensure_*` calls are a small, specified job, left undone here only
because it touches catalog creation and a mistake there lands on the real
archive at cutover:

- `ensure_develop_presets` and `ensure_image_quality` are one line each —
  `await conn.executescript(DDL)`. Move `DEVELOP_PRESETS_DDL` and
  `IMAGE_QUALITY_DDL` into `schema.py`'s `SCHEMA`, delete both functions, delete
  their callers. Those callers — six inside `features/develop/presets.py`, plus
  `preset_routes`, `quality/routes` and `quality/autocull` — are defensive
  "create the table before I touch it" calls, and they are redundant:
  `schema.py` runs the whole DDL on every catalog it opens, new or existing.
- `ensure_virtual_copies` is 24 lines and its only non-test caller is
  `schema.py` itself, so it can move wholesale.

Verification is available and cheap: `python -m harness --check` builds a
catalog from nothing, and `./scripts/smoke` boots the app on a fresh
`AZIMUTH_HOME`. Both would catch a missing table immediately.

Only state coupling was measured here, not the call graph, so this is a reason
not to prioritise splitting them — not proof they would split cleanly.

## The visibility rule is not duplicated — it disagrees with itself

D3 in the plan says "one visibility rule, 178 copies across 39 files". The count
is close: 165 `missing_at IS NULL` fragments, 82 `included = 1`, across 38 files.
The characterisation is wrong in a way that matters. Profiling which predicates
each file that queries `images` actually uses:

| files | predicates |
|---|---|
| 15 | `missing_at` + `status` |
| 12 | `missing_at` + `included` + `status` |
| 3 | `missing_at` + `included` + `online` |
| 3 | `missing_at` + `included` + `status` + `online` |
| 3 | `missing_at` only |
| 2 | `status` only |
| 1 | `missing_at` + `included` |
| 1 | `included` + `online` |
| 1 | `online` only |

Nine profiles. The two dominant ones differ by exactly one predicate —
`included` — so on paper a photo in a source the owner excluded is visible to
fifteen files and invisible to twelve.

**Tested against the running app, and it did not reproduce.** Hiding a source on
a live catalogue and asking every surface: rankings 0, counts 0, date-groups
empty, search 0, stacks 0, trash 0, export empty, map 0, and `stats` correctly
separating `total_catalog_images: 4` from `active_images: 0`. They agree.

One surface briefly disagreed. `/api/filter-options` still offered "2026 (4)"
and "jpg (4)" immediately after the hide, then returned empty a short time
later without a restart — a cache TTL window, not a wrong query.
`_catalog_changed()` invalidates pairing, folders, cache-status and embeddings
but not filter options, so the sidebar can advertise filters that match nothing
for a few seconds after a source is hidden. Small, self-correcting, real.

So the static profile shows genuine variation and the behaviour does not follow
from it: the files testing fewer predicates are evidently constrained by their
joins, which was the caveat all along. D3 remains worth doing for the reason it
was always worth doing — one definition beats nine — but not as a correctness
emergency, and this log said "correctness problem" before testing it.

## Looked for, and not there

**The invalidation fan-out is real but not duplicated.** The plan counted 80
`invalidate_*` functions and said one trash action fires ten of them. Both hold:
83 such functions exist, and `features/trash/routes.py::_invalidate_after_trash`
names nine invalidators in a row. But searching for other functions that restate
four or more of the same seven found **exactly one — that one**. The knowledge
is stated once, in a named helper, which is as contained as it gets without
D9's actual fix: caches keyed off the rendition key so the manual invalidators
stop existing. That depends on D3 and D6, neither of which this branch did.

`core/cache_events.py` already has two functions shaped the right way —
`invalidate_people_dependent_caches` and `invalidate_vector_derived_caches` name
a cause and fan out. Thirty of its thirty-two name an effect. That is the shape
to fix, and it is not fixable one call site at a time.

After the `[hidden]` find, the obvious question was whether `desktop.css`
restates other rules the same way. It does not. Thirty-six single-declaration
`display: none` rules remain and their selectors are genuinely distinct —
`[data-tip]::after`, `body.loupe-lights-out #ctxbar`, `.cell.preview-pending
.c-elo`, scrollbars, details markers. Each is a separate decision about what to
hide, not the same decision written out repeatedly.

`[hidden]` was special because it is a platform attribute with defined
semantics that a `display` rule was quietly overriding; there is no second
instance of that shape. Written down so the analysis is not repeated.

## Tried and abandoned: the same sweep for JavaScript

`scripts_dead_state.py` works because Python has `ast`. The JavaScript
equivalent was attempted with a regex heuristic and is not committed, because it
cannot be trusted: the first pass reported four hits, three of which were read
inside template literals — `${…}` holds live code, and stripping backticks takes
the reads with it. Teaching it to keep interpolations made it report eighteen,
still including one it had already been wrong about.

A tool whose hits are mostly false is worse than no tool: it trains you to skim
the output, which is how the dead state got there in the first place. If this is
worth doing it wants a real parser, not a cleverer regex.

One thing did come out of it, verified by hand: `_activityTimer` in `drawer.js`
is the only interval handle in that file never passed to `clearInterval` —
`scanTimer`, `installTimer` and `drawerTimer` all are. It is not a leak; it
drives the always-visible Background activity chip and is meant to run for the
page's life. The handle is vestigial, and harmless enough to leave alone.

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

**panel.js is two files, and the seam is measured.** 103 top-level functions in
1,738 lines:

- **52 deliver functions, 741 lines** — the overlay, its tabs, draft storage,
  publish polling, share rows.
- **51 panel functions, 947 lines** — collections, saved views, the left panel.
- **Nine module-level variables belong to deliver alone**: `DELIVER_TABS`,
  `DELIVER_LOAD_LABELS`, `DELIVER_TAB_STORAGE_KEY`, `SHARED_CHANGED_EVENT`,
  `deliverOverlay`, `deliverOverlayReturn`, `deliverOverlayToken`,
  `deliverPoll`, `deliverSession`.
- **Zero module state is touched by both halves.**

The catch, and the reason this was measured rather than done: the call graph
crosses both ways. Panel calls into deliver twice (`openDeliverOverlay` from
`openCollectionMenu`, `closeDeliverOverlay` from `selectClientPicks`). Deliver
calls back into panel eleven times across nine functions, for
`configuredGalleryUrl`, `withBusyButton`, `tabCreatedAt`, `collectionById`,
`loadCollections`, `clientPickIds`, `selectClientPicks` and `applyClientPicks`.

A straight two-way split therefore leaves a circular import. Looking at what
deliver actually borrows splits those eight in half again:

- **Four are pure** — `configuredGalleryUrl`, `withBusyButton`, `tabCreatedAt`,
  `clientPickIds`, 25 lines between them, no module state and no calls. They
  move with the split, into `lib.js` or alongside it.
- **Four are not.** `collectionById` and `loadCollections` read and write the
  collections state, which is panel's; deliver wanting collection data is a real
  dependency, not an accident. `selectClientPicks` already calls
  `closeDeliverOverlay`, and `applyClientPicks` calls `rememberCollectionImages`
   — client picks look like deliver's concern living in panel's file, and should
  probably travel to `deliver.js` rather than be shared.

So the end state is two files plus a few helpers, with deliver importing
collection accessors from panel one way and panel importing only
`openDeliverOverlay`/`closeDeliverOverlay` back.

Moving the four pure helpers *first*, as a separate commit, was considered and
rejected: all four are used only inside `panel.js` today, so lifting them now
would be building a shared module for a split that has not happened.

**drawer.js is not four or five — measured, it is one.** 130 functions, 2,187
lines, 50 module variables. The plan called it fifteen unrelated domains; the
state says otherwise. **Seventeen of the fifty variables are touched by five or
more functions** — `catalog` by 15, `open` by 14, `aiStatus` by 12,
`cacheStatus` by 11, `systemSurfaceRender` by 10 — and only three variables
belong to a single function.

Set that against `panel.js`, measured the same way: **zero** module state shared
between its two halves. That is what a file waiting to be split looks like.
`drawer.js` is the opposite — one surface whose sections all read the same
status picture, which is exactly what the System drawer is for. Splitting it
means threading that state through every piece or duplicating it, and neither
buys anything.

Keyword classification does not find seams here either: the largest bucket, 38
functions and 755 lines, matches no domain keyword at all.

Both are mechanical moves that need a browser pass per surface afterwards, not a
test run. Left for a session that can finish and screenshot them.

## Found while walking the UI — needs Sean's call

**A brand-new install reports SYSTEM HEALTH: Bad for up to a day.**
`features/system/health.py:222` returns `status="bad"` with "No catalog
snapshots yet" whenever there are no snapshots, and
`run_daily_backup_scheduler` sleeps until 04:00 local before taking the first
one. So from the end of setup until 4am the next morning, a healthy install
shows Bad — and every other check reads ok or warn, so that one verdict decides
the whole banner.

That matters beyond the first impression: it teaches a new owner that the health
indicator cries wolf, and this is the indicator that would have shown the 07-19
incident, where prod genuinely had zero valid backups for weeks.

Two ways out, and this is deliberately not decided here:

1. **Take a snapshot once, shortly after setup completes.** Makes the state
   true instead of reclassifying it, and a fresh catalog with photos in it
   arguably should be protected before the first night. Risk: boot-time work on
   a 142k-photo catalog, against the standing rule about background work on
   prod.
2. **Report "no snapshots yet" as `warn` until the first 04:00 window passes**,
   and `bad` after. Cheap and safe, but leaves a real zero-backup install
   looking merely warm for a day.

Weakening a safety signal is not a refactor decision, so it is written down
rather than done.

## A preview that never went ready — and was not a bug

The grid polls `/api/rankings` every three seconds while any card reports
`preview_ready: false`, and on the long-lived scratch server one image reported
false for over half an hour. Its `sm` thumbnail served 200 with 2,330 bytes the
whole time: `preview_ready` is `EXISTS(cache_entries …)`, so the file was there
and the row was not. That reads exactly like a recording regression, and this
branch had gutted `thumbnails/data_providers.py`, which owns the recording call.

It is not one. Run fresh on both sides — new `AZIMUTH_HOME`, same four photos —
`main` and this branch behave identically: all four images reach
`preview_ready: true` in about two and a half minutes, with four `cache_entries`
rows each.

What actually happened is that I deleted the source folder mid-session while
pregen was still working, which took the source offline, and the image it had
not reached yet was never retried after the folder came back. Self-inflicted,
and read as a code regression because the symptom was real and the cause was
three hours upstream in my own shell history.

One thing in it is worth keeping: a pregen pass interrupted by a source going
offline does not appear to resume for the images it missed, and the client will
poll every three seconds for as long as that lasts. Chasing *why* did not reach
an answer — pregen's candidate query is a live filter on `s.online = 1` and
`NOT EXISTS(cache_entries …)`, so a returning source should become eligible
again on its own, and no cursor stands in the way. Still open.

It turned up a vein of dead state on the way. `_pregen_scan_offsets` was the
first, and looking for others of its shape — module-level names assigned and
never read — found the rest: an injection slot in `embedding_worker` that
nothing ever filled, a private alias of a public function in
`features/settings/status.py`, and behind them 29 `Callable[...]` type aliases
whose only job was annotating parameters de-injection had already deleted.
`DbPathProvider` alone was declared in 18 modules and used in none.

They all survived the earlier dead-code sweep because they look alive: a name, a
type, a parameter slot, a test. Grep finds them; "is it referenced" does not.

The first of them: `_pregen_scan_offsets` was created,
threaded through `sync_thumb_config_metadata`'s signature, and zeroed on every
config change — and read by nothing, incremented by nothing. A paging cursor
whose paging is gone. Removed, along with the test asserting it got zeroed.

## Verified in a browser

Booted on a scratch `AZIMUTH_HOME` — never the real catalog at `C:\Azimuth
Photo` — seeded with four generated JPEGs through the actual first-run wizard
and scan path. On the desktop surface:

- Zero console errors on load, and none after mounting all seven lenses.
- Every network request 200, including `/api/map/markers` — live confirmation
  that removing the geo *tagging* routes left the Map view intact, which had
  only been judged from reading until then.
- Develop lazy-loads 30+ modules, all 200, and renders every panel.
- The module graph resolves, so the `lib.js` consolidation, the `state.js`
  deletion and the `RAW_EXTENSIONS` move all hold at runtime.

The System drawer opens and renders all seven sections, the archive summary,
the source list and system health — on a real click, every time. It also
correctly flipped the source to "offline" when its folder was deleted and back
when it was restored.

An earlier entry here claimed the drawer "stopped reopening". It never did, and
the real cause is worth keeping: **the drawer repaints on its status poll** —
measured at 6 whole-subtree replacements in 12 idle seconds, the largest
swapping 141 elements. Any held reference to a node inside it goes stale within
about two seconds, which is what my `getElementById('system-nav')` probe and the
accessibility-tree refs were both tripping over. The drawer was open throughout,
as the page text said.

Two hypotheses were tested against that phantom — viewport, and the source being
offline — and both were disproved, which should have been the signal that the
measurement was wrong rather than the app.

The repaint costs nothing visible that could be measured here: text typed into
the drawer's manual-path field survives ten seconds and several repaints intact.
Whether focus survives could not be tested — a browser pane that is not
displayed does not accept focus at all.

**No screenshot.** The browser pane will not display in this environment, so the
page never composites — which is also why image `naturalWidth` stays 0 despite
the thumbnail fetches returning 200. Everything above was read from the DOM,
computed styles and the network log. That is weaker than looking at it.

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
- `./scripts/azimuth-check --quick` runs both sweeps below, so neither depends
  on anyone remembering them. Verified to exit 1 by deleting an import and
  watching it fail.
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
