# CLIENTCACHE — XPS client-side caching audit

Lane: `gxclient` worktree. Date: 2026-07-19.  
Scope: mechanical HTTP/SW/grid wins only — no UI redesign. Prod checkout untouched.

---

## Verdict

Repeat grid browsing was already partly helped by **browser HTTP cache** (`max-age=1d` + ETag), but the XPS NVMe was unused as a durable client tier, desktop never registered the existing mobile SW, and versioned static assets were only cached for **5 minutes**. This lane:

1. Strengthened thumb + static Cache-Control (safe; thumbs are **not** `immutable` — see below).
2. Switched SW thumb strategy to **cache-first** (size-bounded), registered it on desktop when secure, with a kill switch.
3. Prefetches each loaded page’s `sm` thumbs on idle (no visual change).
4. Parked larger designs for CTO decision.

---

## Evidence — before (live hub `:8000`, read-only)

```bash
# GET (HEAD returns 405 on thumb routes)
curl -sD - -o /dev/null http://100.102.150.104:8000/api/thumb/sm/18588
```

Observed:

| Resource | Cache-Control | ETag |
|---|---|---|
| `/api/thumb/sm/18588` | `public, max-age=86400, stale-while-revalidate=604800` | `"cd182e4ea4a1c5c717ab18dea8549818a06f1baf"` (source signature) |
| same + `If-None-Match` | → **304** | same |
| `/static/desktop.css` | `public, max-age=300, stale-while-revalidate=3600` | file etag |
| `/static/desktop.css?v=999` | same 300s (not immutable) | file etag |
| `/sw.js` | `no-cache` + `Service-Worker-Allowed: /` | — |

Content addressing: disk/memory keys are `(size, image_id, source_signature)` where signature hashes cache version + tier + id + size/quality + source size/mtime. **URL is only** `/api/thumb/{size}/{id}` — not content-hashed — so `immutable` on thumbs would freeze wrong bytes after a rescan/replace.

Pending/empty: `204` / errors use `Cache-Control: no-store` (correct).

---

## Evidence — after (this worktree)

```text
BROWSER_CACHE_MAX_AGE 604800          # 7d
BROWSER_CACHE_STALE_WHILE_REVALIDATE 2592000  # 30d
thumb Cache-Control: public, max-age=604800, stale-while-revalidate=2592000
static unversioned: public, max-age=300, stale-while-revalidate=3600
static versioned (?v=): public, max-age=31536000, immutable
```

Proved via worktree import + `TestClient` on `create_base_app` (prod process not restarted).

Note: saved settings may still pin the old `browser_cache_max_age` until settings are re-saved; code defaults are 7d/30d.

---

## Grid JS audit (no Blob/objectURL churn)

| Behavior | Finding |
|---|---|
| Thumb load | `data-src` → `src` via IntersectionObserver (`rootMargin: 900px`) |
| Virtual window | `grid_window.js` **despawns** offscreen chunks (`innerHTML` cleared → ghost); rematerialize rebuilds `<img>` and re-assigns `src` |
| In-memory LRU / object URLs | **None** for grid thumbs — plain URL strings; browser (and now SW) own caching |
| Hover | `warmMediumThumb` preloads `md` on pointerover/focusin |
| Prefetch gap | Page JSON arrived before thumbs started; fixed with idle `prefetchPageThumbs(incoming)` |

### Scroll request trace (live hub, Playwright, HTTP — no SW)

Forced deep scroll until early chunks were `ghost`, then `scrollTop = 0` to rematerialize:

| Phase | Result |
|---|---|
| Load + deep scroll | **364** unique `/api/thumb/sm/*` requests (first paint / first visit) |
| After deep scroll | 4 chunks, **3 ghost**, 1 live |
| Resurface top | **32** request events, **all** overlapped first-pass URLs (0 new ids); `from_service_worker: false` |

Interpretation: rematerialize **does** re-hit `img.src`, but Chromium’s HTTP disk/memory cache absorbs most of it within `max-age`. Without SW, a cleared browser cache or cold XPS session still pays Tailscale for every thumb once. SW cache-first (HTTPS/localhost) keeps a size-bounded Cache API copy across sessions.

