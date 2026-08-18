# IMPORT_SPEC — Lightroom-Classic-grade import (v1, 2026-07-12)

> **Behavior reference awaiting V2 adoption.** Preserve the custody and UX
> guarantees, not the V1 routes, tables, jobs, or module layout.

Supersedes the upload-modal UX in `web/static/js/desktop/importer.js` and absorbs the UX
section of `docs/CARD_IMPORT_SPEC.md` (whose **engine** section — stream-copy + hash-verify +
never-overwrite + clear-card pipeline — remains authoritative and is generalized here).

## Why

Today's import is a blind upload modal: pick files in an OS dialog, watch a progress bar,
hope. Lightroom Classic's import is the opposite — **you see everything before it enters the
library**: a source-first, grid-preview, pick-what-you-want, duplicates-dimmed, one-commit
flow. Desktop's bar is "Lightroom Classic replacement"; import is the front door and must
clear that bar. Where LR makes you decide (destination pickers, rename templates, previews),
we keep Azimuth Photo's opinion: **no decisions that have a right answer** — destination is
always the originals tree, organized by date. The destination panel shows you the truth
instead of asking you questions.

## UX (canvas view — follows the develop-module takeover precedent, not a scrimmed modal)

Entry points: the existing Import verb (toolbar / command palette) and the card-detected
header chip (satellite). Esc at any point before commit discards the staged scan and returns
exactly where you were — zero writes.

Layout, LR Classic's three panes:

