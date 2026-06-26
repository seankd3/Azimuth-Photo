# Product Roadmap

This roadmap turns the product vision into shippable slices. The bias is toward
workflows Sean can actually use, not abstract platform work.

## Phase 1: Collections Become The Center

Goal: make albums/collections a durable first-class object.

- Add persistent collections with a clear exposure state.
- Add/remove photos from Library, imports, search results, and eventually the
  Android app.
- Show collection counts, cover image, and recent update time.
- Filter Library by a collection.
- Make import batches easy to save as collections.

User outcome: Sean can create an album from a trip, person, event, or import
batch and keep working on it over time.

## Phase 2: Private Sharing

Goal: replace the Google Photos private-share use case.

- Create private share links for a collection.
- Build a friend-facing read-only collection page.
- Support revoke/rotate for links.
- Label shared collections clearly in the main app.
- Keep everything private unless explicitly shared.

User outcome: Sean can send a private album link to a friend without uploading
the whole archive to a cloud photo service.

## Phase 3: Website Publishing

Goal: make public galleries flow from the same collection object.

- Mark a collection as public/published.
- Preview the website gallery.
- Generate responsive public images from safe cache derivatives.
- Publish, update, and unpublish a gallery from photoArchive.
- Show published status and destination URL.

User outcome: Sean can publish a curated photo story to his website without
manual export folders.

## Phase 4: Mobile Collection Workflow

Goal: make the Android app useful for more than browsing.

- View collections on the phone.
- Add/remove photos from a collection while browsing.
- Create a new collection from selected phone photos.
- Share a private collection link from Android.
- Import phone photos directly into a collection.

User outcome: Sean can curate and share from the couch or on the road.

## Phase 5: Platform Sharing

Goal: prepare photos for Instagram, X, and future destinations without losing
the archive source of truth.

- Export platform-ready derivatives from a collection.
- Draft captions/titles from collection context.
- Track which photos were posted where.
- Add direct posting only where APIs make it reliable and safe.

User outcome: Sean can use photoArchive as the source of truth for public posts.

## Phase 6: Taste And AI Assistance

Goal: help Sean find and shape better albums faster.

- Suggest collections from imports, dates, people, places, and visual clusters.
- Suggest keepers within a collection from ranking/taste signals.
- Draft album names, descriptions, and captions.
- Surface near-duplicates and weaker alternates inside album curation.

User outcome: the app helps curate without taking control away from Sean.

## Immediate Build Order

1. Persistent collection APIs.
2. Library save-to-collection UI.
3. Collection detail page.
4. Private share links.
5. Friend-facing shared collection page.
6. Website publishing adapter.
7. Android collection browsing and add-to-collection.
