"""Regression gates for desktop interaction correctness fixes."""

from pathlib import Path
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

        self.assertIn("if (Number(api.getImageId?.()) !== Number(imageId)) return;", history)
        self.assertLess(
            history.index("if (Number(api.getImageId?.()) !== Number(imageId)) return;"),
            history.index("controller.setHistory(fresh);"),
        )

    def test_flag_scope_reloads_after_a_committed_membership_change(self):
        grid = read("grid.js")

        self.assertIn("on('flags', ({ imageIds, committed } = {}) =>", grid)
        self.assertIn("if (committed && scope.flag", grid)
        self.assertIn("viewState.images.some((image) =>", grid)
        self.assertIn("loadFirstPage();", grid[grid.index("if (committed && scope.flag"):])

    def test_loupe_removes_trashed_photos_from_grid_and_session_lists(self):
        loupe = read("loupe.js")

        self.assertIn("function discardTrashedImages(imageIds)", loupe)
        self.assertIn("viewState.images = viewState.images.filter", loupe)
        self.assertIn("sessionImages = sessionImages.filter", loupe)
        self.assertIn("if (!remaining.length) {\n        closeLoupe();", loupe)
        self.assertIn("on('trash:changed', ({ imageIds } = {}) => discardTrashedImages(imageIds));", loupe)

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

    def test_keep_covers_button_is_reenabled_if_stack_reload_fails(self):
        duplicates = read("duplicates.js")
        action_start = duplicates.index("async function keepCoversEverywhere()")
        action = duplicates[action_start:duplicates.index("\n}\n", action_start)]

        self.assertIn("await reloadStacks();", action)
        self.assertIn("finally {\n        if (button.isConnected) button.disabled = false;", action)
        self.assertLess(action.index("await reloadStacks();"), action.index("if (button.isConnected) button.disabled = false;"))

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

    def test_sync_refresh_cannot_overwrite_an_in_flight_control_result(self):
        sync_chip = read("sync_chip.js")

        self.assertIn("let controlInFlight = 0;", sync_chip)
        self.assertIn("let statusGeneration = 0;", sync_chip)
        self.assertIn("if (controlInFlight || generation !== statusGeneration) return;", sync_chip)
        self.assertIn("const generation = ++statusGeneration;", sync_chip)

    def test_worker_action_ignores_stale_poll_paint_for_its_row(self):
        drawer = read("drawer.js")

        self.assertIn("const workerActionGenerations = new Map();", drawer)
        self.assertIn("const workerActionsInFlight = new Set();", drawer)
        self.assertIn("const workerGenerations = new Map(workerActionGenerations);", drawer)
        self.assertIn("workerActionsInFlight.has(item.key)", drawer)
        self.assertIn("workerActionGenerations.set(key, (workerActionGenerations.get(key) || 0) + 1);", drawer)
