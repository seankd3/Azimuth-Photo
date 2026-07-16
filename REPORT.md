# ux7-system — System canvas + status peek

## Commits

1. `5f84fa08 feat: add System settings canvas lens` — Registered the System lens, added the seven-section canvas navigation, and mounted the existing System surfaces by section.
2. `f922955e feat: apply System settings on change` — Removed the dirty/save-bar flow; settings now save as they change with undo, while model installation remains explicit.
3. `b93c0c1e feat: reduce System drawer to status peek` — Reduced the slide-over to live background work, read-only storage/library/source status, and one route to System settings.
4. `feat: route System links through canvas lens` — Routed topbar and publishing setup into the System lens, focused Publishing’s folder field, and kept mounted canvas status fresh on a five-second cadence.

## QA walkthrough

1. Select the top-bar System icon: the System canvas opens at its remembered section. Choose each left-nav section; the matching content replaces the canvas pane.
2. Change a normal setting: it saves immediately (text waits 600 ms, blur, or Enter) and shows **Setting saved — Undo**. Use Undo to restore the prior server value. Change the AI model, then use **Save & install** to retain its explicit install flow.
3. Select the activity widget: the slim peek opens. Pause/resume a background worker there, then confirm its readout and control update on the next refresh.
4. In Publish, choose **Open Publishing settings**: the publishing overlay closes, the System lens opens to Publishing, and the Gallery folder field receives focus. Saving a folder preserves the return-to-Publish behavior.

## Verification

```text
node --check web/static/js/desktop/{drawer,system_lens,lenses,state,bootstrap}.js
git diff --check
rg -n "renderSettingsSaveBar|dirtySettings|setDraftSetting|drawer-save-settings" web/static/js/desktop/drawer.js
rg -n "openPublishingSettings" web/static/js/desktop/drawer.js
```

All commands passed. The repository-local browser/server check could not run because `web/.venv` is intentionally absent; it was not created or modified.
