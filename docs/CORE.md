# Azimuth 2.0 — the core

**Status:** the core is complete — five tables, seven functions, and the work
layer, in ~1,100 lines under `web/model/` and `web/work.py`. What remains is
rebuilding each surface on top of it and deleting the machinery it replaces.
**Version:** 1.0.0-rc.1 → 2.0.0-dev.
**Method (08-16):** gut it and rebuild from first principles. Surfaces are not
adapted, wrapped or threaded through — they are rewritten on the core and their
old machinery is deleted in the same commit.
**This is the master document.** Design, order, proofs and lessons. If a change
needs a special case to fit here, the shape is wrong — fix the shape, not the
caller.

---

## Why

**Azimuth does not work well and nobody uses it daily.** That is the owner's
own correction (08-16) to the sentence this document used to open with, and it
changes what the rewrite owes: there is no working daily-driver to protect, so
no commit has to keep the old app runnable, no old surface is owed a migration,
and *current behaviour is not evidence that a shape is right*. A thing that
never worked well is not a requirement.

What is true is the size: **146,178 lines**, 24% of its 2,203 commit subjects
repair-shaped, and simple changes take days. Those two facts are the same fact.

So 2.0 is a rebuild from first principles on a core small enough to hold in your
head. What carries forward is the **appendix** — lessons already paid for — and
nothing else. Where a lesson and an old implementation disagree, keep the lesson
and write the code new.

The UI is not being rewritten — 39k lines of grid, loupe and keyboard work is
the product, and it is the one part that was ever good. The **backend goes from
107k to about 12k**.

### The measurement that settles the argument

Counted on the live catalog, 2026-08-16. It is **2.4 GB across 84 tables**, and
**41 of those tables are empty**. Of everything in the other 43, this is all the
owner ever decided:

| | |
|---|---|
| develop settings | 79,487 |
| stars | 6,165 |
| comparison pairs | 2,532 |
| develop presets | 994 |
| quality marks | 89 |
| keywords | 77 |
| flags | 32 |
| trashings | 2 |
| **total** | **≈ 89,378 rows** |

Everything else — every index, backlog, ledger, cursor, scan state, FTS shadow,
presence table and derived column — is a machine's opinion about bytes it can
read again. **The irreplaceable part of Azimuth fits in one append-only table**,
and it is 0.06% of the rows in the catalog holding it. That is the whole case
for the shape of this rewrite, and it is why "smaller" and "safer" are the same
direction here rather than opposite ones.

---

## The contract

Azimuth knows four things about a photo:

1. **What it is** — its bytes.
2. **Where copies are** — which drives have it.
3. **What you decided** — keeps, stars, edits, names.
4. **What we computed** — thumbnails, dates, embeddings, captions.

Three rules:

- **Only #3 is irreplaceable.** Everything else rebuilds from scratch — so the
  decisions log is written to every record drive and a sweep checks it is there.
- **#2 is a guess, checked when it matters.** A read never consults `copies`,
  `drives` or the cache; a photo with no known copy still browses and still ranks.
- **The machine never deletes the last copy. Only you do.**

---

## Five tables

```sql
drives    (id, uuid, root, is_record)
photos    (id, hash, version_of, tail, taste, <your decisions>, <computed memo>)
copies    (photo_id, drive_id, tail NULL, seen_at)
decisions (subject, family, value, at)
cache     (hash, kind, recipe, state, path NULL, value NULL, bytes)
```

> **An absolute path is a drive plus a tail, and we stored them fused.**

That fusion caused the drive-probing, `hub_remote`, `missing_at`, the
mass-missing breaker and `rebind_moved_source` — each one trying to recover a
tail from a path that swallowed its drive. **Store the tail, compute the path.**
A renamed root is one row; a new drive letter is one row; the same tail on two
drives *is* the two-tier model, with nothing to reconcile.

**drives** — identity is a uuid in a marker file inside the root, never a letter.
`is_record` is the only policy bit in the design: *may this drive be the last
copy?* The archive may; the working disk may not. From that one bit: reads prefer
the working disk, backup copies to the record, reclaim only deletes from the
working disk.

