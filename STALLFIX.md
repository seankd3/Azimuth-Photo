# STALLFIX — pregen decode-budget death spiral

**Branch:** `stallfix` @ `47c5afcc5` (working prod base)  
**Status:** fixed in this worktree — **left uncommitted** per lane instructions  
**Prod:** not touched, no service restarts

## Verdict

Two coupled bugs turned slow DNG demosaic into a total pregen stall:

1. **Budget leak / corrupt accounting** on cancel+watchdog reset
2. **Watchdog false-positive** on slow-but-working (or between-batch) periods

Both are fixed. Sustained DNG demosaic run shows **physical preview files keep growing**, watchdog stays quiet, no `leaked=` budget resets.

## Leak point

| What | Where |
|------|--------|
| Holds acquired per wave | `web/thumbnails/pregen_worker.py` — `run_pregen_bulk_batch` acquire loop (~line 251) |
| Previous failure mode | `finally` released via `await release()`; watchdog `reset_and_notify()` then cancel allowed a **stale finally** to subtract from a *new* batch’s holds (under-count → chaos), and cancel paths were not epoch-safe |
| Fix | `web/thumbnails/decode_budget.py`: epoch-stamped holds, `release_nowait()` (no await in cancelled `finally`), stale release after `reset()` is a no-op; worker finally uses `(epoch, weight)` + `release_nowait` |

Exact balance now holds across: normal completion, exception, cancellation, and watchdog reset.

## Watchdog false-positive fix

| Before | After |
|--------|--------|
| Anchor = `last_generated_at` only (updates when an image *successfully writes*) | Anchor = max(`last_generated_at`, `last_progress_at`, `started_at`) |
| Slow demosaic mid-batch looked stalled | `note_progress()` on decode **start** and every item finish; failures bump `last_progress_at` too |
| Fired even with **no live batch** (scanning / between waves) | Trips only when `active_batch` is live and unfinished |
| Env/default stall could be shorter than one demosaic wave | Production/default path floors stall limit to `PREGEN_WAVE_TIMEOUT_SECONDS` (120s) |

Genuine hangs still cancel: live batch + no progress heartbeat for the (floored) limit → `reset_and_notify` + cancel, now leak-free.

## Proof

### Unit

```bash
cd web && .venv/bin/python -m pytest -x -q \
  test_decode_budget.py test_interactive_isolation.py \
  -k 'cancel_mid_batch or stale_release or stall_watchdog'
# 4 passed — cancel returns budget to 0; progressing batch not cancelled
```

```bash
cd web && .venv/bin/python -m pytest -x -q test_thumbnails.py test_memory_pressure.py
# 81 passed
```

### Sustained DNG demosaic (persistence, not gen counter)

```bash
./scripts/bench_stallfix_dng.py --seconds 150 --stall-seconds 45 --port 8299
```

Corpus: 57× ~76MB R5 DNGs (full demosaic path). Tight env stall (45s) floors to 120s in production path.

| Metric | Before (mid-fix run) | After (full fix) |
|--------|----------------------|------------------|
| Physical files gained | 161 in ~180s | **86 in 151s** (steady growth) |
| Files/min (overall) | 53.3 then watchdog trip | **34.1** (peak ~60s window **96.4**) |
| Stall watchdog log lines | **1** (`leaked=402849792`) | **0** |
| Status stall hits | 1 | **0** |
| Decode budget `leaked=` lines | 1 | **0** |

Scratch evidence: `/tmp/pa-stallfix-dng-rktyfyxe/stallfix-report.json`  
Prior false-positive evidence: `/tmp/pa-stallfix-dng-di4ghgoo/` (watchdog at 49s / 45s limit while files were already warm/flat).

**Prod symptom this replaces:** cache_entries + gen counter flat for minutes, MainThread idle, no worker threads — death spiral from repeated false cancel → budget exhaustion → `acquire()` blocks forever.

## Risks

- Stall recovery is slightly less hair-trigger (120s floor + requires live batch). A wedged decode still dies at wave timeout (120s) and/or stall floor — not left forever.
- `last_progress_at` is a new status field; clients that ignore unknown keys are fine.
- Epoch no-op releases mean a cancelled batch must not assume it still owns budget after a watchdog reset (it doesn’t — by design).

## Intended commit message (when Sean asks to commit)

```
fix(pregen): stop stall-watchdog death spiral — leak-free budget + progress heartbeat

Cancel/reset no longer corrupts bulk_decode_budget; watchdog only cancels
truly stuck live batches, not slow demosaic or between-wave scanning.
```