- **Left — SOURCE.** Detected cards first (satellite removable-volume poller per
  CARD_IMPORT_SPEC: DCIM detection, "Card · 31,971 photos · 425 GB"), then a server folder
  tree (browse roots; reuse `/api/catalog/browse` idiom), with an "Include subfolders"
  toggle. Dragging files/folders from the OS onto the view stages them as a "This device"
  source (keeps today's browser-upload path for hub-over-LAN use).
- **Center — staged grid.** Real thumbnails of source files *before* import (embedded RAW
  JPEG extraction server-side). Per-photo checkbox (LR style: visible when unchecked or on
  hover). `All | New` segment, default **New**: suspected duplicates render dimmed with a
  "already imported" badge and come unchecked. Check All / Uncheck All. Thumb-size slider
  (reuse grid density idiom). Click selects, Space/double-click = loupe peek, Shift-click =
  range check/uncheck. Grid virtualized — a 30k-photo card must scroll like the library.
- **Top — mode.** `Copy` | `Add`. Copy = stream into the originals tree (cards are forced
  Copy). Add = register in place without copying (folders already on this machine's allowed
  roots — the watched-folder semantics, made visible).
- **Right — three collapsible panels** (rung-2, remember state):
  - **File handling** — "Don't import suspected duplicates" (default on); "Clear card as
    files verify" (cards only, persisted, default on — from CARD_IMPORT_SPEC).
  - **Apply during import** — keywords (token field, existing keyword engine), optional
    add-to-collection.
  - **Destination** — read-only tree preview of the originals tree showing exactly which
    date folders will be created/appended, italic-new, with per-folder counts (LR's killer
    detail, minus LR's folder-picking foot-gun). Computed client-side from scan EXIF dates.
- **Footer — commit bar.** "Import 213 photos (3.2 GB) · 14 duplicates skipped" + one
  **Import** button.

After commit: return to Grid scoped to the new import batch (existing `import_batch` scope);
progress lives in Background Work + chip per docs/background-work-behavior.md. Card imports
keep the CARD_IMPORT_SPEC hero: chip counts down "Card free in ~18 min" → "✓ Card empty —
safe to eject". The card fast-path is preserved: insert card → chip → Import view opens with
all new photos already checked → one click. Same one-click speed, now with eyes.

## Engine (web/features/imports/: add staging.py, card.py per CARD_IMPORT_SPEC; SQL in
data/repositories/imports.py; reuse sync/hashing.py, geodata.extract_file_metadata)

### API (frozen — register every route in web/test_modular_contracts.py; mutating routes take typed Pydantic bodies)

- `GET /api/import/sources` → `{sources: [{id, kind: "card"|"root", label, path,
  photo_count?, bytes?}]}` — detected cards (satellite) + browse roots. Card detection per
  CARD_IMPORT_SPEC (5s poller, zero cost when no removable volumes, Windows + Linux).
- `GET /api/import/browse?path=` → `{dirs: [{name, path, file_count}]}` — subtree listing,
  strictly under allowed roots (browse roots + removable volumes). Reject anything else (400).
- `POST /api/import/scan` `{path, include_subfolders}` → `{scan_id}`. Scan runs off the event
  loop (asyncio.to_thread), enumerates supported extensions (scanner.SUPPORTED_EXTENSIONS +
  mp4/mov), extracts size/mtime/EXIF DateTimeOriginal, flags suspects.
- `GET /api/import/scan/{scan_id}?offset=N` → `{status: "scanning"|"done"|"error",
  total_seen, entries: [{key, name, rel_path, size, mtime, taken_at, kind: "image"|"video",
  suspect: bool, suspect_reason}]}` — incremental paging so the grid fills while the scan
  runs. 1s polling while the view is open; no websockets.
- `GET /api/import/scan/{scan_id}/thumb/{key}` → JPEG preview. Embedded-JPEG extraction for
  RAW, downscale for JPEG/PNG/HEIC, poster frame optional for video (placeholder icon is
  acceptable v1). Small bounded disk LRU under the cache root; concurrency-limited; never on
  the event loop. Path safety: thumbs are only ever served by scan key — no raw paths from
  the client.
- `POST /api/import/commit` `{scan_id, keys: [..] | "all_checked_default", mode:
  "copy"|"add", skip_suspects: bool, clear_card: bool, keywords: [..], collection_id?}` →
  `{job_id, batch_id}`. Creates an import_batches row up front (existing machinery).
- `GET /api/import/jobs/{job_id}` → `{phase, files_done, files_total, bytes_done,
  bytes_total, card_free_eta_seconds, cleared_bytes, skipped_duplicates, errors: [..],
  batch_id}` (CARD_IMPORT_SPEC status shape). `POST /api/import/jobs/{job_id}/cancel` —
  finishes the current file, never leaves an unverified card deletion.

### Semantics

- **Copy pipeline = CARD_IMPORT_SPEC engine verbatim:** stream-copy hashing during the read;
  images → `RAWS/YYYY/YYYY-MM-DD/`, video → `Video/YYYY/YYYY-MM-DD/` under the originals
  root (hub) / local originals tree (satellite, marked dirty for sync); full-hash verify of
  the destination, retry once; collision → `-N` suffix, identical hash → skip, NEVER
  overwrite; register via add_or_restore_source + insert_images_batch + content_hash +
  satellite.mark_image_dirty(); fsync before any card delete; interruption-safe/resumable.
- **Add pipeline:** no copy — add_or_restore_source on the folder, insert_images_batch,
  hash computed in the background job, files marked dirty (satellite). Add is refused for
  removable volumes.
- **Suspect heuristic (scan-time, cheap):** catalog match on (filename, file_size) →
  suspect; exact truth at commit time via the streamed content_hash (already-known hash →
  counted `skipped_duplicates`, and still cleared from card when clear_card). Never trust
  the heuristic for deletion decisions — only verified hashes gate card clearing.
- **Apply during import:** after registration, apply keywords + collection membership via
  the existing engines, inside the job (not the request).
- After completion: quality scan of new ids + the existing cache invalidations (see current
  `api_create_import`).

### Legacy

- Old modal + `POST /api/imports` multipart path stays (it backs OS drag-drop / "This
  device"), but the modal UI is retired; drag-drop routes into the staged view by uploading
  to a server-side staging dir and scanning it. `GET /api/imports*` history endpoints
  unchanged.

## Non-goals v1

No rename templates, no develop-preset-on-import, no second-copy backup, no DNG conversion,
no Move mode, no tethering, no multi-card parallel, no card formatting, no in-place card
browsing beyond the staged scan. Hub-side physical card slots out of scope.

## Acceptance

- `./scripts/azimuth-check --area imports` (create the area if missing → routes +
  test_imports.py + new test_import_staging.py) green; full `--unit` green (321+ tests).
- CARD_IMPORT_SPEC acceptance list holds against the new engine (fake card temp dir:
  Canon-9999 filename collisions both land, exact dupe skipped + still cleared, clear-card
  leaves only unverified files, yank-mid-copy heals on rerun, second run is a no-op).
- New: scan pages incrementally under load; suspect flags match (filename,size) fixtures;
  Add-in-place registers without copying; commit with skip_suspects honors verified-hash
  truth; thumb endpoint refuses non-scan paths.
- Contract tests for every new route in test_modular_contracts.py.
