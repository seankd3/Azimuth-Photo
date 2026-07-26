# Hub health incident — 2026-07-15

## Verdict

The incident had three coupled causes:

1. The caption worker was in a real GPU out-of-memory failure loop. It retried a
   one-image batch immediately, consumed a core in Qwen image preprocessing and
   inference, and wrote an error-ledger row after every failure.
2. Missing originals requested by the grid were persisted one image per request.
   A burst of thumbnail requests therefore created many independent SQLite writers.
   The writer lock moved among those short transactions while other aiosqlite
   threads queued for up to 30 seconds each. WAL readers remained fast.
3. Production `main` does not understand the forwarded `hub_image_ids` payload.
   An explicit empty list therefore ran a full trash purge. It performed filesystem
   work before catalog deletion, then the batch-delete/per-image fallback could
   wait another 30 seconds for every lock attempt.

There was no single background mega-transaction continuously holding the lock in
the captured process. The failure was a write stampede plus long per-connection
waits, amplified by the caption failure loop.

## Live evidence (read-only)

Production PID `1710943` was not restarted, stopped, reloaded, or sent a POST
request. `py-spy` 0.4.2 was installed with `pipx`; attach required `sudo` because
the host has `kernel.yama.ptrace_scope=1`.

`py-spy top --pid 1710943` during a quiet interval showed `GIL: 0.00%, Active:
0.00%`, while process accounting still reported about 110% lifetime CPU. `/proc`
accounting identified TID `1714129`; `py-spy dump` named it `caption-gpu_0`.
Repeated dumps caught the active work:

```text
Thread 1714129 (active): "caption-gpu_0"
    _preprocess (.../transformers/models/qwen2_vl/image_processing_qwen2_vl.py:216)
    _preprocess_image_like_inputs (.../transformers/image_processing_utils.py:312)
    preprocess (.../transformers/image_processing_utils.py:400)
    __call__ (.../transformers/models/qwen2_5_vl/processing_qwen2_5_vl.py:90)
    _caption_cached_preview (/home/sean/Projects/azimuth-photo/web/caption_worker.py:308)
    run (.../concurrent/futures/thread.py:59)
```

Other samples caught the same thread in Qwen `get_image_features`, convolution,
and SDPA attention. The live caption status provided the failure-loop counter:

```text
session_captioned: 760
oom_backoffs: 88797
captioned rows: 12534
error rows: 56801
pending_cached_images: 16246
last_batch_size: 0
```

The database queue was also visible in a dump:

```text
Thread 1530230 (active): "Thread-403642 (_connection_worker_thread)"
    _connection_worker_thread (.../aiosqlite/core.py:63)
```

At the instant of the quiet dump, `lslocks` showed only WAL read locks, so no
persistent holder existed to name. The journal identifies the writer population:
from `06:06:14` through `06:06:23`, dozens of `worker=media_request ... marked
unavailable` writes ran together; the caption ledger failed with `database is
locked` at `06:06:22`. The same caption failure appeared repeatedly on July 14
and at `00:32`, `05:37`, `06:06`, `06:19`, `06:24`, and `06:36` on July 15.

## Changes

- Explicit `{"hub_image_ids": []}` requests return a zero-result response before
  querying SQLite or inspecting Trash.
- Empty-trash catalog deletion now uses `BEGIN IMMEDIATE`, a 100 ms busy timeout,
  100 ms bounded retry backoff, and stops the per-image fallback when the database
  remains locked.
- Concurrent media missing marks are coalesced in batches of at most 128. One
  transaction repairs collection covers and refreshes source counts once per
  source instead of once per thumbnail request.
- A persistent lock no longer turns a missing-media GET into a 500; the 410
  response remains prompt and catalog persistence is retried on a later request.
- Caption result writes use `BEGIN IMMEDIATE` with a 250 ms busy timeout and
  bounded retry.
- Three consecutive caption OOMs at the minimum batch size pause Captions with an
  actionable Background Work message. Starting Captions again resets the circuit.

The shared connection helpers already apply `PRAGMA busy_timeout` consistently to
repository sync and async connections. The affected foreground/background write
paths now add bounded retry budgets instead of relying on the former 30-second
default wait.

## Verification

Focused contention proof:

```text
.venv/bin/python -m pytest -q \
  test_trash.py::TrashTests::test_explicit_empty_hub_purge_is_immediate_with_busy_writer \
  test_trash.py::TrashTests::test_nonempty_purge_bounds_catalog_lock_wait \
  test_cache_status.py::CacheStatusTests::test_concurrent_media_missing_marks_share_one_source_count_update \
  test_cache_status.py::CacheStatusTests::test_missing_media_response_stays_prompt_when_catalog_writer_is_busy \
  test_captions.py::CaptionTests::test_caption_worker_pauses_after_repeated_minimum_batch_ooms \
  --durations=5

5 passed in 4.16s
busy-writer empty-hub test: 1.21s total test call, endpoint assertion <2s
busy-writer non-empty purge: 0.87s
busy-writer missing-media response: 0.84s
caption OOM circuit: 0.08s
```

Accepted suite (shared box exclusions requested by the lane):

```text
.venv/bin/python -m pytest -q -k "not perf_budget and not playwright"
878 passed, 2 skipped, 8 deselected, 6 warnings, 9 subtests passed in 91.58s
```

Native quick check:

```text
./scripts/azimuth-check --quick
exit 0
```
