# Export surface QA

- Selection pill: select one or more photos, choose **Export…**, verify the Photos tab queues a Develop batch, Originals downloads a zip, and Data offers CSV and JSON.
- Folder and panel menus: open Export from a folder, source, or current-scope control; verify every tab operates on that scope and the existing download endpoint still receives the scope filters.
- Grid context menu: right-click one photo and a multi-photo selection; verify the menu has one **Export…** item and Photos starts the existing batch export with the selected IDs.
- Develop toolbar: open one photo in Develop and choose Export; verify the Photos controls and presets match the prior dialog, while Originals and Data export that photo.
- Keyboard and memory: select each tab, change a Photos option, reopen from a different entry point, verify the active tab and Photos settings return; press Esc to close and Enter to run the active tab.

## Static verification

`node --check web/static/js/desktop/export_menu.js web/static/js/desktop/context_menu.js web/static/js/desktop/selection.js web/static/js/desktop/develop/export_dialog.js web/static/js/desktop/develop/develop.js`
