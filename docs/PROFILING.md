# Profiling Azimuth Photo

Use the standing 5,000-photo fixture unless a production-scale copy is needed.
Never point a profiler at the long-lived service when an isolated uvicorn can
answer the question.

## Standing benchmark

From the repository root:

```bash
./scripts/bench.py
./scripts/bench.py --check
```

Interactive browse-under-bulk (GET-only, prod-safe) lives in
`./scripts/bench_interactive.py` — budgets and load policy in
[`PERF_BUDGETS.md`](PERF_BUDGETS.md).

Runs are written to `bench-runs/<timestamp>-<sha>.json`. The command prints
deltas against both the previous run and committed `baseline.json`. Set
`AZIMUTH_BENCH_URL` to make a GET-only pass against another instance;
real-instance runs omit cold-start, cold-cache, and RSS metrics they cannot
measure honestly.

`--write-baseline` makes five fixture passes and stores each metric's upper
quartile. Each thumbnail percentile uses 50 disjoint cached or cold samples.
Together, these capture a conservative normal envelope while discarding one
concurrent-job outlier on the shared host; `--check` fails when any current
metric is more than 25% above that reference.

## py-spy against isolated uvicorn

First build or refresh the benchmark fixture:

```bash
AZIMUTH_QA_SCRATCH=/mnt/expansion/tmp/azimuth-profile \
AZIMUTH_QA_ACTIVE_IMAGE_COUNT=5003 \
web/.venv/bin/python -c 'from qa.fixture import reset_fixture; reset_fixture()'
```

Run uvicorn under py-spy. This launch method works even when Linux ptrace rules
reject attaching to an already-running sibling process:

```bash
cd web
AZIMUTH_QA_SCRATCH=/mnt/expansion/tmp/azimuth-profile \
AZIMUTH_QA_ACTIVE_IMAGE_COUNT=5003 \
AZIMUTH_HOME=/mnt/expansion/tmp/azimuth-profile/fixture-home \
AZIMUTH_THUMB_CACHE_DIR=/mnt/expansion/tmp/azimuth-profile/fixture-home/cache/previews \
AZIMUTH_SMOKE_MODE=1 AZIMUTH_MODE=standalone AZIMUTH_ACCESS=local \
py-spy top -- .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 18081
```

The equivalent stack snapshot and flamegraph commands are:

```bash
py-spy dump --pid <uvicorn-pid>
py-spy record --rate 100 --output /mnt/expansion/tmp/azimuth-flamegraphs/<name>.svg \
  -- .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 18081
```

Keep flamegraphs under `/mnt/expansion/tmp/azimuth-flamegraphs/`; they are run
artifacts, not source files.

## Profile one endpoint

Start the isolated server under `py-spy record`, wait for it to listen, then
issue only the endpoint being investigated from another terminal:

```bash
curl --fail --silent --output /dev/null \
  http://127.0.0.1:18081/api/collections/suggestions
```

For a short endpoint, repeat a cold operation in a small driver so sampling is
long enough to be representative. Collection suggestions can be isolated from
HTTP and cache state like this:

```bash
cd web
py-spy record --rate 200 \
  --output /mnt/expansion/tmp/azimuth-flamegraphs/suggestions.svg -- \
  .venv/bin/python -c "import asyncio; from features.collections import suggestions as s; p='/mnt/expansion/tmp/azimuth-profile/fixture-home/data/catalog/azimuth.db'; [(s.invalidate_cache(), asyncio.run(s.collection_suggestions(p, db_signature=str(i)))) for i in range(20)]"
```

Record the fixture size, command, wall timing, and dominant stacks beside any
optimization claim. Re-run `./scripts/bench.py` twice after the change so warm
cache behavior and run-to-run variance remain visible.
