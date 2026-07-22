import unittest
from pathlib import Path


WEB_DIR = Path(__file__).resolve().parent


class SearchExperienceTests(unittest.TestCase):
    def test_search_has_one_automatic_quality_path(self):
        omnibox = (WEB_DIR / "static/js/desktop/omnibox.js").read_text(encoding="utf-8")
        contextbar = (WEB_DIR / "static/js/desktop/contextbar.js").read_text(encoding="utf-8")
        grid = (WEB_DIR / "static/js/desktop/grid.js").read_text(encoding="utf-8")
        state = (WEB_DIR / "static/js/desktop/state.js").read_text(encoding="utf-8")

        self.assertNotIn("data-deep-toggle", omnibox)
        self.assertNotIn("Try Deep search", grid)
        self.assertNotIn("Deep search", contextbar)
        self.assertIn("applyScope({ q: term, deep: true, sort: 'similarity' })", omnibox)
        self.assertIn("next.deep = Boolean(next.q)", state)

    def test_live_preview_does_not_block_on_committed_search_mode(self):
        omnibox = (WEB_DIR / "static/js/desktop/omnibox.js").read_text(encoding="utf-8")
        live_search = omnibox.split("function scheduleLiveSearch()", 1)[1]

        self.assertNotIn("params.set('deep'", live_search)


if __name__ == "__main__":
    unittest.main()