**photos** — one row per photograph. `tail` is a memo so folder browsing stays
fast. `id` is a handle, because 31 tables and every API URL use integers.
`copies.tail` is NULL unless that copy sits somewhere else, which happens: 5,460
rows carry a `-N` collision suffix.

**version_of** — one nullable column covering raws, exports and virtual copies. A
group is an original plus everything whose chain reaches it, so exporting an
export lands in one group rather than a chain of pairs. The link is *read, not
guessed* — Lightroom stamps `crs:RawFileName` and `xmpMM:OriginalDocumentID` into
every export. Bursts are a different axis and stay out of scope: `stacks` holds
0 rows.

> **`version_of` is a NEW column. It never renames `vc_of`.** `vc_of` does not
> mean "version of" — it means **"this row does not own a file."** Verified:
> `idx_images_original_filepath` is `UNIQUE(filepath) WHERE vc_of IS NULL`, and
> the foreign key is `vc_of → images.id ON DELETE CASCADE`. An export owns a real
> file, so putting it in `vc_of` would drop it out of the uniqueness index and
> cascade-delete it with its parent. `version_of` is nullable with
> `ON DELETE SET NULL`, and `vc_of` keeps its own meaning until virtual copies
> are rebuilt on top of it.

**cache** — `path` names a rendition on disk, `value` holds a computed fact.
42,937 embeddings read as one matrix, not 42,937 file opens that reclaim would
mistake for previews.

---

## Seven functions

```
identify(file)             what photo is this
open(photo)                give me the file    (first drive that has it, working disk first)
put(file, drive, tail)     write it, then identify + saw
saw(photo, drive)          record a copy       (a hint)
make(photo, kind, recipe)  thumbnail / preview / embedding
decide(subject, what)      record a decision   (append to the log)
sweep(drive)               check what's on a drive
```

Four rules make them honest:

- **`decide()` takes any stable identity** — a photo's hash, a folder's tail, a
  drive's uuid, a person's name. This kills the all-zeros fake content hash
  collections invented to fit, and seven side tables with it.
- **The machine's read of a file is cache. Your save is a decision. The decision
  wins.** Decision columns on `photos` are an index rebuilt from the log.
- **`recipe` is an argument and a pure function of its inputs — never a
  timestamp.** Feeding `updated_at` in means reset-then-redo re-renders identical
  pixels and two machines never share a cache entry.
- **The hash names candidates.** Never identity, never permission to delete, and
  **never permission to merge** — a merge that drops the loser's decisions breaks
  the one promise this design makes. Proof is a full-file digest whose read
  starts and ends on the same file.

**One walker owns admission**: `.trash/`, `Astrophotography/` (case-blind, on the
resolved path), junk directories, dotfiles, `.lrdata`, zero-length files and
in-flight `.importing`/`.moving` partials. Without `.trash/` in that list, "a new
tail with a known hash gets a copy row" silently un-trashes every photo you ever
threw away.

**The marker file is the gate.** A copy row is retired only by a failed read on a
drive whose marker is readable; a sweep that cannot read the marker changes
nothing.

---

## Around the core

```
core       the four facts, seven functions
work       one query, one worker — everything computed gets made here
features   queries over the facts, plus the decisions they write
ui         renders queries, calls verbs
```

### The whole backend, as files

Not a diagram — the actual target tree, with a line budget per file. A surface
that will not fit its budget is a surface whose shape is still wrong, and that
is the useful thing about writing the numbers down before the code.

| | Lines | What it is |
|---|---|---|
| `model/` | 900 | drives · photos · copies · decisions · cache · backup · schema.sql |
| `work.py` | 200 | owed is a query; one worker; the politeness gate |
| `render.py` | 400 | decode + edits → pixels, at three sizes, for everyone |
| `develop/` | 3,500 | the colour mathematics, and nothing else |
| `library.py` | 400 | the queries the grid reads: folders, filters, counts |
| `rank.py` | 300 | comparisons → Elo → propagation through the vectors |
| `search.py` | 250 | one query, ranked fusion, degrades never blocks |
| `importer.py` | 300 | identify + put, over one pure (date, kind, roll) → tail |
| `ai.py` | 400 | embeddings, captions, faces — registered as cache kinds |
| `app.py` | 600 | routes, thin |
| `boot.py` | 150 | open the catalog, answer one query, paint |
| **total** | **≈ 7,400** | against 107k today |

