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

## Later Goals

- Platform-ready derivatives for Instagram, X, and future destinations.
- Draft captions/titles from collection context.
- Track which photos were posted where.
- Better map/place exploration.
- Multi-device sync of app state while keeping Omarchy as the source of truth.
- A polished setup flow for other prosumer photographers with NAS/external-drive
  presets.
