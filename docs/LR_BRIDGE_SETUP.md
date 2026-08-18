# Lightroom Bridge setup

> **Historical V1 setup guide.** The satellite plugin transport is retired.
> Preserve the Lightroom workflow lessons; V2 adoption is described in
> `CORE.md` under “Alongside Lightroom.”

Cull in Azimuth, edit in Lightroom Classic, rank exports back in Azimuth. Picks/rejects move both ways. Stars from Azimuth are an Elo projection — your own LR stars always win locally and flow back as taste evidence, never into Elo.

## One-click connect (Windows laptop)

1. Run the Azimuth **satellite** on the same Windows machine as Lightroom Classic.
2. Open Azimuth → **System → Connectivity**.
3. When Lightroom Classic is detected, click **Connect Lightroom**. Azimuth copies the plugin into `%APPDATA%\Adobe\Lightroom\Modules` and writes the satellite URL. Next LR launch loads it automatically.
4. Same button becomes **Disconnect Lightroom** (removes the plugin). No Plug-in Manager steps.

Manual fallback (only if the button never appears): File → Plug-in Manager → Add → `clients/lightroom/azimuth-sync.lrplugin`, then Library → Azimuth Sync to confirm the satellite URL.

What syncs:

- **Flags** (pick / reject / unflagged) both ways
- **Your LR stars** → Azimuth as `lr_rating` (taste prior)
- **Elo stars** → written into LR only when you haven’t overridden them
- **Morning collection** — dated “From Azimuth — N picks · date” of picks since the last LR session
- **Export linkage** when the plugin can see source + rendered file (fallback: stem / capture-time matcher on the satellite)

What does **not** sync: develop settings, other collections, other catalogs.

## First-run test (manual — needs live LR)

Plugin ↔ LR catalog writes cannot be exercised headless. Run this once on your machine:

1. Start the Azimuth satellite on the same laptop as LR; confirm `GET http://127.0.0.1:<port>/api/sync/status` answers.
2. Open a small test catalog with a few photos already in the satellite library (same absolute filepaths).
3. In Azimuth, flag one photo **picked**. Within ~10s it should pick in LR **and** land in today’s “From Azimuth — …” collection.
4. In LR, reject a different photo. Within ~10s Azimuth shows rejected; origin on the satellite oplog is `lr`.
5. Leave both alone for three poll cycles (~30s). Flags must not flip back and forth.
6. Optional: export one edit from LR; both RAW and export should show an `export_of` relation via `GET /api/lr/relation/{id}`, and Azimuth should offer the quiet ranking chip for new edits.
7. Optional: confirm an Elo-projected star in Azimuth shows the hover whisper (“Top 2%…”) and a user-set star shows the subtle **yours** tick (nothing extra in LR).

### Needs Sean’s live LR (keep this list short)

Everything that touches a real catalog:

1. **Connect / Disconnect** installs into the real Modules folder and survives an LR relaunch.
2. **Flag round-trip + morning collection** — Azimuth pick appears in LR and in today’s dated collection; LR reject returns to Azimuth without echo loops.
3. **Export → ranking chip** — one LR export links, the chip opens Refine scoped to that edit, dismiss stays dismissed.

Machine-verified without LR: server delta/elo/export/connect/new-exports/health tests + the pure-Lua core tests under `clients/lightroom/azimuth-sync.lrplugin/tests/` + Playwright screenshots of the Connect button and ranking chip.
