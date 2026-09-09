# Performance budgets

Browse speed is a product invariant, and a budget is a design constraint: a
number is never widened to mask slowness, and a faster number never hides an
incomplete answer (zero omitted or misordered photographs is part of every
row). Measure first; then the budget is the measurement, held.

| Daily workflow | Target | Status |
| --- | --- | --- |
| Cull/Refine/ranking action → visible acknowledgement | < 50 ms perceived, no spinner | covenant |
| Prepared local workflow (server absent, network denied) | zero network dependency | covenant |
| Durable ranking-action append | p95 ≤ 50 ms, p99 < 100 ms | proposed |
| Dual click → both photos replaced | p95 ≤ 50 ms, p99 < 100 ms | proposed |
| Export start → acknowledgement | ≤ 50 ms | proposed |
| Launch → first usable library | measure cold/warm, local/NAS first | proposed |
| Grid first content and sustained scroll | measure first; no omitted photos | proposed |
| Loupe open and next/previous | measure cached/uncached, RAW/raster first | proposed |
| Develop adjustment → preview update | measure first; input never dropped | proposed |

The measurements behind the current numbers live in `FINDINGS.md`
§Performance (a fresh 150,000-row catalog, 2026-09-09): a page at any depth in
tens of milliseconds, a rerank that writes only what changed, the window open
before any repair runs. The V1 bench scripts, the perf CI gate and the
committed baseline this document once described were deleted with the server;
a V2 bench is owed and will be named here when it exists.
