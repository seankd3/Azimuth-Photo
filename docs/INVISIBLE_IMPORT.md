# INVISIBLE_IMPORT — local-first ingest, silent drain (design, 2026-07-16)

## The goal (Sean, verbatim intent)
"Quickly ingest locally and begin working, and then have the app quietly move them
over in the background — all invisible to the user." Satellite mode already IS this
architecture; tonight's Holland ingest proved the flow end-to-end and exposed every
seam that still shows. This program removes the seams. Owner: Import & Ingest lane.

## Principles
1. **Local-first is not a mode, it's how laptop import works.** The user never
   chooses between "import here" and "import to server" — photos land on the SSD,
   period. The hub copy is a background guarantee, not a decision.
2. **Silence with one honest tell.** The only visible trace of the drain is the
   existing peek-chip vocabulary: a quiet chip while work is owed, gone when it
   isn't. No panels, no progress modals, no toasts unless something needs the user.
3. **The status never lies (P1-D).** "Nothing owed" must be provable, not inferred.

## Workstreams

### A. Sync-status honesty (P1-D, first — it gates trust in everything else)
`web/features/sync/sync_worker.py` defects (from the 2026-07-16 incident):
- queue_depth / bytes_remaining are set only at cycle boundaries — frozen during
  the upload loop, and an error cycle skips the final refresh; a transient empty
  `record_local_images()` result zeroes them while uploads are owed.
- `current_file` is cleared at cycle end even when the cycle failed mid-file.
- A timeout leaves only `recent_errors: ["timed out"]`; there is no
  recovering/retrying signal, so "queue 0 + old error" reads as done.

Fix shape:
- Add `state: idle | syncing | recovering | paused` to status. `recovering` = a
  failure streak is active and pending work is nonzero.
- Decrement queue_depth/bytes_remaining live after each successful upload;
  on exception, KEEP last-known pending numbers (never zero on error paths).
- `queue_depth: 0` may only be reported when a hub manifest round-trip confirmed
  zero missing AND the local pending snapshot is empty in the same cycle.
- Surface in UI (sync_chip.js + drawer): "Backing up — N to go", and in
  recovering state "Retrying in Ns — N still to back up". Chip hidden at true zero.
- Regression tests: fails-without for (a) mid-loop status snapshot shows live
  decrement, (b) error cycle preserves pending counts, (c) empty-items cycle with
  nonzero sync_state pending does not report zero.

### B. One import surface on the laptop
The staged-import canvas (import_stage.js) becomes satellite-aware:
- Commit bar in satellite mode says "Import" exactly as today — no new words.
  After commit, the drain is queued automatically; the peek chip absorbs sync
  ("Backing up — 74 to go") after the import job completes.
- Field-instance divergences to erase: legacy "photoArchive Imports" local root
  naming; flat local YYYY/YYYY-MM-DD placement vs hub taxonomy trees (align local
  layout with the hub's category trees so freeup/rescan reasoning is symmetric).

### C. Zero-launch (the Tauri tray owns the instance)
The satellite uvicorn must never be something Sean (or an agent) starts by hand.
Tray app auto-starts it, health-checks it, restarts it after failures, and the
CEO-directive concern (background CPU burn) is answered with idle discipline:
pause indexing/prefetch when no import/edit activity for N minutes (policy, not
process-kill).

### D. Trust gates that must land before freeup is advertised in this flow
(qfix-syncint lane, tracked here as dependencies, not owned here)
- P0-A upload finalize byte-verify; P0-B persisted placement decision (idempotent
  retry); P1-C manifest "known" requires byte proof.
- Until merged: freeup stays where it is (System drawer), not surfaced in the
  import/backup chip.

## Sequence
1. A (worker honesty + chip) — small, my lane, immediately after the release freeze.
2. B (canvas satellite-awareness + local-layout alignment) — after A.
3. C (tray ownership + idle discipline) — coordinate with whoever owns desktop/.
4. D gates the marketing of the flow, not the building of it.

Non-goals: no new sync protocol, no second queue, no changes to hub placement
(qfix-syncint owns that), no per-file toasts.
