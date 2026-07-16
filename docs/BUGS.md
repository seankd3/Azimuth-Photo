# Azimuth — Bug Tracker

Bugs found by dogfooding + Grok hunts, with fixes. All FIXED items are committed to `main` and live (frontend served live; backend picked up on prod restart 2026-07-13).

Deploy notes:
- Frontend static JS (`web/static/js/**`) is served **live** — instant on hard-refresh.
- Backend = systemd `photoarchive.service`; Python changes need `sudo systemctl restart photoarchive.service` (~15-25s to bind :8000).

---

## FIXED — 30 bugs (2026-07-13) + 6 (2026-07-15)

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

**Wave 4 — Workers/Settings/Stacks (Grok, reviewed) — 2026-07-13**
- Stacks "Rescan" read `status.state` instead of `rebuild_status.state` → instant fake "done"; error states now terminal with a toast.
- Captions two-gate deadlock: Resume never enabled `caption_scan_enabled`, enabling the setting never cleared `manual_pause` — no UI path could start captions. Both explicit actions now clear both gates.
- People row said Resume but called Pause when auto-scan was disabled (label/action used different predicates).
- Right-panel caption cached "not yet captioned" for the whole session; negatives are no longer cached.
- "Replace existing previews" never enabled Save (`thumbnail_cache_policy` never dirtied the settings).
- "Restart quick guide" was wiped from Help by the shortcut sheet's innerHTML replace; restored + delegated binding.
- Source-scan polling treated one failed status fetch as "scan finished"; now retries through blips.

## DONE — Android Archive viewer → full Google-Photos-style
Immersive Dialog (no tab-bar leak), metadata header (back·date/time·favorite·⋮), RAW chip, highlighted filmstrip, Share/Info/Trash bar, pinch-zoom, info sheet; `ArchiveApi` trash/setFlag/download-for-share; FileProvider. Built on omarchy, emulator-verified, installed to the Pixel 10a over Tailscale adb.

---

## NEEDS YOUR CALL (found, not fixed — product/perf decisions)
1. **Map ignores the "Similar" scope** — Find-Similar chip stays on but Map shows the whole GPS pool. Fix needs `/api/map/markers` to filter by the similar id set (or Map to opt out of similar scope). Which?
2. **People list caps at 100/section** — UI asks for 500 but the backend returns ≤100 named/≤100 unnamed/≤100 other, silently dropping people past the cap (omnibox, Filter→People, People lens). Raise the cap (perf?) or paginate?
3. **Public client-gallery page ignores a custom cover** — cover now persists in the DB but `/s/gallery/{token}` still leads by first-photo order. Want the public page to lead with the chosen cover?
4. **Classic `/s/{token}` "Download all"** fires one browser download per photo (blocked after the first). Needs a new `GET /s/{token}/download-all` zip endpoint on the classic share surface (client galleries already have one). Build it?

5. **Cull-brief zoom always centers** — FE reads `subject_box`/`focus_box` but autocull never returns them; needs backend face/subject boxes. Build?
6. **`/api/quality/scan` has no UI entry** — old archive bursts never get cull scores; endpoint exists, desktop never calls it. Add a "Score stacks" action?
7. **`show_loupe_cache_status` is a dead setting** — persists but nothing reads it. Wire it into the loupe or delete the toggle?

