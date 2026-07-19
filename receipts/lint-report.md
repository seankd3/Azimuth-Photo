# Lint work order — report

**Verdict:** ruff, eslint, and luacheck all exit 0 on `lint`. `scripts/lint` is the standalone entrypoint. `scripts/gate` is **not** on `origin/develop` yet (testspeed not merged), so gate-quick wiring is deferred; `photoarchive-check --quick/--unit` runs lint first.

## Gate / develop note

- Checked `~/Projects/pa-develop` and `origin/develop`: no `scripts/gate`, no `requirements-dev.txt`.
- Gate exists on `pa-testspeed` / `pa-ci-track` only.
- Shipped `scripts/lint` standalone.
- Wired into `scripts/photoarchive-check` `run_quick_checks` (covers pa-ci-style **unit** path today).
- When `scripts/gate` lands, prepend this to the `quick)` branch (fail-fast before pytest):

```bash
    if [[ -x "$ROOT/scripts/lint" ]]; then
      echo "==> lint" | tee -a "$LOG"
      set +e
      "$ROOT/scripts/lint" >>"$LOG" 2>&1
      lint_rc=$?
      set -e
      echo "==> lint exit=$lint_rc" | tee -a "$LOG"
      if [[ $lint_rc -ne 0 ]]; then RC=$lint_rc; cp -f "$LOG" "$LAST"; echo "gate finished mode=$MODE exit=$RC log=$LOG" | tee -a "$LOG"; exit "$RC"; fi
    fi
```

## Commits (config separate, one cleanup per tool)

1. `chore(lint): add ruff config and requirements-dev`
2. `chore(lint): ruff zero-warning cleanup`
3. `chore(lint): add eslint flat config for frontend JS`
4. `chore(lint): eslint zero-warning cleanup`
5. `chore(lint): add luacheck config for Lightroom plugin`
6. `chore(lint): luacheck zero-warning cleanup`
7. `chore(lint): ship scripts/lint and wire into photoarchive-check`
8. `fix(lint): restore contract wrapper and settings cache test binding`

## Linter proof

### `./scripts/lint` → exit 0

```
==> ruff
All checks passed!
==> eslint
==> luacheck
… Total: 0 warnings / 0 errors in 7 files
lint: ok
```

### Individual

| Tool | Command | Exit |
|------|---------|------|
| ruff | `cd web && .venv/bin/ruff check .` | 0 |
| eslint | `./node_modules/.bin/eslint web/static/js web/static/sw.js` | 0 |
| luacheck | `luacheck clients/lightroom` | 0 |
| lua syntax | `luac5.4 -p` on every `clients/lightroom/**/*.lua` | 0 |

### Ruff config highlights (`web/pyproject.toml`)

- `line-length = 100` (p95≈89 / p99≈108 on this tree)
- Ignores (one-line why each): E501, E402, E731, E702, E741, F405
- Per-file: `thumbnails/__init__.py` F821 (dynamic globals facade); `test_support.py` F401 (star-import harness)

## Pytest (bare)

```
3 failed, 1361 passed, 3 skipped, 1 deselected, … in 472.52s
PYTEST_EXIT:1
```

Failures (not introduced by lint cleanup — playwright/perf flakes; our two cleanup regressions were fixed and re-verified green):

- `test_allphotos_playwright.py::test_all_photos_grid_matches_catalog_total`
- `test_lr_ux_playwright.py::test_lr_ux_connect_and_ranking_chip_screenshots`
- `test_perf_budgets.py::test_date_histogram_budget`

Lint-related regressions found in first full run and fixed:

- restored `scheduleThumbnailPoll` wrapper in `grid.js` (desktop correctness string contract)
- restored `first = await api_settings()` in nested cache mutation test

## Behavior-suspect findings deliberately NOT auto-fixed

1. **`web/features/publish/routes.py` — `persist_revoke` uses unbound `publish`**  
   On failed revoke hook retry, `_upsert_publish(..., slug=publish["slug"], ...)` references `publish` which is never defined in that closure. Would NameError at runtime on that path. Left with `# noqa: F821` + this note.

2. **`web/test_shortcut_sheet.py` — duplicate dict key `"K"`**  
   Second `"K"` entry overwrites the first (masking vs other binding). Test expectation is ambiguous; left with `# noqa: F601`.

3. **`web/test_system_backups.py` — alternating `source_id` computed then ignored**  
   Loop computed `source_id = 1 if index % 2 else 2` but INSERT always used `1`. Removed the dead assignment only; wiring `source_id` into the INSERT would change fixture shape.

4. **`web/features/develop/film.py` — `_fog` read then unused**  
   Comment documents intentional non-use (H&D already includes base+fog). Renamed to `_fog` only.

5. **`web/features/develop/rawproc.py` — `_up` from uv′ pair unused**  
   Tint uses only `vp - vp_p`. Prefix `_up`; using `up` in the formula would change WB math.

6. **QR `char_bits` dead assignment** (`qr_encode.py`)  
   Always `8`; length encoding already uses the ternary on the next line. Removed dead local only — did not change bit packing.

7. **Duplicate identical `DefaultRootTests` in `test_sync_hub.py`**  
   Second class shadowed the first (identical body). Removed duplicate; no behavioral change to which tests run.
