# Azimuth 2.0 — the architecture

**Companion to [`docs/CORE.md`](CORE.md), not a replacement for it.** CORE.md
describes the core that exists: four facts, five tables, seven functions. This
describes everything around it — the layers, the eight surfaces, the data layer,
the 31,795-line UI that CORE.md never designs, and the gate CORE.md asks for and
does not have.

This document owns the intended V2 code shape. It is not proof that a surface
is complete: completion requires the running-product evidence named in
`CORE.md`, and release status lives nowhere in this document.

Every number here was measured on 2026-08-16 against this tree, or against the
live catalog at `C:\Azimuth Photo\data\catalog\azimuth.db` opened `mode=ro`. No
file in the repository was modified. The commands are in the appendix so any
figure can be re-derived rather than believed.

---

## Three facts that change the plan before it starts

**The backend is 53,471 lines, not 45,298.** The lower figure counts only
`web/`'s packages and omits 8,071 lines of depth-1 `web/*.py` — `db.py` (1,077),
`api.py` (876), `settings.py` (789) among them. Every "two thirds must go"
percentage derived from the smaller number is optimistic by 15%. The exact
accounting to the 115,005 headline:

| Bucket | Lines |
|---|---:|
| web backend (`web/**` py + sql, non-test) | 53,471 |
| UI (`web/static/**` + `web/templates/**`) | 31,795 |
| tests (`test_*.py`, `conftest.py`) | 18,317 |
| android (`android/**/*.kt`) | 9,551 |
| scripts (`scripts/*.py`) | 1,121 |
| other (`.claude/workflows`, `eslint.config.js`, `desktop/`) | 750 |
| **total** | **115,005** |

**There is no dead code left to find.** CORE.md's own import-graph walk reached
all but 2,650 lines, and its route enumeration found three shadowed handlers.
Two files are the whole of what this pass added: `features/library/storage.py`
(71) and `features/library/preview_priority.py` (52) have zero references
anywhere in the tracked tree. Every deletion in this document is a **rebuild**,
not a sweep. **A program that opens with a dead-code pass finds a thousand lines
and loses a week.**

**The route table is not the cost.** An `ast` pass over every decorated function
body in every non-test route file returns **197 routes and 2,660 handler lines —
density 13.5**. The HTTP surface is **5.0% of the backend**. The famous "24 lines
per route" is `876 / 37` and charges `api.py`'s module preamble and shared
helpers to its handlers; `api.py`'s own marginal density is **13.1**. So
collapsing 194 endpoints to 28 buys at most ~1,800 lines — 3% — and is paid for
by rewriting **184 `/api/` literal sites** in the UI that CORE.md names as the
one part that was ever good. **The rule that survives is where a route may live,
not how many there are.** The count is an output of the surfaces.

---

## 1. The one sentence

> **Azimuth is five tables and seven functions with five layers above them —
> `model/` (the tables, stdlib only), `compute/` (pure functions over bytes and
> numbers, which may not open a connection), `work.py` (owed is a query; the only
> file allowed a module-global registry), `surfaces/` (eight named projections
> that may not import each other) and `routes/` (the only place a route decorator
> may appear) — so every feature is a query with a name plus the decisions it
> writes, and anything that is not a query, a decision or a cache kind has no
> layer to live in.**

The last clause is the architecture. It is an exclusion rule, and an exclusion
rule is the only kind of sentence that keeps a codebase small after its author
stops watching. Nine of the sixteen feature packages exist because a bounded job,
a named set or a computed answer had nowhere to live, so it invented a table, a
dict and a status route.

---

## 2. The layers, and the import rules that hold them

Each rule is one command, and each carries the count that command returns today.
Three return zero or near zero. Those go into CI in the first commit, where they
can only go red by someone doing the wrong thing. That is the beachhead the rest
ratchet onto.

| # | Rule | Violations today |
|---|---|---:|
| **R1** | `model/` imports nothing under `web/` except its own siblings | **0** |
| **R2** | Nothing imports `app` or a `routes` module | **1** for `app` (a script), **10** for route modules |
| **R3** | No `CREATE TABLE` outside `model/schema.sql` | **68 statements in 13 files** |
| **R4** | No route decorator outside `routes/` | **160 of 197, in 26 files** |
| **R5** | A `compute/` file imports only stdlib, third-party maths, and `compute/` | **4** |
| **R6** | No module-global mutable registry outside `work.py` | **78 in 40 files** broad, **4 in 4** if mutation is required — *predicate not yet pinned, so this rule is not gateable* |
| **R7** | No `surfaces/` module imports a sibling surface | **21** |
| **R8** | Nothing imports the `db` facade | **28 files** |
| **R9** | No `/api/` literal outside `net/`; `net/routes.js` ⊆ `GET /api/routes` | **16 dead paths** |

**Total 308 violations across the eight gateable rules.** Every one is a counted
command with a named fix. R6 is excluded from the total on purpose: a rule whose
count moves from 4 to 78 with the predicate is not a rule yet, and a total that
absorbs it would be the kind of number this document exists to stop.

### L0 — `web/model/` (1,250 lines today)

Owns the five tables, the seven functions, `schema.sql` as the only
`CREATE TABLE` in the tree, and the connection rules: busy-timeout is a property
of the request, ephemeral-WAL close is `PASSIVE` and never `TRUNCATE`,
`asyncio.run` inside a worker thread destroys the loop.

*May import:* stdlib, `sqlite3`, siblings.

Verified. `grep -hnE '^(from|import) ' web/model/*.py | sort -u` returns
`__future__`, `dataclasses`, `typing`, `hashlib`, `json`, `os`, `shutil`, `stat`,
`time`, `uuid`, and `from model import copies, drives, photos`. **R1 costs
nothing to adopt and is the first line of the gate.**

Against it: `web/data/schema.py` imports `ensure_virtual_copies`,
`ensure_develop_presets` and `ensure_image_quality` **from `features/`** at
migration time. The dependency runs backwards, which is exactly why R3 cannot be
switched on today.

### L1 — `web/compute/` (new)

Pure functions over bytes and numbers. The 21 colour-mathematics files move here
**byte-identical**. Also: decode, fit and encode; content hashes; native EXIF and
image headers; date inference; the `(date, kind, roll) → tail` rule; the quality
heuristics; the XMP serialiser; the Lua `.lrcat` parser; the DCP reader; the
rclone argv builder.

*May import:* stdlib, `numpy` / `PIL` / `rawpy` / `tifffile` / `lensfunpy` /
`onnxruntime`, and other `compute/` modules. **Never** `model`, `work`,
`surfaces`, `routes`, `fastapi`, `sqlite3`.

