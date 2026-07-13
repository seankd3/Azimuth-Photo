# Azimuth — Bug Tracker

Little bugs found by using the app, plus fixes. All fixes below are committed to `main` and live (frontend served live; backend picked up on prod restart 2026-07-13).

Deploy notes:
- Frontend static JS (`web/static/js/**`) is served **live** from the prod checkout — instant on hard-refresh.
- Backend runs as a **systemd service** `photoarchive.service` (env `PHOTOARCHIVE_ACCESS=tailscale`); Python changes need `sudo systemctl restart photoarchive.service` (binds :8000 after ~15-25s startup).

---

## FIXED (2026-07-13) — round 1 (Fable)
- **Empty-trash button** "Trash couldn't be emptied" — `emptyTrash()` sent no body → 422; send `{}`. (api.js)
- **Develop Auto / As Shot** dead — auto-tone listener nested in the As-Shot handler + `this.root` typo. (develop/panels.js)
- **Client-gallery Title** not saved on edit — `update_gallery()` omitted `title` from its UPDATE. (publishing)

## FIXED (2026-07-13) — round 2 (Grok, Fable-reviewed)
- **Save IPTC** stuck disabled after success — re-enable in `finally`. (keywords_panel.js)
- **Event "Open in Refine"** didn't scope — `setScope({similarIds, similarLabel})` first. (events.js)
- **Mobile Library drill-in** wiped on tab return — `render()` early-returns when `showingCollection`. (mobile/library.js)

## FIXED (2026-07-13) — round 3 (Grok, Fable-reviewed)
- **Client-gallery "First photo" cover** not applied on edit — null cover now persists NULL, not the old cover. (publishing/galleries.py)
- **Develop Compare** left canvas clip-path'd after exit — clear `clipPath` on off. (develop/compare_view.js)
- **1:1 proof button** collided with SoftProof on `data-action="proof"` — 1:1 uses `data-action="pixel"`. (develop/develop.js)
- **Loupe Fit/100% chip** had no click handler — wired to `toggleFitOneToOne()`. (loupe.js)
- **Mobile Search → People** stuck on skeleton forever — flatten `/api/people` sections (contract mismatch). (mobile/search.js)
- **Mobile Refine** failed pick advanced the duel + killed undo — restore prior set on failure. (mobile/refine.js)
- **Mobile "Add to collection"** cleared selection on failure — `finish()` only after success. (mobile/selection.js)

## DONE (2026-07-13) — Android: Archive photo view → full Google-Photos-style detail
- `ArchiveScreen.kt` `ArchiveViewer` rebuilt: immersive fullscreen `Dialog` (no tab-bar leak), top metadata bar (back · date/time · favorite · ⋮), RAW/format chip, highlighted filmstrip, Share/Info/Trash action bar, pinch-zoom, info sheet. `ArchiveApi` gained trash/setFlag/download-for-share; added a `FileProvider`. Built on omarchy, emulator-verified on the XPS (API-35), **installed to the Pixel 10a** over Tailscale adb.

---

## OPEN
- [low, latent] mobile `writeRating()` → `/api/image/{id}/rating` has no backend route; never called from UI. Left as-is.

## FOLLOW-UPS (Android, need backend endpoints before they're real buttons)
- "Add to" (collection) + "Edit" actions, and photo location in the metadata header — deferred; read-only mobile ArchiveApi + ArchiveImage model don't expose them yet.
