"""Regression gates for desktop interaction correctness fixes."""

from pathlib import Path
import unittest


WEB = Path(__file__).parent


def read(*parts):
    return (WEB / "static" / "js" / "desktop").joinpath(*parts).read_text(encoding="utf-8")


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