Measured. Scanning all 21 named files for imports that escape the set returns
**exactly four**:

```
web/features/develop/looks.py:10      from features.develop.xmp_write import _normalize_value
web/features/develop/masks.py:21      from core import pil_limits
web/features/develop/masks.py:23      from core.runtime_paths import resolve_runtime_paths
web/features/develop/discovery.py:5   from core.runtime_paths import _platform_family
```

`core.numbers` appears seven times and is itself pure — 86 lines importing only
`math`, `collections.abc` and `typing` — so it moves into this layer rather than
counting against it. Two of the four reach for a *private* symbol, which is the
alias trap: an import-graph walker keyed on module names sees them, a reader
looking for public API does not.

`masks.py:21` is the one that matters. A file declared pure disables PIL's
decompression-bomb limit **process-wide, at import time**. Fixing those four
lines is the entire cost of making *"the maths survives verbatim, the plumbing
dies"* executable rather than aspirational.

### L2 — `web/work.py` (385 lines today)

Owed is a query — the anti-join. One worker. Lanes as an integer. The politeness
gate. Cache kinds are explicit immutable capabilities passed in by boot;
importing a feature cannot silently alter the work graph. `cache.evict` uses
those same capabilities against one ceiling. **This file may own a running
worker instance, but no process-global work or kind registry.**

*May import:* `model`, `compute`.

R6 is the highest-yield rule in the set, and the reason is one signature:

```python
def owed(conn, kind: cache.Kind, *, recipe: dict | None = None,
         on_screen: Iterable[int] = (), limit: int = 200) -> list[dict]:
```

**There is no scope parameter.** A bounded piece of work cannot name itself, so
it invents a dict. **How many is definition-dependent, and no committed script
pins the definition** — every module-level dict, list or set in `features/` and
`core/` counts 78 across 40 files; restricted to those mutated after definition
in their own file, 4 across 4. Do not plan a deletion on either number until the
predicate is written down and committed; the command that produced both is in the
appendix.

The number that does not move is the machinery: `core/background.py` 444,
`memory_pressure` 615, `model_pool` 618, `bulk_scheduler` 187,
`propagation_queue` 108, `hdd_governor` 84 — **2,056 lines, verified file by
file** — plus 35 start-a-job or ask-a-status routes. `work_coordination` 341 was
the seventh and is gone already: it did not need `owed(scope=)` to replace it,
because nothing production read the lease state it kept. Measure before
scheduling the demolition — one of the seven had already fallen over.
`background.py` reaches up into eleven feature modules purely to start their
loops, which the `layers` gate counts.

### L3 — `web/surfaces/` (eight files)

`library.py · rank.py · search.py · develop.py · importer.py · organise.py ·
keep.py · ai.py`. Each is named queries over L0 plus calls into L1, plus the
decisions it writes. Nothing here owns a table, runs a loop, or holds state
between requests.

*May import:* `model`, `compute`, `work`. **Never a sibling.**

Measured: **21** cross-package import lines in `web/features/`. Twenty-one is not
a small number once you see what hangs off them. `features/quality/autocull.py`
is the **only** importer of `features/library/taste.py` (533 lines) in the whole
tree, and it reads `stacks` and `stack_members`, which hold 0 rows, so the call
always returns nothing. `features/trash/service.py` is the single line holding up
`stacks/builders.py` + `search/similarity.py` + `search/service.py` — 664 lines —
against the same two empty tables.

### L4 — `web/routes/` (at most six files)

Every route decorator in the tree. A handler is a name, an argument or two, one
call, and the five state words computed at read time. No `if`, no threshold, no
state.

*May import:* everything below. **Nothing may import it.**

Six files, not one. At 13.5 lines per route even a heavily reduced table does not
fit 850 lines in a single file honestly, and a cap of six preserves what actually
matters: a **seventh** file fails the build, so a new route must be added to a
budgeted directory rather than dropped into its own package.
`features/imports/routes.py` and `staging.py` import `features.quality.routes`
and `features.catalog.routes` purely to trigger work; with a scoped `owed` they
call `work.ask` and R2 goes to zero.

### L5 — `web/static/` (`kit · net · store · shell · lens`)

Nobody has governed 31,795 lines of UI, and gating only the backend relocates
regrowth rather than stopping it. Section 5 gives this layer the same treatment
as the backend.

---

## 3. The core primitives to add

Seven, of which one is a keyword argument rather than a new function. Two more
that were proposed are cut, because a plan whose first move is seven simultaneous
changes to a core that `CORE.md` calls *done* is not the low-risk path.

