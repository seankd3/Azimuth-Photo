# Browser Modules

`/static/app.js` is the ES module entrypoint loaded by `base.html`. It forwards
the cache-busting query string into this folder and imports `bootstrap.js`.

`bootstrap.js` owns the public `window.PhotoArchive` compatibility assignment.
During migration it imports `legacy/app.js`, which contains the old no-build
frontend monolith behind compatibility wrappers. `legacy/public_api.js` owns the
exported compatibility method table that backs `window.PhotoArchive.*`.
`legacy/filter_query_bridge.js` owns the legacy filter/query state bridge that
connects Library, Mosaic, and Compare URL construction to the extracted filter
and query modules. `legacy/flag_bridge.js` owns the legacy Library image-flag
facade that binds flag updates to Loupe and selection state.
`legacy/export_bridge.js` owns the legacy full-ranking export facade that binds
the extracted export helper to current Library query/sort state.
`legacy/batch_bridge.js` owns the legacy batch-selection
controller facade for Library card selection, batch flagging, and selected-image
export. `legacy/library_shell_bridge.js` owns the legacy Library shell and card
keyboard-selection facade around scroll persistence, empty state, and card
navigation. `legacy/library_init_bridge.js` owns the legacy Library page
initialization workflow around restored state, first rankings load, infinite
scroll setup, keyboard binding, and Loupe/search startup. `legacy/date_scrubber_bridge.js`
owns the legacy Library date scrubber state facade around date grouping,
jumping, visibility, and scroll tracking. `legacy/loupe_bridge.js` owns the legacy Loupe controller and
media-status facade wiring. `legacy/search_sort_bridge.js` owns the legacy
search/sort state bridge that connects persisted search state and sort-control
rendering to the extracted Library modules. `legacy/ui_runtime_bridge.js` owns
the legacy bottom-bar measurement, visibility refresh, AI-status polling, and
UI-settings loader glue.

`ui.js` owns shared toast, confirm-modal, shortcut overlay, bottom-bar
measurement, visibility-refresh wiring, visual-row navigation math, formatting,
and escaping helpers.
`warmup.js` owns the browser image preloader, image probe, timeout,
warm-cache helpers, background warmup scheduling, warmup URL extraction,
request warming, and image-tier warm batching used by the legacy
compatibility module.
`warmup_neighbors.js` owns Library/Compare cross-view and neighbor-request
warmup scheduling constants while legacy keeps current view, query, and paging
state.
`api.js` owns small shared request helpers as fetch calls move out of
`legacy/app.js`. `query_state.js` owns pure filter-state normalization, URL
parameter helpers, Library URL-state syncing, and filter-neighbor state generation.
`query_controller.js` owns the state adapter for current query state plus
Library, Mosaic, and Compare URL construction while the legacy bridge keeps the
mutable filter state. `media_status.js` owns media-status cache, inflight
request coordination, and warm-tier invalidation callbacks. `media_metadata.js` owns shared media date, camera,
resolution, and title formatting helpers. `filters.js` owns shared filter-state
summary helpers, metadata filter controls, lazy filter-option/folder-list
population, and star-hover UI.

