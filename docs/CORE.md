# The Core

The whole design. If a change needs a special case to fit here, the shape is
wrong — fix the shape, not the caller.

---

## The contract

Azimuth knows four things about a photo:

1. **What it is** — its bytes.
2. **Where copies are** — which drives have it.
3. **What you decided** — keeps, stars, edits, names.
4. **What we computed** — thumbnails, dates, embeddings, captions.

Three rules:

- **Only #3 is irreplaceable.** Everything else rebuilds from scratch.
- **#2 is a guess, checked when it matters.** A stale guess costs nothing.
- **Nothing is deleted without proving another copy exists.**

---

## Five tables

```sql
drives    (id, uuid, root, is_record)
photos    (id, hash, version_of, tail, <your decisions>, <computed memo>)
copies    (photo_id, drive_id, tail, seen_at)
decisions (subject, family, value, at)
cache     (hash, kind, recipe, state, path)
```

> **An absolute path is a drive plus a tail, and we stored them fused.**

That fusion caused the drive-probing, `hub_remote`, `missing_at`, the
mass-missing breaker and `rebind_moved_source` — each one trying to recover a
tail from a path that swallowed its drive. **Store the tail, compute the path.**
A renamed root is one row. A new drive letter is one row. The same tail on two
drives *is* the two-tier model, with nothing to reconcile.

**drives** — identity is a uuid in a marker file inside the root, never a letter,
because letters move. `is_record` is the only policy bit in the design: *may this
drive be the last copy?* The archive may; the SSD may not. From that one bit:
reads prefer the SSD, backup copies SSD→archive, reclaim only deletes from the
SSD.

**photos** — one row per photograph. `tail` is a memo so folder browsing stays
fast. `id` is a handle, because 31 tables and every API URL use integers.

**version_of** — one nullable column covering raws, their exports and virtual
copies. A group is an original plus everything whose chain reaches it, so
exporting an export lands in one group, not a chain of pairs. It replaces
`vc_of`: a virtual copy is a version with no file of its own. The link is *read,
not guessed* — Lightroom stamps `crs:RawFileName` and `xmpMM:OriginalDocumentID`
into every export. Bursts are a different axis (several pictures, not several
files of one) and stay out of scope: `stacks` holds 0 rows.

---

## Six functions

```
identify(file)        what photo is this
open(photo)           give me the file        (first drive that has it, SSD first)
saw(photo, drive)     record a copy           (a hint)
make(photo, kind)     thumbnail / preview     (memoized)
decide(photo, what)   record a decision       (append to the log)
sweep(drive)          check what's on a drive
```

| Feature | Sits on |
|---|---|
| Import | `identify` → pick a tail → copy to the SSD → row + `saw` |
| Grid | query `photos`; tiles are `make`. Never touches originals, so it works with the archive unplugged |
| Loupe / full | `open` |
| Pick, rate, rank | `decide` |
| Develop | your edits are **decisions**; decode and render are **cache** |
| Search | `photos` columns + embeddings (cache) |
| Export | `make` at full size, onto the SSD |
| Backup | copy to a record drive, verify, `saw` |
| Free up space | verify the archive copy in full, then delete the SSD file |
| Trash | the tail moves under `.trash/`; `status='trashed'` is a decision |
| People / captions | the machine's answer is **cache**; your answer is a **decision** |

**Develop and AI both fold in with no new primitives** — the strongest evidence
the shape is right, since they are the two hardest parts of the app. Develop's
edit history *is* the decisions log filtered to one photo, so that table goes.
Saving an edit changes the recipe, so the grid tile is owed again automatically.
Grid, Develop and export become one function at three sizes, ending exports that
don't match the canvas. AI results are cache with two riders: never evict them
(small, and hours to remake), and the human's answer about them — a name, "not a
face", a fixed caption — is a decision stored elsewhere, or a re-scan erases your
work.

---

## Build order

Each step is worth having even if the next never lands.