| Primitive | Signature | What it makes unnecessary | Freed |
|---|---|---|---:|
| **`owed(scope=)`** *(a parameter)* | `owed(conn, kind, *, scope=None, recipe=None, on_screen=(), limit=200)`<br>`ask(conn, kind, subjects, recipe=None) -> job_uuid` | The module-global ledgers — count not yet pinned, see R6 — and every scheduler around them. A job becomes `decide(job_uuid, CHORES, {...})` plus an anti-join over its subjects; its status is `len(remaining)`; cancelling it is forgetting the row. No queue, no lease, no visibility timeout, because a worker that dies leaves nothing to expire. Deletes `core/background.py` 444, `memory_pressure` 615, `model_pool` 618, `bulk_scheduler` 187, `propagation_queue` 108, `hdd_governor` 84 — **2,056 measured** — plus the ledger machinery inside the features and 35 job/status route handlers. `work_coordination` 341 was on this list and is already gone: nothing production read its lease state, so it needed no replacement at all. The `chores` decision family already exists and holds 2 rows: the shape is proven, it just has no scope. | ~6,200 |
| **`photos.move`** *(the eighth function)* | `move(conn, photo_id, drive_uuid, tail) -> str` | Every relocation in the tree. Writes to the new place, verifies the digest, updates `photos.tail` and `copies` in one transaction, refuses if the destination exists. Collision-proof **before** any unlink, which makes the guard rails unnecessary rather than better. Deletes `imports/relocation.py` 293, `imports/move_journal.py` 104 (whose `taxonomy_move_journal` table is **absent from the live catalog** — it has never held a row), `imports/service.py` 166, taxonomy's reclassify pass, and collapses `trash/service.py` 938 → ~200. **This discharges a named guard rail:** `trash/service.py` is a bare `os.rename` whose `_inspect_trash_file` treats `FileNotFoundError` as success. | ~1,500 |
| **`Scope`** *(one filter value)* | `Scope(sql, args)` composed by `all_of`, `folder`, `starred`, `in_set`, and `ids` | Makes a folder, collection, keyword, star floor, search result, and export selection **the same argument** without inventing a string query language. `ids` binds one JSON value, so a whole-library selection never hits SQLite's parameter ceiling. Saved views wait for a real typed query language. `library.photos()` gains `scope=` and loses its filter keyword ladder. | ~1,350 |
| **`photos.group`** *(`version_of` as a query)* | `group(conn, photo_id) -> list[int]` | Raws, exports and virtual copies as one nullable column instead of three mechanisms. Deletes `virtual_copies.py`'s boot-time rewrite of the `images` table by regex over its own `CREATE TABLE` text, `stacks/builders.py` 539 with its `_UnionFind` and exiftool subprocess, `search/similarity.py` 45, `quality/autocull.py` 237, and trash's family expansion. **Must not rename `vc_of`**, which means "this row does not own a file" and carries a `UNIQUE(filepath) WHERE vc_of IS NULL` index and an `ON DELETE CASCADE`. | ~1,050 |
| **`decisions.amend`** | `amend(conn, subject, family, patch: dict) -> int` | The mechanical form of a named guard rail. `develop/routes.py:466–523` is 58 lines of settings-merge whose job is to keep 377 mask rows and 7,035 `LocalExposure2012` values alive through a partial save. As a primitive it is ~25 lines used by develop, keyword add and remove, IPTC writes and set membership. **Doctrine decays; a merge that is the only way to write a partial does not.** | ~340 |
| **`decide(by=)`** *(provenance as a column)* | `decide(conn, subject, family, value, *, at=None, by='you')` — `by ∈ {you, file, lrcat, oplog}` | Verified on the live catalog: `develop_settings.origin` is **79,482 `'xmp'` against exactly 5 `'user'`**. That column is what stops an XMP re-import overwriting an edit the owner made here, and today the rule is a `WHERE` clause copied into separate writers (`develop/importer.py:191`, `develop/xmp_write.py:437`). With `by`, "an outside answer never overwrites yours" is one comparison in one place, and every future adoption — XMP, `.lrcat`, oplog, darktable — is the same shape. | ~420 |
| **`decisions.current(glob=)`** *(a keyword)* | `current(conn, family: str, *, glob: bool = False)` | Makes settings, keywords, set membership, per-folder names and per-drive labels one call. A setting becomes `decide(machine_uuid, 'setting:<key>', value)` — the shape `work.paused()` already uses. Deletes `settings.py`'s JSON store and its per-key side-effect ladder (789 → ~120, over 66 `DEFAULT_SETTINGS` keys), `core/stored_json.py` 22, `features/settings/status.py` 215. | ~900 |

**Cut, deliberately.** `cache.note()` is unnecessary: with `scope=` on `owed`, a
job's state *is* `len(owed(kind, scope))` and its identity is a decision row, so
there is nothing disposable left to store. Opaque saved-query payloads are cut
too: persisting a value no evaluator can execute is not a saved view. `sets.py`
owns only the honest policy around decision rows—stable ids, descriptors,
membership, and counts—and adds no table or alternate source of truth.

### One primitive on the seam, and it is three lines

```
GET /api/routes  ->  [path]      # over the mounted app's OpenAPI paths
```

CI asserts that `net/routes.js` is a subset of that list, and that no `/api/`
literal exists outside `net/`.

**This gate fails on the tree as it stands.** Diffing every `/api/` literal in
`web/static` and `web/templates` against all 170 declared route paths, then
confirming each by hand, gives **sixteen paths that live UI code calls and no
handler in the tree serves**:

```
/api/lr/status              /api/lr/connect
/api/people/{id}/label      /api/people/{id}/ignore
/api/people/merge           /api/people/merge-suggestions/{id}/reject
/api/people/scan/pause      /api/people/scan/resume
/api/captions/scan/pause    /api/captions/scan/resume
/api/propagation/last       /api/auth/key
/api/pair/connect           /api/discover
/api/remote-access          /api/remote-access/serve
```

Thirteen of the sixteen are called from `web/static/js/desktop/api.js` over
fifteen sites — `/api/lr/connect` appears three times — and three from
`web/templates/setup.html`: sixteen paths over eighteen sites, which is what
`python scripts/gates/check.py --list` prints. **A line budget prices growth but
cannot see a seam rotting.** This one costs 77 lines of Python and names every
broken call site.

---

## 4. Every surface

The eight surfaces of the target tree, mapped onto what serves them today. Each
row's "today" is the measured sum of the files listed under it; each budget names
its contents. Surfaces account for **39,526** of the backend's 53,471 lines. The
residue — `data/schema.py`, `db.py`, `api.py`, `core/`, `model/`, `work.py`,
`model/schema.sql` — is 13,945 and is handled by the layers rather than by a
surface.

| Surface | Today | Routes | Target shape, in one sentence | Budget |
|---|---:|---:|---|---:|
| **library** | 7,042 | 35 | The queries the grid reads — folders, filters, counts, folder tree — over one `scope` argument, with no path in any of them. | 500 |
| **rank** | 2,146 | 0 | Comparisons are decisions; Elo and taste are derivations recomputed from 2,532 pairs and the embeddings, through one sort registry that rejects a sort it does not know. | 300 |
| **search** | 570 | 0 | One query with ranked fusion — filename, keyword and EXIF always, embeddings when they exist — that degrades and never blocks. | 250 |
| **develop** | 11,673 | 41 | Edits are decisions, decode and render are cache, one function at three sizes serves grid, Develop and export; the 5,605 lines of colour mathematics move to `compute/` untouched. | 700 |
| **importer** | 3,796 | 16 | `identify` + `put` over one pure `(date, kind, roll) → tail`, with `synchronize`'s `changed` word as the only state. | 450 |
| **organise** | 4,507 | 27 | A set is a decision family and membership is a subject in it; there is no collection table, no stack table and no saved-view table. | 300 |
| **keep** | 7,817 | 31 | Trash, backup, reclaim, health, settings, quality and export as queries over the five tables, with the restore engine and the always-keep-premigrate rule carried through intact. | 800 |
| **ai** | 1,975 | 4 | Embeddings, captions and faces registered as cache kinds with a `here()` and a cost, never a precondition for anything. | 300 |
| **totals** | **39,526** | **154** | | **3,600** |

What each rebuild deletes, with the measured line counts:

