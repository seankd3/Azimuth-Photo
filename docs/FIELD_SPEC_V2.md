# FIELD_SPEC v2 addendum — one library everywhere (frozen 2026-07-11 evening)

Extends FIELD_SPEC v1. Goal: the satellite shows the ENTIRE hub library, browsable instantly and
offline; edits made anywhere converge everywhere. Magic bar: the user never thinks about where a
photo lives.

## A. Catalog mirror (hub → satellite)
1. `GET /api/sync/catalog/export?cursor=<rowver>` (hub): gzip NDJSON stream of image rows changed
   since cursor. Row payload: hub_image_id (=images.id on hub), content_hash, filename, file_ext,
   file_size, date_taken, width/height/orientation, camera/lens fields, latitude/longitude/
   location_source, flag, elo + comparisons, quality score fields, stack membership
   (stack_id/kind/is_representative), collection ids, keywords (paths), develop_settings
   (JSON + updated_at + origin). Terminator line `{"cursor": <new_cursor>}`. Cursor = a hub-side
   monotonically increasing change counter: add `row_version INTEGER` to images bumped by trigger on
   UPDATE/INSERT (additive migration + backfill), so incremental export = `WHERE row_version > ?`.
2. Satellite mirror puller (`features/sync/mirror.py`): applies rows into the local catalog.
   - Match by content_hash: if a LOCAL row exists (field import), attach hub_image_id to it and
     merge metadata by the v1 LWW family rules; do NOT create a duplicate.
   - Else insert a row under a dedicated catalog source "Hub library" (path `hub://`), with
     `hub_image_id` and `hub_remote=1` (new columns, additive), filepath = the HUB filepath verbatim
     (display only). CRITICAL: source-availability and missing-file logic MUST treat hub_remote rows
     as online-and-remote — never mark missing, never "source offline", never decode locally.
   - Deletes/trash propagate as status changes in the export rows (no hard deletes).
   - `POST /api/sync/mirror/refresh` + status in `GET /api/sync/status` (rows applied, cursor,
     last_refresh_at). Auto-refresh every 10 min when hub reachable, plus after each sync push.
3. Thumb tier mirror: `GET /api/sync/thumbs/pack?size=sm&after_id=N&limit=500` (hub): a single
   stream (uncompressed tar) of up-to-500 already-cached thumbs keyed by hub_image_id. Satellite
   bulk-prefetch worker walks the library newest-first for sm then md, writing straight into the
   local thumb cache under the LOCAL image id with correct cache_entries rows; budgeted
   (settings key, default 8GB), throttleable, resumable by after_id cursor, progress in sync status.
   Hub packs only what its thumb cache already holds (never triggers hub-side decodes; skipped ids
   returned in a trailer line so the satellite can retry later).
4. Read-through singles (v1.5 of readcache): any thumb/full/base request on the satellite for a
   hub_remote row proxies `GET hub /api/thumb/{size}/{hub_image_id}` (or full/base), caches locally,
   serves. Timeout 10s → clean 503 with a UI-friendly "hub unreachable" body; NEVER mark missing.
5. Predictive prefetch (satellite): background md+lg prefetch for (a) loupe neighbors ±5,
   (b) picked/flagged photos of the last 30 days, (c) develop bases for anything opened in Develop
   this session's shoot-date siblings. Simple priority queue, network-quiet aware (pause while a
   sync upload is running unless idle).

## B. Oplog convergence (replaces dirty-tracking; both roles)
1. Table `oplog(seq INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT, origin_seq INTEGER,
   content_hash TEXT, family TEXT, payload TEXT(JSON), ts REAL, applied_from TEXT NULL)`.
   origin = stable device id (settings key, generated once). Families: flag, rating, keywords
   (payload = full additive set), iptc, develop (payload = full settings JSON + updated_at),
   collection_membership. Every existing write path appends an entry (same seams as v1
   mark_image_dirty — replace it).
2. Exchange: `POST /api/sync/oplog/pull {device_id, cursors: {origin: last_seen_origin_seq}}`
   (hub) → entries the caller hasn't seen, capped 1000/page. Satellite pushes its own entries via
   `POST /api/sync/oplog/push {entries: [...]}` (idempotent by (origin, origin_seq) unique index).
   Hub applies pushed entries to its catalog through the SAME apply function the satellite uses
   (shared module), with v1 LWW-per-family by entry ts; applying an entry does NOT create a new
   oplog entry with a new origin (no echo loops — applied_from records provenance and relays the
   ORIGINAL entry to other pullers).
3. The v1 `/api/sync/metadata` endpoint stays for compatibility this wave; oplog becomes the
   preferred path; dirty-table code is deleted when oplog lands green.
4. Clock skew: entry ts is authoring-device wall clock; LWW compares ts with 24h sanity clamp —
   entries >24h in the future are applied with hub receive time and logged loudly.

## Non-goals (still)
- Multi-satellite same-photo simultaneous editing UX (converges by LWW; no merge UI).
- Hub-side deletion of originals from satellite command.
- Auth beyond tailnet trust.

## Acceptance
- Mirror: fresh satellite + seeded temp hub (200 fake rows, 30 with thumbs) → refresh → satellite
  grid query returns 200 rows, thumbs served locally for the 30, read-through path exercised for 1,
  no rows marked missing; re-refresh = no-op; a field-import row with matching content_hash gains
  hub_image_id without duplication.
- Oplog: two temp catalogs exchange entries both directions until cursors equal; flag set on A
  visible on B and vice versa; replay/push twice = idempotent; echo loop impossible (assert oplog
  count stable after triple exchange).
