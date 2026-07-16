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

    def test_clearing_a_smart_owned_facet_restores_its_smart_value(self):
        timeline = self.read("static", "js", "mobile", "timeline.js")

        self.assertIn(
            """const smartQueryKey = Object.keys(SMART_SCOPE_FIELDS).find(
                (queryKey) => SMART_SCOPE_FIELDS[queryKey] === field,
            );
            const smartValue = smartQueryKey ? smartQuery[smartQueryKey] : undefined;
            if (scope.smartName && smartValue !== undefined && smartValue !== null && smartValue !== '') {
                scope[field] = String(smartValue);
            } else {
                scope[field] = '';
            }""",
            timeline,
        )

    def test_search_replaces_a_local_similar_scope(self):
        search = self.read("static", "js", "mobile", "search.js")

        self.assertIn(
            """function applySearchScope(patch) {
    if (scope.similarId || scope.similarImages) {
        setScope(patch);
    } else if (scopeActive()) {
        patchScope(patch);
    } else {
        setScope(patch);
    }
}""",
            search,
        )
        self.assertEqual(search.count("applySearchScope({"), 2)

    def test_smart_chip_retains_its_collection_actions_without_becoming_a_collection_scope(self):
        state = self.read("static", "js", "mobile", "state.js")
        library = self.read("static", "js", "mobile", "library.js")
        timeline = self.read("static", "js", "mobile", "timeline.js")

        self.assertIn("smartCollectionId: ''", state)
        self.assertIn("scope.smartCollectionId = '';", state)
        self.assertIn("smartCollectionId: String(coll.id)", library)
        self.assertIn("if (coll.smart) patchScope({ smartName: next });", library)
        self.assertIn("const smartChip =", timeline)
        self.assertIn("const actionChip = scope.smartCollectionId ? smartChip : collectionChip;", timeline)
        self.assertIn("id: scope.smartCollectionId || scope.collectionId", timeline)


if __name__ == "__main__":
    unittest.main()
