# Lightroom Bridge setup

Cull in Azimuth, edit in Lightroom Classic, rank exports back in Azimuth. Picks/rejects move both ways. Stars from Azimuth are an Elo projection — your own LR stars always win locally and flow back as taste evidence, never into Elo.

## Install the plugin

1. Open **Lightroom Classic**.
2. **File → Plug-in Manager → Add**.
3. Choose `clients/lightroom/azimuth-sync.lrplugin` from this repo (or a copy on the laptop that runs LR).
4. Enable **Azimuth Sync**. It starts polling in the background.

## Point it at the satellite

Library menu → **Azimuth Sync**. Set the satellite URL (default `http://127.0.0.1:8000`). The plugin only talks to the local satellite — never the hub directly.

What syncs:

- **Flags** (pick / reject / unflagged) both ways
- **Your LR stars** → Azimuth as `lr_rating` (taste prior)
- **Elo stars** → written into LR only when you haven’t overridden them
- **Export linkage** when the plugin can see source + rendered file (fallback: stem / capture-time matcher on the satellite)

What does **not** sync: develop settings, collections, other catalogs.

## First-run test (manual — needs live LR)

Plugin ↔ LR catalog writes cannot be exercised headless. Run this once on your machine:

1. Start the Azimuth satellite on the same laptop as LR; confirm `GET http://127.0.0.1:<port>/api/sync/status` answers.
2. Open a small test catalog with a few photos already in the satellite library (same absolute filepaths).
3. In Azimuth, flag one photo **picked**. Within ~10s it should pick in LR.
4. In LR, reject a different photo. Within ~10s Azimuth shows rejected; origin on the satellite oplog is `lr`.
5. Leave both alone for three poll cycles (~30s). Flags must not flip back and forth.
6. Optional: export one edit from LR; both RAW and export should show an `export_of` relation via `GET /api/lr/relation/{id}`.

Machine-verified without LR: server delta/elo/export tests + the pure-Lua core tests under `clients/lightroom/azimuth-sync.lrplugin/tests/`.
