"""Regression gates for desktop interaction correctness fixes."""

from pathlib import Path
import json
import re
import subprocess
import unittest


WEB = Path(__file__).parent


def read(*parts):
    return (WEB / "static" / "js" / "desktop").joinpath(*parts).read_text(encoding="utf-8")


def read_mobile(*parts):
    return (WEB / "static" / "js" / "mobile").joinpath(*parts).read_text(encoding="utf-8")


class DesktopCorrectnessTests(unittest.TestCase):
    def test_develop_keyboard_close_unmounts_even_while_grid_is_active(self):
        keyboard = read("keyboard.js")
        develop = read("develop", "develop.js")

        self.assertIn("export function closeDevelop()", develop)
        self.assertIn("function closeDevelop() {\n    unmount();\n}", develop)
        self.assertEqual(keyboard.count("closeDevelop();"), 2)
        self.assertIn("if (developOpen()) return;", keyboard)

    def test_develop_history_reload_does_not_paint_after_an_image_switch(self):
        history = read("develop", "history_panel.js")

        # Formatting-insensitive: the staleness guard must compare the live
        # image id against the reload-time id and bail before painting.
        guard = "Number(api.getImageId?.()) !== Number(imageId)"
        self.assertIn(guard, history)
        self.assertLess(history.index(guard), history.index("controller.setHistory(fresh);"))

    def test_develop_status_allows_the_retry_control_to_be_absent(self):
        develop = read("develop", "develop.js")
        start = develop.index("function setStatus(")
        set_status = develop[start:develop.index("\n}\n", start)]

        self.assertIn("if (statusRetry) {", set_status)
        self.assertIn("statusRetry.hidden", set_status)
        self.assertIn("statusRetry.onclick", set_status)

    def test_flag_scope_reloads_after_a_committed_membership_change(self):
        grid = read("grid.js")

        self.assertIn("on('flags', ({ imageIds, committed } = {}) =>", grid)
        self.assertIn("if (committed && scope.flag", grid)
        self.assertIn("viewState.images.some((image) =>", grid)
        self.assertIn("loadFirstPage();", grid[grid.index("if (committed && scope.flag"):])

    def test_pending_previews_refresh_idle_desktop_and_mobile_photo_views(self):
        grid = read("grid.js")
        timeline = read_mobile("timeline.js")

        for source in (grid, timeline):
            self.assertIn("let thumbnailPollTimer = 0;", source)
            self.assertIn("function scheduleThumbnailPoll()", source)
            self.assertIn("pending_thumbnails", source)
            self.assertIn("refreshPendingPreviews", source)
            self.assertIn("refreshFirstPagePreviews", source)
            self.assertIn("preview-pending", source)
            self.assertIn("data-preview-src", source)
            self.assertIn("selection.size", source)

        self.assertIn("scrollTop <= 160", grid)
        self.assertIn("stopThumbnailPoll();", grid[grid.index("export function unmountGrid()"):])
        self.assertIn("scrollTop <= 160", timeline)
        self.assertIn("on('tab', (tab) =>", timeline)

    def test_events_keep_pending_previews_off_the_thumbnail_decode_path(self):
        events = read("events.js")
        cell_html = events[events.index("function cellHtml"):events.index("function patchCells")]
        group_html = events[events.index("function groupHtml"):events.index("function render()")]

        self.assertIn("previewThumbUrl(img)", cell_html)
        self.assertIn("preview-pending", cell_html)
        self.assertIn("previewSrc ? `data-src=", cell_html)
        self.assertIn("image.preview_ready !== false", events)
        self.assertIn("previewThumbUrl(hero, 'md')", group_html)
        self.assertIn("async function refreshPendingPreviews()", events)
        self.assertIn("params.set('ids', ids.join(','));", events)
        self.assertIn("stopThumbnailPoll();", events[events.index("export function unmountEvents"):])

    def test_warm_events_revalidates_group_coverage_after_flags_change(self):
        events = read("events.js")

        self.assertIn("revalidate({ refreshCoverage: true })", events)
        self.assertIn("function patchCoverageDots()", events)
        self.assertIn("dotHost.innerHTML = coverageDots(group);", events)
        revalidate = events[events.index("async function revalidate"):events.index("export function initEvents")]
        self.assertLess(
            revalidate.index("patchCoverageDots();"),
            revalidate.index("resetData();"),
        )

    def test_loupe_removes_trashed_photos_from_grid_and_session_lists(self):
        loupe = read("loupe.js")

        self.assertIn("function discardTrashedImages(imageIds)", loupe)
        self.assertIn("viewState.images = viewState.images.filter", loupe)
        self.assertIn("sessionImages = sessionImages.filter", loupe)
        self.assertIn("if (!remaining.length) {\n        closeLoupe();", loupe)
        self.assertIn("on('trash:changed', ({ imageIds } = {}) => discardTrashedImages(imageIds));", loupe)

    def test_trash_change_emitters_use_only_server_confirmed_ids(self):
        trash = read("trash.js")
        duplicates = read("duplicates.js")
        outcome_path = WEB / "static" / "js" / "desktop" / "trash_outcome.js"
        script = f"""
            import {{ imageMutationOutcome, mutationFailureReason, mutationPartialSuffix }}
                from {json.dumps(outcome_path.as_uri())};
            const empty = imageMutationOutcome({{
                ok: true,
                data: {{ trashed: [], errors: [{{ id: 4, reason: 'source path missing' }}] }},
            }}, 'trashed');
            if (empty.imageIds.length !== 0) throw new Error('empty result reported moved ids');
            if (mutationFailureReason(empty.errors, 'fallback') !== 'source path missing') {{
                throw new Error('server reason was not surfaced');
            }}
            const partial = imageMutationOutcome({{
                ok: true,
                data: {{ trashed: [7, 7], errors: [{{ id: 8 }}, {{ id: 9 }}] }},
            }}, 'trashed');
            if (JSON.stringify(partial.imageIds) !== '[7]') throw new Error('server ids were not normalized');
            if (mutationPartialSuffix(partial.errors, 'trashed') !== " · 2 couldn't be trashed") {{
                throw new Error('partial failure count was not surfaced');
            }}
        """
        subprocess.run(
            ["node", "--experimental-default-type=module", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )

        for source in (trash, duplicates):
            self.assertNotIn("emit('trash:changed', { imageIds });", source)
        self.assertEqual(trash.count("imageMutationOutcome("), 4)
        self.assertEqual(duplicates.count("imageMutationOutcome("), 4)

        trash_start = trash.index("export async function trashSelectedImages()")
        trash_action = trash[trash_start:trash.index("\n}\n", trash_start)]
        self.assertLess(
            trash_action.index("imageMutationOutcome(result, 'trashed')"),
            trash_action.index("if (!trashedIds.length)"),
        )
        self.assertLess(
            trash_action.index("if (!trashedIds.length)"),
            trash_action.index("clearSelection();"),
        )
        self.assertLess(
            trash_action.index("clearSelection();"),
            trash_action.index("emit('trash:changed', { imageIds: trashedIds });"),
        )
        self.assertIn("fmt(trashedIds.length)", trash_action)
        self.assertIn("mutationPartialSuffix(errors, 'trashed')", trash_action)

        for function_name in ("keepCoverForStack", "keepCoversEverywhere"):
            start = duplicates.index(f"async function {function_name}(")
            action = duplicates[start:duplicates.index("\n}\n", start)]
            self.assertLess(
                action.index("if (!trashedIds.length)"),
                action.index("emit('trash:changed', { imageIds: trashedIds });"),
            )
            self.assertIn("mutationPartialSuffix(errors, 'trashed')", action)

    def test_duplicate_undo_reapplies_server_flags_when_the_undo_write_fails(self):
        duplicates = read("duplicates.js")
        undo_start = duplicates.index("undo: async () => {")
        undo = duplicates[undo_start:duplicates.index("    });", undo_start)]

        self.assertIn("if (!undone) setFlagsLocally(normalized);", undo)
        self.assertLess(undo.index("const undone = await writeGrouped(previous);"), undo.index("if (!undone) setFlagsLocally(normalized);"))

    def test_keyword_assignment_confirms_before_announcing_success(self):
        keywords = read("keywords_panel.js")
        assign_start = keywords.index("async function assign(")
        assign = keywords[assign_start:keywords.index("\n}\n", assign_start)]

        self.assertLess(assign.index("await mutation.commit;"), assign.index("showToast(`${keyword.path}"))

    def test_collection_undo_only_confirms_after_the_remove_succeeds(self):
        panel = read("panel.js")
        events = read("events.js")
        mobile_selection = read_mobile("selection.js")

        helper_start = panel.index("async function undoCollectionAdd(")
        helper = panel[helper_start:panel.index("\n}\n", helper_start)]
        self.assertIn("if (!result?.ok)", helper)
        self.assertLess(helper.index("if (!result?.ok)"), helper.index("showToast(successMessage);"))
        self.assertIn("await loadCollections();", helper)
        self.assertIn("if (removed?.ok) showToast('Event photos removed from collection');", events)
        self.assertIn("emit('collections:refresh');", events)
        self.assertEqual(mobile_selection.count("if (removed?.ok) showToast('Removed from collection');"), 2)
        self.assertEqual(mobile_selection.count("new CustomEvent('collections-changed')"), 2)

    def test_refine_undo_surfaces_ranking_drift_on_desktop_and_mobile(self):
        desktop_refine = read("refine.js")
        mobile_refine = read_mobile("refine.js")

        for refine in (desktop_refine, mobile_refine):
            self.assertIn("result?.partial", refine)
            self.assertIn("Undo partial — ranking drifted", refine)

    def test_keep_covers_button_is_reenabled_if_stack_reload_fails(self):
        duplicates = read("duplicates.js")
        action_start = duplicates.index("async function keepCoversEverywhere()")
        action = duplicates[action_start:duplicates.index("\n}\n", action_start)]

        self.assertIn("await reloadStacks();", action)
        self.assertIn("finally {\n        if (button.isConnected) button.disabled = false;", action)
        self.assertLess(action.index("await reloadStacks();"), action.index("if (button.isConnected) button.disabled = false;"))

    def test_partial_keep_cover_reload_keeps_stack_visible(self):
        duplicates = read("duplicates.js")
        action_start = duplicates.index("async function keepCoverForStack(")
        action = duplicates[action_start:duplicates.index("\n}\n", action_start)]

        self.assertIn(
            "const trashComplete = errors.length === 0 && trashedIds.length === imageIds.length;",
            action,
        )
        self.assertIn(
            "if (trashComplete) {\n"
            "        removeFinishedStack(stackId);\n"
            "    } else {\n"
            "        await reloadStacks();\n"
            "    }",
            action,
        )

    def test_export_poll_reports_when_the_export_status_cannot_be_checked(self):
        export_dialog = read("develop", "export_dialog.js")
        poll_start = export_dialog.index("async function pollBatchStatus")
        poll = export_dialog[poll_start:export_dialog.index("\n}\n", poll_start)]

        self.assertEqual(poll.count("showToast?.('Export status unknown — check Exports later');"), 3)
        self.assertLess(poll.index("if (!response.ok)"), poll.index("const status = await response.json();"))

    def test_cull_brief_startup_failure_keeps_a_retry_state_visible(self):
        cull_brief = read("cull_brief.js")

        self.assertIn("loadError: false", cull_brief)
        self.assertIn(r"Couldn\'t load cull suggestions.", cull_brief)
        self.assertIn("data-cull-retry", cull_brief)
        self.assertIn("setTimeout(refreshCullBriefWithErrorState, 3000);", cull_brief)

    def test_filter_source_failures_are_not_presented_as_empty_results(self):
        filters = read("filters.js")

        self.assertIn("let foldersLoadError = false;", filters)
        self.assertIn("let tagsLoadError = false;", filters)
        self.assertIn("Couldn't load folders.", filters)
        self.assertIn("Couldn't load tags.", filters)
        self.assertIn("data-filter-retry", filters)
        self.assertIn("loadOptions({ force: true })", filters)

    def test_collection_scope_pages_compose_filters_through_rankings(self):
        scope_data = read("scope_data.js")
        page_loader = scope_data[scope_data.index("export async function loadScopePage"):]

        self.assertIn("const params = scopeParams({ limit, offset });", page_loader)
        self.assertIn("getRankings(params, options)", page_loader)
        self.assertNotIn("getCollection(", page_loader)
        self.assertNotIn("loadCollectionImages", scope_data)
        self.assertNotIn("loadCollectionImageIds", scope_data)

    def test_collection_scope_export_uses_server_composition(self):
        panel = read("panel.js")
        export_menu = read("export_menu.js")
        export_scope = panel[
            panel.index("export async function exportCurrentScope"):
            panel.index("export function openScopeExportMenu")
        ]
        shared_dialog_scope = export_menu[
            export_menu.index("async function scopedImageIds"):
            export_menu.index("export function openExportMenu")
        ]

        self.assertIn("const params = scopeParams({ format });", export_scope)
        self.assertNotIn("loadCollectionImageIds", export_scope)
        self.assertIn("Preparing ${count} file", export_scope)
        self.assertIn("getRankings(scopeParams({", shared_dialog_scope)
        self.assertIn("while (offset < maxIds)", shared_dialog_scope)
        self.assertIn("SCOPE_EXPORT_PAGE_SIZE", shared_dialog_scope)
        self.assertIn("SCOPE_EXPORT_PROGRESS_DELAY_MS", shared_dialog_scope)
        self.assertNotIn("50000", shared_dialog_scope)
        self.assertNotIn("loadCollectionImageIds", shared_dialog_scope)

    def test_collection_membership_loaders_stay_out_of_scope_consumers(self):
        js_root = WEB / "static" / "js"
        allowed_get_collection_sites = {
            ("desktop/api.js", "export async function getCollection(collectionId, { limit = 500, offset = 0, signal = null } = {}) {"),
            ("desktop/gallery_editor.js", "getCollection(collectionId, { limit: 500 }),"),
            ("desktop/panel.js", "const data = await getCollection(collectionId, { limit: 1000 });"),
            ("desktop/panel.js", "const detail = await getCollection(collectionId, { limit: 1000, offset });"),
            ("mobile/api.js", "export async function getCollection(collectionId, limit = 500) {"),
            ("mobile/library.js", "data = await getCollection(coll.id, 500);"),
            ("mobile/sharing.js", "const data = await getCollection(collection.id, 1000);"),
        }
        allowed_id_loader_sites = set()

        def matching_sites(pattern):
            sites = set()
            for path in js_root.rglob("*.js"):
                source = path.read_text(encoding="utf-8")
                lines = source.splitlines()
                for match in re.finditer(pattern, source):
                    line_number = source.count("\n", 0, match.start())
                    sites.add((path.relative_to(js_root).as_posix(), lines[line_number].strip()))
            return sites

        class_rule = (
            "Collection scopes must resolve through server composition (scopeParams plus "
            "getRankings or another composed endpoint), never raw collection membership. "
            "getCollection and loadCollectionImageIds are membership-chrome only; add a site "
            "to this explicit allowlist only after a conscious membership-UI decision."
        )
        self.assertEqual(
            matching_sites(r"\bgetCollection\s*\("),
            allowed_get_collection_sites,
            class_rule,
        )
        self.assertEqual(
            matching_sites(r"\bloadCollectionImageIds\b"),
            allowed_id_loader_sites,
            class_rule,
        )

    def test_collection_month_counts_compose_filters_through_histogram(self):
        filters = read("filters.js")
        month_counts = filters[
            filters.index("async function scopedMonthCounts"):
            filters.index("async function expandYear")
        ]

        self.assertIn("getDateHistogram(params)", month_counts)
        self.assertNotIn("loadCollectionImages", filters)

    def test_sync_refresh_cannot_overwrite_an_in_flight_control_result(self):
        sync_chip = read("sync_chip.js")

        self.assertIn("let controlInFlight = 0;", sync_chip)
        self.assertIn("let statusGeneration = 0;", sync_chip)
        self.assertIn("if (controlInFlight || generation !== statusGeneration) return;", sync_chip)
        self.assertIn("const generation = ++statusGeneration;", sync_chip)

        control = sync_chip[
            sync_chip.index("async function control("):
            sync_chip.index("function patchOffline()")
        ]
        completion_guard = "if (generation === statusGeneration) statusGeneration += 1;"
        self.assertIn(completion_guard, control)
        self.assertLess(control.index(completion_guard), control.index("controlInFlight -= 1;"))

    def test_sync_chip_surfaces_pending_metadata_operations(self):
        sync_chip = read("sync_chip.js")

        self.assertIn("const pendingOps = Number(status.pending_ops) || 0;", sync_chip)
        self.assertIn("data-sync-pending", sync_chip)
        self.assertIn("change${pendingOps === 1 ? '' : 's'} waiting to retry", sync_chip)
        self.assertIn("depth || pendingOps", sync_chip)

    def test_worker_action_ignores_stale_poll_paint_for_its_row(self):
        drawer = read("drawer.js")

        self.assertIn("const workerActionGenerations = new Map();", drawer)
        self.assertIn("const workerActionsInFlight = new Set();", drawer)
        self.assertIn("const workerGenerations = new Map(workerActionGenerations);", drawer)
        self.assertIn("workerActionsInFlight.has(item.key)", drawer)
        generation_bump = "workerActionGenerations.set(key, (workerActionGenerations.get(key) || 0) + 1);"
        action_start = drawer.index("for (const btn of body.querySelectorAll('[data-worker-action]'))")
        worker_action = drawer[
            action_start:
            drawer.index("body.querySelector('#clear-cache-btn')", action_start)
        ]
        self.assertEqual(worker_action.count(generation_bump), 2)
        completion_bump = worker_action.rindex(generation_bump)
        self.assertLess(completion_bump, worker_action.index("workerActionsInFlight.delete(key);"))

    def test_background_workers_default_new_productive_states_to_active(self):
        drawer = read("drawer.js")

        self.assertIn("const INACTIVE_WORKER_STATES = new Set([", drawer)
        for state in ("idle", "ready", "paused", "complete", "caught_up", "error", "disabled", "unavailable", "stale"):
            self.assertIn(f"'{state}'", drawer)
        self.assertIn("return Boolean(state) && !INACTIVE_WORKER_STATES.has(state);", drawer)
        self.assertNotIn("const ACTIVE_WORKER_STATES", drawer)
        self.assertNotIn("const CACHE_ACTIVE_PREGEN_STATES", drawer)
        self.assertNotIn("const METADATA_ACTIVE_WORKER_STATES", drawer)
        self.assertIn("workerStateIsActive(aiStatus)", drawer)
        self.assertIn("workerStateIsActive(peopleStatus)", drawer)
        self.assertIn("workerStateIsActive(captionStatus)", drawer)

    def test_people_progress_uses_scan_counts_with_an_active_floor(self):
        drawer = read("drawer.js")
        people_progress = drawer[
            drawer.index("function peopleProgress"):
            drawer.index("function activeProgress")
        ]

        self.assertIn("counts.scan", people_progress)
        self.assertIn("counts.pending_cached_images", people_progress)
        self.assertIn("workerStateIsActive(status)", people_progress)
        self.assertIn("Math.max(5, countProgress)", people_progress)
        self.assertEqual(drawer.count("peopleProgress(peopleStatus)"), 2)

    def test_metadata_activity_uses_metadata_worker_states_and_pause(self):
        drawer = read("drawer.js")
        metadata_line = drawer[
            drawer.index("function metadataLine"):
            drawer.index("function statusText")
        ]

        self.assertIn("worker.state", metadata_line)
        self.assertIn("!status?.manual_pause", drawer)
        self.assertIn("workerStateIsActive(status)", drawer)
        self.assertNotIn("Boolean(status?.active)", drawer)
        self.assertEqual(drawer.count("metadataStateIsActive(metadataStatus)"), 3)

    def test_cache_waiting_keeps_system_activity_active(self):
        drawer = read("drawer.js")
        active_progress = drawer[
            drawer.index("function activeProgress"):
            drawer.index("function modelStateLine")
        ]
        activity = drawer[
            drawer.index("function renderActivity"):
            drawer.index("async function refreshActivity")
        ]

        self.assertIn("function cachePregenStateIsActive(status)", drawer)
        self.assertIn("!pregen.manual_pause", drawer)
        self.assertIn("workerStateIsActive({ worker: pregen })", drawer)
        self.assertIn("cachePregenStateIsActive(cacheStatus) && cacheProgress <= 0 ? 50 : cacheProgress", active_progress)
        self.assertIn("|| cachePregenStateIsActive(cacheStatus)", activity)

    def test_import_scan_never_rechecks_a_user_cleared_key(self):
        import_stage = read("import_stage.js")

        self.assertIn("let insertedEntryKeys = new Set();", import_stage)
        self.assertIn("insertedEntryKeys = new Set();", import_stage)
        self.assertLess(
            import_stage.index("if (insertedEntryKeys.has(entry.key)) continue;"),
            import_stage.index("if (!entry.suspect) checked.add(entry.key);"),
        )
        self.assertIn("Import status lost — this import may still be running", import_stage)
        self.assertIn("setScope({ import_batch: '', importBatchLabel: '' });", import_stage)

    def test_publish_poll_cannot_clobber_active_input(self):
        # The ux7 Deliver rewrite re-renders the website tab on each poll tick.
        # That is safe only while the protection invariant holds: polls run
        # solely while a publish is in_progress, and the slug input is disabled
        # for that whole window — so a rebuild can never clobber live typing.
        panel = read("panel.js")

        # Polls only reschedule while the job is running.
        self.assertIn("if (session.publish?.in_progress) scheduleDeliverPoll(session, token);", panel)
        # The editable input is disabled whenever the poll loop could rebuild it.
        self.assertIn("data-deliver-slug", panel)
        slug_markup = next(
            line for line in panel.splitlines() if "input data-deliver-slug" in line
        )
        self.assertIn("busy || publish || setupNeeded ? 'disabled'", slug_markup)
