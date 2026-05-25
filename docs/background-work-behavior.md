# Background Work Behavior Anchor

This document is the authoritative product direction for photoArchive background
work. Code, tests, and UI should be audited against it.

## User-Facing Jobs

The app has exactly three background jobs:

- **Search** builds the local embedding index used by semantic search,
  similarity, and related ranking features.
- **Previews** builds fast browsing previews and, from the same source read,
  copies original bytes into the full-size cache when storage permits.
- **People** runs local face detection/embedding work for People views and
  filters.

Full-size cache is not a separate user-facing job. It is part of Previews.

## Controls

- Background jobs are manually controlled with Start/Stop from the Background
  Work menu and matching Catalog detail panels.
- There is no global Start All or Stop All.
- Each row exposes one primary action for its current state.
- App restart resets jobs to stopped.
- Stop finishes the current item or safe point, then stops taking new work.
- Done means current catalog work is complete. If new catalog photos or stale
  work appear, the row returns to Start-needed.

## Dependencies

- Search and People depend on Previews.
- Starting Search or People starts Previews as needed.
- Search and People pipeline immediately as previews become available.
- Stopping Previews also stops dependent Search and People.
- If Search and People both need GPU-heavy work, the first-started job keeps
  priority until Done or Stopped; later GPU-heavy work queues behind it.

## Previews And Cache

- Previews should read each original source file once.
- That same read generates preview tiers and copies original bytes into the
  full-size cache when storage permits.
- Preview tiers take priority over full-size cache when storage is tight.
- If full-size cache cannot be written, previews continue and the row may show
  compact pending/storage-limited context.

## Completion Scope

- All included catalog photos count for Search, Previews, and People.
- Flags such as rejected/picked do not affect generated-work totals.
- Offline included sources still count, but Start processes what can run and
  leaves blocked work pending.
- Partial Search and People results are usable while work is incomplete.

## Search Query Cache

- Normal Search may cache the embedding vector for a typed query so repeated
  searches are faster across refreshes/restarts.
- Query embedding cache is keyed by the normalized query and active embedding
  model.
- This cache is not a separate user-facing job and must not reintroduce Deep
  Search, query queues, pinned query lists, scheduled query embedding, or a
  separate semantic-result cache.

## Setup And Failure

- Pressing Start is consent for app-local model/dependency downloads when a job
  needs them.
- Installs must be app-local or model-cache-local, not system package changes.
- Per-file failures should not clutter the compact menu.
- Real job-level failures should be rare; failed install/model-load cases may
  show Error with Retry.
- Deeper details belong in Catalog panels, not the compact Background Work menu.

## Source Policy Still Needing Design

Source deletion, moved folders, external deletion, and purge semantics are not
fully decided. Current direction:

- Prefer non-destructive defaults.
- Try to detect moved folders where feasible.
- Ask after scans before purging externally missing files.
