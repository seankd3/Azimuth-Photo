import os
import unittest


class MobileSmartScopeContractsTests(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(os.path.dirname(__file__), *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_smart_chip_only_suppresses_facets_it_still_owns(self):
        timeline = self.read("static", "js", "mobile", "timeline.js")

        self.assertIn("const SMART_SCOPE_FIELDS = {", timeline)
        self.assertIn("scope[scopeKey] === String(smartQuery[queryKey])", timeline)
        self.assertIn("!smartSets('flag')", timeline)


if __name__ == "__main__":
    unittest.main()
