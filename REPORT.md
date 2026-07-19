# healthpanel — System Health surface

## Verdict

Shipped. `/api/health/details` aggregates existing signals into one owner-auth payload; System → Library shows a **System Health** panel with ok/warn/bad chips and a degraded banner, auto-refreshing while open.

## What landed

| Surface | Behavior |
|---------|----------|
| `GET /api/health/details` | Owner-auth aggregation: catalog quick_check age, catalog backup age + verified, cloud vault (or “Not configured”), library/cache disk free, memory watermarks, pregen, workers, last import/sync |
| System → Library panel | Status row per check, overall badge, banner when overall is `bad`, 30s refresh while open |
| Header affordance | **Panel-only.** No existing persistent “health degraded” topbar chip/toast pattern (activity widget = worker activity; sync chip = satellite). Did not invent one. |

## Proof

```text
.venv/bin/python -m pytest test_system_health.py -q
# 9 passed

.venv/bin/python -m pytest test_system_health_playwright.py -q
# 1 passed

node --check static/js/desktop/{system_health,drawer,api}.js
```

Screenshot: [`receipts/system-health-panel.png`](receipts/system-health-panel.png)

## Notes

- Opsvault cloud module is not on this branch yet — vault check returns `ok` / “Not configured” until that feature lands; then it reads `features.backup.cloud.status_payload()` without re-probing rclone.
- Aggregation composes caches/status helpers only (no fresh `PRAGMA quick_check`, no cache_stats rebuild for pregen).
- Non-goals honored: no ntfy/alerting, no history charts, probes unchanged.
