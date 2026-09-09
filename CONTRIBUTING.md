# Contributing to Azimuth Photo

Thanks for helping make a photo app people can trust with their archives.

## Run it

```powershell
python -m venv web\.venv
web\.venv\Scripts\python.exe -m pip install -r web\requirements-v2.txt -r web\requirements-dev.txt
npm ci
.\scripts\start_azimuth_windows.ps1 -DataRoot C:\some\empty\folder
```

`./scripts/azimuth-check` is the whole check; `docs/development.md` is the
source of truth for setup, the shape of the tree, and verification.

## Keep the product clear

Read `AGENTS.md`. A feature is a query with a name plus the decisions it
writes; anything that is not a query, a decision, or a cache kind has no layer
to live in. Prefer the simplest real behavior over frameworks, layers, or
defensive ceremony, and never a mockup where the app can do the work for real.

## Pull requests

One focused change per PR with an atomic commit history. Explain the
user-facing result, add a refuter with a fix, say exactly what you ran, and
keep generated files and drive-local data out of the diff.
