# Feature Guide

photoArchive is organized around four main screens: Catalog, Library, Compare,
and People.

## Catalog

Catalog is the setup and control room.

- Add source folders with **Choose Folder**, **Tree Browse**, or a typed path.
- Scan and rescan folders when files change on disk.
- Remove sources from the active catalog without deleting source photo folders.
- Keep temporarily offline drives browseable from cached previews, search data,
  rankings, and People labels while source-file work waits for the drive.
- Start or stop the three Background Work jobs: Search, Previews, and People.
- Install the local semantic-search model.
- Configure People recognition and review local face model status.
- Tune thumbnail sizes, JPEG quality, RAM cache, SSD cache, cache profile, and
  idle cache warming.
- Clear generated cache files when previews should be rebuilt.

## Library

Library is for daily browsing, culling, filtering, and export.

- Sort by rating, confidence, date taken, date modified, file size, resolution,
  camera, or filename.
- Search with text. Metadata search works without AI; semantic search improves
  as local embeddings are built.
- Filter by orientation, ranked/unranked/confident status, flag, minimum stars,
  person, one or more folders, date, file type, camera, and lens.
- Switch between **Grid** and **Map** when photos have GPS metadata.
- Use the thumbnail-size slider to choose dense browsing or larger inspection.
- Open the loupe for progressive image loading, zoom, pan, filmstrip
  navigation, metadata overlay, and optional cache status.
- Flag photos as picked, unflagged, or rejected.
- Use **Select** for batch flagging and selected-image JSON/CSV export.
- Export the current ranked and filtered result set as JSON or CSV.
- Smart collections currently save a single folder scope; multi-folder live
  scopes are intentionally rejected until the smart-query vocabulary expands.

## Compare

Compare builds ranking signal from your choices.

- **Mosaic** shows a grid. Pick the best image and the app records one winner
  against the visible alternatives.
- **Swiss** shows A/B matchups chosen to improve ranking confidence.
- **Top 50** focuses comparison work on the current best images.
- Mosaic strategies control the pool: **Diverse**, **Explore**, **Compete**,
  **Top Cut**, and **Random**.
- Search and filters limit the comparison pool just like Library.
- **Shuffle** refreshes Mosaic candidates.
- Undo is available from the toolbar or the up-arrow shortcut.
- The bottom bar shows rank-signal count, pool size, coverage, and background
  work status.

## People

People recognition runs locally from app-generated cached previews.

- The People page groups results into **Most Seen**, **Named People**,
  **Needs Review**, and **Other Faces**.
- Add labels to people you recognize.
- Merge suggested duplicates when two groups are the same person.
- Ignore unwanted groups.
- Use People filters in Library and Compare to narrow browsing and ranking to a
  person.

## Search, Similarity, And AI

photoArchive uses a local embedding index for interactive semantic search.
Metadata search works without an AI model, and semantic search becomes
available as local embeddings are built.

Advanced local API surfaces also exist for similar images, duplicates, EXIF, and
collections:

```text
/api/similar/{image_id}
/api/duplicates
/api/image/{image_id}/exif
/api/collections
```

## Background Work

The bottom **Work** panel appears in Library and Compare. It summarizes the
manual jobs: Search, Previews, and People. Previews includes full-size cache
copies when storage permits.

Start jobs only when you want them to run. Nearby previews, next-image warmups,
and hot full-size cache fills stay on automatically for responsive browsing.

## Keyboard Shortcuts

- Library: arrow keys navigate, Enter opens the loupe, `P` picks, `X` rejects,
  `U` clears the flag, Tab jumps to Compare, Esc clears selection.
- Loupe: left/right moves through photos, scroll zooms, Esc closes.
