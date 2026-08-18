# Contributing to Azimuth Photo

> **Transitional contributor guide.** V2 is under active reconstruction; read
> `AGENTS.md` and `docs/README.md` before relying on the V1 run commands below.

Thanks for helping make a self-hosted photo app people can trust with their archives.

## Run it

```bash
cd web
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..
./scripts/azimuth-server start
```

Use `./scripts/azimuth-check --quick` for a fast local pass. Run the relevant tests for every behavior change; `docs/development.md` is the source of truth for setup, ownership, and verification.

`AZIMUTH_SMOKE_MODE=1` exists for tests only. Do not use it for normal local development or a running archive: it deliberately skips archive initialization.

## Keep the product clear

Keep modules small and owned by the product surface they serve. Prefer the simplest real behavior over frameworks, layers, or defensive ceremony that the product does not need. Do not add mockups or simulated paths when the app can do the work for real.

Treat [the development guide](docs/development.md) and [the UI architecture](docs/ui-architecture.md) as law. They define the app’s route ownership, verification ladder, and interaction bar.

## Pull requests

Make each PR one focused change with an atomic commit history. Explain the user-facing result, include tests with fixes, and say exactly what you ran. Keep unrelated formatting, generated files, and drive-local data out of the diff.
