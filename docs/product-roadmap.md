# Product Roadmap

This roadmap turns the product vision into shippable slices. The bias is toward
workflows Sean can actually use, not abstract platform work.

## Shipped Foundation

### Collections

- Persistent collection APIs and left-panel collection UI.
- Add/remove photos, rename, delete, and collection detail views.
- Smart collections saved from validated live scopes.
- Collection suggestions from dates, people, imports, and visual/library
  structure.

User outcome: Sean can create an album from a trip, person, event, import
batch, search, or manual selection and keep working on it over time.

### Private Sharing

- Private collection links at `/s/{token}`.
- Friend-facing read-only gallery with favorites/proofing.
- Password protection, revoke, rotate, expiry, analytics, and owner-side pick
  review.
- Shared triage view for private links and website galleries.

User outcome: Sean can send a private album link without uploading the whole
archive to a cloud photo service.

### Website Publishing

- Publish, update, and unpublish collection galleries.
- Static gallery bundles generated from safe preview derivatives.
- `publish_dir`, `publish_hook`, and `publish_site_base_url` settings.
- Manifest generation and Shared status for local/live/hook-failed states.

User outcome: Sean can publish a curated photo story to his website without
manual export folders.

### Library Power Tools

- Grid, Events, People, Map, Stacks, Trash, Shared, Import, Loupe, Refine.
- Tags, captions, smart scopes, multi-folder browsing, sort direction, import
  history, ZIP export, and semantic Refine pairing.

User outcome: the archive is now a working culling, organizing, search,
sharing, and publishing tool rather than just a browser.

## Current Build Priorities

### Mobile Collection Workflow

Goal: make the phone useful for curation, sharing, and import.

- Improve collection browsing and add/remove flows on mobile.
- Make private share creation and client-pick review feel native on the phone.
- Keep mobile Refine, search, and People aligned with desktop scopes.
- Import phone photos directly into useful collections or import batches.

User outcome: Sean can curate and share from the couch or on the road.

### Publishing Polish

Goal: make public galleries feel reliable enough to trust.

- Improve preview/open flows before publish.
- Make hook output and retry states clearer.
- Tighten manifest/site integration documentation.
- Add small guardrails around incomplete preview caches.

User outcome: Sean can publish and update website galleries without babysitting
the deployment path.

### Shared And Exposure Clarity

Goal: make privacy state obvious at a glance.

- Surface private-link and published status consistently on collection cards,
  collection detail, Shared, and mobile.
- Make expired, revoked, local-only, and hook-failed states visually distinct.
- Keep client favorites easy to review and apply.

User outcome: Sean always knows what is private, privately shared, public, or
only staged locally.

### Taste And AI Assistance

Goal: help Sean find and shape better albums faster.

- Improve suggested collections from imports, dates, people, places, and visual
  clusters.
- Suggest keepers within a collection from ranking/taste signals.
- Use captions and tags to improve search, grouping, and story drafts.
- Surface near-duplicates and weaker alternates inside album curation.

User outcome: the app helps curate without taking control away from Sean.

## Next Slices

### Instant, Trustworthy Ranking

Goal: make every ranking click durable, instant, and exactly undoable.

- Record each choice as one durable action that is acknowledged immediately and
  survives restart, with expensive learning deferred to the background.
- Treat a mosaic pick as one action over its full candidate set, not dozens of
  independent comparisons.
- Make Random mean random: draw from the complete eligible scope with filters
  and exclusions applied, not from a top-ranked window.
- Keep direct choices and inferred scores visibly separate, and extend learning
  across the whole catalog — including photos with no direct signal yet —
  with honest uncertainty.

User outcome: ranking feels instant and honest, a wrong click never costs real
work, and taste signals reach the entire archive instead of a refined minority.

### Dual As An Atomic Rhythm

Goal: make the two-photo Dual comparison a decisive rhythm.

- After every successful choice, replace both photos with two genuinely new
  candidates — no stale survivor.
- Mouse and keyboard behave identically; rapid input never double-fires or
  skips a round.
- Failure and undo restore the exact previous pair and focus.

User outcome: Dual becomes a fast, decisive flow instead of one new photo at a
time.

### A Professional Format And Export Covenant

Goal: photographers know exactly which files Azimuth Photo handles faithfully.

- Declare the supported RAW, raster, and video formats, and fail honestly on
  the rest.
- Round-trip orientation, timestamps, ratings, keywords, EXIF/XMP metadata, and
  color profiles predictably.
- Make exports match the chosen recipe exactly: dimensions, color space,
  quality, metadata, and filenames.
- Never modify an original file.

User outcome: pros can trust the archive with real client work because format,
color, and metadata behavior is documented and proven.

### The Installed Windows Day

Goal: a photographer's entire working day runs on one installed Windows
machine with no server anywhere.

- Prove the full session on a clean install: browse, search, cull, refine,
  organize, edit, export, close, and restart — offline, against local,
  external, and NAS folders.
- Direct choices and edits survive restart, source disconnects and reconnects,
  and app upgrades.
- Every failure state offers a clear retry, locate, or keep-offline action
  instead of a dead end.

User outcome: Azimuth Photo is a real Windows app, not a hosted service that
has to be kept alive.

### One-Action Catalog Restore

Goal: recovering from a corrupt catalog needs no terminal.

- Keep restore preparation as safe as it is today: validate a snapshot and
  stage it beside the live catalog without overwriting anything.
- Add the missing apply step: one explicit action stops the engine, preserves
  the failed catalog, promotes the staged one, restarts, verifies health, and
  offers rollback.

User outcome: a scary catalog failure becomes a calm, guided recovery instead
of a manual file-move procedure.

## Later Goals

- Platform-ready derivatives for Instagram, X, and future destinations.
- Draft captions/titles from collection context.
- Track which photos were posted where.
- Better map/place exploration.
- Multi-device sync of app state while keeping Omarchy as the source of truth.
- A polished setup flow for other prosumer photographers with NAS/external-drive
  presets.
