# Desktop end-to-end QA gate

The desktop QA harness click-drives `/d` against an isolated, deterministic
library. It exists to catch the owner-visible regressions that unit and API
tests miss: frozen scope changes, incomplete context menus, destructive actions
that do not update the UI, dead navigation after Trash, and loupe/Develop
render failures.

## Run it

From the repository root:

```bash
./scripts/qa.sh
```

Or, from `web/`:

```bash
TMPDIR=/mnt/expansion/tmp .venv/bin/python -m qa.run
```

List or isolate scenarios without changing their behavior:

```bash
./scripts/qa.sh --list
./scripts/qa.sh --scenario empty_trash_and_leave
```

The command exits nonzero when any scenario fails. Every result names the
surface and last user action. Failures include console errors, failed browser
requests, HTTP 500 responses, timeout detail, and a screenshot. The complete
machine-readable report is
`/mnt/expansion/tmp/az1/qa-harness/report.json` by default.

Set `PHOTOARCHIVE_QA_SCRATCH` when CI does not mount `/mnt/expansion/tmp`.
Do not install packages into `web/.venv`; the lane venv is a symlink and already
contains Playwright. Chromium always launches with `--no-sandbox` and
`--disable-dev-shm-usage`.

## What it covers

Each scenario gets a fresh browser context and is independently named:

- Library load, multi-chunk scroll, and virtualization.
- Hub source, local folder, and collection scope changes, including return to
  All Photos.
- Sort, filter, and an actual date-scrubber jump into a later grid window.
- Windows source and folder context menus, including the exact
  `Open in Explorer` action.
- Trash load, typed Empty Trash confirmation, response payload, database
  result, empty-state repaint, and prompt return to All Photos.
- Photo click into Loupe, then a full Develop canvas render.
- A real RAW workflow: tone and presence sliders, crop and rotate, preset,
  Before/After, reset, and a non-empty rendered export without NaN or canvas
  failures.
- A three-photo collection created from Grid selection, then carried through a
  private link, website publishing node, and verified link revocation.
- Hierarchical keyword assignment plus all editable IPTC fields, reloaded in
  the photo panel to prove persistence.
- Settings, Import, and live metadata search entry points.

Every scenario rejects `console.error`, uncaught page errors, failed requests,
and HTTP 500+ responses. Chromium cancellations caused specifically by the grid
removing offscreen thumbnail `<img>` sources are ignored; API cancellations and
all other request failures still fail the scenario.

## Fixture and isolation

`web/qa/` owns the harness. It builds a versioned fixture under a temporary
`PHOTOARCHIVE_HOME` with:

- 4,000 active images across primary, removable, and `hub://` mirror sources;
- nested folders, nine years of dates, flags, metadata, and a 30-photo
  collection;
- one tiny standards-readable DNG plus five valid small JPEG originals for
  Loupe and Develop, and a deterministic Develop preset;
- cached previews for every catalog row; and
- six source-local Trash files that Empty Trash can really delete.

The pristine SQLite catalog is reused across scenarios and runs. Before each
run it is copied back into place and the six Trash files are restored, so a red
scenario cannot contaminate the next invocation. The server binds a kernel-
selected localhost probe port and is stopped as a process group. The harness
never addresses `:8000`, the `photoarchive` service, or a production path.

## Pre-deploy behavior

`scripts/deploy.sh` runs this gate after the Python suite and before catalog
backup, push, or service restart. A red browser scenario therefore aborts the
deploy before any production mutation. The expected runtime is well under five
minutes; on the standard Omarchy host the fixture is normally reused, so only
the browser and probe server startup costs remain.
