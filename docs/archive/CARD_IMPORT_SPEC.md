# CARD_IMPORT_SPEC — card → satellite ingest (v1, addendum to FIELD_SPEC, 2026-07-11)

> **Archived 2026-09-09.** This describes the V1 product, which is deleted; it is kept for the learning in it, not as instructions. Every path, route, table and surface it names is gone.

> **Behavior reference awaiting V2 adoption.** Preserve verified-copy and
> clear-card safety, not the satellite job architecture.

Goal: insert a camera card into the satellite laptop; one click imports everything new,
verifies every byte, frees the card as fast as physically possible, and the photos are
browsable/cullable/editable locally immediately. Offload to the hub is FIELD_SPEC's job —
card import ends when files are safe locally, registered, and marked dirty for sync.
The hero UX moment is "card empty — safe to eject": the user should never think past it.

## UX (obeys docs/ui-architecture.md: chip + overlay verb; no new pages)
- Card detection (satellite only): background poller (reuse background-runtime idiom,
  5s interval, zero cost when no removable volumes) scans removable volumes for a DCIM/
  directory. Cross-platform: Windows + Linux (satellite laptops are Windows today).
- On detection a header chip appears next to the sync chip: "Card · 31,971 photos · 425 GB".
  Click → Import overlay.
- Import overlay: ONE primary action: **Import**. Two persisted toggles:
  "Clear card as files verify" (default ON) and nothing else. No folder pickers, no
  destination dialogs — destination is the originals tree, always.
- Progress hero = countdown to card-clear, not total work: "Card free in ~18 min" →
  "✓ Card empty — safe to eject". Copying/verifying/registering phases collapse into that
  single number. After card-clear the overlay can be dismissed; remaining work (thumbnails,
  sync) continues in Background Work + sync chip per docs/background-work-behavior.md.
- Already-imported awareness: files whose content_hash is already in the local catalog are
  skipped and counted as "already imported" (supports the common re-insert-same-card case).
- Reuse import_batches: the import is a batch; "View import" jumps to the batch scope.

## Engine (new: web/features/imports/card.py + card_routes.py; reuse sync/hashing.py and
  imports/service.py idioms; SQL in data/repositories/)
Per-file pipeline (copy may parallelize per card-folder; verify strictly per file):
1. Stream-copy card → originals tree, hashing DURING the copy (single card read):
   images → RAWS/YYYY/YYYY-MM-DD/<original filename>; video (mp4/mov) → Video/YYYY/YYYY-MM-DD/.
   Date from EXIF DateTimeOriginal (geodata.extract_file_metadata), fallback file mtime.
2. After copy, compute full_hash (sync/hashing.compute_full_hash) of the DESTINATION file and
   compare against the hash streamed from the card. Mismatch → delete dest, retry once, then error row.
3. Collisions (Canon 9999 rollover means one day can span multiple card folders with
   repeating filenames — this has caused real data loss): destination exists with different
   content_hash → suffix -N. Identical content_hash → skip as duplicate. NEVER overwrite. Same
   rule as hub intake (sync/hub.py).
4. Only after hash verification: register via existing importer machinery
   (add_or_restore_source + insert_images_batch), set content_hash, satellite.mark_image_dirty(),
   THEN if clear-card enabled, delete the card original. Card deletion strictly follows
   verification+registration of that file. fsync dest before card delete.
5. Interruption-safe: card yanked mid-copy → partial dest fails hash and is discarded on next
   run; verified files are done; re-insert resumes by skipping known content_hashes.
6. Status: GET /api/import/card (detected cards + stats), POST /api/import/card/start,
   GET /api/import/card/status → {phase, files_done/total, bytes_done/total,
   card_free_eta_seconds, cleared_bytes, errors[], batch_id}, POST /api/import/card/cancel
   (finishes current file, never leaves an unverified deletion). Polling, 1s while overlay open —
   existing idiom, no websockets. Typed Pydantic bodies; register routes in test_modular_contracts.py.

## Non-goals v1
- No card formatting (clear = delete verified originals only; leave card folder structure).
- No multi-card parallel import; one card at a time.
- No tethering; no in-place card browsing.
- Video sync to hub: card import copies/verifies/clears videos locally; uploading the Video
  tree to the hub is a marked v1.1 follow-up (FIELD_SPEC upload registers images only).
- Hub-mode card import (plugging a card into omarchy directly) is out of scope.

## Acceptance
- ./scripts/azimuth-check --unit green.
- New unit test (temp dir as fake card, temp originals root): mixed CR3/JPG/MP4 set including
  (a) two different files with the same filename in different card folders → both land, one
  suffixed; (b) an exact duplicate of an already-imported file → skipped, card copy still
  cleared; (c) clear-card ON → card retains only unverified/errored files; (d) yank simulation:
  truncate a dest mid-pipeline → not registered, card original retained, second run heals;
  (e) second full run is a no-op.
- Round-trip with sync: imported files appear in sync manifest as dirty and upload to a temp
  hub (reuse test_sync_satellite.py harness).
