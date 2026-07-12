# azimuthphoto.com

Marketing site for [Azimuth Photo](https://github.com/Sean-Kenneth-Doherty/azimuth-photo) —
a self-hosted photo library with Lightroom Classic instincts.

**Show, don't tell:** there are no screenshots of the app here. Every panel is a live
widget built from real product data:

- `js/data.js` — the author's *Selected Landscapes* collection exported from the live
  catalog (real Elo ratings, comparisons, EXIF).
- `js/grid.js` — justified library grid with composable filters over that data.
- `js/loupe.js` — loupe with a histogram computed from the actual pixels, and the app's
  key grammar (`←/→`, `P/X/U`, `L` lights-out).
- `js/refine.js` — the Refine mosaic running the real Elo update (K=24), session-local.
- `js/search-data.js` — rankings captured from the live archive's `/api/search` fusion
  (metadata + embeddings + captions). Regenerate with `tools/build_search_data.py`.

Static site, no build step. Serve the folder any way you like:

```
python -m http.server 8630
```
