# The Core

Derived from first principles, 2026-08-15. Every feature stands on what is in
this file. If something here needs a special case to hold, the shape is wrong —
fix the shape, not the caller.

---

## What is actually true

Strip the app to what exists in the world and there are **four kinds of fact**:

| | | |
|---|---|---|
| **A photograph** | bytes someone made | survives copying, moving, renaming, re-importing |
| **A place** | a volume that currently holds it | many per photograph, cheap, disposable |
| **A judgment** | something the owner decided | irreplaceable — the only thing worth backing up |
| **A derivation** | anything computable from the bytes and the judgments | disposable by definition |

Everything the catalog stores is one of those four. Anything that is none of
them is machinery, and machinery is what we are removing.

---

## Three names, three jobs

A single concept called "the photo's path" was doing three unrelated jobs. Split
it and most of the complexity disappears:

| | | |
|---|---|---|
| `id` | **the handle** | arbitrary, stable, what 31 foreign-key columns point at |
| `tail` | **the address** | `Raws/Digital/2026/2026-07-11/SKDA3268.CR3` — where it sits *inside a library*, on any volume |
| `content_hash` | **the identity** | survives a move; proves a copy is a copy |

### The one sentence

> **An absolute path is a volume plus a tail, and we stored them fused.**

That fusion is the disease behind every workaround in the tree: `location.py`'s
drive probing, `hub_remote`, `missing_at`, the 5% mass-missing breaker,
`rebind_moved_source`, and the two-tier problem itself. All of them are attempts
to recover a tail from a path that swallowed its volume.

So: **store the tail, compute the path.** `filepath` stops being a column and
becomes a function. A renamed root is one row. A changed drive letter is one
row. The same tail under two roots *is* the two-tier model, with nothing to
reconcile.

---

## The shapes

Five tables. Two already exist and are kept as they are.

```sql
volumes(id, uuid, root, kind, online)
    -- kind: hot | cold. `uuid` lives in a marker file inside the root, so a
    -- drive letter is never an identity. N rows, not two: a card is a volume.

photos(id, tail UNIQUE, content_hash, vc_of, <judgments>, <derived memo>)
    -- one row per photograph in the library. No source_id. No filepath.

sightings(photo_id, volume_id, seen_at)
    -- three columns. A hint that this volume held this tail. Never a truth.

judgments(content_hash, family, value, at)        -- today's `oplog`, kept
derivations(content_hash, kind, recipe, state, path)  -- exists, from H2
```

`photos` keeps its derived memo columns (`date_taken`, `camera`, `width`,
`orientation`, …) because the grid sorts and filters on them and a join per tile
is not free. They are memos, and the law below says what that means.

---

## The core

Six functions. Nothing else is core.

```
identify(path)              -> hash             bytes name themselves
locate(photo)               -> path | None      first online volume whose root+tail exists,
                                                hot preferred, size-verified
sight(photo, volume)                            record a hint
derive(photo, kind)         -> bytes | row      memoized pure function
judge(photo, family, value)                     record a decision
sweep(volume)                                   walk a root; reconcile tails and hints
```

---

## The two rules that delete the most

### 1. A sighting is a hint. A read is the proof.

Nothing precious may depend on the sightings table being correct. `locate()`
tries the volumes and verifies; if a hint was stale, the read still succeeds and
the hint is corrected in passing.

This is what makes the rest simple. Because a wrong hint costs nothing:

- there is no mass-missing breaker, no 5% ratio, no `AZIMUTH_ALLOW_MASS_MISSING`
- there is no proven-read scoping, no per-file skip list, no scan-void logic
- there is no exFAT DST clock guard
- `missing_at` does not exist — **missing is what a failed read returns**, not a
  verdict a sweep writes down

The corollary is the safety rule: **anything with a consequence verifies for
itself.** Deleting a hot copy re-reads the cold one and compares a full-file
digest at that moment. It never trusts a hint, a prefix hash, or a timestamp.

### 2. Judgment beats derivation. Always.

One rule replaces every per-column authority `CASE` in the tree — including the
film-delivery date guards. If a human said it, it wins, permanently, and a later
worker may not overwrite it.

The test of the whole design: **delete every derivation, run one pass, and the
library comes back identical.** Anything that does not come back was a judgment
hiding in a memo. That test is what found the Elo problem.

---

## Every use case, walked

The shapes are only justified if the real work falls out of them.

| Use case | How it falls out |
|---|---|
| **Import** | `identify()` the file, pick its `tail` from the taxonomy, copy to the hot volume, insert `photos`, `sight()`. |
| **Grid** | Query `photos`; tiles are `derive(photo, 'thumb:md')`. Never touches an original — so the grid is fully alive with the archive unplugged. |
| **Loupe / full size** | `locate(photo)` → decode. No volume answers → *away*. |
| **Judge** (pick, rate, rank) | `judge()`. Writes the durable log and the fast column together. |
| **Develop** | The recipe is a judgment. Base and render are `derive(photo, 'base' \| 'render', recipe)`. |
| **Find** | Query over `photos` memo columns, judgments, and embeddings (a derivation). |
| **Export / share** | Render at a size, write to the hot volume. |
| **Owner reorganizes in Lightroom** | The tail changed. `sweep()` finds a new tail carrying a known hash and updates `photos.tail`. Judgments follow the `id`, derivations follow the hash. **Nothing else moves.** |
| **Backup** | Copy hot→cold at the same tail, verify, `sight()`. *Backed up* = has a cold sighting. |
| **Free up space** | For photos with a cold sighting: verify the full digest on cold, delete the hot file, drop the hot sighting. |
| **Archive unplugged** | `volumes.online = 0`. `locate()` skips it. Photos still list, thumbnails still paint. |
| **Trash** | The tail moves under `.trash/`; `status = 'trashed'` is the judgment. No role column, no new state. |
| **Virtual copies** | One photograph, two recipes. A VC is a `photos` row sharing tail and hash, with `vc_of` — so it keeps its own id, its own judgments, its own derivations. |
| **Duplicates** | Two tails, one hash. Honest: two files really do exist. The dedup view lists them; merging is optional, never automatic. |
| **Stacks, collections, keywords, people** | Set-shaped judgments over photo ids. |

