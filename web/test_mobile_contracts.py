import os
import unittest

class MobileOfflineContractsTests(unittest.TestCase):
    def setUp(self):
        self.base_dir = os.path.dirname(__file__)

    def read(self, *parts):
        with open(os.path.join(self.base_dir, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_flag_writes_use_the_durable_mobile_queue(self):
        api = self.read("static", "js", "mobile", "api.js")
        queue = self.read("static", "js", "mobile", "write_queue.js")

        self.assertIn("enqueueWrite(`/api/image/${imageId}/flag`, { flag })", api)
        self.assertIn("enqueueWrite('/api/images/flag', { image_ids: imageIds, flag })", api)
        self.assertIn("localStorage.setItem(STORAGE_KEY", queue)
        self.assertIn("BASE_RETRY_MS", queue)
        self.assertIn("window.addEventListener('online'", queue)
        self.assertIn("registration.sync.register(WRITE_SYNC_TAG)", queue)
        self.assertIn("drain-write-queue", queue)
        self.assertNotIn("write-outcome", queue)
        self.assertIn("emit('flag-write'", api)
        self.assertIn("emit('rating-write'", api)
        self.assertNotIn("'#mv-pick'", self.read("static", "js", "mobile", "bootstrap.js"))

    def test_service_worker_is_secure_only_and_versioned(self):
        template = self.read("templates", "mobile.html")
        service_worker = self.read("static", "sw.js")
        manifest = self.read("static", "manifest.webmanifest")

        self.assertIn("window.isSecureContext", template)
        self.assertIn("/sw.js?v=", template)
        self.assertIn("data-static-version", template)
        self.assertIn("searchParams.get('v')", service_worker)
        self.assertIn("'/static/js/mobile/write_queue.js'", service_worker)
        self.assertIn("pa-write-queue", service_worker)
        self.assertIn('"display": "standalone"', manifest)
        self.assertIn('"theme_color": "#141517"', manifest)
        self.assertIn("/static/icons/icon.svg", manifest)

    def test_https_origin_helper_keeps_http_usable(self):
        helper = self.read("static", "js", "mobile", "https_origin.js")
        bootstrap = self.read("static", "js", "mobile", "bootstrap.js")
        self.assertIn("buildHttpsOrigin", helper)
        self.assertIn("/api/remote-access", helper)
        self.assertIn("resolveSecureAppUrl", bootstrap)
        self.assertIn("fallbackSecureAppUrl", bootstrap)

    def test_loupe_vertical_swipes_favorite_or_close_without_replacing_navigation(self):
        viewer = self.read("static", "js", "mobile", "viewer.js")

        self.assertIn("function favoriteSwipe()", viewer)
        self.assertIn("void applyFlags([image.id], 'picked');", viewer)
        self.assertIn("settleDismissSwipe();", viewer)
        self.assertIn("favoriteSwipe();", viewer)
        self.assertIn("const dir = dx < 0 ? 1 : -1", viewer)
        self.assertIn("flagBadge.textContent = flag === 'picked' ? 'Favorited' : 'Rejected'", viewer)

    def test_search_flattens_grouped_people_before_rendering(self):
        search = self.read("static", "js", "mobile", "search.js")

        self.assertIn("function flattenPeople(data)", search)
        self.assertIn("sections.named_people", search)
        self.assertIn("people = { people: flattenPeople(data) }", search)

    def test_background_lease_waits_remain_active_on_mobile(self):
        library = self.read("static", "js", "mobile", "library.js")

        for state in ("waiting_for_gpu", "waiting_for_turn", "waiting_retry", "waiting_for_model"):
            self.assertIn(f"'{state}'", library)

    def test_smart_collection_scopes_hide_membership_actions(self):
        state = self.read("static", "js", "mobile", "state.js")
        library = self.read("static", "js", "mobile", "library.js")
        selection = self.read("static", "js", "mobile", "selection.js")

        self.assertIn("collectionSmart: false", state)
        self.assertIn("collectionSmart: Boolean(coll.smart)", library)
        self.assertIn("scope.collectionSmart ? ''", selection)
        self.assertIn(".filter((collection) => !collection.smart)", selection)


if __name__ == "__main__":
    unittest.main()
