# ux5-mediums report

Branch: `ux5-mediums` (not merged).

## Status by work-order item

1. Done — Events warm remount revalidation now treats a changed `visible_images` total as a change, including mutations beyond the first 100-photo page. Commit: `512aa7a4`.

2. Done — Timeline keeps its warm-remount render, then refreshes samples for cached month bands currently in the viewport. Changed samples replace stale day thumbnails even when month counts are unchanged. Commit: `a22c2e7b`.

3. Done for regular collections — `scopeParams()` sends `collection_id` only when `collectionSmart` is false. Timeline month samples use the same static collection constraint. `/api/date-groups` accepts `collection_id` and derives matching groups from the collection-aware histogram. Commit: `5dbe09e2`.

   Investigation: Grid collection scope is applied by `scope_data.js`, which calls the collection endpoint instead of normal rankings. The ranking and histogram backend already support static `collection_id`. Smart collections are live queries resolved by the collection endpoint; no `collection_id` is emitted for `collectionSmart`, so the change does not double-scope or alter smart-query behavior. Supporting smart collections in Timeline would require passing or resolving their saved query, which is outside this work order.

4. Done — History reloads have a per-image monotonic token. An older same-image response is ignored once a newer reload begins. Commit: `f089cf87`.

5. Done — Luminance range controls capture the active pointer on pointerdown, so pointerup outside the control still completes the coalesced undo gesture. The existing cross-photo guard remains in place. Commit: `2430b8b8`.

## QA

- `node --check static/js/desktop/events.js`
- `node --check static/js/desktop/timeline.js`
- `node --check static/js/desktop/state.js`
- `node --check static/js/desktop/develop/history_panel.js`
- `node --check static/js/desktop/develop/color_wheels.js`
- `/home/sean/Projects/photo-archive/web/.venv/bin/python -m pytest test_library.py -q` from `web/` — `75 passed, 4 warnings`.
- `git diff --check`

Manual browser QA was not run.
