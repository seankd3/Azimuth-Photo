# Azimuth — Bug Tracker

Little bugs found by using the app, plus fixes. Status key: OPEN / WIP / FIXED.

Deploy note: frontend static JS (`web/static/js/**`) is served **live** from this checkout — edits are instant on hard-refresh. Python changes need a uvicorn restart (prod runs detached on 100.102.150.104:8000, no `--reload`).

---

## FIXED (live) — Empty-trash button: "Trash couldn't be emptied"
- `web/static/js/desktop/api.js` `emptyTrash()` sent `POST /api/trash/empty` with no body; backend requires JSON → 422. Fix: send `{}`. (api.js:524)

## FIXED (live) — Develop "Auto" / "As Shot" buttons dead
- `develop/panels.js`: auto-tone listener was nested inside the As-Shot handler and used `this.root` (undefined). As Shot threw; Auto never bound. Fix: un-nested + `this.root`→`this.host`.

## FIXED (needs server restart) — Client-gallery Title not saved on edit
- `features/publishing/galleries.py` `update_gallery()` omitted `title` from its UPDATE. Fix: accept + write `title`; `routes.py` threads `body.title`. **BACKEND — activates on next uvicorn restart.**

## FIXED (live, Grok) — Save IPTC stuck disabled after success
- `keywords_panel.js`: re-enabled only in `catch()`. Fix: re-enable in `finally` (guarded by `document.contains`). Diff-reviewed + tests pass.

## FIXED (live, Grok) — Event "Open in Refine" didn't scope to the event
- `events.js`: only `emit('refine:open')` with no scope. Fix: `setScope({ similarIds: ids, similarLabel: titleFor(group) })` first — refine.js consumes similarIds as the exact `ids=` set. Diff-reviewed + tests pass.

## FIXED (live, Grok) — Mobile Library drill-in wiped on tab return
- `mobile/library.js`: `render()` always forced `showingCollection=false`. Fix: `if (showingCollection) return;` — only Back clears it (refresh callers already guard). Diff-reviewed + tests pass.

## DONE (Android, built + emulator-verified) — Archive photo view → full Google-Photos-style detail
- `ArchiveScreen.kt` `ArchiveViewer` rebuilt: immersive fullscreen `Dialog` (no tab-bar leak), top metadata bar (back · date/time · favorite · ⋮), RAW/format chip, highlighted filmstrip, Share/Info/Trash action bar, pinch-zoom, info sheet. `ArchiveApi` gained trash/setFlag/download-for-share; added a `FileProvider`. Nav-inset threaded from parent (Dialog windows don't report bottom insets). Built on omarchy, verified on the XPS API-35 emulator against the live hub.

---

## OPEN
- [low, latent] mobile `writeRating()` → `/api/image/{id}/rating` has no backend route; never called from UI (landmine if wired). Left as-is.

## FOLLOW-UPS (Android, need backend endpoints before they're real buttons)
- "Add to" (collection) and "Edit" actions, and photo location in the metadata header — deferred; the read-only mobile ArchiveApi + ArchiveImage model don't expose them yet.