Three photo states — **backed up**, **only here**, **away** — are `SELECT`s over
sightings, never stored columns.

---

## What this deletes

Concepts, not just lines:

- `source_id` and `catalog_sources` as an addressing concept — volumes replace
  them, and **"excluded source" stops existing**. Measured: all 10,689
  `C:\Pictures` hashes are also in the archive, **zero unique, zero judgments**
  (one develop row, from a recoverable XMP sidecar). So those rows are not a
  source to exclude — they are *sightings on a third volume*. The
  10,750-duplicate-identity merge, with its Elo self-pair hazard and 31-column
  FK rewrite, does not get solved: it stops existing.
- `filepath` as stored data; `relative_path`'s three competing conventions
- `missing_at`, `would_mass_mark_missing`, `StorageUnavailableDuringScan`,
  `SuspiciousEmptyScan`, `AZIMUTH_ALLOW_MASS_MISSING`
- `photo/location.py`'s strip-and-probe, `_MAX_STRIP`, the mapping cache
- `hub_remote`, `hub_image_id`, `row_version` and its two triggers
- move detection, rename detection, `_key`, `Plan.moved`/`Plan.gone`,
  `rebind_moved_source`
- the `crosssource` stack kind — it existed to group "the same photo from two
  sources," which is now one photo with two sightings
- every per-column date-authority `CASE`

---

## What must survive

These are bugs already paid for once; the shapes above must not lose them.

- **Decode by content, not extension** — 1,306 `.CR2` files here are JPEGs
- **The prefix hash is not proof** — it covers 8 MiB + size. A roll of film scans
  can share both. Fine for finding candidates; never permission to delete
- **The app mutates its own identity** — fixed-size DNG XMP splices change the
  hashed prefix without changing size. Any in-app write into an original must
  re-identify it
- **`compute_content_hash` needs the re-stat guard** `compute_hash_pair` already
  has, or a file mid-write gets a permanent name
- **Busy-timeout belongs to the request, not the connection**
- **Room, not budget, decides admission**; `draft()` before `load()`; 9 GB peak
  per demosaic worker, measured
- **Nothing heavy on the boot path**; stop DB-touching work before releasing
  handles
- **`Astrophotography/` is fenced case-blind in every walker** (done —
  `scanner.is_fenced_directory`)
- **Statuses are only kept/maybe/trashed**
- **Perf budgets are product invariants**, enforced in the default test suite

---

## Migration, in order

Each step is provable on its own and reversible until the one after it.

| | Step | Proof |
|---|---|---|
| **1** | `volumes` table; marker uuid written into `D:\Pictures` and `E:\Photos` | both resolve by uuid with letters swapped |
| **2** | `tail` on `photos`, computed from today's paths; one convention, namespace segment included | `Raws/Digital/2026/x.CR3` is the same tail on both drives |
| **3** | `locate()` replaces `location.py`; the six cold paths that read `filepath` raw move onto it | archive photos open and reveal with `hub://` still in place |
| **4** | `sightings` + `sweep()`; hints only, no verdicts | unplug and replug mid-session; the grid never blinks |
| **5** | Re-key the develop base cache and thumbnail ETag onto `content_hash`/recipe | plug in the HDD warm: no RAW re-decode wave |
| **6** | Retire `source_id`; `hub://` and `C:\Pictures` become volumes | starred/Elo/develop counts identical, by exact SQL |
| **7** | Backup + free-up-space | the 33 GB hot-only 2026 gap drains; reclaim refuses without a verified twin |
| **8** | Delete the hub residue and the guard apparatus | ~3,100 lines, plus six mechanisms rule 1 made unnecessary |

**Order is load-bearing.** Step 6 before step 3 would be catastrophic:
`_within()` compares a catalogued path against the source folder, so pointing
source 3 at `E:\Photos` while its rows still carry `/mnt/expansion/...` makes
every row look absent and every file look new — and the reconcile worker, which
runs every 300 s, would `_adopt` the entire archive as ~144,000 duplicate rows.

---

## The standing rules

- Originals are never deleted by a sweep, a merge, or a guess. Only the reclaim
  verb deletes, only a hot copy, only after a full-digest match, only after
  showing what it will remove.
- **Cold is read-only except for whole-file archival copies.** Sidecar writes,
  trash moves and DNG splices happen on hot. This settles which sidecar wins and
  why the archive never diverges.
- `Astrophotography/` is out of scope for every path that reads, indexes,
  imports, cleans, dedups or reclaims.
- A hint may be wrong. A consequence may not.