Two things this table asserts, and both are load-bearing:

**Develop is 47% of the backend, and that is correct.** It is the only surface
whose bulk is irreducible mathematics — tone curves, colour matrices,
highlight reconstruction, guided filter, noise profiles, lens corrections —
fitted against real acceptance data and expensive to be wrong about. Everything
around it (caches, schedulers, history tables, proof tiles, progressive UI) is
plumbing that the core already provides. The maths survives verbatim; the
plumbing dies.

**Nothing in this tree is a subsystem.** There is no `services/`, no
`repositories/`, no `managers/`, no `coordinators/`. A file here is a set of
queries over the five tables plus the decisions it writes, and when one starts
wanting a table of its own it has found either a decision family or a cache
kind — never a new noun.

**A feature may not own a table.** If it thinks it needs one, it is either a
decision family or a cache kind. Mechanically checkable: no `CREATE TABLE`
outside `model/schema.sql`.

**Work — one query, one worker.** Owed = *what should exist* minus *what is
cached*: an anti-join, not a queue. New photos are not special; they sort first
because they are newest. Order is closeness to your eyes — on screen now, then
the rest of this view, then newest, then oldest debt. One gate: chores yield
while you are using the app (measured: browsing was 23 ms quiet, minutes with
chores running). One small registry per kind: how to compute it, what it costs,
whether this machine can do it, whether it is evictable. Failures record *why*
once instead of being rediscovered every pass.

**Cache and thumbnails.** A thumbnail is a cached answer to *what does this photo
look like, at this size, with these edits*. Tiers are kinds. Eviction is by age
against one ceiling, never touching originals or never-evict kinds. Painting a
smaller tile while the right one renders is UI behaviour, not a cache concept.

**Ranking.** You make comparisons; everything else is computed from them. A
comparison is a decision, Elo and taste are derivations, the mosaic is a
candidate query. One sort registry — a sort not in it is rejected, never
silently falling back to Elo.

> **Correction (08-16).** An earlier draft called the `elo`/`comparisons`
> columns irreplaceable because "the pair ledger was never replicated". Measured
> against the live catalog, the opposite is true, and the real numbers are the
> best argument this design has:

| | Rows | What it is |
|---|---|---|
| `comparisons` table | **2,532 pairs over 665 photos** | the owner's actual judgement |
| `images.comparisons` counter | 415,360 across 21,376 photos | fiction — **20,811 of those photos appear in no pair at all**, and the counts cluster at 10–14, which is a machine's wave, not a person clicking |
| `images.elo` | 29,845 moved off the 1200 default | of which only 585 are in the ledger — the other 29,260 are **propagated**, see below |
| `images.stars` | 6,165 | judgement |
| `images.flag` | 32 | judgement |

The ledger exists and *is* the record. The columns are derivations written into
the same place as the judgement, which is precisely the failure this design
refuses with **the machine's read is cache, your save is a decision**.

> **Propagation is the point, not the pollution.** Those 29,260 Elos are the
> taste model spreading real comparisons through the embedding space to
> photographs that *look like* the ones you judged. It is the mechanism that
> multiplies ranking power — it is why 2,532 comparisons can order 157,000
> photos, and it is a feature, not drift.

That makes the shape better rather than worse. Elo is **derived from
comparisons *and* vectors**, so it is a cache kind whose answer improves every
time either input grows: one more comparison, or one more embedding off the
owed queue, re-ranks everything that resembles it. Recomputing is a feature.
Storing it as if it were judgement is what stopped it improving.

Two consequences worth stating plainly:

- **Embeddings are load-bearing for ranking, not only for search.** The ~131k
  owed vectors are not a nice-to-have; each one is a photograph that cannot yet
  receive propagated taste.
