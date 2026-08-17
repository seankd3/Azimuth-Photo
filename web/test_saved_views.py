"""A saved view is a set with a query on it.

The old test drove HTTP against a bare `FastAPI()` carrying one router, which
worked only because the routes opened their own connections. They do not any
more — reads take the app's WAL reader — so the test was asserting against an
app the product never builds.

What is worth holding is the round trip, and it is a `model.sets` property now:
a view is created, listed among views and not among collections, renamed without
losing its query, and forgotten. Eight lines of HTTP sit on top of that.
"""

from __future__ import annotations

import re
import sqlite3
import pathlib
import tempfile
import unittest

from model import sets

SNAPSHOT = '{"scope":{"folder":["2026"]},"layout":{"density":"cosy"}}'


class SavedViewsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.conn = sqlite3.connect(pathlib.Path(self.dir.name) / "t.db")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        core = re.sub(r"--[^\n]*", "", (pathlib.Path(__file__).parent / "model" / "schema.sql").read_text(encoding="utf-8"))
        for statement in [s.strip() for s in core.split(";") if "decisions" in s and s.strip()]:
            self.conn.execute(statement)

    def test_saved_view_round_trip(self):
        view = sets.create(self.conn, "Winter selects", kind=sets.VIEW, query=SNAPSHOT)
        sets.create(self.conn, "Portfolio", kind=sets.COLLECTION)

        views = sets.all(self.conn, kind=sets.VIEW)
        self.assertEqual([v["id"] for v in views], [view])
        self.assertEqual(views[0]["query"], SNAPSHOT)
        self.assertEqual([c["name"] for c in sets.all(self.conn, kind=sets.COLLECTION)], ["Portfolio"])

        renamed = sets.amend(self.conn, view, name="Winter keepers")
        self.assertEqual(renamed["name"], "Winter keepers")
        self.assertEqual(renamed["query"], SNAPSHOT, "a rename must not drop the query")

        sets.forget(self.conn, view)
        self.assertEqual(sets.all(self.conn, kind=sets.VIEW), [])
