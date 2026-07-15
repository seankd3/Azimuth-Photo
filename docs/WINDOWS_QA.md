# Windows QA

Run the desktop QA matrix from a Windows clone of Azimuth Photo. Nothing here
contacts the hub or mutates a real archive — probe servers are loopback-only
with an isolated PHOTOARCHIVE_HOME.

## One-time setup

```bash
python -m venv web/.venv
web/.venv/Scripts/python.exe -m pip install -r web/requirements.txt
```

Set runtime roots first if the Windows install does not use its normal defaults:

```bash
export PHOTOARCHIVE_EXPORT_DIR='D:\Azimuth\Exports'
export PHOTOARCHIVE_LIBRARY_EXPORT_DIR='D:\Pictures\Azimuth Photo Exports'
```

## Fast smoke (<60s)

```bash
web/.venv/Scripts/python.exe scripts/qa_windows_smoke.py
```

Checks Explorer argv selection, the hub-mirror reveal guard, Windows cross-drive
handling, portable date text, and the export-directory override.

## Full suite

```bash
cd web && ../web/.venv/Scripts/python.exe -m pytest -q --ignore=test_allphotos_playwright.py --ignore=test_trash_navigation_playwright.py
cd .. && bash scripts/qa.sh
```

PowerShell equivalent for the harness: `.\scripts\qa.ps1`.

The two isolated Playwright tests are skipped on Windows because their harness
stops servers through POSIX process groups; all application tests remain in the
pytest command above.

## Harness behavior

The runner uses `web\.venv\Scripts\python.exe`, starts only loopback probe
servers, and stores fixture data plus durable evidence in the platform temporary
directory by default. Set `PHOTOARCHIVE_QA_SCRATCH` to retain it elsewhere, and
set `QA_WAIT_MULTIPLIER` (for example `2`) when the machine is under load.

Each invocation creates `qa-harness\runs\<UTC timestamp>-<git sha>\` with its
report, combined server log, and failure artifacts. `report.json` at the scratch
root remains a copy of the latest report for existing tools. Flake trend:

```powershell
& web\.venv\Scripts\python.exe scripts\qa_report.py
```

A scenario with three flakes in the latest 20-run window prints a `QUARANTINE`
warning.