- **The owner's ranking judgement is 8,729 rows** — 2,532 pairs, 6,165 stars,
  32 flags. That is all that must survive. Everything the ranking engine
  produces from it can be made again, and should be.

**AI.** Cache, with two riders: never evict (small, hours to remake), and your
answer about the machine's answer — a name, "not a face", a fixed caption — is a
decision stored elsewhere. Never a precondition: ranking and culling work at zero
coverage and sharpen as results land.

**Import.** `identify` + `put`, over one pure function: (date, kind, roll) → tail.

**Search.** One query with ranked fusion — filename, keyword and EXIF always;
embeddings when they exist. Degrades, never blocks, never says "not enough
photos".

**Develop.** Edits are decisions; decode and render are cache; one function at
three sizes serves grid, Develop and export, which is what stops them
disagreeing. Edit history *is* the decisions log filtered to one photo, so
`develop_history` goes.

---

## Boot

> **Boot is: open the catalog, answer one query, paint.**

Nothing the grid needs depends on a drive being attached. The app opens instantly
**by construction**, not by optimisation, and library size and drive state stop
mattering. Everything else — sweeping, hashing, thumbnailing, embeddings,
integrity — is owed work that starts after the first paint and yields to you.

Every boot wound was the same mistake, something correct-but-expensive placed
before the first paint: a search index one row out of step cost **12.3 s per
launch**; `PRAGMA quick_check` cost ~1 s/GB and blocked boot **~64 s**; warmers
firing at the port opening made a 150k library serve its first thumbnail in
**5–50 s**; a table-scanning `UPDATE` inside a lazy connect froze boot for **30 s**.

Integrity moves behind the paint — after an unclean shutdown it runs in the
background and a failure becomes a plain offer to restore from backup. Shutdown
keeps its order: stop everything database-touching *first*, then release handles,
or Windows keeps the library file. **The gate:** a boot test that fails when
first paint exceeds its budget on a 150k-row catalog.

---

## What you see when something is wrong

Five words, each with exactly one action.

| Word | Means | You |
|---|---|---|
| *(nothing)* | fine | — |
| **preparing** | the tile isn't made yet | wait — a smaller tile paints meanwhile |
| **away** | its drive isn't attached | plug the drive in |
| **lost** | no copy answers anywhere | restore it, or forget it |
| **unreadable** | the file is there and won't decode | replace the file |

Today there are nine, several unreachable. These are computed at read time, never
stored — and one sentence does the work the entire mass-missing apparatus was
built for:

> **A photo can only be *lost* if every drive that could hold it is attached and
> none of them has it.**

So with the archive unplugged, nothing can ever be declared missing. More correct
than a 5% ratio, no threshold, no override switch, and it cannot be defeated by
an interrupted scan. **`preparing` is not an error and must never look like one.**

---

## Helpers

**A share is a drive.** `\\192.168.1.72\Expansion\Photos` instead of `E:\Photos`
— same marker, same uuid. `open()` still prefers the local copy. A server that is
off is a drive that is unplugged, already a first-class state.

**A helper is another machine doing owed work.** Owed is a query, so a helper
needs no protocol: it runs the same worker against the same shared drive and
writes results where this machine already looks; `sweep()` finds them. No HTTP
client, no pairing, no discovery, no mirror, no API revision.

> **This machine never waits for a helper. A helper's absence is
> indistinguishable from a slow day.**

That is the entire difference from the hub design this replaces, which made the
laptop *depend* on the server — fifteen "is it reachable" branches are still in
the tree. Two hard lines: **the catalog never lives on a share** (SQLite over SMB
corrupts), and **nobody ever asks a helper a question and waits.** The first
synchronous call is the hub coming back.

**First use:** omarchy finishes the ~131k owed embeddings when it is next
powered on. The Qwen3-VL-8B model stays; nothing is recomputed with a weaker one.

---

## The shape: a desktop app, not a server

