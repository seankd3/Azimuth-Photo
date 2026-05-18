# Repositories

Target repository split:

- `images.py`: image lookup, active/top-image fetches, orientation/metadata writes, recent active images, and flag/status writes. Pairing-pool orchestration is still behind `web/db.py`.
- `catalog.py`: source path normalization, active-source SQL snippets, active source-id reads, source writes, scan accounting, source purge SQL, scan-folder lookup, and catalog source/summary TTL caches. `web/db.py` still provides compatibility facades and cross-repository invalidation fanout.
- `rankings.py`: ranking SQL constants, filter builders, index/source selection helpers, cached ranking/facet orchestration, rankable image-id reads, date-group facets, and map-marker payloads. `web/db.py` still provides compatibility facades and app-specific callback injection during migration.
- `ratings.py`: compare/mosaic writes, undo state, and matchup reads. Cache invalidation still belongs to the `web/db.py` facade.
- `embeddings.py`: embedding rows, embedding model rows, deep-search query cache, vector counts.
- `metadata_search.py`: metadata FTS query escaping and bounded active-image ID lookups.
- `common.py`: shared repository helpers that do not own domain behavior.
- `people.py`: People filters, membership refresh, face backlog queries, face thumbnail context, labels, merges, manual face assignment, and ignore actions. Scan-result storage, clustering, and full review assembly are still behind `web/db.py`.
- `cache_entries.py`: thumbnail/full-original cache entry ID and count lookups.
- `stats.py`: catalog count snapshots, full dashboard stats, full-stats TTL/stale-refresh caching, and AI-status aggregate counts. `web/db.py` keeps compatibility facades and cross-repository invalidation fanout during migration.

Until the split is complete, keep `web/db.py` as the stable import path for existing code and tests.