| | Step | Done when |
|---|---|---|
| 1 | `drives` + marker uuids in both roots | both resolve with letters swapped |
| 2 | `tail` on every photo, one convention | `Raws/Digital/2026/x.CR3` is the same tail on both drives |
| 3 | `open()` replaces the path-probing | archive photos open and reveal |
| 4 | `copies` + `sweep()` — hints only | unplug and replug mid-session; the grid never blinks |
| 5 | Re-key Develop base + thumbnail ETag onto the hash | plug in the archive warm: no re-decode wave |
| 6 | Retire `source_id`; old sources become drives | starred / Elo / develop counts identical, by exact SQL |
| 7 | Backup + free-up-space | the 33 GB of unarchived 2026 work drains |
| 8 | `version_of` + read the export links | the 22 finished frames group with their scans |
| 9 | Delete what's now unreachable | ~3,100 lines of hub residue + six guard mechanisms |

**Order is load-bearing.** Step 6 before step 3 is catastrophic: pointing a source
at `E:\Photos` while its rows still carry `/mnt/expansion/...` makes every row
look absent and every file look new, and the reconcile worker (every 300 s) would
adopt the archive as ~144,000 duplicate rows.

**Guard rails.** Cold backup with the app stopped before anything writes (a hot
copy of a WAL database is not a backup). `AZIMUTH_ALLOW_MASS_MISSING` stays
unset — it is the one switch that turns a stuck scan into 142,024 photos marked
missing. Do not run a full scan of E: after step 6: it overwrites
`file_modified_at` on 143,263 rows and churns every thumbnail signature.
Reconcile is enough, and it is watched, not left unattended.

---

## What disappears

`source_id` and "excluded source" — measured: all 10,689 `C:\Pictures` hashes are
also in the archive, zero unique, zero judgments, so they are copies on a third
drive and the 10,750-duplicate merge stops existing rather than needing a
procedure. `filepath` as stored data. `missing_at` and the whole mass-missing
apparatus. `hub_remote`, `row_version` and its triggers. Move and rename
detection, `rebind_moved_source` — a file at a new tail with a known hash simply
gets a copy row, so there is no rename code path. `crosssource` stacks.
`develop_history`. Every per-column date-authority `CASE`.

## What must survive

Bugs already paid for. Decode by content, not extension — 1,306 `.CR2` files here
are JPEGs. The hash covers 8 MiB plus size: good for finding candidates, **never
permission to delete** — a roll of film scans can share both. The app mutates its
own identity (embedded DNG writes are fixed-size splices inside the hashed
region), so any in-app write must re-identify. Busy timeout belongs to the
request, not the connection. Room, not budget, decides admission. `draft()`
before `load()` — the archive holds 527 MP photos. 9 GB peak per demosaic worker,
measured. Nothing heavy on the boot path. Stop database work before releasing
handles or Windows keeps the library file. `Astrophotography/` is fenced
case-blind in every walker. Statuses are only kept / maybe / trashed. Perf
budgets are product invariants in the default test suite.

---

## Size, and the one gate

Measured 2026-08-15: **146,178 lines** — 74,266 Python, 32,777 tests, 39,135 UI.

The UI is product, not debt; cutting it removes the app. The honest target is
**backend 107k → ~12k, tests → ~5k, UI → ~25k**: roughly **40k total**, with the
backend at a tenth of today's.

**One gate keeps it there: a line budget that fails the build.** One number, zero
maintenance, and it prices every decision where the decision is made — a second
path, a stranded old path, ceremony and indirection all cost lines you do not
have. Raising the budget is allowed and must be its own one-line commit, so
growth is visible instead of ambient.

Everything else is advice, and advice decays. Two pieces worth keeping anyway:

- **If you had to *build* a feature, the model was missing a shape.** Fix the
  shape first. Evidence: Develop history stopped needing a table, move detection
  stopped needing code, the duplicate merge stopped existing.
- **Comments explaining *why* are not debt.** Debt is code nobody dared delete —
  not knowledge nobody wants to relearn.

---

## How you judge it

1. A photo opens whether it's on the SSD or the archive drive.
2. You move folders in Explorer or Lightroom; Azimuth follows without being told.
3. Unplug the archive: the library still browses, searches and ranks.
4. It tells you what isn't backed up, and backs it up when the drive is attached.
5. "Free up space" never removes anything that isn't provably archived.

**A guess may be wrong. A consequence may not.**

---

*Open, not blocking: ~407,000 of your Elo comparisons exist here only as counters
— the pairs that produced them stayed on the hub and may still be on omarchy,
which is powered off. Nothing in this plan touches them; recover that ledger when
the box next comes up.*