One person, one machine. Everything that exists because the app was reachable
over a network is deleted: owner auth, the unlock page, device tokens,
browser-origin and rebinding checks, CORS, remote access, Tailscale serving,
pairing. **The seven functions are the API**; HTTP is a transport and it goes
too. The phone is parked — the Android client stops working until a transport is
deliberately rebuilt.

---

## Build order

**The order changed on 08-16** and it is worth saying why. The original eleven
steps were a *migration*: each one moved the live app from an old shape to a new
one without breaking it. Once the owner said the app never worked well and he
does not use it, migration stopped being the job. What is left is far simpler —
finish the core, then rebuild each surface on it and delete what it replaces.

### Part one — the core. Done.

| | Landed |
|---|---|
| `drives` + marker uuids | `69cf6d76` |
| every photo has a tail | `bcc747b1` |
| `open()` replaces path probing | `d908b32f` |
| `copies` + a sweep that refuses rather than guesses | `210789b6` |
| the phantom purge, and both drives swept | `d05c7c9e` · `ce1d3052` |
| `decide()` — the log | `e03c628a` |
| `make()` — one cache for every computed answer | `e03c628a` |
| `identify()`, `put()`, `reclaim()` | `5265300c` |
| owed is a query, one worker | `1bfa9ad1` |
| 88,379 judgements adopted into the log | `d1e39c5f` |
| ranking as two pure functions | `2fd8ab16` |

### Part two — the surfaces. Each lands whole, on the core, and deletes its own machinery.

| Surface | Replaces | Done when |
|---|---|---|
| `render.py` | `thumbnails/` (9,157) + `features/media/` (703) | one function answers grid, Develop and export, so they cannot disagree |
| `library.py` | `features/library/` + `features/catalog/` + four repositories | the grid paints from one query with no path in it |
| `rank.py` **done** | `elo_propagation.py`, `rankings.py`, `ratings.py`, `features/compare/` | ranking recomputes from 2,532 pairs and improves as embeddings land |
| `importer.py` | `features/imports/`, `scanner.py`, `synchronize.py` | a card imports through `identify` + `put` and one pure tail rule |
| `search.py` + `ai.py` | `features/search/`, `people/`, `captions/`, `ai/`, `embed_cache.py` | one ranked fusion; embeddings are a never-evict cache kind |
| `develop/` | `features/develop/` minus its plumbing | the colour maths survives; the schedulers, history table and proof tiles do not |
| `app.py` + `boot.py` | `app.py`, `core/`, `features/system/`, `data/schema.py` | boot is: open the catalog, answer one query, paint |
| *(nothing)* | the hub residue, the network layer, `features/sync/` | no port reachable from outside, no pairing, no oplog |

**The only ordering constraint left.** `render.py` before the surfaces that read
pixels, because the Develop base keyed on `(image_id, source_path)` and the
thumbnail ETag folded in `filepath` — 143,803 of 144,473 cache rows belong to
the archive, and re-keying onto the hash is what stops a re-decode wave the
first time the archive is plugged in warm. Everything else may land in any
order, which is itself a result of the core: surfaces no longer share state, so
they no longer share a schedule.

**Step 0 — purge the phantoms (blocks step 2).** `make_test_library` output was
scanned into the live catalog and its files later deleted, leaving **17,132 rows
that point at nothing** — 89.7% of source 5. That, not the Lightroom work, is
what would trip the mass-missing breaker on the next scan and stall reconcile
permanently.

They are unmistakable: sequential names `20260520-000000.CR3` onward, no
dimensions, no camera, no content hash, all stamped exactly noon, files on
neither drive, and no judgment of any kind. But **`2026-05-20` is also a real
shoot folder** — an EOS R5 row with EXIF `03:34:08`, real dimensions and a hash
sits among them — so the folder is not the discriminator and a folder-wide
delete would take photographs.

The rule that cannot: purge a row only when it is *simultaneously* absent from
disk, hash-less, dimension-less, camera-less, not EXIF-dated, and judgment-free.
Measured: 18,911 evidence-free rows, of which **1,779 exist on disk and are kept**
(real photos merely not scanned yet) and **17,132 are purged**. Source 5 lands at
2,050 against 2,240 image files on `D:\Pictures`.