Feature folders are being introduced gradually. `loupe/tiers.js` owns Loupe
tier names, ranks, timeouts, blank image source, and tier URL construction.
`loupe/loading.js` owns Loupe progressive tier loading, tier probe lifecycle,
and probe cancellation helpers.
`loupe/status.js` owns Loupe cache-status text formatting, overlay status-line
rendering, and tier-loading display.
`loupe/metadata.js` owns Loupe metadata line assembly and overlay metadata rendering.
`loupe/navigation.js` owns Loupe list/standalone entry navigation, next/previous
movement, neighbor ordering, and hot-set tier selection.
`loupe/warmup.js` owns Loupe neighbor image preloading and hot-set cache warming.
`loupe/filmstrip.js` owns Loupe filmstrip windowing, thumbnail DOM rendering,
active-thumb updates, counter text, and centering behavior.
`loupe/focus.js` owns Loupe focus targeting, tabbable-element filtering, and
Tab focus trapping.
`loupe/zoom.js` owns Loupe zoom/pan math, image transform writes, and zoom
indicator text.
`loupe/interaction.js` owns Loupe mouse drag, click-to-zoom, wheel zoom, and
resize interaction binding while legacy keeps the page state facade.
`library/query.js` owns Library rankings query-string construction.
`library/pagination.js` owns Library rankings page-size and neighbor-limit
constants plus scroll-restore page-size math.
`library/filters.js` owns Library filter session persistence, URL/session
restore precedence, DOM application for filter buttons, stars, flags, and
selects, plus the behavior behind the legacy `setFilter`, `clearLibraryFilters`,
`toggleFilter`, and `toggleStar` compatibility methods.
`library/filter_controller.js` owns the legacy filter controller glue that
reloads the correct Library, map, Mosaic, or Compare surface after filter
changes while delegating pure filter mutations to `library/filters.js`.
`library/sort.js` owns Library sort keys and sort state conversion.
`library/search_state.js` owns Library/search session persistence keys and
helpers for sort, search query, deep-search flag, and search-specific sort state.
`library/search_controls.js` owns Library and Compare search/sort DOM control
rendering; legacy wires both through `legacy/search_sort_bridge.js`.
`library/search_controller.js` owns Library search input debounce, clear-search,
and deep-search control flow.
`library/similar.js` owns the Library "find similar" action, similar-results
request flow, search-control state, and similar-card DOM rendering.
`library/flags.js` owns Library image-flag local DOM/state updates, single-image
flag POSTs, and current-image flag selection.
`library/batch.js` owns Library batch-selection click/toggle/clear mechanics,
batch-selection bar HTML, batch flag POSTs, rollback handling, and the
compatibility facade for selected-image export.
`export/actions.js` owns full-ranking export and selected-image export URL
construction/opening for JSON and CSV downloads.
`library/display.js` owns Library card flag, tier, similarity, info-line, and date labels.
`library/rank_cards.js` owns Library ranking-card and date-header DOM construction.
`library/shell.js` owns Library empty-state display, scroll persistence keys and
helpers, back-to-top visibility, and scroll-to-card helpers.
`library/date_scrubber.js` owns Library date-scrubber fetch orchestration, DOM
rendering, active group highlighting, offset math, jump-to-group loading, and
scroll tracking.
`library/navigation.js` owns Library card keyboard selection, visibility
scrolling, and visual-row navigation wiring.
`library/map.js` owns Library map library loading, info/error display, and popup DOM construction.
`library/map_controller.js` owns Library map-view state, grid/map toggle behavior,
marker loading, marker-layer replacement, and map-bound fitting.
`compare/query.js` owns Compare/Mosaic neighbor-count constants and API
query-string construction.
`compare/navigation.js` owns Mosaic keyboard cell selection and visual-row navigation wiring.
`compare/mode_controller.js` owns Compare/Mosaic mode and strategy switching
glue while legacy keeps pair and mosaic state.
`compare/pair_controller.js` owns Compare pair fetching, pair display, low-water
prefetch, pair image-token freshness, and pair-status side effects.
`compare/image_controller.js` owns Compare pair image rendering adapter wiring,
displayed tier state, media-status lookup, tier probing, and tier URL injection.
`compare/mosaic_action_controller.js` owns Mosaic pick orchestration,
optimistic replacement swapping, propagation follow-up, and rollback on failed
save while legacy keeps board state storage.
`compare/mosaic_render_controller.js` owns Mosaic render/upscale adapter wiring,
render token management, resize-frame scheduling, and tier probe dependencies.
`compare/mosaic_replacements.js` owns Mosaic replacement-buffer fetching,
deduping, readiness probing, retry scheduling, and replacement watermarks.
`compare/images.js` owns Compare pair image rendering and progressive tier
upgrades.
`compare/action_controller.js` owns Compare submit/undo state orchestration
while `compare/actions.js` owns pure request and Elo helper functions.
`compare/view.js` owns Compare mode container visibility and empty-state display.
`compare/actions.js` owns Compare pair action payloads, comparison POST/result
parsing, returned ELO application, undo POST/result parsing, and undo toast text.
`compare/propagation.js` owns Compare/Mosaic propagation prediction and last
propagation-count request helpers.
`compare/status_controller.js` owns Compare progress, coverage-stat refresh,
ranking-signal mutation, propagation-count application, and status UI glue.
`compare/mosaic.js` owns Mosaic grid sizing/rendering, progressive cell tier
upgrades, pick request/result helpers, and replacement-index selection.
`compare/status.js` owns Compare pool/progress labels, ranking-signal count
mutation, coverage percentage/bar display, propagation badge display, and
counter roll-up animation.
`search/query.js` owns shared text/deep-search query helpers.
`people/labels.js` owns People display label, worker status, and settings status text helpers.
`people/cards.js` owns People card, grid, and merge-suggestion HTML.
`people/page.js` owns People page loading/polling, rendering, label-draft
preservation, focus restoration, and face-thumbnail fallback behavior.
`people/actions.js` owns People label, merge, reject, ignore, and Library-filter
actions.
`people/controller.js` owns the People `window.PhotoArchive` compatibility
adapter for inline handlers and action refresh/toast wiring.
`settings/status.js` owns Settings page status banner updates.
`settings/cache_status.js` owns Settings cache status and auto-tuning panel rendering.
`settings/ai_status.js` owns Settings AI/model/deep-search status panel rendering and embedding model preset controls.
`settings/people_status.js` owns Settings People status panel rendering.
`settings/work_banner.js` owns Settings background-work banner HTML.
`settings/deep_search.js` owns Settings deep-search term HTML and schedule
normalization/summary text.
`settings/display.js` owns Settings work-mode, cache-profile, rate, ETA,
auto-tuning, thumbnail-output change, and embedding-index badge formatting.
`settings/form.js` owns Settings form population/collection, deep-search
schedule controls, work-mode selection state, cache-profile hint updates, and
thumbnail-output change notices.
`settings/actions.js` owns Settings save/reset, cache clear/pregen, embedding
pause/resume, and model-install request flows.
`settings/controller.js` owns the Settings public-action `window.PhotoArchive`
compatibility adapter while legacy keeps page initialization and page state.
`settings/ui_settings.js` owns lightweight browser UI setting loading used by
shared views, including the Loupe cache-status display toggle.
`legacy/app.js` should import stateless Settings helpers directly; keep local
Settings wrappers only when they bind page state such as `SETTINGS_FIELDS` or
`settingsPageData`.
`cache/status.js` owns cache status, resource, and pregen display formatting helpers.
`cache/guide.js` owns Settings cache-tier guide rendering.
`ai/status.js` owns AI status delay logic, bottom-bar/panel rendering, model
install display, and deep-search status/query HTML. `ai/poller.js` owns the AI
status polling lifecycle; `legacy/app.js` still shares the visibility-refresh
hook with Settings metadata refresh.
`catalog/status.js` owns catalog source status and scan-state display helpers.
`catalog/sources.js` owns Catalog source-list row HTML, source-list rendering, and remove-source dialog rendering.
`catalog/directory.js` owns Catalog directory-browser root, row, breadcrumb HTML, and browser rendering.
`catalog/browser.js` owns Catalog directory-browser path state, folder-picker
actions, and browse/use/up controls.
`catalog/actions.js` owns Catalog source loading, add/rescan/remove actions,
and scan-completion polling.
`catalog/home_scan.js` owns the home-page "Scan Folder" button flow.
`catalog/scan_entrypoint.js` owns the `PhotoArchive.startScan` page-dispatch
compatibility shim between the home page and Settings/Catalog source flow.
`catalog/controller.js` owns the Catalog `window.PhotoArchive` compatibility
adapter for inline handlers, source lookup, status/toast wiring, and fallback
stats handoff.

Future extractions should move code out of `legacy/app.js` into feature folders
here while keeping the same `window.PhotoArchive.*` methods available.

## Browser Smoke Boundary

`scripts/photoarchive-browser-smoke` is the live browser acceptance gate for the
no-build frontend. Its `SMOKE_WORKFLOW_CONTRACT` maps the goal-level smoke
workflows to concrete page selectors and public `window.PhotoArchive` methods:
Settings, Catalog, People, Library, Compare/Mosaic, Loupe, Filters, Search,
Export, Cache Status, and AI Status. The smoke run still avoids destructive
actions; it proves that the page shells, compatibility API, static modules, and
AI status polling surface are wired in the running app.