**library** — `features/catalog/synchronize.py` 504 (superseded by
`web/synchronize.py` 242, already live). `features/catalog/metadata.py` is now
deleted: its ThreadPoolExecutor, pause and resume verbs, status dict, and
orientation-failure retry ledger all violated *owed is a query*. Remaining debt:
`repositories/catalog.py` 1,307, `repositories/images.py` 336,
`core/query_constraints.py` 432, `repositories/filter_options.py` 95,
`features/library/storage.py` 71 and `preview_priority.py` 52 (**both have zero
references anywhere in the tracked tree**). *Carried, not deleted:* the
orientation rule — **a stored dimension is a cached answer and can be stale; the
photograph cannot** — moves with the work.

**rank** — `repositories/ratings.py` 1,133, `elo_stars.py` 414,
`shoot_rank.py` 214, `features/library/taste.py` 533 and
`preference_model.py` 62. `web/rank.py` (277) already does this on the core.
The judgement is already in the log: `compare` 2,532 rows and `star` 6,263.

**search** — `features/search/service.py` 80, whose `visible_embedding_page` has
zero callers and whose only live symbol is a two-line dict invalidated by a
cache that `/api/duplicates` does not read; `features/search/similarity.py` 45,
guard-railed only because `stacks/builders.py` is its second caller.

**develop** — the 202-generating admission protocol, the
`_base_generation_failures` TTL ledger, two thread pools, an RLock'd proof cache,
the 16-byte `PABASE1` gzip payload format with its OrderedDict LRU,
`develop_settings` (79,487 rows, against a `develop` decision family already
holding 79,512), `develop_presets` (994 rows), `develop_history` (8 rows), and
`virtual_copies.py`'s boot-time regex rewrite of the `images` table. *Handled,
not deleted:* the settings-merge becomes `decisions.amend`; `origin` becomes
`by=`.

**importer** — `relocation.py` 293, `move_journal.py` 104, `service.py` 166,
`staging.py`'s Scan and ImportJob registries, `scanner.py` 230,
`repositories/imports.py` 277. **Verify `web/synchronize.py` carries the
`changed` word before deleting `features/catalog/synchronize.py`** — that one
word is the whole Lightroom save-metadata answer.

**organise** — `features/collections` 2,167, `features/stacks` 1,249,
`repositories/collections` 409, `repositories/stacks` 682,
`library/keywords.py` 518, `library/saved_views.py` 139, and eleven tables.
Verified on the live catalog: `collections` 0, `collection_images` 0,
`collection_links` 0, `saved_views` 0, `stacks` 0, `stack_members` 0,
`iptc_fields` 0, `autocull_history` 0, `people` 0, `person_image_membership` 0,
`keywords` 1, `watched_folders` 1 — while `decisions.keyword` holds 296 rows and
`decisions.collection_meta` holds 5. **40 of the catalog's 86 tables hold zero
rows.**

**keep** — `settings.py`'s JSON store 789 → ~120, `features/settings/status.py`
215, `system/health.py`'s nine bespoke probes and its `_worst()` folder (579 →
~140), `repositories/stats.py`'s three-tier TTL cache 644,
`backup/cloud.py`'s persisted status file and its runtime mirror (840 → ~300),
`trash/service.py`'s repairs over zero-row tables, `system/backups.py`'s
`image_checksums` integrity audit (0 rows). *Blocked on:* `model/backup.py` (183
lines) has one caller and it is `scripts/back_up_photos.py` — no `web/` code
reaches it. Route the surface onto it or delete it, but do not leave two paths.

**ai** — `repositories/embeddings.py` 477 (`embeddings_by_model` holds 42,937
rows and `cache` holds 42,937 rows of `kind='embedding'` — the migration is
finished and this is the duplicate), `repositories/captions.py` 567 (every table
it owns holds 0 rows), `ai/routes.py`'s 166-line status composer and its response
cache, `core/model_pool.py` 618.

**And the facade.** `db.py` (1,077) belongs to no surface. Its own docstring
reads *"Compatibility facade… Do Not Add New Logic Here"*.
`grep -rn 'from db import'` returns **0** across the tree, so every use is
`db.NAME` and the count of 28 dependent files is exact rather than a lower bound.
It dies when the last surface stops importing it, and not before.

---

## 5. The UI

31,795 lines across 121 files. CORE.md says the UI is the one part that was ever
good and is not being rewritten. That is right about the grid, the loupe and the
keyboard, and it is not a reason to leave the layer ungoverned: **a gate that
covers only the backend moves regrowth, it does not stop it.**

Three measurements say where the shape is wrong.

**`fetch(` appears in 16 files and `/api/` literals appear 184 times.** There is
no seam. `web/static/js/desktop/api.js` is 608 lines holding 118 exported
functions, of which **104 have two body lines or fewer, totalling 322 lines**. A
wrapper per endpoint is a second copy of the route table maintained by hand,
which is precisely how sixteen wrappers pointing at deleted handlers survived
undetected.

**`web/static/js/desktop/keyboard.js` is 600 lines importing 21 lens modules by
name**, and the same key table is written a third time as 73 `<kbd>` elements in
the templates. Until that inverts — lenses register chords, the shell reads the
registry — no *shell may not import a lens* rule can be switched on at all.

**`innerHTML =` appears 181 times and `esc(` appears 373 times.** Escaping is a
thing a person remembers to do, 373 times, and the failure is silent in both
directions: a missed `esc` is an injection, a doubled `esc` is a visible `&amp;`.

### The layers

| Sub-layer | Owns | May import |
|---|---|---|
| `kit/` | the `html` tagged template (escapes by construction), `el()`, formatters, motion, focus trap, icons | `kit/` |
| `net/` | one `call(name, params)`, one `photoUrl()`, one `owed()` poller, and `routes.js`. **The only place `fetch(` appears.** | `kit/` |
| `store/` | scope (the query object), view, selection, `on/emit`, hash sync. The only module-level mutable state and the only `localStorage` — 52 sites today. | `kit/`, `net/` |
| `shell/` | toast, contextMenu, **keymap** (one document keydown listener over a chord→verb registry, against 38 today), layers, emptyState, jobs, cell, lensRouter | `kit/`, `net/`, `store/` |
| `lens/<name>/` | one surface, exporting exactly `query`, `cell`, `keys`, `mount/unmount` | everything above, and its own directory. **Never another lens.** |

### One way to do each recurring thing

