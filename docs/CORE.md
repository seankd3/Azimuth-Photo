# The Core

The whole design, in plain words. If a change needs a special case to fit here,
the shape is wrong — fix the shape, not the caller.

---

## Four things

Azimuth needs to know four things about a photo. Nothing else is stored.

1. **What it is** — its bytes.
2. **Where copies are** — which drives have it.
3. **What you decided** — keeps, stars, ratings, edits, names.
4. **What we computed** — thumbnails, previews, dates, embeddings, captions.

## Three rules

- **Only #3 is irreplaceable.** Everything else can be rebuilt from scratch.
- **#2 is a guess, checked when it matters.** A stale guess costs nothing,
  because reading verifies.
- **Nothing is deleted without proving another copy exists.**

That is the entire product contract. The rest of this file is how.

---

## Five tables

```sql
drives    (id, uuid, root, is_record)
photos    (id, hash, version_of, tail, <your decisions>, <computed memo>)
copies    (photo_id, drive_id, tail, seen_at)
decisions (subject, family, value, at)
cache     (hash, kind, recipe, state, path)
```

**drives** — where photos live. `D:\Pictures` and `E:\Photos` today, a card
tomorrow. Identity is a uuid in a marker file inside the root, never a drive
letter, because letters move. `is_record` is the only policy bit in the whole
storage model: **may this drive be the last copy?** The archive may. The SSD may
not. From that one bit: reads prefer the SSD (fast and expendable), backup
copies SSD→archive, and reclaim only ever deletes from the SSD.

**photos** — one row per photograph. `hash` is what it is; `tail` is a memo of
where it sits (`Raws/Digital/2026/2026-07-11/SKDA3268.CR3`) kept only so folder
browsing stays fast; `id` is a handle, because 31 tables and every API URL use
integers.

**copies** — which drives hold it. Many per photo. A guess, never a truth.

**decisions** — the append-only log of what you said. The only thing worth
backing up.

**cache** — anything computable, keyed by *what photo, what kind, what version*.

### The one sentence behind all of it

> **An absolute path is a drive plus a tail, and we stored them fused.**

That fusion caused the drive-probing, `hub_remote`, `missing_at`, the
mass-missing breaker, `rebind_moved_source`, and the two-tier problem itself —
every one of them an attempt to recover a tail from a path that swallowed its
drive. So: **store the tail, compute the path.** A renamed root is one row. A
changed drive letter is one row. The same tail on two drives *is* the two-tier
model, with nothing to reconcile.

---

## Six functions

```
identify(file)          what photo is this          (bytes name themselves)
open(photo)             give me the file            (first drive that has it, SSD first)
saw(photo, drive)       record a copy               (a hint)
make(photo, kind)       give me a thumbnail/preview (memoized)
decide(photo, what)     record a decision           (append to the log)
sweep(drive)            check what's on a drive
```

Every feature stands on these six. Nothing else is core.

---

## Versions: raws, exports, virtual copies

A photograph often has several files: the raw, the JPEGs you exported, a virtual
copy with different settings. **One nullable column handles all of it** —
`photos.version_of` points at the original. A group is an original plus
everything whose chain reaches it, so exporting an export still lands in one
group instead of a chain of pairs.

This replaces `vc_of` rather than joining it: a virtual copy is a version with
no file of its own, an export is a version with one. Same idea, one column.

**The link is read, not guessed.** Lightroom already writes it — your exports
carry `crs:RawFileName` and `xmpMM:OriginalDocumentID` naming their source. So
linking is a *computation over metadata*, and evidence has grades: exactly one
match in the library links it, more than one proposes it, none leaves it alone.
Your own grouping always outranks the evidence.

In the grid this is one tile per photograph — the finished edit, because that's
the one you want to see — with a badge for what's underneath.

Bursts and brackets are a different axis (several *pictures* taken together, not
several files of one picture). Today `stacks` holds **0 rows**, so that axis is
out of scope until it is wanted.

---

## Where every feature sits

