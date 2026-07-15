# Windows QA harness

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