Temporary Playwright counter script was run ad-hoc and **not** left in the tree.

---

## Changes in this lane

| Area | Change |
|---|---|
| Thumb headers | Default max-age **7d**, SWR **30d** (`thumbnails/config.py`, `settings.py`). Still ETag-validated; **not** immutable. |
| Static headers | `?v=` → `max-age=31536000, immutable`; unversioned stays 300s (`StaticCacheHeadersMiddleware`). |
| Service worker | `thumbCacheFirst`: only store **status 200 + `image/*`** (never 204/JSON). Cap **4000** entries. Versioned via `?v=`. |
| Registration | Shared `static/js/sw_register.js`; desktop `bootstrap.js` + mobile template. Kill switch: `?pa_sw=0` or `localStorage.pa_sw=0` (unregisters). Requires secure context. |
| Prefetch | `prefetchPageThumbs` after each grid page load. |
| Satellite | Same-origin `/api/thumb/*` only; rankings/API stay network-only. Caching hub-filled JPEGs on the XPS satellite is desirable and safe. |

Disable SW without redeploy: open `/d?pa_sw=0` (or set `localStorage.pa_sw=0`).

---

## Parked design options (not implemented)

### 1. Satellite-as-cache-tier (XPS NVMe as explicit client cache)

Run satellite on XPS with aggressive local `.thumbcache` + hub read-through. Browser always talks to localhost → sub-ms thumbs after first sync. Overlaps server-side `features/sync/prefetch.py` / preview mirror. **Product call:** is the XPS always a satellite, or sometimes a thin browser against the hub?

### 2. IndexedDB metadata cache

Cache rankings/facet JSON with TTL + ETag/generation. High risk of stale counts/flags; SW deliberately excludes `/api/` except thumbs. Needs explicit invalidation on flag/trash/import — larger product contract.

### 3. Content-addressed thumb URLs (`/api/thumb/sm/{id}?s={signature}` or path hash)

Enables true `immutable` + CDN-style caching. Requires API/card URL changes across desktop, mobile, shares. Highest correctness for aggressive cache; not a one-line header tweak.

### 4. Larger SW / OPFS original/full tier

Cache `lg` / `/api/full/` in Cache API or OPFS with GB budgets. Competes with server SSD tiers; needs eviction UX and satellite coordination.

### 5. In-page thumb LRU (object URLs)

Avoid rematerialize decode cost. Adds Blob churn/revoke complexity; HTTP+SW already handle bytes. Low ROI unless decode jank is measured.

### 6. Shared worker / BroadcastChannel warm across tabs

Nice for multi-window XPS; secondary after SW.

---

## Proof commands

```bash
# Before (prod, unchanged):
curl -sD - -o /dev/null http://100.102.150.104:8000/api/thumb/sm/18588 | grep -iE 'cache-control|etag|HTTP/'

# After (worktree code):
cd web && .venv/bin/python -c "
from thumbnails import config as c
from thumbnails.source_identity import response_headers
print(response_headers(etag='\"x\"', browser_cache_max_age=c.BROWSER_CACHE_MAX_AGE,
    browser_cache_stale_while_revalidate=c.BROWSER_CACHE_STALE_WHILE_REVALIDATE))
"

cd web && .venv/bin/python -m pytest -x -q \
  test_mobile_contracts.py::MobileOfflineContractsTests::test_service_worker_is_secure_only_and_versioned \
  test_ui_contracts.py::UiContractsTests::test_service_worker_precaches_mobile_shell_only \
  test_modular_contracts.py::ModularContractTests::test_frontend_shells_use_living_entrypoints \
  test_modular_contracts.py::ModularContractTests::test_versioned_static_assets_are_immutable \
  test_desktop_correctness.py::DesktopCorrectnessTests::test_grid_prefetches_page_thumbs_and_registers_sw \
  --override-ini='filterwarnings=ignore::DeprecationWarning'
```

Work left **uncommitted** per lane instructions.
