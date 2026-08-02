"""A repair that runs at startup must not stop you seeing your photos.

The phantom-preview sweep asks the filesystem about every cached preview — one
call each, and this laptop holds 121,826 of them. It did that while holding the
same lock the grid needs to know whether a photo has a preview yet, so opening
the app started a repair and browsing queued behind it. Measured on the owner's
laptop: the first page of photos never arrived at all.
"""

import re
import sqlite3
import threading
import unittest

from thumbnails import maintenance

SCHEMA = """
CREATE TABLE cache_entries (
    image_id INTEGER NOT NULL,
    size TEXT NOT NULL,
    cache_root TEXT NOT NULL,
    path TEXT NOT NULL
);
"""


class _LoudLock:
    """A lock that records how much work happened while it was held."""

    def __init__(self):
        self._lock = threading.Lock()
        self.held = False
        self.checks_while_held = 0
        self.times_taken = 0

    def __enter__(self):
        self._lock.acquire()
        self.held = True
        self.times_taken += 1
        return self

    def __exit__(self, *_exc):
        self.held = False
        self._lock.release()
        return False


class _KeptOpen:
    """db_connect() hands out a connection the caller may close; this one survives."""

    def __init__(self, connection):
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def close(self):
        pass


class SweepLockTests(unittest.TestCase):
    def setUp(self):
        self.raw = sqlite3.connect(":memory:", check_same_thread=False)
        self.raw.row_factory = sqlite3.Row
        self.raw.executescript(SCHEMA)
        self.raw.executemany(
            "INSERT INTO cache_entries(image_id, size, cache_root, path) VALUES (?, 'sm', 'root', ?)",
            [(i, f"C:/cache/{i}.jpg") for i in range(1, 121)],
        )
        self.raw.commit()
        self.lock = _LoudLock()
        self.removed_rows = []

    def _sweep(self, present, **kwargs):
        def path_exists(path: str) -> bool:
            self.lock.checks_while_held += 1 if self.lock.held else 0
            return present(path)

        def remove(conn, row):
            self.removed_rows.append(int(row["image_id"]))
            conn.execute("DELETE FROM cache_entries WHERE rowid = ?", (row["rowid"],))

        return maintenance.sweep_missing_cache_entries(
            meta_lock=self.lock,
            db_connect=lambda: _KeptOpen(self.raw),
            remove_cache_entry_locked=remove,
            path_exists=path_exists,
            batch_size=50,
            **kwargs,
        )

    def test_no_file_is_checked_while_the_lock_is_held(self):
        """The whole point: browsing must not queue behind a filesystem walk."""

        self._sweep(lambda path: True)
        self.assertEqual(self.lock.checks_while_held, 0)

    def test_rows_pointing_at_missing_files_are_still_removed(self):
        result = self._sweep(lambda path: not path.endswith(("7.jpg", "8.jpg")))
        self.assertEqual(result["removed"], len(self.removed_rows))
        self.assertGreater(result["removed"], 0)
        left = self.raw.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
        self.assertEqual(left, 120 - result["removed"])

    def test_every_row_is_examined_once(self):
        result = self._sweep(lambda path: True)
        self.assertEqual(result["scanned"], 120)
        self.assertEqual(result["removed"], 0)

    def test_a_library_with_nothing_missing_deletes_nothing(self):
        self._sweep(lambda path: True)
        self.assertEqual(self.removed_rows, [])

    def test_it_stops_where_it_was_told_to(self):
        result = self._sweep(lambda path: True, max_batches=1)
        self.assertEqual(result["batches"], 1)
        self.assertEqual(result["scanned"], 50)

    def test_it_steps_politely_rather_than_running_flat_out(self):
        """A chore with tens of thousands of rows must give way as it goes."""

        waits = []
        maintenance.sweep_missing_cache_entries(
            meta_lock=self.lock,
            db_connect=lambda: _KeptOpen(self.raw),
            remove_cache_entry_locked=lambda conn, row: None,
            path_exists=lambda path: True,
            batch_size=50,
            step_politely=lambda items: (waits.append(1) or items),
        )
        self.assertGreater(len(waits), 0, "the deletes must go through the shared courtesy")

    def test_the_default_courtesy_needs_no_argument(self):
        """A caller supplies nothing; the shared one is the default."""

        result = self._sweep(lambda path: True)
        self.assertEqual(result["scanned"], 120)

    def test_the_lock_is_never_held_for_a_whole_batch_of_deletes(self):
        """A request already waiting for this lock does not look like activity,
        so the sweep will take it straight back — it must hold it only briefly."""

        holds = []
        original_enter = type(self.lock).__enter__

        def counting_enter(lock):
            holds.append(0)
            return original_enter(lock)

        def remove(conn, row):
            holds[-1] += 1

        type(self.lock).__enter__ = counting_enter
        try:
            maintenance.sweep_missing_cache_entries(
                meta_lock=self.lock,
                db_connect=lambda: _KeptOpen(self.raw),
                remove_cache_entry_locked=remove,
                path_exists=lambda path: False,
                batch_size=120,
            )
        finally:
            type(self.lock).__enter__ = original_enter
        self.assertLessEqual(max(holds), 1, "the lock is held for one row at a time")
        self.assertEqual(sum(holds), 120, "every missing row is still removed")

    def test_an_empty_cache_is_not_an_error(self):
        self.raw.execute("DELETE FROM cache_entries")
        self.raw.commit()
        result = self._sweep(lambda path: True)
        self.assertEqual((result["scanned"], result["removed"]), (0, 0))


class TheWrapperAgreesWithTheSweepTests(unittest.TestCase):
    """A rename that misses one caller is a crash at runtime, not a red test.

    Every test here calls the implementation directly, so renaming one of its
    parameters and forgetting the wrapper stayed green and broke the app. This
    compares the two without needing the app wired up.
    """

    def test_the_wrapper_passes_only_arguments_the_sweep_accepts(self):
        import inspect

        import thumbnails
        from thumbnails import maintenance

        accepted = set(inspect.signature(maintenance.sweep_missing_cache_entries).parameters)
        source = inspect.getsource(thumbnails.sweep_missing_cache_entries)
        passed = set(re.findall(r"^\s*(\w+)=", source, re.M))
        self.assertTrue(passed <= accepted, f"wrapper passes unknown: {passed - accepted}")


if __name__ == "__main__":
    unittest.main()
