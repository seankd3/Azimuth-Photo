"""Activity classification — monitoring must never look like browsing."""

from __future__ import annotations

import unittest
from unittest import mock

from core.user_activity import marks_user_activity
from core import background as background_runtime


class UserActivityClassificationTests(unittest.TestCase):
    def test_browse_paths_mark_activity(self):
        for path in (
            "/api/rankings",
            "/api/thumb/sm/12",
            "/api/full/12",
            "/api/images/42",
            "/library",
        ):
            self.assertTrue(marks_user_activity(path), path)

    def test_status_health_counts_never_mark(self):
        for path in (
            "/api/cache/pregen/status",
            "/api/counts",
            "/api/sync/status",
            "/api/health/details",
            "/api/health",
            "/api/ai/status",
            "/api/auth/status",
            "/api/quality/status",
            "/api/geo/status",
            "/api/pair/status",
            "/api/catalog/metadata/status",
            "/api/system/integrity/status",
            "/api/settings",
            "/static/js/desktop/app.js",
            "/api/telemetry/ping",
        ):
            self.assertFalse(marks_user_activity(path), path)

    def test_suffix_rule_covers_future_status_endpoints(self):
        self.assertFalse(marks_user_activity("/api/future-worker/status"))
        self.assertFalse(marks_user_activity("/api/future/counts"))

    def test_middleware_skips_pregen_status_and_counts(self):
        noted = []

        class Thumb:
            def note_user_activity(self):
                noted.append(True)

        async def run():
            async def call_next(_request):
                return "ok"

            for path in ("/api/cache/pregen/status", "/api/counts", "/api/sync/status"):
                request = mock.Mock()
                request.url.path = path
                await background_runtime.track_idle_activity(
                    request,
                    call_next,
                    thumbnails=Thumb(),
                )
            request = mock.Mock()
            request.url.path = "/api/rankings"
            await background_runtime.track_idle_activity(
                request,
                call_next,
                thumbnails=Thumb(),
            )

        import asyncio

        asyncio.run(run())
        self.assertEqual(noted, [True])


if __name__ == "__main__":
    unittest.main()
