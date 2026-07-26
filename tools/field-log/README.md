# Azimuth · Field Log — a Factorio-style dev log of Azimuth Photo

An interactive, period-accurate development log telling the story of **Azimuth Photo**
across its full git history (1,562 commits, 2023-08 → 2026-07).

## View it
Open **`build/index.html`** in a browser (double-click works — it's a self-contained
static site with relative assets), or serve it:

```
cd build && python -m http.server 8791
# → http://127.0.0.1:8791/index.html
```

## What's in it
- 11 chapters + 3 interludes: the toy → web rebirth → the AI mind → the speed doctrine →
  the great refactor → three faces → **the darkroom** → measured wins → the field →
  becoming Azimuth → war stories → the fleet → what it cost.
- **14 period-accurate screenshots**, each captured by *checking out the exact commit and
  running that era's software* against a fixed set of 64 of Sean's real photos — the 2023
  Tkinter app included.
- **4 live teaching demos**: the 2023 Elo rule; a benchmark before/after race; the
  GL↔numpy "twin" parity (Δ 0.00/255); and a physically-modeled **halation** canvas that is
  a faithful port of `film.py`.
- Real code excerpts + benchmarks, each quoted from the commit that shipped it.

## How the screenshots were made (the interesting part)
`captures/` holds the capture rig:
- `harness.py` — boots a historical commit in an isolated git worktree on a scratch port
  against a scratch data dir, scans the sample photos, warms previews, and Playwright-shoots
  UI routes. Never touches prod.
- `drive.py` — per-era config (commit, routes, interaction steps, which Python interpreter).
- Three interpreters were needed for historical fidelity:
  - `.legacy-venv` (Starlette 0.37) for the Jinja-template eras (M1–M3) — modern Starlette
    dropped the old `TemplateResponse(name, context)` signature.
  - `.py310-venv` attempted for the May-schema eras; those fail to boot on any modern
    `aiosqlite` (`executescript` inside an open transaction — a bug later commits fixed),
    so the Develop/Film shots were taken from HEAD, which boots clean.
  - `.capture-venv` (current) for HEAD-era and Playwright.
- `serve.py` / `grab_develop.py` — boot-and-hold + interactive feature capture.

## Sources
- `research/full_log.txt` — the raw `git log` (1,562 commits).
- `research/chapters.md` — the narrative spine.
- `snippets/code-snippets.md` — verbatim code excerpts with commit provenance.

## Design note
Deliberately single-theme (dark). The whole story lives among dark period screenshots, so a
light chrome would clash — the "darkroom instrument" identity is a choice, not an omission.
