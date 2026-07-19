# Preview mirror cold vs warm (2026-07-19)

## Method
50 sequential `thumbnail_response(..., "md", id)` calls on a temp satellite catalog
(50 hub_remote rows). Cold path: each miss hits a stub hub with **15 ms** simulated
latency and tees into the mirror. Warm path: same 50 ids after the cold pass, memory
cache cleared so hits come from the SSD mirror; hub must not be called.

## Numbers (Omarchy local)
| Path | Total | Per fetch | Hub calls |
|------|------:|----------:|----------:|
| Cold hub + tee | 1.447 s | 28.95 ms | 50 |
| Warm local mirror | 0.348 s | 6.95 ms | 0 |

**Speedup: 4.2×** under a mild 15 ms hub RTT. Real WiFi RTT is typically higher, so
grid cells that stay on the mirror feel correspondingly snappier.
