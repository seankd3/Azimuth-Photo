"""What counts as worth retrying, asked of the failure rather than its wording."""

from __future__ import annotations

import socket
import unittest
import urllib.error

from archive import transport


class TransientFailureTests(unittest.TestCase):
    def test_anything_the_network_raises_is_worth_retrying(self):
        for error in (
            urllib.error.URLError("connection refused"),
            TimeoutError("timed out"),
            ConnectionRefusedError(),
            socket.gaierror("name or service not known"),
            OSError("network is unreachable"),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertTrue(transport.is_transient(error))

    def test_a_rejection_is_not(self):
        for error in (
            RuntimeError("sync POST /api/sync/oplog/push failed (422): bad entry"),
            ValueError("invalid status payload"),
        ):
            with self.subTest(error=str(error)):
                self.assertFalse(transport.is_transient(error))

    def test_an_overloaded_hub_is_worth_retrying_though_its_message_says_nothing(self):
        """A 503 matched none of the six words the old rule searched for."""
        self.assertTrue(
            transport.is_transient(
                transport.HubUnavailable("sync GET /api/sync/catalog/export failed (503): ")
            )
        )

    def test_the_words_alone_decide_nothing(self):
        """A rejection that happens to contain 'timeout' is still a rejection."""
        self.assertFalse(
            transport.is_transient(RuntimeError("failed (400): timeout must be a number"))
        )


if __name__ == "__main__":
    unittest.main()