**Wave 5 — Android + sync (2026-07-15)**
- **[DATA LOSS — Fable-authored + regression-tested]** Sync manifest counted trashed/missing/satellite-mirror hub rows as "backed up" — Free-up-space could delete the phone's last copy of a photo whose hub twin sat in Trash. `known` now requires status kept/maybe + missing_at NULL + non-mirror. (features/sync/hub.py + test_sync_hub.py; live on prod)
- Archive infinite scroll appended duplicate pages (per-cell LaunchedEffect race) — single-flight guard with failure retry.
- Trashing the last photo in the Archive viewer could crash the pager (index out of range after list shrink) — getOrNull + snap-back.
- ViewerActivity rendered videos as a static AsyncImage — ExoPlayer path added for video/*.
- Archive Share often failed on modern Android — ClipData grant added.
- trashImage treated HTTP 200 as success even when the hub trashed nothing — now parses the trashed[] array.

**Note (2026-07-15):** the Pixel runs the `android-app` branch (versionCode 3, installed 17:42 — unified viewer, Library parity, own QA). Main's android line is superseded; wave-5 android fixes NOT ported to `android-app` — verify there: share intents set ClipData, and hub-trash calls parse the `trashed[]` response instead of trusting HTTP 200. The sync data-loss fix was hub-side and protects every client already.

**Wave 5 — deferred (product calls)**
8. **ShareActivity doesn't ingest** — sharing a non-gallery file into Azimuth just kicks backup (which only scans MediaStore) → nothing uploads. Real fix = an ingestion path for shared content:// URIs.
9. **No device-token support in the app** — if the hub enables require_device_token, backup 401s. Needs the pairing flow in the app (pa-harden owns hub auth).
10. **BackupWorker retries forever on permanent per-item failures** — needs a terminal-failure policy + surfacing in UI.

**Refine sampling (2026-07-15, Sean dogfooding + Fable-authored)**
- **Scoped Refine didn't sample the full selection** — the candidate pool was a fixed 480-row head of a deterministic ordering (`comparisons ASC` / `elo DESC`, no tie-break), so big selections kept re-serving the same slice and elo strategies never reached the bottom of the set. Fix: scoped window now spans the whole id set (cap 5000) + new sampler-only `least_compared_shuffled` sort (`RANDOM()` tie-break) for explore, in both scoped and filtered branches; grid pagination untouched. Regression test + live 3-call proof of variety. (rankings.py / compare/service.py; prod restarted)

## OPEN (latent)
- **Manual star rating is unwired (product decision, NOT a quick fix)** — mobile `static/js/mobile/api.js:93 writeRating()` POSTs `/api/image/{id}/rating`, which does not exist. But the deeper truth: `oplog.append_rating` has NO caller in the app, `_lr_rating` is only ever written by LR-catalog import + hub sync-merge, and desktop has no rating-set UI either. The app’s rating model is deliberately Taste↔Elo, so wiring manual 0–5 stars is a design choice that could conflict with Elo — needs Sean’s call before building. (2026-07-16 diagnosis)

**Classic-share "Download all" (2026-07-16, Codex lane + Fable security review)**
- Classic public shares (`/s/{token}`) had a "Download all" that looped one browser download per photo — Chromium blocks after the first, so clients could not get the full set. Added `GET /s/{token}/download-all` streaming a server-side ZIP (mirrors the client-gallery zip), with the SAME auth gate as every other share endpoint (`_resolve_token` + `auth.is_unlocked` → 404 on unknown/locked shares BEFORE any image is read). Path-safe + de-duplicated arcnames, `lg` download tier (no full-res leak beyond per-photo download), temp-file cleanup on success and error. Two acceptance tests incl. a security test proving a password-protected share is 404 until unlocked. (features/share/routes.py, share_gallery.html; prod restarted 27a6b40e)

**Empty-trash deleted files before committing the catalog delete (2026-07-16, Grok signal → Fable-verified → Codex sol fix + Fable review)**
- `features/trash/service.py::_purge_trash_rows` unlinked each trash FILE, THEN deleted the catalog rows via `_delete_emptied_catalog_rows`, which SOFT-FAILS under SQLite write-lock (returns `[], [deferred]`). A lock at that instant left files permanently gone but rows still `status=trashed` — the Trash UI showed them recoverable during the self-heal gap, and a restore-in-gap produced a broken missing-file `kept` row. Violated the destructive-pipeline rule (never delete source before the commit gate). Fix: inverted the order — non-destructive guard pre-check (`_guard_trash_file`) → delete catalog rows FIRST → unlink files ONLY for committed ids, re-running the symlink/regular/stat-token TOCTOU guard immediately before `os.remove`. On lock soft-fail, ZERO files are unlinked (fully retryable). Also fixed a latent half-restore: a missing trash file during restore now reverts the row to `trashed` instead of leaving a broken `kept` row. Regression test patches the catalog-delete to soft-fail and asserts the file survives + row stays trashed + restore returns original bytes. (78f5cb3d; prod restarted)
