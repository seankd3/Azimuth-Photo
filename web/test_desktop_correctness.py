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
