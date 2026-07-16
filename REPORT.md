# Canvas lens safety nets report

Branch: `ux2-canvas` (not merged to `develop`).

## Status by work-order item

1. Not completed — `web/static/js/desktop/import_canvas.js` is absent from this worktree and from `develop`; the present import implementation is the out-of-scope `import_stage.js`.
   Manual QA: Not applicable in this branch.

2. Done — People merge mode shows a fixed source-name banner; Escape and selecting the source card cancel it; the banner clears on cancellation, merge, and unmount. Files: `web/static/js/desktop/people.js`, `web/static/desktop.css`.
   Manual QA: In People, choose Merge with on a person. Confirm the banner names that person. Press Escape, then start again and click the source card; each action cancels without navigating. Choose another person and confirm the merge popover still appears.

3. Done — Skip supplies an undo that returns its suggestion to its original place and corrects the skip count. File: `web/static/js/desktop/cull_brief.js`.
   Manual QA: Open a Cull Brief, skip a scene, click Undo in the toast, and confirm the same scene is restored with the skip count decremented.

4. Done — Collection deletion uses the standard confirmation and its undo recreates the cached collection name, query, and member IDs. File: `web/static/js/desktop/panel.js`.
   Manual QA: Delete a regular collection; confirm no typed count is required and photos remain in the archive. Click Undo and confirm the collection and its members return. Empty Trash still requires its typed-count confirmation.

5. Done — Smart collections show a not-allowed drag target and explain that filters drive membership when dropped on. Files: `web/static/js/desktop/panel.js`, `web/static/desktop.css`.
   Manual QA: Drag selected photos over a smart collection. Confirm the not-allowed state, then drop and confirm the filter explanation toast appears without changing membership.

6. Done — Bulk non-cover trashing asks for confirmation using the computed count before moving any photos. File: `web/static/js/desktop/duplicates.js`.
   Manual QA: In Duplicates, choose Trash non-covers. Confirm the dialog names the exact count; cancel and confirm nothing moves, then confirm and verify the existing undo toast.

7. Not completed — `web/static/js/desktop/import_canvas.js` and `import_stage_grid.js` are absent from this worktree and from `develop`; the referenced `onPeek` surface does not exist here.
   Manual QA: Not applicable in this branch.

8. Done — Trash supports Ctrl+A, arrow-key focus movement using the grid focus-ring class, and Enter selection toggling. Files: `web/static/js/desktop/trash.js`, `web/static/js/desktop/keyboard.js`.
   Manual QA: Open Trash, press Ctrl+A, then clear selection. Use all arrow keys to move the ring across thumbnails and press Enter to select and deselect the focused photo.

9. Done — Escape clears selection before closing a lens, and persistent non-grid lenses fall back to Grid after foreground layers are exhausted. The existing Develop case remains ahead of the fallback. File: `web/static/js/desktop/keyboard.js`.
   Manual QA: In Trash or Duplicates with rows selected, press Escape once and confirm selection clears while staying in the lens; press Escape again to leave it. Open People, Map, Events, or Timeline and press Escape to return to Grid. In Develop, press Escape with no transient control open and confirm it returns to Grid.

## Verification

After the scoped JavaScript edits, run:

```sh
for f in $(git diff --name-only develop -- '*.js'); do node --check "$f" || exit 1; done
```

This report records that import items 1 and 7 could not be implemented without changing the out-of-scope import module or bootstrap wiring.

Result: passed with exit code 0 and no output on 2026-07-15. `git diff --check` also passed with exit code 0 and no output.
