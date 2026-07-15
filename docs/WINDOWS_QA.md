# Windows QA

Run this from a fresh Azimuth Photo clone on the Windows satellite. It does not
contact the hub or mutate an archive.

In Git Bash:

```bash
cd /c/Users/sean/Projects/photo-archive
python -m venv web/.venv
web/.venv/Scripts/python.exe -m pip install -r web/requirements.txt
web/.venv/Scripts/python.exe scripts/qa_windows_smoke.py
cd web && ../web/.venv/Scripts/python.exe -m pytest -q --ignore=test_allphotos_playwright.py --ignore=test_trash_navigation_playwright.py
cd .. && bash scripts/qa.sh
```

Set the runtime roots before starting the local app if the Windows install does
not use its normal defaults:

```bash
export PHOTOARCHIVE_EXPORT_DIR='D:\Azimuth\Exports'
export PHOTOARCHIVE_LIBRARY_EXPORT_DIR='D:\Pictures\Azimuth Photo Exports'
```

The smoke script finishes in under a minute and checks Explorer argv selection,
the hub-mirror reveal guard, Windows cross-drive handling, portable date text,
and the export-directory override. The two isolated Playwright tests are
explicitly skipped on Windows because their harness stops servers through POSIX
process groups; all application tests remain in the pytest command above.
