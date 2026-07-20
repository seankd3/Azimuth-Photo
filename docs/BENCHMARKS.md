# Azimuth Benchmarks

Reproducible hot-path timings tracked over time (newest first). Run `scripts/perfbench.py --label "..." --md`. p50/p95 in ms; preview = files/min (background, load-dependent). Interactive latency is the comparable-over-time signal.

| commit | label | preview/min | rankings | counts | folders | warm thumb |
|--------|-------|------------:|---------:|-------:|--------:|-----------:|
| `6615e2dfe` | overnight quality wave — baseline | 77 | 1.2/1.7 | 108.8/111.2 | 17.4/24.6 | 4.8/5.1 |

## Current bottleneck (bottleneck-hunting loop)

**`/api/counts` = 109 ms p50** — the slowest interactive endpoint by far (rankings is 1.2 ms). Root cause (audit-db): unfiltered counts walks the full images table (~88 ms) instead of reading maintained per-source counters. Next optimization target.

## Milestone wins — 2026-07-19/20 overnight

Big before/afters from the overnight bottleneck-hunt (measured, not estimated):

| area | metric | before | after | change |
|------|--------|-------:|------:|--------|
| Preview backfill | files/min (prod) | 2–3 | 77 | **~30×** — sequencing + candidate anti-join + mem-flap fix |
| HDD read under contention | MB/s | 2.5 | 80 | **~32×** — one bulk stream at a time (gxseq) |
| Warm thumbnail (cached) | p50 latency | 356 ms | 4.8 ms | **~74×** — cache-first serving, no HDD lstat (gxlat) |
| Cold md under bulk load | p50 latency | 356 ms | 9.8 ms | bounded decode + 204/retry (gxlat) |
| Laptop boot JS (static graph) | bytes parsed | 1.33 MB | 0.90 MB | **−33%** — lazy-load Develop/GL (bootsplit) |
| Memory gate | swap visibility | RSS-blind | RSS+swap (cgroup) | sheds correctly; false-green health fixed |
| Idle GPU model | VRAM held while paused | ~1956 MiB | ~160 MiB | unload on idle/pause (frees ~1.8 GB) |

### Bottleneck chain (the loop, in order attacked)
1. Two bulk streams thrashing one HDD → **sequenced** (previews first, vault after) — gxseq (live)
2. Cached thumbs paying an HDD stat on every request → **cache-first** — gxlat (live)
3. Candidate selection walking warmed prefixes → **cache anti-join O(pending)** — wavefix (live)
4. Memory gate blind to swap + flapping at 6 GB → **swap-aware + threshold-tuned** (live) → previews 16 → 77/min
5. **→ NOW: `/api/counts` 109 ms full-table walk** (next)

Held (not merged): gxharvest (sequential burst-read — 2 completion bugs + no win vs gxseq); dbanalyze full ANALYZE (regresses some query plans: idx_images_flag→SCAN, +TEMP B-TREE — pragmas-only + right-indexes is the rework).
