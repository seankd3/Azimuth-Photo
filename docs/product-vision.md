# Product Vision

Azimuth Photo should become the unified home for managing Sean's photo life: the
private archive, the working library, the album maker, and the publishing hub.

The north star is Lightroom Classic plus Google Photos, but local-first,
self-hosted, and built around real control. It should be powerful enough for a
large personal/prosumer archive, easy enough to use from a phone, and safe
enough that private photos stay private by default.

The product can eventually be useful to other people, but the first priority is
to make it excellent for Sean's actual workflow.

The execution order lives in [Product Roadmap](product-roadmap.md).

## What It Should Feel Like

- Open the phone app and see the whole archive without thinking about where the
  files physically live.
- Find photos by date, person, place, event, camera, filename, visual similarity,
  memory, or plain language.
- Create collections and albums as naturally as making a playlist.
- Pick, reject, rank, and curate without feeling like the app is asking for
  library-management chores.
- Share a private set with a friend as easily as Google Photos, but with clearer
  control over who can see it.
- Publish a selected set publicly to Sean's website without exporting files by
  hand or rebuilding pages manually.
- Send selected photos to Instagram, X, or other platforms from the same source
  of truth.
- Trust that originals are safe, private material is not accidentally exposed,
  and public/share states are obvious.

## Non-Negotiables

These are covenants, not backlog ideas. A change that violates one is a defect
regardless of what it improves.

- **One coherent app.** A user sees Azimuth Photo — never Python, servers,
  ports, URLs, databases, or deployment concepts.
- **One name.** Everything customer-visible — installers, windows, packages,
  documentation — is Azimuth Photo, with no prior branding.
- **Windows-first installation.** A normal Windows user can download, install,
  choose local, external, mapped-drive, or NAS folders, and reach their library
  without a terminal.
- **Local-first and offline-capable.** Once a device has its catalog, previews,
  and embeddings, browsing, search, culling, ranking, organizing, and learning
  keep working with no server and no network.
- **The server is optional.** Another machine may add storage, backup, bulk
  compute, sync, sharing, or publishing — it never becomes a prerequisite for
  day-to-day work.
- **Nothing leaves the device by default.** Originals, previews, embeddings,
  faces, captions, ranking history, and catalog facts stay local unless the
  user enables a specific capability with a specific destination.
- **Taste is durable user data.** Direct choices, undos, curation, and edits
  are preserved locally and portably. Elo scores, models, indexes, and caches
  are rebuildable projections.
- **Interactions feel instant.** No click spinner in culling or ranking; a warm
  device advances immediately while expensive learning happens quietly in the
  background.
- **Originals and the catalog are sacred.** No cleanup, migration, free-space,
  scan, restore, or sync path may risk the only verified copy of an original or
  the only durable record of the user's work.

## Product Principles

### The Archive Is The Source Of Truth

Azimuth Photo should become the trusted map of the whole photo collection. The
actual files can live on Omarchy, external drives, cache storage, or future NAS
storage, but the user experience should feel like one continuous library.

### Private By Default

Everything starts private. Sharing and publishing should be deliberate,
previewable, reversible where possible, and clearly labeled.

### Curation Before Administration

The app should help Sean make taste decisions, build sets, and tell stories. It
should not feel like database maintenance, folder management, or cloud sync
plumbing.

### Phone-First Access, Desktop-Grade Power

The phone should be a first-class way to browse, review, share, and import. The
desktop/web app can carry heavier workflows such as catalog setup, deep culling,
metadata review, background jobs, exports, and site publishing.

### Sharing Is A Product Surface

Sharing should not be an export afterthought. Private links, friend albums,
website publishing, and platform posting should all be part of the same
collection workflow.

### Safety Must Be Visible

The app should make it easy to understand whether something is private, shared
with specific people, publicly published, or staged for posting.

## Core Workflows

### 1. Browse The Whole Archive Anywhere

Sean should be able to open the Android app and browse the archive on Omarchy as
if it were a personal Google Photos replacement.

Goals:

- Fast mobile grid and loupe views.
- Search from the phone.
- People, date, place, camera, and album filters.
- Smooth behavior when source drives are offline but previews are cached.
- Automatic reconnect without making server state visible unless action is
  needed.

### 2. Import From Phone And Laptop

The app should make it easy to bring new photos into the archive without losing
track of originals.

Goals:

