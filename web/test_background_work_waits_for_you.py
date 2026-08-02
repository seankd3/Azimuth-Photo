"""One rule about when background work may run, in one place.

Four chores — the catalog mirror, the preview catch-up, the phantom-preview
sweep, the taste blend — each grew its own idea of being considerate, with its
own threshold and its own copy of the clock. Four half-considerate chores add up
to an app that does not respond: measured on a 142,000-photo library, browsing
was 23ms with them quiet and minutes with them running.
"""

import asyncio
import pathlib
import time
import unittest

from core import user_activity

WEB = pathlib.Path(__file__).parent


class OneClockTests(unittest.TestCase):
    def setUp(self):
        user_activity.note_activity()

    def test_activity_makes_the_app_busy(self):
        self.assertTrue(user_activity.someone_is_here())

    def test_leaving_it_alone_makes_it_quiet(self):
        self.assertFalse(user_activity.someone_is_here(quiet_seconds=0.0))

    def test_the_thumbnail_clock_is_the_same_clock(self):
        """It used to keep its own, so a chore could disagree with the app."""

        import thumbnails

        thumbnails.note_user_activity()
        self.assertLess(abs(thumbnails.get_idle_seconds() - user_activity.idle_seconds()), 0.05)

    def test_a_chore_waits_while_someone_is_here(self):
        async def scenario():
            started = time.monotonic()
            waiter = asyncio.create_task(user_activity.wait_for_quiet(quiet_seconds=0.4))
            await asyncio.sleep(0.6)
            user_activity.note_activity()  # someone is still browsing
            await asyncio.sleep(0.3)
            await asyncio.wait_for(waiter, timeout=3)
            return time.monotonic() - started

        self.assertGreater(asyncio.run(scenario()), 0.9)

    def test_a_chore_gets_going_once_it_is_quiet(self):
        async def scenario():
            started = time.monotonic()
            await asyncio.wait_for(user_activity.wait_for_quiet(quiet_seconds=0.2), timeout=3)
            return time.monotonic() - started

        self.assertLess(asyncio.run(scenario()), 1.5)

    def test_the_thread_flavour_waits_too(self):
        started = time.monotonic()
        user_activity.wait_for_quiet_sync(quiet_seconds=0.3)
        self.assertGreater(time.monotonic() - started, 0.2)


class EveryChoreAsksTests(unittest.TestCase):
    """The point of one rule is that nobody keeps a private copy."""

    CHORES = {
        "features/sync/mirror.py": "the catalog mirror",
        "features/sync/prefetch.py": "the preview catch-up",
        "features/library/service.py": "the taste blend",
    }

    def test_each_chore_asks_the_shared_authority(self):
        for path, what in self.CHORES.items():
            text = (WEB / path).read_text(encoding="utf-8")
            self.assertIn("user_activity.wait_for_quiet", text, f"{what} must ask")

    def test_the_sweep_asks_it_too(self):
        text = (WEB / "thumbnails" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("user_activity.wait_for_quiet_sync", text)

    def test_nobody_keeps_a_private_idle_clock(self):
        for path in list(self.CHORES) + ["thumbnails/__init__.py"]:
            text = (WEB / path).read_text(encoding="utf-8")
            self.assertNotIn("_last_user_activity = time.monotonic()", text)
            self.assertNotIn("_wait_while_someone_is_browsing", text)

    def test_the_catch_up_re_asks_inside_a_pack(self):
        """A pack is 500 previews; asking once per pack is asking too rarely."""

        text = (WEB / "features" / "sync" / "prefetch.py").read_text(encoding="utf-8")
        self.assertIn("_QUIET_CHECK_EVERY", text)
        self.assertIn("stored % _QUIET_CHECK_EVERY", text)


if __name__ == "__main__":
    unittest.main()