| Feature | Sits on |
|---|---|
| Import | `identify` → pick a tail → copy to the SSD → row + `saw` |
| Grid | query `photos`; tiles are `make(photo, 'thumb')`. Never touches originals, so it works with the archive unplugged |
| Loupe / full size | `open(photo)` |
| Pick, rate, rank | `decide` |
| Develop | your edits are **decisions**; the decode and the render are **cache** |
| Search | `photos` columns + embeddings (cache) |
| Export | `make` at full size, written to the SSD |
| Backup | copy to a record drive, verify, `saw` |
| Free up space | verify the archive copy in full, then delete the SSD file |
| Trash | the tail moves under `.trash/`; `status='trashed'` is a decision |
| People / captions | the machine's answer is **cache**; your answer is a **decision** |

### Two consequences worth naming

**Develop needs no new primitives.** Edit history *is* the decisions log filtered
to one photo, so the separate `develop_history` table goes. Saving an edit
changes the recipe, so the grid tile is owed again automatically — nothing has to
remember to invalidate it. And grid, Develop and export become one function at
three sizes, which ends exports that don't match the canvas.

**AI needs no new primitives.** Embeddings, captions and faces are cache with two
riders: never evict them (small, and hours to remake), and the human's response
to them — a name, "that's not a face", a fixed caption — is a decision stored
elsewhere, or a re-scan silently erases your work.

---

## Build order

Each step works on its own and is worth having even if the next one never lands.

| | Step | Done when |
|---|---|---|
| 1 | `drives` + marker uuids in both roots | both resolve with the letters swapped |
| 2 | `tail` on every photo, one convention | `Raws/Digital/2026/x.CR3` is the same tail on both drives |
| 3 | `open()` replaces the path-probing | archive photos open and reveal |
| 4 | `copies` + `sweep()` — hints only, no verdicts | unplug and replug mid-session; the grid never blinks |
| 5 | Re-key the Develop base and thumbnail ETag onto the hash | plug in the archive warm: no re-decode wave |
| 6 | Retire `source_id`; the old sources become drives | starred/Elo/develop counts identical, by exact SQL |
| 7 | Backup + free-up-space | the 33 GB of unarchived 2026 work drains |
| 8 | `version_of` + reading the export links | your 22 finished frames group with their scans |
| 9 | Delete what's now unreachable | ~3,100 lines of hub residue, plus six guard mechanisms |

**Order is load-bearing.** Step 6 before step 3 would be catastrophic: pointing a
source at `E:\Photos` while its rows still carry `/mnt/expansion/...` paths makes
every row look absent and every file look new, and the reconcile worker — which
runs every 300 seconds — would adopt the entire archive as ~144,000 duplicate
rows.

---

## What disappears

Not just lines — concepts. `source_id` and "excluded source" (measured: all
10,689 `C:\Pictures` hashes are also in the archive, zero unique, zero
judgments — so they are copies on a third drive, and the 10,750-duplicate merge
stops existing rather than needing a procedure). `filepath` as stored data.
`missing_at` and the whole mass-missing apparatus. `hub_remote`, `row_version`
and its triggers. Move detection, rename detection, `rebind_moved_source` — a
file at a new tail with a known hash simply gets a copy row, so there is no
rename code path at all. The `crosssource` stack kind. `develop_history`. Every
per-column date-authority `CASE`.

---

## What must survive

Bugs already paid for once. Decode by content, not extension — 1,306 `.CR2`
files here are JPEGs. The hash covers 8 MiB plus size: excellent for finding
candidates, **never permission to delete** — a roll of film scans can share both.
The app mutates its own identity, because embedded DNG writes are fixed-size
splices inside the hashed region, so any in-app write must re-identify. Busy
timeout belongs to the request, not the connection. Room, not budget, decides
admission. `draft()` before `load()` — the archive holds 527 MP photos. 9 GB peak
per demosaic worker, measured, not guessed. Nothing heavy on the boot path. Stop
database work before releasing handles, or Windows keeps the library file.
`Astrophotography/` is fenced case-blind in every walker. Statuses are only
kept / maybe / trashed. Perf budgets are product invariants, enforced in the
default test suite.

---

## How you judge it

Not by the diagram — by these five:

1. A photo opens whether it's on the SSD or the archive drive.
2. You move folders in Explorer or Lightroom; Azimuth follows without being told.
3. Unplug the archive: the whole library still browses, searches and ranks.
4. It tells you what isn't backed up, and backs it up when the drive is attached.
5. "Free up space" never removes anything that isn't provably archived.

And one standing rule underneath all of them: **a guess may be wrong; a
consequence may not.**
