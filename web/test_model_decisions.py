"""The decisions log: the one fact that cannot be recomputed.

These tests are about not losing what the owner said. A decision survives being
overwritten, being undone, and being made about something that is not a photo.
"""

import os
import sqlite3
import unittest

from model import decisions, drives


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        schema = os.path.join(os.path.dirname(drives.__file__), "schema.sql")
        with open(schema, encoding="utf-8") as handle:
            self.conn.executescript(handle.read())
        self.addCleanup(self.conn.close)

    def test_the_last_word_wins(self):
        decisions.decide(self.conn, "abc", decisions.STATUS, "kept", at=1.0)
        decisions.decide(self.conn, "abc", decisions.STATUS, "trashed", at=2.0)
        self.assertEqual(decisions.latest(self.conn, "abc", decisions.STATUS), "trashed")

    def test_changing_your_mind_never_erases_what_you_said(self):
        decisions.decide(self.conn, "abc", decisions.STAR, 5, at=1.0)
        decisions.decide(self.conn, "abc", decisions.STAR, 2, at=2.0)
        said = [row["value"] for row in decisions.history(self.conn, "abc")]
        self.assertEqual(said, [2, 5])

    def test_two_decisions_in_the_same_instant_keep_their_order(self):
        # A clock with millisecond resolution will hand out the same timestamp
        # twice under a fast keyboard. The later row is still the later answer.
        decisions.decide(self.conn, "abc", decisions.STATUS, "maybe", at=7.0)
        decisions.decide(self.conn, "abc", decisions.STATUS, "kept", at=7.0)
        self.assertEqual(decisions.latest(self.conn, "abc", decisions.STATUS), "kept")

    def test_a_subject_is_any_stable_identity(self):
        # A folder, a drive and a person are decided about exactly like a photo
        # is. This is what killed the fake all-zeros content hash that a
        # collection needed in order to be a row.
        decisions.decide(self.conn, "Raws/Digital/2026", decisions.NAME, "Iceland")
        decisions.decide(self.conn, "abf9ae36-drive-uuid", decisions.NAME, "Archive")
        decisions.decide(self.conn, "person:mum", decisions.NAME, "Mum")
        self.assertEqual(decisions.latest(self.conn, "Raws/Digital/2026", decisions.NAME), "Iceland")
        self.assertEqual(decisions.latest(self.conn, "person:mum", decisions.NAME), "Mum")

    def test_a_decision_holds_a_whole_edit(self):
        edit = {"exposure": 0.4, "crop": [0, 0, 1, 1], "masks": [{"kind": "radial"}]}
        decisions.decide(self.conn, "abc", decisions.DEVELOP, edit)
        self.assertEqual(decisions.latest(self.conn, "abc", decisions.DEVELOP), edit)

    def test_edit_history_is_the_log_filtered_to_one_photo(self):
        decisions.decide(self.conn, "abc", decisions.DEVELOP, {"exposure": 0.1}, at=1.0)
        decisions.decide(self.conn, "abc", decisions.STAR, 4, at=2.0)
        decisions.decide(self.conn, "abc", decisions.DEVELOP, {"exposure": 0.5}, at=3.0)
        edits = decisions.history(self.conn, "abc", family=decisions.DEVELOP)
        self.assertEqual([row["value"] for row in edits], [{"exposure": 0.5}, {"exposure": 0.1}])

    def test_undo_says_the_earlier_thing_again(self):
        decisions.decide(self.conn, "abc", decisions.STATUS, "kept", at=1.0)
        decisions.decide(self.conn, "abc", decisions.STATUS, "trashed", at=2.0)
        self.assertEqual(decisions.undo(self.conn, "abc", decisions.STATUS), "kept")
        self.assertEqual(decisions.latest(self.conn, "abc", decisions.STATUS), "kept")
        # And the trashing is still on the record, because undo appends.
        self.assertEqual(len(decisions.history(self.conn, "abc")), 3)

    def test_undo_with_nothing_behind_it_changes_nothing(self):
        decisions.decide(self.conn, "abc", decisions.STATUS, "kept")
        self.assertIsNone(decisions.undo(self.conn, "abc", decisions.STATUS))
        self.assertEqual(len(decisions.history(self.conn, "abc")), 1)

    def test_the_index_is_rebuilt_in_one_pass(self):
        decisions.decide(self.conn, "a", decisions.STATUS, "kept", at=1.0)
        decisions.decide(self.conn, "b", decisions.STATUS, "kept", at=1.0)
        decisions.decide(self.conn, "b", decisions.STATUS, "trashed", at=2.0)
        decisions.decide(self.conn, "c", decisions.STAR, 3, at=1.0)
        self.assertEqual(
            decisions.current(self.conn, decisions.STATUS), {"a": "kept", "b": "trashed"}
        )

    def test_a_decision_without_a_subject_is_refused(self):
        # Silently accepting one would file it under "", where it is neither
        # findable nor attached to anything.
        with self.assertRaises(ValueError):
            decisions.decide(self.conn, "  ", decisions.STATUS, "kept")

    def test_never_deciding_reads_as_None_not_as_a_default(self):
        self.assertIsNone(decisions.latest(self.conn, "unseen", decisions.STATUS))


if __name__ == "__main__":
    unittest.main()
