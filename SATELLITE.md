# SATELLITE.md — laptop local-first audit (lane gxsat)

Branch: `gxsat` (worktree). Prod (`~/Projects/photo-archive`) untouched.

## Verdict

Satellite mode already has the right shape: catalog mirror + bulk thumb packs +
local-first sm/md serve + background oplog. The XPS still waited on the hub
because (1) default thumb budget was 8GB (sm+md need ~32GB), (2) bulk pack fill
stalled whenever the user was browsing (idle gate), and (3) packs walked oldest
ids first. This lane fixes those three mechanically; write sync is documented,
not redesigned.

## How satellite mode works today

| Piece | What | When |
|---|---|---|
| Catalog mirror | Hub `GET /api/sync/catalog/export` → local `images` rows with `hub_remote=1` | Every sync cycle (≈15s) + every 10 min; also after pushes |
| Originals upload | Local field imports → hub by content-hash chunks | Sync worker when queue owed |
| Thumb pack | Hub `GET /api/sync/thumbs/pack` tar of already-cached thumbs | Background; sm then md |
| Preview mirror filler | Miss-by-miss sm/md for recent ∪ starred | Idle-only bursts |
| Read-through | Single thumb/full/base proxy to hub, tee into local cache | On cache miss only |
| Oplog | Flags, ratings, keywords, IPTC, develop, collections | Each sync cycle |
| Dirty metadata (legacy) | `sync_state` + `/api/sync/metadata` | Still runs alongside oplog |
| Free-up | Deletes local originals after hub has them | Explicit user action |

Satellite does **not** hold hub originals by default. Hub-remote rows never
decode locally; Develop can pull a PABASE1 artifact via read-through.

## Interaction table (refine / browse session)

| Interaction | Local or hub? | Why |
|---|---|---|
| All Photos / grid sm thumbs (cached) | **Local** | Memory → disk via `preview_mirror`; no hub |
| Grid sm miss | Hub then local cache | Tee through `_remote_media_response` |
| Loupe md (cached) | **Local** | Same mirror path |
| Loupe md miss | Hub then local | Same |
| lg / full | Local if cached; else hub | Not in sm/md mirror budget by default |
| Catalog list / rankings GET | **Local DB** | Mirror keeps catalog warm |
| Flag / reject / pick | **Local write** → oplog push | Request path never waits on hub |
| Star rating | **Local** → oplog `rating` | Same |
| Develop slider save | **Local** → oplog `develop` | Same; base may read-through once |
| Elo compare vote | **Local DB** | Not an oplog family today (see parked) |
| Keywords / IPTC | **Local** → oplog | Same |
| Empty Trash | **Local first**; hub queued | Documented local-first trash path |
| Import from card | **Local**; upload queues | FIELD_SPEC upload |
| Sync chip / status | Local worker status (+ hub health probe) | Health may touch hub |

## Storage quantification

| Tier | Hub total (Sean’s library) | Satellite target |
|---|---|---|
| sm | ≈ 2.9 GB | Hold **all** |
| md | ≈ 29 GB | Hold **all** |
| lg | ≈ 91 GB | Most-recent / working set under remaining budget |

**Budget to hold all sm+md:** ≈ 32 GB + headroom → **40 GB** default.

| Mechanism | Before (this lane) | After |
|---|---|---|
| `sync_thumb_budget_gb` | 8 | **40** |
| `PHOTOARCHIVE_MIRROR_MAX_BYTES` / default | 20 GB | **40 GB** |
| Pack order | `id ASC` (oldest first) | **`order=newest`** (additive hub param) |
| Pack while browsing | Blocked by idle gate | **Runs always**; idle only gates burst/predictive |
| Bulk path | Exists (`/api/sync/thumbs/pack`) | Same endpoint, better defaults |
| Miss-by-miss | `PreviewMirrorFiller` recent 2k | recent **8k**, burst 48 |

Existing XPS `settings.json` that already baked `sync_thumb_budget_gb: 8`
will keep 8 until raised once — new installs / unset keys get 40.

## Writes (current shape — not redesigned)

1. UI write hits local SQLite immediately.
2. Most metadata families append to `oplog` (`flag`, `rating`, `keywords`,
   `iptc`, `develop`, `collection_*`).
3. Sync worker `exchange_with_hub` pushes local origin entries and pulls peers;
   LWW per family by entry timestamp (24h skew clamp).
4. Legacy path: `satellite.mark_image_dirty` + `/api/sync/metadata` still runs
   for flag/develop/keywords on `sync_state` dirty rows.
5. Originals: content-hash upload queue (`sync_state.uploaded=0`).
6. No conflict UI; multi-satellite same-photo edits converge by LWW only.

## Changes in this lane

- `mirror_export` / `hub_routes`: additive `order=newest` on thumb pack.
- `prefetch`: request newest packs; default budget 40 GB.
- `sync_worker._run_prefetch`: bulk pack no longer waits for idle.
- `preview_mirror`: 40 GB cap, larger recent/burst fill.
- `settings` default `sync_thumb_budget_gb=40`.
- Tests: newest pack, warm local-hit never contacts hub, pack-while-browsing.
- Proof: `receipts/gxsat/prove_satellite_local_first.py` (+ seed helper).

## Measured evidence

```text
cd web && python -m pytest -x -q \
  test_preview_mirror.py test_sync_prefetch.py test_sync_mirror_export.py \
  test_sync_end_to_end.py test_sync_satellite.py test_sync_readthrough.py
# → 28 passed
```

Live pair (ports 18000/18010, ephemeral HOME — not prod):

```text
web/.venv/bin/python receipts/gxsat/prove_satellite_local_first.py
# cold_thumbs_cached: 23 / 24 hub rows
# warm_browse with hub process stopped: 16/16 local 200s, hub_thumb_requests: 0
# PROOF_OK → receipts/gxsat/proof.json
```

Local-first serve path verified in unit test
`test_warm_local_hit_never_contacts_hub` (hub client raises if called).

## Parked design questions

1. **Elo / compare votes** are local-only today — not an oplog family. Should
   pairwise ranking converge hub ↔ satellite?
2. **lg working set** policy (30 GB newest-first slice) — not built; sm+md
   completeness was the safe subset.
3. **Full offline mode** / conflict UI — out of scope; document only.
4. **Existing XPS settings** still on 8 GB — one-time bump to 40 on the laptop
   install (or delete the key to pick up the new default).
5. **Pack completeness gaps** (proof saw 23/24 once): gap-retry already rewinds
   hourly; worth a tighter “missing cell” pass later without idle gating.
6. **Hub must deploy** `order=newest` for satellites to get newest-first; old
   hubs ignore the query param and stay ASC (still converges, just cold-tail
   first).
