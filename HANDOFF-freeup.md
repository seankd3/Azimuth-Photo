# Free up space handoff

## Catalog representation

No schema change is needed. Satellite mirror rows already use:

- `images.hub_image_id` for the hub's stable media identity.
- `images.hub_remote = 1` to make grid, loupe, Develop, export, and publishing use the existing authenticated hub readthrough paths.

A field import that later reaches the hub initially remains `hub_remote = 0`. After Free up space receives a fresh hub confirmation, verifies the local sync state is still clean, re-hashes the local original, and unlinks it, the job changes that row to `hub_remote = 1` while preserving its local catalog ID, source, filepath, metadata, and content hash.

## Safety and interruption behavior

The local JSONL deletion log records a durable `delete_ready` event before unlink and a `deleted` event after the catalog transition. If the process stops after unlink but before the database update, the next Free up space request repairs the row from that durable record. Cancellation is checked between files, so a new job naturally resumes from the remaining local rows.

## Deviations

None. The requested API, authenticated hub confirmation, System drawer control, per-file cancellation/resume behavior, and existing readthrough representation are used without a migration.