Every source-5 figure quoted before this was ~89% phantom: the hot tier is 2,050
rows, and hash coverage is **13%**, not 1.4%.

**Also found, and fixed before any migration runs:** `data/schema.py:2307`
commits `PRAGMA user_version = SCHEMA_VERSION` *before* returning the answer
computed from the old value — so a migration that fails after that point stamps
the new version anyway and is silently skipped forever after.

**Guard rails.** Cold backup with the app stopped before any data step — one
exists at `C:\Azimuth Photo\data\manual-backups\2026-08-15-pre-core\`, verified.
`AZIMUTH_ALLOW_MASS_MISSING` stays unset; it is the switch that turns a stuck
scan into 142,024 photos marked missing. Do **not** run a full scan of E: after
step 6 — it overwrites `file_modified_at` on 143,263 rows and churns every
thumbnail signature. Reconcile is enough, watched, not left unattended.

---

## Tests

**32,892 lines across 141 files** (measured 08-15). The source-text contract
suite — `test_desktop_correctness.py`, `test_ui_contracts.py`,
`test_mobile_contracts.py`, `test_modular_contracts.py`, and the 424 assertions
that read frontend source as literal text — **is already gone**, deleted in an
earlier wave. So the remaining problem is size, not coupling: the two largest
files alone are 6,332 lines.

- **Tests arrive with their step**, as step 1 did: 7 behaviour tests, 0.21 s.
- **What earns a test**: the seven functions; the five acceptance properties; the
  perf budgets; and each lesson in the appendix that a naive rewrite would
  destroy.
- **A test may not read source code as text**, so the old suite cannot grow back.
- Old tests die with the code they cover — a step that deletes a subsystem
  deletes its tests in the same commit, rather than leaving them to rot red.

Target: **~5,000 lines**, full suite under a minute.

## First run

"Point me at your photos." Pick a folder → attach it as a drive → sweep → the
grid fills while you watch. Then one optional question: *do you have an archive
drive?* → attach, mark as record. **First run and "add a drive later" are the
same code path**, so no wizard duplicates settings. Today's `setup.html` is 309
lines, ~125 of them hub-era pairing against routes that no longer exist.

---

## What disappears

`source_id` and "excluded source" — measured: all 10,689 `C:\Pictures` hashes are
also in the archive, zero unique, zero judgments, so they are copies on a third
drive and the 10,750-duplicate merge stops existing rather than needing a
procedure. `filepath` as stored data. `missing_at` and the whole mass-missing
apparatus. `hub_remote`, `row_version` and its triggers. Move and rename
detection, `rebind_moved_source`. The 93,220-row face backlog and its six
triggers, three scan ledgers, five cursor tables, seven preview schedulers, three
near-identical AI workers, thirteen admission gates. `crosssource` stacks.
`develop_history`. Every per-column date-authority `CASE`. The entire network
layer.

## Size, and the one gate

Measured 2026-08-15: **146,178 lines** — 74,266 Python, 32,777 tests, 39,135 UI.
Target: **backend ~12k, tests ~5k, UI ~25k ≈ 40k total.**

**One gate: a line budget that fails the build.** One number, zero maintenance,
and it prices a second path, a stranded old path, and every piece of ceremony at
the moment someone writes them. Raising it is its own one-line commit, so growth
is visible rather than ambient.

Two pieces of advice worth keeping anyway:

- **If you had to *build* a feature, the model was missing a shape.** Develop
  history stopped needing a table; move detection stopped needing code; the
  duplicate merge stopped existing.
- **Comments explaining *why* are not debt.** Debt is code nobody dared delete.

## How you judge it

1. A photo opens whether it's on the working disk or the archive.
2. You move folders in Explorer or Lightroom; Azimuth follows without being told.
3. Unplug the archive: the library still browses, searches and ranks.
4. It says what isn't backed up, and backs it up when the drive is attached.
5. "Free up space" never removes anything that isn't provably archived.

**A guess may be wrong. A consequence may not.**

---

# Appendix — lessons that must survive

Reference, not plan. Each is a bug already paid for once, and a clean rewrite is
exactly the thing that reintroduces them.

**Formats and decoding**
- This archive holds **1,306 files named `.CR2` that are full-resolution JPEGs**.
  RAW-ness is decided by the first three bytes, never the extension.
- "Is this RAW?" was three different questions — is-it-RAW-data, can-Develop-
  render-it, could-a-phone-have-made-it. Collapsing them into one set
  reintroduces all three bugs.
- Phones shoot DNG, so DNG provenance comes from the source, not the extension.
- **2,480 of 44,521 JPEGs** end without an EOI marker; Pillow refuses them all
  without `LOAD_TRUNCATED_IMAGES`. The picture is entirely there.

**Memory and decode**
- **9 GB peak per demosaic worker, measured, not estimated.** The old guess was
  3 GB, so a 15 GB box was sized for two workers wanting 17 GB and was OOM-killed
  every four minutes.
- `rawpy`'s `postprocess` holds the GIL — a thread pool cannot parallelise
  demosaic, it only adds contention.
- `draft()` before `load()`: the archive holds photos to **527 MP**, 1.5 GB of RGB
  each; full-decoding one for a 400 px tile is how the server reached 6.6 GB.
- A budget that clamps an oversized weight to its own ceiling admits a frame at a
  price the machine cannot pay: one 4.2 GB panorama was charged 768 MB and
  OOM-killed the service four times in an hour.

**SQLite and connections**
- **Busy-timeout is a property of the request, not the connection.** Treating it
  as connection state quietly opted every *interactive* caller out of the pool —
  29 fresh connections against a 2.1 GB WAL while the grid waited.
- Use `GLOB`, not `LIKE` — `LIKE` is ASCII-case-insensitive. For prefix rewrites
  use `substr(filepath,1,?) = ?` so a bracket in a folder name cannot match
  something else.
- The visibility predicate's emitted SQL text is load-bearing: partial indexes
  only apply when the query's `WHERE` implies theirs. Reword it and ranking falls
  back to a table scan on 147k rows.
- `INDEXED BY` is forced on the identity query, not left to the planner: 25.1 ms
  versus 1.7 ms until someone runs `ANALYZE`, and nothing here ever does.
- Ephemeral-WAL close uses `wal_checkpoint(PASSIVE)`, never `TRUNCATE`.

**Windows**
- `os.path.commonpath` and `relpath` **raise across different drives**. Group by
  drive first.
- `asyncio.run()` inside a worker thread destroys the loop and anything left open
  with it — an aiosqlite connection then can never be closed, and the library
  file cannot be deleted.
- `fsync` on a directory is a no-op; `FlushFileBuffers` needs a *writable* handle,
  so `"rb"` fsync fails — open `"rb+"`.
- Folder keys travel the API as `/` while local rows store `\`.

**Scanning and safety**
- An unplugged drive and an emptied library look identical from inside a scan.
  Hence the *lost* rule above.
- `os.walk(onerror=…)` must raise rather than silently yield a partial tree.
- The content hash covers 8 MiB + size: excellent for candidates, **never
  permission to remove a file**.
- `Astrophotography/` is fenced case-blind on the *resolved* path, everywhere —
  over-fencing skips a file, under-fencing loses one.
- Junk directories (`previewcache`, `__macosx`, `*.lrdata`, `.lrt`, …) are
  recorded when excluded, not silently dropped.

**Behaviour**
- Statuses are only **kept / maybe / trashed**; startup rewrites anything else,
  which is how a day of repairs once undid itself silently.
- Film scanners stamp every frame `2026-01-01`. The roll's day comes from the
  lab's own archive name.
- Inferred dates are stamped at **noon**, so a timezone shift cannot move the day.
- "A scope we cannot read matches nothing" — an unparseable filter must not
  silently return everything.
- **The politeness law:** browsing was 23 ms with chores quiet and *minutes* with
  them running.
- Perf budgets are product invariants, enforced in the default test suite. Never
  widen a budget to mask slowness.
