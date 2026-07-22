# Azimuth Benchmarks

Reproducible hot-path timings tracked over time (newest first). Run `scripts/perfbench.py --label "..." --md`. p50/p95 in ms; preview = files/min (background, load-dependent). Interactive latency is the comparable-over-time signal.

| commit | label | preview/min | rankings | counts | folders | warm thumb |
|--------|-------|------------:|---------:|-------:|--------:|-----------:|
| `c00369af8` | harvestslot: parallel decode | 63 | 1.7/1.9 | 9.0/15.6 | 35.2/56.8 | 4.9/8.7 |
| `c00369af8` | clean quiet-box post-dngthumbs | 0 | 1.2/1.4 | 5.6/6.0 | 14.9/17.0 | 5.6/6.9 |
| `084c26e0d` | counts+pathorder+dngthumbs (clean re-measure) | ~50* | 1.2/1.7 | 5.8/7.9 | 17.0/47.0 | 6.0/12.5 |
| `6615e2dfe` | overnight quality wave — baseline | 77 | 1.2/1.7 | 108.8/111.2 | 17.4/24.6 | 4.8/5.1 |

## Current bottleneck (bottleneck-hunting loop)

**`/api/counts` = 109 ms p50** — the slowest interactive endpoint by far (rankings is 1.2 ms). Root cause (audit-db): unfiltered counts walks the full images table (~88 ms) instead of reading maintained per-source counters. Next optimization target.

## Milestone wins — 2026-07-19/20 overnight (bottleneck-hunt chain)

Measured, shipped, benchmark-logged. Each fix targeted the *then-current* limiting factor, re-measured, moved to the next.

| # | Bottleneck (measured) | Fix | Before | After |
|--:|----------------------|-----|-------:|------:|
| 1 | 2 bulk streams thrash 1 HDD | sequence previews-then-vault (gxseq) | 2.5 MB/s | 80 MB/s |
| 2 | cached thumb pays HDD lstat | cache-first serving (gxlat) | 356 ms | 4.8 ms |
| 3 | memory gate blind to swap, flapping | swap-aware gate + threshold tune | previews paused | previews run |
| 4 | `/api/counts` full-table walk | maintained counter + flag index | 108.8 ms | 5.8 ms |
| 5 | DNG thumbs run Develop+4.3s gzip | embedded preview + one fast demosaic (dngthumbs) | 20.6 s/DNG | 1.7 s/DNG |
| 6 | taste sort scores whole library/request | vectorize + order cache + split invalidation | 7-13 s | 35 ms* |
| 7 | HDD slot held through CPU demosaic | slot=read-only, parallel decode, pool 2→8 (harvestslot) | 5 files/min | **49 files/min** |
| — | laptop boot JS | lazy-load Develop/GL (bootsplit) | 1.33 MB | 0.90 MB |

*taste common/scroll case; a limit-change edge (~27 s cold rebuild) remains as follow-up.

**Preview backfill end-to-end: ~2-5/min → 49 files/min (~10-20×).** Interactive all 1-6 ms.
Held/follow-ups: gxharvest (burst-read, 2 bugs); LR .lrprev integration (tool proven); faces on GPU (1.3 img/s, GPU ~82%% idle); taste limit-edge; embedding batch=1.

