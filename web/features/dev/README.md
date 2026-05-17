# Dev Feature

Owns: lightweight local process/version diagnostics for server freshness checks.
Depends on: injected `started_at` and `git_commit`; uses `os` and `time` only.
Public routes: `/api/dev/status`.
Frontend modules: None.
Data repositories: None.
Tests to run: `cd web && .venv/bin/python -m unittest test_modular_contracts -q`.
Do not touch: keep diagnostics cheap, local, and side-effect free.
