> **PARKED (CTO verdict 2026-07-16):** ship-as-is at ~1.0s — this architecture buys ~650ms on a satellite-only interactive surface at the cost of a permanently trickier startup model. Revisit if satellite start becomes a felt pain or a desktop-app wrapper lands.

# Boot campaign — architectural phase design brief (Fix Campaigns & Speed)

## Where the milliseconds are (measured, -X importtime + isolated first-200 probes, loaded box)

Boot-to-first-200 ≈ 1000 ms after the cv2/numpy deferral (was ~1190):
- ~130 ms  interpreter + uvicorn import + process launch
- ~250 ms  fastapi + pydantic ecosystem import (fixed cost; openapi.models alone 94 ms)
- ~400 ms  feature route-module execution + decoration (~40 modules × 5–35 ms each; FastAPI builds each route's Dependant at decoration AND AGAIN at include_router — the cost is structural, not one hotspot)
- ~150 ms  app.py body: create_app + configure_* + ~30 include_router calls
- ~50 ms   startup callbacks/template warmup
- ~130 ms  first rankings query + serialization (already SWR-cached afterwards)

In-flight lanes shave the heavy-lib tail (numpy/rawpy/PIL/imagecodecs deferral). Realistic floor with safe deferrals only: ~800–900 ms. Sub-500 requires not paying route decoration for ~40 modules before first-200.

## Proposed design: early-bind + gated background registration

1. app.py registers ONLY the instant-surface eagerly: health/version, auth, pages (UI shell), media/thumbnail routes. Target: first-200 on /api/version and the UI shell in <400 ms.
2. Immediately after bind, a startup task registers the remaining feature routers in priority order (library → develop → sync → the rest) on the SAME app. Starlette consults the route table per-request, so post-bind registration is legal.
3. NO 404 window: a tiny middleware holds any request for a not-yet-registered path on an asyncio.Event("wiring_complete") (bounded wait ~2 s, then proceed to real 404). A user who beats the warm-up waits milliseconds instead of seeing an error.
4. OpenAPI schema is built lazily on first /docs access (already effectively true in FastAPI) — no correctness cost.

Properties: no route moves, no import restructure beyond app.py/wiring, UI shell + photos visible at the same moment the socket opens, full API surface ready ~600 ms later, zero behavior change for any client that isn't racing the first half-second.

## Cost/risk
- app.py/wiring reorganization: the eager/deferred split must be explicit and boring (two lists, no framework).
- Middleware is ~20 lines but sits on the hot path — must be a no-op after the event fires (single bool check).
- Tests: route-availability race tests + the standing boot KPI bench.

## Ask
Sign-off to campaign this (one lane, Fable-spec'd, Codex-executed, bench-gated). If the 1.0 s status quo is acceptable for prod (systemd restarts are rare) but the XPS satellite's interactive start matters, we can scope it satellite-first behind the same code path.
