# Azimuth — Bug Tracker

Bugs found by dogfooding + Grok hunts, with fixes. All FIXED items are committed to `main` and live (frontend served live; backend picked up on prod restart 2026-07-13).

Deploy notes:
- Frontend static JS (`web/static/js/**`) is served **live** — instant on hard-refresh.
- Backend = systemd `photoarchive.service`; Python changes need `sudo systemctl restart photoarchive.service` (~15-25s to bind :8000).

---

## FIXED — 2026-07-13 (23 bugs)

**Trash / Develop / Gallery (round 1)**
- Empty-trash button 422 (no body → send `{}`).
- Develop Auto / As Shot dead (nested listener + `this.root` typo).
- Client-gallery Title not saved on edit.

**Reliability round 2 (Grok, reviewed)**
- Save-IPTC stuck disabled after success.
- Event "Open in Refine" didn't scope to the event.
- Mobile Library drill-in wiped on tab return.

**Round 3 (Grok, reviewed)**
- Gallery "First photo" cover not applied on edit.
- Develop Compare left canvas clip-path'd after exit.
- 1:1 proof vs SoftProof `data-action` collision.
- Loupe Fit/100% chip had no click handler.
- Mobile Search→People stuck on skeleton (flatten /api/people sections).
- Mobile Refine failed-pick advanced the duel + killed undo.
- Mobile Add-to-collection cleared selection on failure.

**Browse/Find + Output/Config (wave 3, Grok, reviewed)**
- Filter options stranded `loading=true` after a failed load (permanent skeleton).
- People lens stuck on "Loading faces" on failure (no retry).
- `findSimilar` swallowed HTTP errors → no toast + unhandled rejection.
- Omnibox person-pick didn't switch to the grid lens.
- Ctrl/Cmd+K (Commands) dead on non-grid lenses.
- Omnibox thumb → Loupe truncated to 6 photos (now full grid session).
- Omnibox live preview ignored flag/date/lens/etc. (now full scope).
- `/api/shares` 500 crash on node shares with null collection_id.
- Client-gallery password could not be removed once set.
- Export toast claimed success even on 400/507 (now surfaces real error).

## DONE — Android Archive viewer → full Google-Photos-style
Immersive Dialog (no tab-bar leak), metadata header (back·date/time·favorite·⋮), RAW chip, highlighted filmstrip, Share/Info/Trash bar, pinch-zoom, info sheet; `ArchiveApi` trash/setFlag/download-for-share; FileProvider. Built on omarchy, emulator-verified, installed to the Pixel 10a over Tailscale adb.

---

## NEEDS YOUR CALL (found, not fixed — product/perf decisions)
1. **Map ignores the "Similar" scope** — Find-Similar chip stays on but Map shows the whole GPS pool. Fix needs `/api/map/markers` to filter by the similar id set (or Map to opt out of similar scope). Which?
2. **People list caps at 100/section** — UI asks for 500 but the backend returns ≤100 named/≤100 unnamed/≤100 other, silently dropping people past the cap (omnibox, Filter→People, People lens). Raise the cap (perf?) or paginate?
3. **Public client-gallery page ignores a custom cover** — cover now persists in the DB but `/s/gallery/{token}` still leads by first-photo order. Want the public page to lead with the chosen cover?
4. **Classic `/s/{token}` "Download all"** fires one browser download per photo (blocked after the first). Needs a new `GET /s/{token}/download-all` zip endpoint on the classic share surface (client galleries already have one). Build it?

## OPEN (latent)
- mobile `writeRating()` → `/api/image/{id}/rating` has no backend route; never called from UI.
