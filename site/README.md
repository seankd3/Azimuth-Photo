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

The `/log/` directory is the **Field Log** — an interactive development log of Azimuth Photo,
brand-matched to this site (self-contained: its own `devlog.css` / `devlog.js` / `assets`).

Static site, no build step. Serve the folder any way you like:

```
python -m http.server 8630
```

## Hosting & deploy

**This site is hosted on Cloudflare Pages** (project `azimuthphoto`, serving `azimuthphoto.com`
+ `www`). The custom domain and DNS live in Cloudflare, not in this repo.

> **Deploying is a manual `wrangler` step — a `git push` does NOT publish anything.**
> The Cloudflare Pages project is *not* connected to GitHub (Git Provider: No). This GitHub
> repo is a source backup only.

```bash
# from a clean copy of the site (exclude .git), authed via `wrangler login`:
npx wrangler pages deploy . --project-name azimuthphoto --branch main
```

GitHub Pages is **intentionally disabled** for this repo — an early abandoned attempt. Do not
re-enable it; it would only cause host ambiguity. There is deliberately no `CNAME` file.
