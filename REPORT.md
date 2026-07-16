# ux3-collections report

Branch: `ux3-collections` (not merged to `develop`).

## Status by work-order item

1. Done — `/api/rankings` accepts `collection_id` and `ids`. Collection membership composes with ranking sorts and facets; `count_rankings` uses the same collection constraint. `test_library.py` covers scoped order and `flag=picked` composition.

   Manual QA: Not run. Request `/api/rankings?collection_id=<id>&sort=elo&limit=10`; confirm only collection members appear in descending rating order. Add `flag=picked`; confirm only picked members remain.

2. Done — Opening a mobile collection activates the Photos timeline with a collection scope chip. The timeline uses normal paging, selection, badges, sorting, and viewer behavior. Selected members can be removed through the selection bar; the toast restores them with Undo. Clearing the collection chip returns to Library.

   Manual QA: Not run. Open a collection with more than one page of photos, scroll for another page, select photos, remove them, and use Undo. Clear the Collection chip and confirm Library opens.

3. Done — `/api/date-histogram` returns `cover_id` for the highest-rated image in each dated month. Mobile month cards render the small thumbnail below a contrast scrim; cards without `cover_id` retain the text-only layout.

   Manual QA: Not run. Open month view for photos spanning multiple months. Confirm month cards use representative photos and text remains legible. Confirm an undated card stays text-only.

## Verification

- `for f in $(git diff --name-only develop -- '*.js'); do node --check "$f" || exit 1; done` — passed.
- `~/Projects/photo-archive/web/.venv/bin/python -m pytest test_library.py -q` from `web/` — `74 passed`.
- `git diff --check` — passed.
