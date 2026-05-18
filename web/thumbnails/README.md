# Thumbnail Engine

`import thumbnails` now resolves to this package so existing callers and tests
keep the same import path while the engine is split apart.

`config.py` owns thumbnail default constants, extension sets, cache profiles,
quality sizing, and pure budget-allocation math. `budget.py` owns the runtime
archive-estimate adapter that samples cache metadata for those budget helpers.
`memory_store.py` owns the
in-memory thumbnail LRU operations. `disk_store.py` owns stateless disk path,
marker-safety, temp cleanup, and index helpers; `maintenance.py` wires those
helpers to the active cache root while preserving source-safe marker behavior.
`generation.py` owns source
image loading, resizing, and JPEG encoding helpers. `source_identity.py` owns source stat
caching, catalog/source signatures, missing-source probes, and thumbnail HTTP
cache header payloads. `data_providers.py` owns app-injected DB/cache
invalidation callbacks for the legacy facade. `status.py` owns pure cache and
pregeneration status payload helpers. `full_cache.py` owns full-original cache room checks,
atomic source-copy/write helpers, cached-path lookup, and full-image inflight
orchestration. `pregen_candidates.py` owns reusable
candidate-selection helpers for background cache warming. The package still
keeps most legacy behavior in `__init__.py` because callers monkeypatch module
globals in tests and worker setup. `runtime.py` owns generic
bool/time/SQLite-lock/executor helpers that remain aliased from the facade.
`pregen.py` owns reusable background-cache history, rate, cursor, and batch-size
helpers; keep moving worker internals there in behavior-neutral slices, and keep
`window`/API/cache semantics unchanged.

The default cache root must stay at `web/.thumbcache`; do not let package
relocation move it under `web/thumbnails/.thumbcache`.
