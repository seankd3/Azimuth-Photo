# FIELD_SPEC — satellite mode + hub sync (v1, frozen 2026-07-11)

Goal: Azimuth Photo works fully offline/remote on a laptop ("satellite") against the always-on
"hub" (omarchy). Import/cull/edit happen locally at native speed; originals and metadata sync in
the background; the full hub library stays browsable remotely through a read-through cache.

## Roles
- **hub**: the existing prod instance (omarchy :8000). Authoritative catalog. Default mode.
- **satellite**: same codebase on a laptop. `AZIMUTH_MODE=satellite`,
  `AZIMUTH_HUB_URL=http://100.102.150.104:8000`. Own SQLite catalog, own caches,
  own originals dir for field imports. AI models optional (smoke-degraded is fine).

## Identity
- Every image gets `content_hash = hex(BLAKE2b-128(prefix || le_u64(size)))`, where `prefix`
  is exactly `file[0:min(size, 8 MiB)]` and `le_u64(size)` is one unsigned 8-byte
  little-endian integer. Files below 8 MiB hash the entire file and still append the size.
  The shared hashing helper is used by satellite discovery, hub backfill, and upload verify.
  New column on images (additive migration, both roles).
- Upload integrity is separate from identity: a manifest's `full_hash` is BLAKE2b-128 over
  every file byte. The hub verifies both digests before registering the original.
- Sync matches by content_hash ONLY. Filepath differences are expected and irrelevant.

## Hub sync API (all endpoints hub-side, tailnet-only like everything else)
1. `POST /api/sync/manifest`  body `{items: [{content_hash, full_hash, bytes, filename, date_taken?}]}`
   → `{missing: [content_hash...], known: [{content_hash, image_id}]}`.
   "missing" = hub wants the original uploaded. "known" = already present (any path).
2. `POST /api/sync/upload/{content_hash}`  chunked/resumable:
   headers `X-Offset`, `X-Total-Bytes`; body = raw chunk (≤32MB). Hub appends to
   `/mnt/expansion/Photos/_intake/<content_hash>.part`; each chunk is fsynced before an
   offset journal advances. Resume truncates any uncommitted tail. When complete, the hub
   verifies both `content_hash` and the satellite-supplied full-file `full_hash`,
   moves to `/mnt/expansion/Photos/RAWS/<YYYY>/<YYYY-MM-DD>/<original filename>`
   (date from EXIF DateTimeOriginal; collision → suffix), registers through the EXISTING
   importer machinery (idempotent), returns `{image_id}`. Finalize keeps `.part` intact until
   registration succeeds, so a failed or interrupted registration is retry-safe.
   `GET /api/sync/upload/{content_hash}/status` → `{offset}` for resume.
3. `POST /api/sync/metadata`  body `{items: [{content_hash, flag?, rating?, develop_settings?,
   develop_updated_at?, keywords?: [path...], iptc?}]}` — applied to the hub image matched by
   hash. Conflict rule: per-family last-writer-wins by timestamp; develop_settings only
   overwrite if incoming develop_updated_at > hub's updated_at AND hub origin != 'user'-newer;
   flags/ratings same rule; keywords are ADDITIVE union (never remove on sync). Response lists
   applied/skipped per item with reasons. Idempotent.
4. `GET /api/sync/base/{content_hash}` → the PABASE1 .bin.gz + meta JSON (multipart or two
   endpoints) so satellites can develop hub photos without originals.

## Satellite-side
- `sync_worker`: background loop (existing background-runtime idiom). Pipeline per batch:
  manifest → upload missing originals (resumable, one at a time, bandwidth cap setting,
  pause/resume API) → metadata push for dirty rows (dirty = local change since last push;
  track in sync_state table: content_hash, last_pushed_at, last_local_change_at).
- Read-through cache: requests for thumbs/bases of images that exist only on the hub
  (satellite browsing hub library is v2 — NON-GOAL for this wave except the base fetch
  endpoint above). v1 scope: satellite serves ONLY its local field imports.
- Status surface: `GET /api/sync/status` (satellite) → queue depth, bytes remaining, current
  file, throughput; a compact sync chip in the desktop header (patch) showing
  "↑ 34 photos · 12.4 GB left" with pause/resume.

## Non-goals v1
- No hub→satellite catalog replication (browsing the full hub library from the satellite UI).
- No two-satellite reconciliation, no deletes propagation, no auth (tailnet trust, same as app).
- No transcoding on upload; originals byte-identical (hash-verified).

## Acceptance (each lane)
- Full suite green. Round-trip test: temp "hub" + temp "satellite" DBs in one test process;
  fake 3-file import on satellite → manifest/upload/metadata → hub has files registered,
  settings/flags/keywords present, second sync is a no-op (idempotent).