| Thing | Today | The one way |
|---|---|---|
| call the backend | 184 `/api/` literals, 16 files with `fetch(`, 104 hand-written wrappers | `net.call(name, params)` against `routes.js`, which CI diffs against `GET /api/routes` |
| build markup | 181 `innerHTML =`, 373 `esc(` | the `html` tagged template — escaping by construction, so there is nothing to remember |
| a keyboard chord | one 600-line module importing 21 lenses, plus 73 `<kbd>` elements | a lens exports `keys`; the shell reads the registry and renders the sheet from it |
| ask what work is left | four status modules polling four endpoints | one `net.owed()` poller against `GET /api/work/owed` |
| remember something | 52 `localStorage` sites | `store` |

### The budget

| | Lines |
|---|---:|
| today | 31,795 |
| **files that die outright** | **−2,188** |
| `people.js` 643 · `collections.js` 251 · `cull_brief.js` 311 · `stack_cull.js` 63 · `similar.js` 43 · `jobs.js` 28 — every table behind them holds 0 rows, and six of `people.js`'s endpoints are in the dead sixteen | |
| `sw.js` 25 — a service worker in a desktop webview | |
| `develop/ops_constants.js` 308 · `guided_filter.js` 211 · `film.js` 162 · `dng_glsl.js` 143 — line-for-line ports of Python that already exists (`guided_filter.py` is 210 against the .js's 211) | |
| **rewrites, itemised** | **−2,457** |
| `api.js` 608 → 120, once `net.call` replaces 104 wrappers | −488 |
| `keyboard.js` 600 → 150, once lenses register their chords | −450 |
| `import_stage.js` 826 → 350, once staging is *a photo with no tail* | −476 |
| four status modules (`system_health` 100 · `library_health` 255 · `cloud_backup` 309 · `quiet_sources` 91) merged onto one poller | −405 |
| three source/drive dialogs (`source_picker` 204 · `library_manage` 178 · `watched_folders` 122) merged | −304 |
| `setup.html` 534 → 200, once the pairing block and its three dead endpoints go | −334 |
| **the new layer** | **+1,550** |
| `kit/` 400 · `net/` 250 · `store/` 300 · `shell/` 600 | |
| **budget** | **28,700** |

**CORE.md's 25,000 is refused, and the refusal is the honest half of this
document.** Nothing counted above reaches it. Closing the last 3,700 lines is a
question about what the grid, loupe and develop lenses *are*, and nobody has
answered it with counted lines. **A target reached by lowering a number in a
table is not a target.**

The one rule about how this lands: **the `html` migration touches 181
`innerHTML` assignments and 373 `esc(` sites, and it lands with each lens's own
rewrite, never as a repo-wide sweep.** A sweep across that many sites is how
blank renders and double-escapes ship.

---

## 6. The budget

### Backend

| Layer | Today | Budget | The arithmetic |
|---|---:|---:|---|
| **L0 `model/`** | 1,250 | **1,600** | 1,250 today (`web/model/*.py` 1,148 + `schema.sql` 102). Grows: `schema.sql` absorbs the surviving `images` DDL so no `CREATE TABLE` exists elsewhere (+70); `data/connection.py` folds in at 180 of its 340 (+180); the new primitives land here — `move` +60, `amend` +25, `group` +40, `scope` +120. Trimmed by dropping the unused half of `backup.py` (−145). |
| **L1 `compute/`** *(new)* | 0 | **8,600** | 21 maths files **5,605** (measured, `wc -l` over the files CORE.md itself names) + rawproc's decode and white-balance 373 + render geometry/resize/TIFF writer 292 + hdr merge 330 + XMP serialiser 450 + Lua `.lrcat` parser 193 + `photo/kind.py` 75 + image headers 350 + date inference 95 + `(date,kind,roll)→tail` 180 + quality heuristics 250 + rclone argv and stats regex 250 + `core/numbers.py` 86 = **8,529**. |
| **L2 `work.py`** | 385 | **500** | 385 today, plus `scope` (+70) and the kind registrations that move in from the deleted schedulers (+45). |
| **L3 `surfaces/`** *(new)* | 0 | **3,600** | The eight rows of §4: 500 + 300 + 250 + 700 + 450 + 300 + 800 + 300. `keep` is largest because the restore engine and the retention rule are guard-railed and survive intact. |
| **L4 `routes/`** *(new, ≤6 files)* | 0 | **1,800** | ~70 surviving routes × **13.5** measured lines per route = 945, plus ~140 lines of preamble and shared helpers × 5 files = 700 → 1,645, rounded to 1,800. The 70 is an output, not a target: 35 job and status routes die with the ledgers, ~30 named-set routes die with the empty tables, 16 already 404. |
| | **53,471** | **16,100** | |

### The whole tree

| Area | Today | Budget | The arithmetic |
|---|---:|---:|---|
| backend | 53,471 | **16,100** | above |
| UI | 31,795 | **28,700** | §5, itemised to the file |
| tests | 18,317 | **5,000** | Reached by arithmetic, not cleverness: old tests die in the commit that deletes their code. Named now: `test_collections.py` 796 (0 rows), `test_settings_status.py` 690, `test_import_staging.py` 563, `test_catalog_relocation.py` 308, `test_import_move.py` 298, `test_trash_schema_deletion.py` 297 = **2,952**. What earns the 5,000: the seven functions (`test_core.py` 515), the develop acceptance set (**2,351 lines** across `test_develop_parity`, `test_dng_acceptance`, `test_dng_pipeline`, `test_develop_masks`, `test_develop_pipeline`, `test_develop_film`, `test_develop_guided_filter`, `test_develop_looks`, `test_develop_lens`, `test_develop_ramps`, `test_develop_autotone`, `test_develop_rangemask`, `test_develop_sigmoid`, `test_develop_grade`, `test_develop_detail2`, `test_develop_heal`, `test_develop_invariants`, `test_develop_transform`, `test_develop_calibration` — **the maths cannot be moved without it**), the five acceptance properties, the perf budgets, and one test per appendix lesson a naive rewrite would destroy. |
| scripts | 1,121 | **500** | Keep `azimuth-check`, `make_test_library`, `run_test_app`, the Windows shell, and the new gate. `give_photos_tails.py` and `purge_phantom_rows.py` are one-off migrations that die the day they finish — and **`give_photos_tails` has not finished: 12,965 rows still carry a NULL or empty tail**, up from CORE.md's 12,793. |
| other | 750 | **400** | Lane-era one-offs go. |
| android | 9,551 | **0 or 9,551** | An owner decision, stated both ways. CORE.md: *"The seven functions are the API; HTTP is a transport and it goes too. The phone is parked."* A client whose transport is deliberately deleted cannot run, be tested, or compile against anything — 9,551 lines, 8.3% of the tree, larger than the whole budgeted backend minus the maths. It is the largest single-commit deletion available and it has **zero** anti-regrowth value, so it must not be counted as progress. |

```
backend        16,100
UI             28,700
tests           5,000
scripts           500
other             400
               -------
               50,700      android deleted
android        +9,551
               -------
               60,251      android kept
```

**115,005 → 50,700. Not 40,000.** The gap is itemised, not hedged:
**+4,600** because CORE.md's own develop budget of 3,500 is arithmetically
impossible against the 21 files CORE.md itself names, which measure 5,605; and
**+3,700** because a 25,000-line UI target is a 21% cut of code the same document
says not to rewrite. **CORE.md's "≈40k total" and its "develop/ 3,500" row must
be amended in the same commit that lands this document.** A core document
carrying one known-false number teaches the next reader that its numbers are
aspirational, and that is how doctrine decays.

### The gate — count couplings, not lines

CORE.md asks for *one gate: a line budget that fails the build*. It was built,
run, and refuted. **Deleting every blank line removes 13,603 of 115,007 lines —
11.8% — and every file still compiles.**

```
$ git ls-files | grep -E '\.(py|js|css|html|kt|sql)$' \
  | xargs awk '{t++; if ($0 ~ /^[ \t]*$/) b++} END{printf "%d of %d = %.1f%%\n", b, t, 100*b/t}'
13603 of 115007 = 11.8%
```

Nothing in this stack can see that edit: `web/pyproject.toml` ignores `E501` and
`E702`, `eslint.config.js` holds three AST-scope rules, and CI's JavaScript job
is `node --check`, a parser. So a line budget is satisfiable by a commit a
reviewer calls *formatting*, which leaves the code denser and the number
greener. **A gate that a formatter can move by 11.8% prices nothing.**

The gates that shipped count something a formatter cannot touch:

> **Every number goes up when you add code and down when you delete it. There
> is no line you can add that makes one of them smaller.**

| Gate | Counts | Today |
|---|---|---:|
| `seam` | UI `/api/` literals no handler serves | **16** |
| `layers` | imports pointing up the stack | **21** |
| `tables` | table DDL outside `web/model/schema.sql` | **68** |
| `invokes` | paths a build file names that are not tracked | **4** |

A stranded call site, an upward import, a hand-made table and a missing script
are couplings and lies. Writing more code produces more of them, never fewer —
which is the one property a gate on this project cannot do without.

`scripts/gates/` is **286 lines of Python and an 8-line budget file**, six files,
one gate each, stdlib only, no config and no allowlist. `python
scripts/gates/check.py` runs in **0.6 s** and prints every offending site. A
number falls by measurement and rises only by hand: `--write` records a fall and
refuses a rise, so growth is a line somebody typed under a subject that has to
say why.

**Do not install it beside a broken gate.** `.github/workflows/ci.yml` runs
`python scripts/bench.py --check` and **`scripts/bench.py` does not exist** —
`git ls-files | grep -c scripts/bench.py` returns `0`, and `web/perf/` is empty,
so `standing.py` and `baseline.json` are absent too. The reason nobody noticed
is worse than the bug: `gh api repos/:owner/:repo/actions/runs --jq .total_count`
returns **0**. **CI has never run, once.** `scripts/lint` is not wired into it
either. Fix that in the same commit as the gates, or they inherit its
credibility.

### The order

Each surface lands whole, on the core, with its machinery deleted in the same
commit and every gate number it lowers recorded in the same diff.

| Step | Lands | Deletes |
|---|---|---|
| **0** | `scripts/gates/` into a CI that runs; the broken perf job fixed or removed; `scripts/lint` wired | nothing — **the only step that adds more than it removes, and the reason the rest can be trusted** |
| **1** | `model/` and the schema, once: `decide(by=)`, `photos.move`, `photos.group`, `decisions.amend`, `schema.sql` as the only `CREATE TABLE` | `data/schema.py` 2,393, and the three `features/` imports that make the dependency run backwards. **`by` is an `ALTER` on the one irreplaceable table — 89,374 rows, written to every record drive — so it lands with the schema or not at all**, otherwise two machines disagree about what a decisions row means |
| **2** | `work.py` gains `scope`; `base` becomes a kind, and every other computed answer registers as one | the 52 ledgers and 2,397 lines of scheduler. Registering `kind='base'` is a **repair, not a refactor**: `rawproc.py:628` registers it, but the live `cache` holds only `embedding` 42,937 and `tile` 5,322 and **zero `base`**, so the Develop base cache is unbounded loose files on this machine right now. **Each new kind's `wants` predicate is a silent failure mode** — being wrong makes the anti-join return fewer rows and the work is simply never done, the way 12,853 tail-less rows once livelocked tiles at 95. Each needs a counted acceptance: `debt()[kind]` before and after |
| **3** | Library, and the seam pivot. `GET /api/photos?scope=` is implemented with typed refusal and index proof; it must be mounted in the native app before a single old browse route is removed | `collections/smart.py`, `library/saved_views.py`, `collections/graph.py`, `core/query_constraints.py`, `repositories/filter_options.py`. If the UI cannot be moved onto `scope`, `routes/`'s budget is wrong and the UI is not |
| **4** | Organise: a set is a decision family | `features/collections`, `features/stacks`, two repositories, and eleven zero-row tables |
| **5** | Keep | `settings.py`'s JSON store, `settings/status.py`, `health.py`'s nine probes, `stats.py`'s TTL tiers, `cloud.py`'s status file and its runtime mirror, `backups.py`'s integrity audit over 0 rows |
| **6** | Import and Synchronize | `relocation.py`, `move_journal.py`, `service.py`, `staging.py`'s registries, `catalog/synchronize.py`, `scanner.py` |
| **7** | Develop — fix the four L1 violations first, then move 5,605 lines of maths **verbatim** | the admission protocol, the failure ledger, two thread pools, the proof cache, the `PABASE1` format, `develop_settings`, `develop_presets`, `develop_history` |
| **8** | Rank, Search, AI | `repositories/ratings.py`, `elo_stars.py`, `shoot_rank.py`, `taste.py`, `preference_model.py`, `repositories/embeddings.py`, `repositories/captions.py`, and finally `db.py` |
| **9** | the UI, per lens: `net/` and the manifest, then `store/`, then `shell/keymap`, then one lens at a time | see §5 |
| **10** | the android decision, put to the owner with both totals stated | — |

### Develop's acceptance criterion — the one thing the owner still owes

Step 7 moves 5,605 lines of colour mathematics. It is the one seam where being
wrong is silent: the output is a plausible photograph that is not the one the
owner made. Without a criterion, *"the maths survived"* is unfalsifiable.

**Measured first, because it changes the answer. The suite that looks like an
acceptance set is not one.** `web/test_develop_parity.py:25` renders a **64×64
synthetic hue field** built with `np.mgrid`. The one corpus test,
`test_dng_acceptance.py:21`, skips itself unless `AZIMUTH_RUN_DNG_ACCEPTANCE=1`,
and `grep -c AZIMUTH_RUN_DNG_ACCEPTANCE .github/workflows/ci.yml` returns **0**.
**Today the colour maths is proven against generated pixels and nothing else.**

Proposed criterion, three parts:

**Which images.** Four sets, all small enough to render in one command.

| Set | Count | Why it is in |
|---|---:|---|
| every `origin='user'` develop row | **5** | `develop_settings.origin` is 79,482 `xmp` against exactly **5** `user`. These are the only edits the owner made in this application, so all five are in the set by construction |
| the film roll CORE.md proved orientation on | 40 | already has a known-correct answer: 18 turned, 22 upright |
| one frame per camera model in the archive | ~12 | the profile chain differs per camera and nothing else exercises it |
| the DNG corpus `test_dng_acceptance.py` already reads | corpus | Adobe's embedded previews are the only outside opinion available |

**What counts as identical.** Not bytes — a different libjpeg build changes bytes
and no pixel, and a test that fails for that reason gets a tolerance band bolted
on, and a tolerance band is a threshold, and a threshold is exactly what this
maths must never acquire.

> **Maximum absolute channel difference of zero, on the 16-bit linear array,
> before encode, at the size the grid asks for.**

Zero, not a band. A difference is a defect, not noise.

**When it runs.** Once before the first line of Develop moves, once after, same
command, same files, both numbers in the commit message. **A passing suite is not
the evidence. The rendered pair is.**

The rule that follows belongs in CORE.md's appendix: **an acceptance set is built
before the step it protects, never during it.** A set assembled while the code is
in motion tests the code that is in motion.

---

## 7. What is deliberately not solved

**The sixteen dead endpoints are detected, not decided.** The route-diff gate
makes them fail the build. Whether the answer is to build the handlers or delete
the call sites is a product decision, and it covers people, captions, Lightroom
connect, remote access, pairing and discovery. The default assumption is *delete
the call sites* — `people` and `person_image_membership` hold 0 rows, and
`/api/people/status` reports `available: false`, so a lens whose kind cannot be
computed should not be offered rather than rendered empty — but that is the
owner's call. **The gate's job is to force the decision, not to make it.**

**The route count.** Collapsing 194 endpoints to ~28 is dropped. It buys ~1,800
lines, 3% of the backend, at the price of rewriting 184 UI call sites, and its
supporting arithmetic rests on a density of 24 that measurement puts at 13.5.
What is kept is the rule that prices regrowth — where a route may live. If the
count lands at 60 or at 90, the budget holds either way.

**`import_batch_image_ids` is not re-homed here.** CORE.md: *"Scopes the
comparison engine — the thing that produces the irreplaceable pairs."* The real
table is `import_batch_images`, 114 rows over 3 batches, and it lives in
`data/repositories/imports.py` and `features/export/routes.py`. This document
deletes both homes. The comparison ledger it scopes is **2,532 rows** and is the
one thing in the catalog that cannot be recomputed. **It must be explicitly
re-homed onto `scope()` before either file is touched.** It is named rather than
solved because solving it needs someone to read what the scoping actually does,
and this pass was read-only.

**Nobody ran the app.** Every claim here comes from static measurement and SQL.
CORE.md rule 7 is plain: *"Verify by running it. Every real bug this session was
found this way and none by reading."* In particular, the claim that the UI
renders acceptably against the empty payloads for collections, saved views,
stacks and people is **read, not seen**. The tables hold 0 rows; nobody watched
it paint. Step 4 deletes ~30 routes on the strength of a row count. **Drive the
app and screenshot it first.**

**`scope()` is a new parser in the request path and its failure mode is quiet.**
It can silently return the whole library, and it must stay index-shaped or it
turns `work.owed()`'s anti-join into a table scan on 157,236 rows — CORE.md's
appendix records 25.1 ms against 1.7 ms when the identity query loses its index.
So: it raises `ValueError` producing a 400, never an empty `WHERE` and never a
fallback; it joins a temporary table of ids rather than emitting an `IN` list;
and CI carries an `EXPLAIN QUERY PLAN` assertion. **This is the one new primitive
to build a refuter for before building the primitive.**

**The develop maths floor is 5,605 and it is not negotiable.** The backend cannot
reach 12,000 while the colour mathematics survives. The honest floor is ~16,100.

**The Develop interactive preview is still a fourth renderer.** Putting `recipe`
into the tile URL makes the GLSL path the same cache read as the grid, and
`model/cache.canonical()` already refuses a recipe that is not a pure function —
so the client must not be computing its own key, and `proof_tile.js` computes a
hand-rolled hash and keeps its own LRU. Whether the GLSL path is retired in
favour of served tiles is a product decision about interactive latency and it is
not made here. **Being wrong about the colour maths is silent**, so this is the
one seam where a disagreement would never be reported.

**Bursts and stacks as a real feature.** `stacks` and `stack_members` hold 0 rows
against 157,236 photographs. The machinery is deleted; the idea is out of scope,
as CORE.md already says.

**The last 3,700 UI lines.** 28,700 against CORE.md's 25,000. That is a
UI-architecture question — what the grid, loupe and develop lenses *are* — and it
should be answered by a lens rewrite that measures itself.

**Android.** Priced at 9,551 lines, both totals stated, decision left with the
owner.

---

## Appendix — the commands behind the load-bearing numbers

```bash
# sizes
git ls-files | grep -E '\.(py|js|css|html|kt|sql)$' | xargs wc -l | tail -1            # 115,005
git ls-files 'web/*' | grep -E '\.(py|sql)$' | grep -vE '(^|/)(test_|conftest)' \
  | xargs wc -l | tail -1                                                              # 53,471
git ls-files 'web/static/*' 'web/templates/*' | grep -E '\.(js|css|html)$' \
  | xargs wc -l | tail -1                                                              # 31,795
git ls-files | grep -E '(^|/)(test_[^/]*\.py|conftest\.py)$' | xargs wc -l | tail -1   # 18,317
git ls-files 'android/*' | grep '\.kt$' | xargs wc -l | tail -1                        # 9,551
cd web/features/develop && wc -l pipeline.py masks.py … native_exif.py | tail -1       # 5,605 (21 files)

# routes and density
git ls-files '*.py' | grep -vE '(test_|conftest)' \
  | xargs grep -hoE "@[A-Za-z_]+\.(get|post|put|delete|patch)\(" | wc -l               # 197 (27 files; 160 outside api.py; 170 distinct paths)
python - <<'PY'     # ast over every decorated body -> 197 routes, 2,660 lines, 13.5 l/route; api.py 37/483/13.1
import ast, pathlib, subprocess
files = [f for f in subprocess.run(["git","ls-files","web/*.py"], capture_output=True,
         text=True, check=True).stdout.split() if "test_" not in f and "conftest" not in f]
VERBS = {"get","post","put","delete","patch"}
routes = lines = api_r = api_l = 0
for rel in files:
    try: tree = ast.parse(pathlib.Path(rel).read_text(encoding="utf-8", errors="replace"))
    except SyntaxError: continue
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)): continue
        deco = [d for d in node.decorator_list if isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute) and d.func.attr in VERBS]
        if not deco: continue
        n = (node.end_lineno or node.lineno) - node.lineno + 1
        routes += len(deco); lines += n
        if rel == "web/api.py": api_r += len(deco); api_l += n
print(routes, "routes,", lines, "lines,", round(lines/routes, 1), "per route")
print("api.py:", api_r, api_l, round(api_l/api_r, 1))
PY

# the import rules
grep -hnE '^(from|import) ' web/model/*.py | sort -u                                   # stdlib + model siblings only -> R1 = 0
git ls-files | grep -E '\.(py|sql)$' | xargs grep -c 'CREATE TABLE' | grep -v ':0$'    # 68 statements, 13 non-test files outside model/schema.sql
grep -nE '^\s*(from|import) ' <the 21 maths files> | grep -E '(core|db|data|model|features|fastapi|sqlite3)'
                                                                                       # 4 escapes: looks.py:10, masks.py:21, masks.py:23, discovery.py:5
python scripts/gates/check.py --list   # cross-package imports: layers -> 21, every site printed
# R6's registries. The predicate decides the answer, which is why R6 is not a gate:
python - <<'PY'                        # -> 78 in 40 files broad; add the mutation test -> 4 in 4
import ast, pathlib, re, subprocess
files = [f for f in subprocess.run(["git","ls-files","web/features/*.py","web/core/*.py"],
         capture_output=True, text=True, check=True).stdout.split()
         if "test_" not in f and "conftest" not in f]
broad = mutated = 0
for rel in files:
    text = pathlib.Path(rel).read_text(encoding="utf-8", errors="replace")
    try: tree = ast.parse(text)
    except SyntaxError: continue
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Dict, ast.List, ast.Set)):
            for t in (t for t in node.targets if isinstance(t, ast.Name)):
                broad += 1
                n = re.escape(t.id)
                if re.search(rf"\b{n}\s*\[[^\]]*\]\s*=|\b{n}\.(append|add|update|pop|remove|clear|setdefault|extend)\(", text):
                    mutated += 1
print(broad, "broad /", mutated, "mutated")
PY
git ls-files '*.py' | grep -vE '(test_|conftest)' | xargs grep -l '\bdb\.' | wc -l     # 28
grep -rn 'from db import' --include=*.py web | wc -l                                   # 0 -> the 28 is exact

# the seam
# The $ in the class is load-bearing: without it `/api/photos/${id}` truncates and
# the distinct count reads 129 instead of 156. A wrong command is worse than none.
git ls-files 'web/static/*' 'web/templates/*' | xargs grep -ohE '/api/[A-Za-z0-9_/{}.$-]*' | wc -l   # 184 literals, 156 distinct
python scripts/gates/check.py --list   # every UI literal against all declared paths -> seam 16, over 18 sites
git ls-files 'web/static/*' | grep '\.js$' | xargs grep -c 'fetch(' | grep -v ':0$'    # 16 files
git ls-files 'web/static/*' | grep '\.js$' | xargs grep -c 'innerHTML *='              # 181
git ls-files 'web/static/*' | grep '\.js$' | xargs grep -ohE '\besc\(' | wc -l         # 373
git ls-files 'web/templates/*' | xargs grep -ohE '<kbd' | wc -l                        # 73
grep -ohE "from '[^']+'" web/static/js/desktop/keyboard.js | sort -u | wc -l           # 21 modules, 600 lines

# the gate
git ls-files | grep -c 'scripts/bench.py'                                              # 0  -> ci.yml runs a file that is not there
git ls-files 'web/perf/*' | wc -l                                                      # 0  -> the CI perf job cannot pass
gh api repos/:owner/:repo/actions/runs --jq .total_count                               # 0  -> CI has never run, which is why nobody noticed
grep -rn 'scripts/lint' .github/ | wc -l                                               # 0  -> CI never lints
python scripts/gates/check.py                                                          # seam 16 · layers 21 · tables 68 · invokes 4, in 0.6s
git ls-files | grep -E '\.(py|js|css|html|kt|sql)$' \
  | xargs awk '{t++; if ($0 ~ /^[ \t]*$/) b++} END{printf "%d of %d = %.1f%%\n", b, t, 100*b/t}'
                                                                                       # 13,603 of 115,007 = 11.8% blank -> why the gate counts couplings, not lines

# the live catalog, mode=ro
images 157,236 · decisions 89,374 · cache 48,259 (embedding 42,937 · tile 5,322 · base 0)
copies 144,363 · drives 2 · cache_entries 144,702 · develop_settings 79,487 (xmp 79,482 / user 5)
comparisons 2,532 · develop_presets 994 · propagation_updates 2,263 · import_batch_images 114
image_quality 89 · image_keywords 77 · folder_scan_state 39 · develop_history 8 · catalog_sources 3
keywords 1 · watched_folders 1 · stacks 0 · stack_members 0 · collections 0 · collection_images 0
collection_links 0 · saved_views 0 · iptc_fields 0 · autocull_history 0 · people 0 · image_captions 0
image_tags 0 · face_detections 0 · image_checksums 0 · embeddings 0
decisions.family: develop 79,512 · star 6,263 · compare 2,532 · rotate 508 · keyword 296 · flag 163
                  quality 89 · collection_meta 5 · status 4 · chores 2
tables 86, of which 40 hold zero rows · tail NULL or '' = 12,965
taxonomy_move_journal and develop_export_presets are ABSENT
```

*One note on arithmetic. Summing per-file line counts over the backend gives
53,472 against `wc -l`'s 53,471; one tracked file ends without a newline. The
surface totals in §4 use the per-file sum.*
