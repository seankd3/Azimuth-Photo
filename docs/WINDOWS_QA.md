# Windows QA

Quality bar: "it works on the harness" must mean "it works on the Windows
satellite." Run both layers from a Windows clone — the fast smoke first, then
the full isolated harness.

## Fast smoke (platform-sensitive checks)

From a fresh Azimuth Photo clone on the Windows satellite (no hub contact, no
archive mutation), in Git Bash:

```bash
cd /c/path/to/photo-archive
python -m venv web/.venv
web/.venv/Scripts/python.exe -m pip install -r web/requirements.txt
web/.venv/Scripts/python.exe scripts/qa_windows_smoke.py
cd web && ../web/.venv/Scripts/python.exe -m pytest -q --ignore=test_allphotos_playwright.py --ignore=test_trash_navigation_playwright.py
```

The smoke script finishes in under a minute and checks Explorer argv selection,
the hub-mirror reveal guard, Windows cross-drive handling, portable date text,
and the export-directory override. The two isolated Playwright tests are
explicitly skipped on Windows because their harness stops servers through POSIX
process groups; all application tests remain in the pytest command above.

Set the runtime roots before starting the local app if the Windows install does
not use its normal defaults:

```bash
export PHOTOARCHIVE_EXPORT_DIR='D:\Azimuth\Exports'
export PHOTOARCHIVE_LIBRARY_EXPORT_DIR='D:\Pictures\Azimuth Photo Exports'
```

## Harness runner

Run the same isolated desktop suite from a Windows clone with:

```powershell
.\scripts\qa.ps1
```

The runner uses `web\.venv\Scripts\python.exe`, starts only loopback probe
servers, and stores fixture data plus durable evidence in the platform temporary
directory by default. Set `PHOTOARCHIVE_QA_SCRATCH` to retain it elsewhere, and
set `QA_WAIT_MULTIPLIER` (for example `2`) when the machine is under load.

Each invocation creates `qa-harness\runs\<UTC timestamp>-<git sha>\` with its
report, combined server log, and failure artifacts. `report.json` at the scratch
root remains a copy of the latest report for existing tools. Use:

```powershell
& web\.venv\Scripts\python.exe scripts\qa_report.py
```

to see the latest 20-run flake trend. A scenario with three flakes in that window
prints a `QUARANTINE` warning.
