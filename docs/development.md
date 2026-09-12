# Development

Azimuth Photo is one Python process. `web/desktop.py` opens a native WebView2
window on one bundled HTML document and hands it the product boundary in
`web/boot.py`; there is no server, port, route, or framework. The UI is plain
ES modules under `web/static/v2/`, bundled by esbuild into the one document the
window opens.

## Setup

Windows, Python 3.12, Node 22:

```powershell
python -m venv web\.venv
web\.venv\Scripts\python.exe -m pip install -r web\requirements-v2.txt -r web\requirements-dev.txt
npm ci
```

The embedding space needs one more pack (SigLIP-2 through `torch` and
`transformers`, ~4.5 GB of weights fetched once by `embed.fetch()`); without it
the app runs, and search, Best and labels wait for vectors. People needs
nothing extra: InsightFace ships in `requirements-v2.txt` and runs on the CPU.

## Run it

```powershell
.\scripts\start_azimuth_windows.ps1                          # your library
.\scripts\start_azimuth_windows.ps1 -DataRoot C:\some\empty  # an isolated home
```

The app as its own program -- `Azimuth Photo.exe` in Task Manager, the compass
on the taskbar, a version in Properties -- is `scripts/make_launcher.py`: it
makes the exe beside the venv's own python and points the Desktop and Start
Menu shortcuts at it. Run it again after the venv is rebuilt.

The launcher opens `web/desktop.py`, which builds the document when any file
under `web/static/v2/` or the template is newer than the build (half a
second, marked in the log), so the window shows the UI the tree says. A build
that fails refuses the launch and writes why to `logs/azimuth.log`. To build
by hand:

```powershell
web\.venv\Scripts\python.exe scripts\build_desktop_ui.py
```

`AZIMUTH_HOME` names the home folder (catalog and previews) without touching
the per-user pointer, which is how every proof stays isolated from the real
library.

## The shape

| | |
|---|---|
| `web/model/` | the five tables and the seven functions; imports nothing of the product |
| `web/photo/` | pure format, EXIF and tag reads; below `model` |
| `web/pixels/` | the colour mathematics; pure functions over arrays |
| `web/work.py` | owed work: what should exist minus what is cached |
| `web/tiles.py`, `web/render.py` | one decode per photograph, two sizes, the file the window reads |
| `web/library.py`, `web/rank.py`, `web/search.py`, `web/develop.py`, `web/labels.py`, `web/people.py`, … | one surface each: queries over the facts plus the decisions it writes |
| `web/boot.py` | `Library` / `OwnedLibrary`, the one product boundary |
| `web/desktop.py` | the native edge: window, bridge verbs, shutdown |
| `web/static/v2/` | `kit ← net ← store ← lens ← shell` |

`docs/ARCHITECTURE.md` owns the rules; `scripts/gates/layers.py` counts the
imports that break them.

## Checks

```bash
./scripts/azimuth-check --quick   # lint and the gates, a few seconds
./scripts/azimuth-check           # plus the suite, the node specs, and the ledger
```

The suite is `pytest` over `web/` (every test under ten seconds, by
`web/pytest.ini`), plus two node specs for the grid kit. Run one file when the
loop needs to be narrower:

```powershell
cd web; .venv\Scripts\python.exe -m pytest -q test_core.py
```

`scripts/gates/check.py` counts couplings: imports pointing up, tables made
outside the schema, guides naming missing paths, undefined names, a suite that
cannot collect, imports of files that are not there. It fails on any drift from
`scripts/gates/budget.txt`. `scripts/rewrite_status.py --check` fails while any
code file is unregistered in `docs/REWRITE_LEDGER.md`.

## Proofs

A change to the running app is proven by running it, and none of it has to
touch the screen:

- `scripts/harness.py` serves the bundled UI with a stub bridge on :8765. The
  whole shell runs in a browser tab with no window and no catalog; read its
  DOM, drive it, screenshot it. Shape and behaviour, never pixels.
- `scripts/native_proof.py <home> <out.png> --probe probe.js` opens the real
  app off-screen on an isolated `AZIMUTH_HOME`, evaluates a JavaScript probe in
  the page, captures the window with `PrintWindow`, and closes. Pixels, tiles,
  the worker, the lot, with nothing on the desktop.
- `test_desktop.py` crosses the real bridge with no window at all.
- `scripts/journeys/*.js` are the probes for what a photographer actually
  does (cull, loupe, survey, search, album, people), each run through
  `native_proof.py` to a screenshot; the gallery is read side by side with the
  last round's, never diffed.

`sim_rank.py` and `sim_learn.py` are the instruments the ranking modes were
chosen with; `make_test_library.py` builds a few hundred real photos on fast
disk to develop against instead of the archive.

## Build the app

```powershell
.\scripts\build_windows_desktop.ps1
```

produces `dist\azimuth-photo\azimuth-photo.exe`, one directory, no installer
yet. The frozen directory carries the Python runtime, the one inlined UI
document, the schema and the native dependencies; the person running it
installs nothing. App data lives in the home `web/home.py` resolves.

First-install smoke, on a Windows account with no Azimuth data:

1. Launch `azimuth-photo.exe` without a terminal.
2. The welcome asks where Azimuth should live; no browser address or engine
   window appears.
3. Choose a photo folder in the native chooser; the library fills.
4. Quit; no Azimuth process remains and the catalog file can be renamed.
5. Relaunch; the same library opens without asking again.
6. Repeat with a mapped drive and a UNC folder.