- Phone import for selected images.
- Laptop/import-station workflow for camera cards and folders.
- Clear post-import review: what came in, what was skipped, where it landed.
- Preserve source safety: imports copy into the archive; originals are not
  silently deleted.

### 3. Curate Albums And Collections

Albums should be lightweight, fast, and central. A collection can be a private
memory set, a public website gallery, a social post candidate pool, a client
delivery, a travel album, or a ranked creative selection.

Goals:

- Create a collection from search results, people, dates, imports, or manual
  selection.
- Add/remove photos quickly from phone or desktop.
- Use ranking, flags, and AI suggestions to help narrow a set.
- Support collection states: draft, private shared, public published, archived.
- Keep collection history and destination state visible.

### 4. Private Sharing With Friends

Replace the current Google Photos private-sharing use case with self-hosted,
private links and clear access controls.

Goals:

- Share selected collections through private links.
- Optional expiry, password, invite list, or tokenized access.
- Friend-friendly viewing experience that does not require technical setup.
- Ability to revoke or rotate a link.
- Clear indication in Sean's app that a photo or collection is shared.

### 5. Publish To Sean's Website

Publishing should make curated public galleries feel native to the archive.

Goals:

- Mark a collection as public.
- Preview how it will appear on the website.
- Publish/update/unpublish without manual file export.
- Generate responsive web images from the archive cache.
- Keep website publication status visible inside Azimuth Photo.

### 6. Share To Social Platforms

The app should help prepare and send selected photos to Instagram, X, and other
platforms without fragmenting the archive.

Goals:

- Export platform-ready images and captions from a collection.
- Track what has been posted where.
- Eventually support direct posting where platform APIs make sense.
- Keep originals untouched and publishing derivatives reproducible.

### 7. Taste And AI Assistance

AI should help with search, grouping, curation, and storytelling, but it should
not override Sean's taste.

Goals:

- Semantic search over the whole archive.
- Similar-image and duplicate workflows.
- Suggested album candidates from events, people, places, and visual coherence.
- Picks from an import batch or collection based on rank/taste signals.
- Caption, title, and gallery-description drafts.

## Privacy And Sharing Model

Each photo and collection should have a clear exposure state:

- Private: visible only inside Sean's archive.
- Shared privately: visible through a controlled private link or invite.
- Public: published to a website/gallery.
- Exported/post-ready: prepared for another platform but not necessarily public
  through Azimuth Photo.

The UI should avoid ambiguous states. A user should never wonder whether a
private photo has been accidentally published.

## Near-Term Goals

### Mobile Archive Foundation

- Make the Android app feel reliable and invisible: browse, search, reconnect,
  preview, and import.
- Keep the no-phone Android release gate as the default quality check.
- Improve first-run and connection behavior until it feels automatic.

### Collections Polish And Mobile Parity

Shipped on desktop: collection creation, editing, membership, smart
collections, suggestions, status icons, and counts. Next work is polish and
mobile parity.

### Private Sharing Polish And Exposure Clarity

Shipped on desktop: private share links, friend-facing galleries, password
protection, revoke/rotate, analytics, favorites/proofing, and Shared triage.
Next work is clearer exposure state across mobile and collection surfaces.

### Website Publishing Polish

Shipped on desktop: collection publishing to a configured folder, responsive
gallery assets, manifest generation, optional hook command, unpublish, and
Shared status. Next work is polish around preview, retries, and site feedback.

## Later Goals

- Social export/posting workflow for Instagram and X.
- Smarter AI album suggestions.
- Better map/place exploration.
- Multi-device sync of app state while keeping Omarchy as the source of truth.
- A polished setup flow for other prosumer photographers with NAS/external-drive
  presets.

## Non-Goals For Now

- Do not chase a generic cloud photo startup before the product is excellent for
  Sean.
- Do not make sharing public by default.
- Do not build complicated enterprise permissions.
- Do not prioritize technical admin panels over album, sharing, and publishing
  workflows.
- Do not require users to understand server/runtime details during normal use.

## Success Criteria

Azimuth Photo is working when Sean can:

- Open the phone app and browse his whole archive.
- Create an album from a trip, event, person, or import batch in minutes.
- Share that album privately with a friend without using Google Photos.
- Publish a public gallery to his website from the same collection.
- Know, at a glance, what is private, shared, and public.
- Trust that originals and private photos are safe.
