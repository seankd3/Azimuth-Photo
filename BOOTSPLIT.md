# Boot-split (impl-bootsplit)

Cut XPS boot weight: lazy-load Develop/GL; stop full-inspector re-renders on focus/page-append.

Work is **uncommitted** on `impl-bootsplit`. Prod (`~/Projects/photo-archive`) untouched.

## A. Lazy Develop / export dialog

### Before (static reachability from `bootstrap.js`)

| Metric | Value |
|--------|------:|
| Modules | 94 |
| `develop/` modules | 27 |
| Total source bytes | 1,331,862 |
| Develop source bytes | 439,899 |

Largest develop pulls: `gl.js` 107KB, `develop.js` 63KB, `masking.js` 41KB, `panels.js` 27KB, `export_dialog.js` 24KB.

Paths: `keyboard.js` → static `develop/develop.js`; `export_menu.js` → static `develop/export_dialog.js`.

### After

| Metric | Value |
|--------|------:|
| Modules | 67 |
| `develop/` modules | **0** |
| Total source bytes | 896,071 |
| Develop source bytes | **0** |

Delta: **−27 develop modules, −435,791 bytes (~33%) off the static boot graph.**

`keyboard.js` and `export_menu.js` use `await import(...)` (same pattern as `loupe.js` / `lenses.js`). Tiny sync stubs keep `developOpen()` / Escape routing / `savedOriginalsExportSize()` working before the chunk loads.

### Graph walker (reproducible)

```bash
cd /home/sean/Projects/pa-impl-bootsplit && python3 - <<'PY'
import re
from pathlib import Path
ROOT = Path('web/static/js/desktop').resolve()
FROM_RE = re.compile(r"""(?:^|\n)\s*(?:import|export)\s[\s\S]*?\sfrom\s+['"](\.[^'"]+)['"]""", re.M)
SIDE_RE = re.compile(r"""(?:^|\n)\s*import\s+['"](\.[^'"]+)['"]""")

def resolve(base, spec):
    p = (base.parent / spec).resolve()
    return p if p.suffix else p.with_suffix('.js')

def static_imports(f):
    text = re.sub(r'/\*.*?\*/', '', f.read_text(encoding='utf-8'), flags=re.S)
    text = re.sub(r'(?m)^\s*//.*$', '', text)
    return {m.group(1) for m in FROM_RE.finditer(text)} | {m.group(1) for m in SIDE_RE.finditer(text)}

seen, queue, order = set(), [ROOT / 'bootstrap.js'], []
while queue:
    f = queue.pop(0)
    if f in seen or not f.exists(): continue
    seen.add(f); order.append(f)
    for spec in static_imports(f):
        t = resolve(f, spec)
        if t not in seen and str(t).endswith('.js'): queue.append(t)
develop = [f for f in order if '/develop/' in str(f)]
print(f'modules={len(order)} develop={len(develop)} '
      f'total_bytes={sum(f.stat().st_size for f in order)} '
      f'develop_bytes={sum(f.stat().st_size for f in develop)}')
PY
```

After change: `develop.js` / `gl.js` / `export_dialog.js` are **not** in the static graph.

## B. Right-panel render cull

Changes in `panel_right.js`:

1. **Focus id diff** — skip work when focus emit repeats the same id.
2. **Set vs focus paths** — `renderSet()` on init/expand/flags; `renderFocus()` on distinct focus; `images` only rebuilds histo/rank (and caption/EXIF only if focus id changed).
3. **Collapsed** — ignore `images` / selection / flags / focus DOM work while right panel is collapsed; re-`renderSet()` on expand.
4. **Debounced detail fetches** — 80ms debounce on EXIF enrichment + caption network.
5. **Histogram counts only** — bins store `count`, not full `ids[]`; highlight via focused/selected elo → bin index.

### Temporary counter proof (then removed)

Instrumented `window.__paBootsplitPanel`, then mirrored the guards in a decision sim:

| Event | Count |
|-------|------:|
| `renderSet` (init + expand) | 2 |
| `renderFocus` (distinct ids 2…6) | 5 |
| skip same-id focus (8 repeats) | 8 |
| skip `images` while collapsed (5 appends) | 5 |
| open-panel page appends (histo/rank light, not full set) | 10 |

**Not** once per arrow when id repeats; **not** full inspector on every page append; collapsed appends are no-ops.

Counters removed from source after evidence.

## Tests

```bash
cd web && PATH="/usr/bin:/usr/local/bin:$PATH" \
  .venv/bin/python -m pytest -x -q \
  test_desktop_correctness.py test_modular_contracts.py test_ui_contracts.py
# 56 passed
```

Contract updates: keyboard/export_menu must use dynamic `import()` (no static `from './develop/...'`); `openExportMenu` is async; `closeDevelop()` count includes stub.

## Risks

| Risk | Mitigation |
|------|------------|
| First D / Develop tab pays one chunk fetch | Same as loupe/lenses; subsequent opens cached by browser module map |
| First Export menu open pays `export_dialog` fetch | Same; zip/data paths load dialog only when needed |
| `savedOriginalsExportSize` duplicated localStorage key | Same key `pa_d_export_dialog_photos` as dialog; stays sync for panel.js |
| Histogram highlight uses elo bin, not id membership | Identical bins when elo maps uniquely; edge: two photos same elo still same bin |
| Caption/EXIF 80ms debounce | Feels identical while arrowing; settled focus still loads |

## Non-goals / untouched

- gxclient / gxlat (SW cache, prefetch, soft-miss retry)
- Prod checkout / service restarts
- Visual/UX changes
