# What to carve next, and how the list was made

Measured 2026-08-17 against `46,749` lines of backend Python (`web/`, tests
excluded). Every number here came out of a script, not a reading. Re-run it
before trusting any row — a stale roadmap is worse than none, which is why the
last one was deleted rather than corrected.

    python scripts/gates/check.py --list      # the gates, and every offending site

## The measures

These are CLAUDE.md's, applied mechanically rather than by eye. Each one earned
its place by finding something the others missed.

| measure | what it counts | what it caught |
|---|---|---|
| **unreferenced** | top-level functions whose name appears once in the whole tree | `sweep_cache` (unwired), `purge_expired_trash` (unwired *and* destructive) |
| **unreachable surface** | public functions no other file calls | `ratings.py` — 6 of 26, one caller, all six with no callers of their own |
| **blast radius** | files touched to add one sort / kind / family | sorts: **3**, and the middle one had drifted — three of seven sorts did not sort |
| **one sentence** | module docstrings needing an "and" | 53 modules; `backups.py` is snapshots *and* integrity audits |
| **does the product promise it?** | is there a route, a button, a declared ceiling | the question that decides wire-vs-delete |

The last is the one that matters most and cannot be automated. A function with
no caller is as often an unwired capability as it is dead code. `sweep_cache`
enforces a ceiling the app *declares and reports*, so wiring it made the product
true. `purge_expired_trash` deletes photographs on a schedule the product
mentions nowhere, so it went. Same signature, opposite answers.

## Ranked, as of this commit

Score is `dead_lines*2 + unreachable_public*12 + (lines/10 if two ideas)`. Route
modules are exempt from the reachability term — FastAPI calls them by decorator,
and a metric that does not know that reports every router as dead.

| lines | dead | public reached | file | the call |
|---:|---:|---|---|---|
| 533 | 224 | **0 / 12** | `features/library/taste.py` | **decide** — a real pairwise preference model over 2,532 duels and 42,937 vectors. The UI offers a Taste sort and has it disabled, because nothing returns `taste_available`. Wiring it means marrying a computed per-image score to a SQL-paginated query, which is the design the deleted `library/service.py` held. Not a patch. |
| 1245 | 0 | 6 / 24 | `features/system/backups.py` | **split** — 697 lines of catalog snapshots, 166 of original-file integrity audit, 79 touching both. Two modules. Both halves are promised and surfaced, so this is a split, not a deletion — worth doing only alongside a change that also removes something. |
| 1173 | 30 | 27 / 35 | `data/repositories/catalog.py` | **carve** — the last large repository. 13 callers, so it is the coupling to cut before `data/` can go. |
| 1902 | 16 | 6 / 23 | `data/schema.py` | **measure first** — 47 tables, 29 of which hold zero rows in the live catalog. Migrations are load-bearing for old catalogs; deleting steps needs a decision about which catalogs must still upgrade. |
| 412 | 81 | 4 / 7 | `elo_stars.py` | **check** — stars are a stored projection of Elo. `_rankings_moved()` now re-derives them; what remains may be the old scheduler around that. |
| 322 | 65 | 9 / 11 | `features/develop/presets.py` | **sweep** — 65 unreferenced lines. |
| 288 | 63 | 5 / 7 | `features/library/keywords.py` | **sweep** |
| 204 | 42 | 2 / 10 | `core/responses.py` | **sweep** |

## Two standing traps

**Route modules are not dead.** Fifteen files came back "0 of N public names
reached" on the first run of the reachability measure and every one was a
FastAPI router. Check what the entry point *is* before believing a count.

**Measure before declaring two things the same.** `catalog_sources` looked like
a duplicate of `drives` until the cardinality was checked — sources are scan
folders, drives are volumes, one drive holds several sources — and its stored
counts cost 80 ms to compute against 0 ms to read. Two wrong carves stopped by
measuring.
