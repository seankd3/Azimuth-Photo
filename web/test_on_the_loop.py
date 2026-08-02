"""Work that starts on a thread comes back to the app's loop, not a new one.

`asyncio.run()` in a worker thread builds a loop, runs the coroutine and
destroys the loop. An aiosqlite connection opened there is a thread plus an
open catalog handle whose loop no longer exists, so nothing can ever close it —
the gallery ZIP builder did this once per image.
"""

import asyncio
import threading
import unittest

from core import on_the_loop


class RunFromAThreadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        on_the_loop.remember_the_loop()

    async def test_it_runs_on_the_remembered_loop(self):
        here = asyncio.get_running_loop()
        seen = {}

        async def which_loop():
            seen["loop"] = asyncio.get_running_loop()
            return "done"

        result = await asyncio.to_thread(on_the_loop.run, which_loop())
        self.assertEqual(result, "done")
        self.assertIs(seen["loop"], here, "a second loop would strand what it opens")

    async def test_an_exception_reaches_the_caller(self):
        async def fails():
            raise ValueError("from the loop")

        with self.assertRaises(ValueError):
            await asyncio.to_thread(on_the_loop.run, fails())

    async def test_calling_it_from_the_loop_is_refused(self):
        """Blocking on the loop for the loop deadlocks; say so instead."""

        async def anything():
            return 1

        with self.assertRaises(RuntimeError):
            on_the_loop.run(anything())


class WithoutAnAppTests(unittest.TestCase):
    def test_a_script_with_no_app_loop_still_works(self):
        """A CLI has no shared loop, so a private one strands nothing."""

        on_the_loop._loop = None

        async def value():
            return 7

        out = {}
        thread = threading.Thread(target=lambda: out.setdefault("v", on_the_loop.run(value())))
        thread.start()
        thread.join()
        self.assertEqual(out["v"], 7)


if __name__ == "__main__":
    unittest.main()
