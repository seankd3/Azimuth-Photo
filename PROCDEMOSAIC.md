# PROCDEMOSAIC — RAW demosaic via process pool (GIL bypass path)

**Lane:** `procdemosaic` (off develop)  
**Date:** 2026-07-20  
**Status:** implemented in this worktree — **left uncommitted** (prod untouched, no service restart)

Suggested commit message:

```
Bench: RAW demosaic+encode 25 → 72 DNGs/min via process pool (path IPC)

Route GIL-sensitive rawpy demosaic through a persistent ProcessPoolExecutor;
embedded JPEG thumbs stay on the thread pool. HDD single-flight read unchanged.
```

## Problem

Preview backfill for small-embedded DNGs (R5 and similar) spends most of its
time in `rawpy.postprocess`. Prior harvest-slot / pregen-pipe work parallelized
*threads*, but if libraw holds the GIL (or OpenMP oversubscribe fights itself),
thread pools cannot multiply demosaic throughput — and a wedged demosaic can
take down a worker thread.

Contrast: embedded-preview JPEG extract + plain JPEG decode release the GIL and
already parallelize on the existing thumb/prefetch thread pools. Those stay put.

## Architecture

```
HDD governor (main, single-flight)
  → read original bytes once
  → release slot
  → thread pool task:
        embed path  → extract_thumb + resize + JPEG   (same thread)
        demosaic    → ProcessPoolExecutor worker:
                        demosaic → resize md/lg → JPEG
                      ← returns only JPEG bytes (KB–100KB)
  → main caches JPEGs to memory/disk
```

| Path | Executor | Why |
|------|----------|-----|
| Embedded RAW preview (sm when embed covers) | Thread pool | PIL/JPEG releases GIL |
| JPEG / non-RAW | Thread pool | same |
| Real demosaic (md/lg for small-embed DNG, lossy DNG, no usable embed) | **Process pool** | isolate libraw; multiply across cores without GIL coupling |

### Modules

| File | Role |
|------|------|
| `web/raw_thumb_ops.py` | Spawn-safe demosaic + resize + JPEG (no `thumbnails.__init__` side effects) |
| `web/thumbnails/demosaic_pool.py` | Persistent `ProcessPoolExecutor` (spawn), bulk gate, crash recreate |
| `web/thumbnails/generation.py` | Routes uncovered tiers through the pool; embed stays in-process |

### IPC choice: **path** (default)

Env: `PHOTOARCHIVE_DEMOSAIC_IPC=path|bytes`

| Mode | Bench N=12 R5 (demosaic+encode md/lg, 6 workers) | Notes |
|------|--------------------------------------------------|-------|
| **path** | **72.1 DNGs/min** | Parent already read under HDD slot → page cache warm; no 45MB pickle |
| bytes | 56.6 DNGs/min (0.78× vs path) | Doubles source bytes over the pipe |

Default **path**. Bytes remains available for hosts where page cache is unreliable.

### Pool sizing + memory

- `PHOTOARCHIVE_DEMOSAIC_PROCESSES` default `min(ncores-1, 6)` (this host: 6). `0` = in-process fallback (tests).
- Interactive reserves 1 bulk slot (`workers-1` for backfill) so loupe/grid misses are not starved.
- Decode-byte budget still caps how many harvests the pregen pump submits; process RSS is additional.
- Live sample under 6-wide load: **7 child procs**, peak child RSS **~1.1 GiB** combined (~150–220 MB per busy half_size worker). Under the 6 GiB soft memory gate with headroom for the app.

### Crash handling

`BrokenProcessPool` → recreate the persistent pool, surface the failure to the
caller (image marked failed / retry), continue. `max_tasks_per_child=64` bounds
leakage. Spawn context so workers import `raw_thumb_ops` cleanly without the
thumbnail facade's thread pools.

### Interactive latency

On-demand (`generate_missing_thumbnails`) submits with `interactive=True` and
skips the bulk semaphore. Bulk backfill cannot occupy every worker.

## Benchmarks (this worktree, 2026-07-20)

Corpus: 12× Canon R5 DNGs under `/mnt/expansion/Photos/RAWS/2022/2022-05-09/`
(~45–50 MB, warm page cache). Script: `scripts/bench_procdemosaic.py`.

### Demosaic + resize + JPEG (md+lg) — product path

| Mode | Wall | Rate | vs serial |
|------|------|------|-----------|
| Serial in-process | 28.80s | **25.0 DNGs/min** | 1.00× |
| Thread pool ×6 | 11.85s | **60.7 DNGs/min** | 2.43× |
| Process pool ×6, **path IPC** | 9.99s | **72.1 DNGs/min** | **2.88×** (1.19× vs threads) |
| Process pool ×6, bytes IPC | 12.73s | 56.6 DNGs/min | 2.26× |

Parity: process-pool md+lg JPEG bytes **byte-identical** to in-process (`test_demosaic_pool.py` + bench spot-check).

### Pure `rawpy.postprocess` (half_size) — GIL note

Lane brief cited serial 77.5s / thread 151.2s = **0.51×** on a prior isolated
run. On this host’s current rawpy build, pure postprocess **does** parallelize
on threads (~4× with 6 workers; similar with `OMP_NUM_THREADS=1`). Process pool
is still the right product path: crash isolation, stable IPC, and a measured
win on the full demosaic+encode pipeline (72 vs 61 DNGs/min). If a future
libraw build re-holds the GIL, the process pool is already in place.

Script: `scripts/bench_rawpy_gil.py`.

### Process proof (not one thread)

`scripts/sample_demosaic_procs.py` during 6-wide demosaic:

- `peak_child_procs=7` (6 workers + tracker)
- Per-worker RSS climbing to ~220 MB while demosaicing
- Combined child RSS peak ~1.1 GiB

### Live prod

**Not restarted / not deployed** (lane constraint). Live-like rate for the
demosaic-heavy folder is the isolated R5 bench above; expect the preview
backfill’s demosaic-needed subset to move from ~single-file cadence toward
the process-pool rate once this lands behind the existing HDD read + pregen
pump (unchanged).

## Tests

```text
cd web && python -m pytest -x -q test_thumbnails.py test_demosaic_pool.py test_hddgov.py
79 passed
```

`conftest.py` sets `PHOTOARCHIVE_DEMOSAIC_PROCESSES=0` by default so unit tests
stay in-process; pool tests enable it explicitly.

## Risks

- **RSS**: 6× ~200–500 MB workers + app must stay under the memory soft gate; lower `PHOTOARCHIVE_DEMOSAIC_PROCESSES` on tighter boxes.
- **Page-cache miss on path IPC**: rare second read if cache was evicted; switch to `PHOTOARCHIVE_DEMOSAIC_IPC=bytes` if measured.
- **Bulk reserve**: one idle worker during pure bulk — intentional for interactive latency.
- **Lossy DNG** still spills to a temp file inside the worker (rare).
- Spawn from interactive `python -` / stdin REPLs breaks worker bootstrap — always run via a real script / uvicorn (production path is fine).
