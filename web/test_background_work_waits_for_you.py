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


class PolitelyTests(unittest.TestCase):
    """The courtesy, as one sentence a chore wraps its own loop in."""

    def test_it_gives_way_before_the_first_step(self):
        gave_way = []

        async def scenario():
            user_activity.note_activity()
            work = user_activity.politely(range(3), every=1)
            task = asyncio.create_task(_drain(work, gave_way))
            await asyncio.sleep(0.4)
            started = len(gave_way)
            user_activity.note_activity(time.monotonic() - 99)
            await asyncio.wait_for(task, timeout=3)
            return started

        self.assertEqual(asyncio.run(scenario()), 0, "it began while someone was here")

    def test_every_item_still_arrives(self):
        seen = []
        user_activity.note_activity(time.monotonic() - 99)
        asyncio.run(_drain(user_activity.politely(range(7)), seen))
        self.assertEqual(seen, list(range(7)))

    def test_the_thread_flavour_yields_the_same_items(self):
        user_activity.note_activity(time.monotonic() - 99)
        self.assertEqual(list(user_activity.politely_sync(range(5))), list(range(5)))

    def test_an_empty_chore_does_nothing_at_all(self):
        user_activity.note_activity()
        self.assertEqual(list(user_activity.politely_sync([])), [])


async def _drain(source, into):
    async for item in source:
        into.append(item)


class EveryChoreSaysItTheSameWayTests(unittest.TestCase):
    """One sentence, three chores, no private counters or thresholds."""

    CHORES = {
        "features/sync/mirror.py": "the catalog mirror",
        "features/sync/prefetch.py": "the preview catch-up",
        "thumbnails/maintenance.py": "the phantom-preview sweep",
    }

    def test_each_chore_steps_politely(self):
        for path, what in self.CHORES.items():
            text = (WEB / path).read_text(encoding="utf-8")
            self.assertRegex(text, r"politely(_sync)?\(", f"{what} must step politely")

    def test_the_taste_blend_waits_before_it_starts(self):
        text = (WEB / "features" / "library" / "service.py").read_text(encoding="utf-8")
        self.assertIn("user_activity.wait_for_quiet", text)

    def test_no_chore_keeps_its_own_threshold_or_counter(self):
        for path in list(self.CHORES) + ["thumbnails/__init__.py"]:
            text = (WEB / path).read_text(encoding="utf-8")
            for private in ("_QUIET_CHECK_EVERY", "_DELETE_GROUP", "_wait_while_someone_is_browsing"):
                self.assertNotIn(private, text, f"{path} kept {private}")

    def test_nobody_keeps_a_private_idle_clock(self):
        for path in list(self.CHORES) + ["thumbnails/__init__.py"]:
            text = (WEB / path).read_text(encoding="utf-8")
            self.assertNotIn("_last_user_activity = time.monotonic()", text)

    def test_there_is_exactly_one_number(self):
        text = (WEB / "core" / "user_activity.py").read_text(encoding="utf-8")
        self.assertEqual(text.count("STEPS_BETWEEN_PAUSES = "), 1)


if __name__ == "__main__":
    unittest.main()
