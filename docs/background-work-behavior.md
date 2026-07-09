# Background Work Behavior Anchor

This document is the product anchor for photoArchive background work. Code,
tests, and UI should be audited against it.

## User-Facing Rows

The System drawer currently exposes five background rows:

- **AI embeddings** builds the local embedding index used by semantic search,
  similarity, Refine pairing, and ranking-adjacent features.
- **Cache pregeneration** builds fast browsing preview tiers and warms cached
  media used by desktop, mobile, People, sharing, publishing, and captions.
- **People scan** runs local face detection and embedding work for People views
  and filters.
- **Captions** generates local VLM captions and tags for caption search and the
  right-side caption panel.
- **Metadata** keeps searchable file details current after scans and setting
  changes.

Full-size cache is not a separate user-facing row. It belongs with cache and
media generation.

## Controls

- The compact activity widget opens the System drawer.
- Rows use the current UI language: primarily **Pause** and **Resume**, plus
  enable/disable settings where a worker depends on a feature toggle.
- There is no global Start All or Stop All.
- Each row exposes one primary action for its current state.
- Pause finishes the current item or safe point, then stops taking new work.
- Done means current catalog work is complete. If new catalog photos or stale
  work appear, the row returns to a pending/runnable state.

## Dependencies

- AI embeddings and People depend on generated previews.
- Resuming AI embeddings or People may also wake cache pregeneration.
- Pausing cache pregeneration can pause dependent AI embeddings and People.
- Captions read generated preview/media derivatives and run locally.
- If multiple GPU-heavy workers need the GPU, work is serialized so only one
  heavy model path runs at a time.

## Previews And Cache

- Cache pregeneration should avoid repeated source reads where possible.
- Preview tiers take priority over local full-size media when storage is tight.
- If local full-size media cannot be written, previews continue and the row may
  show compact pending/storage-limited context.
- Nearby thumbnails, next-image warmups, and recently viewed media can still
  warm automatically while browsing.

## Completion Scope

- Included catalog photos count for AI embeddings, cache pregeneration, People,
  captions, and metadata when the corresponding feature is enabled.
- Flags such as rejected/picked do not remove photos from generated-work totals.
- Offline included sources still count, but workers process what is available
  and leave blocked work pending.
- Partial AI, People, caption, and metadata results are usable while work is
  incomplete.

## Search Query Cache

- Normal search may cache the embedding vector for a typed query so repeated
  searches are faster across refreshes/restarts.
- Query embedding cache is keyed by the normalized query and active embedding
  model.
- This cache is not a separate user-facing job and must not reintroduce query
  queues, pinned query lists, scheduled query embedding, or a separate semantic
  result cache.
- The omnibox **Deep** chip is a live query option. It must stay separate from
  the old scheduled/persisted query-cache product.

## Setup And Failure

- Pressing a row's primary action is consent for app-local model/dependency
  downloads when that row needs them.
- Installs must be app-local or model-cache-local, not system package changes.
- Per-file failures should not clutter the compact activity menu.
- Real job-level failures should be rare; failed install/model-load cases may
  show Error with Retry.
- Deeper details belong in System drawer sections, not modal blockers.

## Source Policy Still Needing Design

Source removal, moved folders, external deletion, and purge semantics are not
fully settled. Current direction:

- Prefer non-destructive defaults.
- Try to detect moved folders where feasible.
- Ask after scans before purging externally missing files.
- Keep Trash explicit and reversible until the user empties it.
