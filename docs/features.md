# Feature Guide

Azimuth Photo opens into one desktop shell at `/` or `/d`. The current scope is
shared across the grid, lenses, overlays, panels, exports, and background work.

## Desktop Shell

- The top lens switcher shows **Grid**, **Events**, **People**, and **Map**.
- The left panel holds Collections, Library shortcuts, Folders, Sources, and
  Tools: **Export view**, **Shared**, **Stacks**, and **Import**.
- The context bar shows scope breadcrumbs, count, Sorted %, filters, **Refine**,
  **Best of**, sort, sort direction, auto-advance, and thumbnail size.
- The right panel shows histogram, ranking, metadata, caption, and selection
  details for the focused photo.
- The System drawer owns source setup, background work, publishing, import,
  cache, AI, People, caption, metadata, remote-access, and preference settings.

## Browsing And Lenses

- **Grid** is the daily browsing and culling surface.
- **Events** groups the current scope by capture-time gaps.
- **People** shows **Named people**, **Unnamed**, and **Review merges**.
- **Map** shows photos with GPS metadata.
- Filters compose across text, person, folder, date, file type, camera, lens,
  tag, orientation, flag, minimum rating, and ranked/unranked states.
- Folder scope can include multiple folders at once; the API receives repeated
  `folder` query parameters.
- The sort menu exposes **Rating**, **Date**, **Camera**, **Filename**, and
  **Size**. The adjacent sort-direction button toggles ascending/descending.
- **Best of** narrows the current non-collection scope to the top slice by
  rating.

## Search, Captions, Tags, And Similarity

- Metadata search works without AI. Semantic search and similar-photo results
  improve as local embeddings are built.
- The omnibox supports facet completions such as `camera:`, `lens:`, `folder:`,
  `tag:`, and natural typed searches.
- The **Deep** chip appears for a typed search and asks the app to use the
  active embedding model when that differs from the fast search model. It is a
  live query mode, not a background queue or separate cache product.
- Captions and tags are generated locally when the Captions worker is enabled.
  Captions appear in the right panel, `/api/image/{id}/caption`, and the
  caption full-text index; tags appear in `/api/tags` and `tag:` scopes.
- Similar-photo workflows use `/api/similar/{image_id}` and can scope the grid
  to the top 100, 250, or 500 similar images.
- Advanced local APIs also include `/api/duplicates` and
  `/api/image/{image_id}/exif`.

## Collections

- Collections live in the left panel and use `/api/user-collections`.
- Create regular collections manually, add/remove selected photos, rename, or
  delete collections.
- Smart collections save a live query from the current scope. Supported smart
  keys include `q`, `people`, `folder`, `camera`, `lens`, `tag`, `flag`,
  `date_taken`, `file_type`, `orientation`, `compared`, `min_stars`, and
  `sort`.
- Live browsing can scope multiple folders. Smart collection save keeps one
  folder value when exactly one folder is active; multi-folder live scopes are
  browsable but not persisted as multi-folder smart rules.
- Collection suggestions are exposed at `/api/collections/suggestions`.

## Refine

Refine is the ranking overlay for the current scope.

- **Mosaic** shows a grid. Pick the best photo and the app records one winner
  against the visible alternatives.
- **Duel** shows A/B choices.
- Strategies are **Diverse**, **Explore**, **Compete**, and **Random**.
- Search, filters, people, folder, tag, collection, import, and similar scopes
  limit the Refine pool just like the grid.
- Semantic pairing can compare like-with-like when enabled.
- **Shuffle** refreshes candidates. **Undo** is available from the toolbar or
  `Ctrl+Z`.
- The overlay shows pick count, pace, Sorted %, and propagation feedback.

## Stacks

Stacks group related photos behind one cover.

- Builders detect burst sequences, export variants, and cross-source
  duplicates.
- The grid can collapse stack members by default or show expanded stacks.
- The Stacks tool shows review rows, ad-hoc scans, stack metadata differences,
  cover promotion, unstacking, and "trash non-covers" workflows.
- Stack APIs live under `/api/stacks`.

## Trash

Trash is the reversible delete surface.

- `Delete` and stack cleanup actions move originals into a `.trash` area under
  the source root.
- Trashed images leave normal active views and appear in the Trash tool.
- Restore uses `/api/images/restore`; listing uses `/api/trash`.
- Empty trash permanently deletes the moved files via `/api/trash/empty`.

## Shared And Publishing

- **Share** creates private collection galleries at `/s/{token}` through
  `/api/user-collections/{id}/share`.
- Share links can be password protected, revoked, rotated, and inspected for
  client favorites.
- **Publish to website** writes static gallery bundles into the configured
  `publish_dir` through `/api/user-collections/{id}/publish`.
- The **Shared** view aggregates private links and website publishes through
  `/api/shares`, including expiry, picks, local publish state, live URLs, and
  hook failures.
- See [Publishing Static Galleries](publishing.md) for `publish_dir`,
  `publish_hook`, and `publish_site_base_url`.

## Import And Export

- Import accepts dropped photos, chosen files, or a chosen folder and can
  preserve folder structure into the configured import inbox.
- **Past imports** lists recent batches from `/api/imports`; selecting a batch
  scopes the grid with `import_batch`.
- Export can write the current view or selected IDs as JSON, CSV, or ZIP.
- ZIP export supports original or preview sizes, path hardening, size budgets,
  and manifest reporting.

## Background Work

The compact activity widget opens the System drawer. Background work rows are:

- **AI embeddings**
- **Cache pregeneration**
- **People scan**
- **Captions**
- **Metadata**

Rows use **Pause** and **Resume** language where applicable. Heavy workers stay
local, and GPU-heavy work is coordinated so model jobs do not fight each other.

## Keyboard Shortcuts

- Grid: arrow keys navigate, Enter opens Loupe, `P` picks, `X` rejects, `U`
  clears the flag, `Delete` moves the selection to Trash, `S` expands or acts
  on a stack, `F` opens filters, `J` cycles density, and Esc clears layers or
  selection.
- Refine: `R` opens Refine, `1`-`4` pick in Mosaic, left/right choose in Duel,
  and `Ctrl+Z` undoes a pick.
- Loupe: left/right moves through photos, Space toggles zoom, `I` toggles info,
  `L` toggles lights-out, `P`/`X`/`U` flags, and Esc returns to Grid.
- Stacks and People: arrows move inside review surfaces; `C`, `K`/Enter, `U`,
  `Y`, and `N` drive cover, keep, unstack, and merge-review actions.
