"""Catching up must never freeze the app.

A migration on the hub once told a laptop that 150,000 rows had changed. The
mirror set about re-applying all of them and the grid stopped answering for a
minute — the local library is supposed to work regardless of what the hub is
doing, so a catch-up has to cost patience, not responsiveness.
"""

import asyncio
import unittest
from unittest import mock

from features.sync import mirror


class MirrorYieldTests(unittest.IsolatedAsyncioTestCase):
    async def test_it_stands_aside_while_someone_is_browsing(self):
        with mock.patch.dict("sys.modules", {"thumbnails": mock.Mock(get_idle_seconds=lambda: 0.0)}):
            with mock.patch("asyncio.sleep", new=mock.AsyncMock()) as slept:
                await mirror._yield_to_the_person_using_the_app()
        slept.assert_awaited_once()
        self.assertGreater(slept.await_args.args[0], 0, "a busy app must get the disk back")

    async def test_it_does_not_dawdle_on_an_idle_machine(self):
        with mock.patch.dict("sys.modules", {"thumbnails": mock.Mock(get_idle_seconds=lambda: 30.0)}):
            with mock.patch("asyncio.sleep", new=mock.AsyncMock()) as slept:
                await mirror._yield_to_the_person_using_the_app()
        slept.assert_awaited_once_with(0)

    async def test_no_activity_signal_still_yields(self):
        broken = mock.Mock()
        broken.get_idle_seconds.side_effect = RuntimeError("no thumbnail subsystem here")
        with mock.patch.dict("sys.modules", {"thumbnails": broken}):
            with mock.patch("asyncio.sleep", new=mock.AsyncMock()) as slept:
                await mirror._yield_to_the_person_using_the_app()
        slept.assert_awaited_once_with(0)

    async def test_the_event_loop_keeps_running_during_a_long_catch_up(self):
        """The real property: other work still gets scheduled mid-sync."""

        ticks = 0

        async def something_else():
            nonlocal ticks
            for _ in range(50):
                ticks += 1
                await asyncio.sleep(0)

        async def a_long_catch_up():
            with mock.patch.dict("sys.modules", {"thumbnails": mock.Mock(get_idle_seconds=lambda: 30.0)}):
                for _ in range(50):
                    await mirror._yield_to_the_person_using_the_app()

        other = asyncio.create_task(something_else())
        await a_long_catch_up()
        await other
        self.assertEqual(ticks, 50, "a catch-up that never yields would starve everything else")


if __name__ == "__main__":
    unittest.main()
