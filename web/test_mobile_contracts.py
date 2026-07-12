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
        self.assertNotIn("'#mv-pick'", self.read("static", "js", "mobile", "bootstrap.js"))

    def test_loupe_swipes_cull_without_replacing_navigation(self):
        viewer = self.read("static", "js", "mobile", "viewer.js")

        self.assertIn("cullSwipe('picked')", viewer)
        self.assertIn("cullSwipe('rejected')", viewer)
        self.assertIn("const dir = dx < 0 ? 1 : -1", viewer)
        self.assertIn("flagBadge.textContent = flag === 'picked' ? 'Picked' : 'Rejected'", viewer)


if __name__ == "__main__":
    unittest.main()
