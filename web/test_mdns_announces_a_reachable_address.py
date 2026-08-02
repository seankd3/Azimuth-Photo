"""A hub must advertise an address it actually answers on.

Measured on the owner's hub: it serves only its Tailscale address, and the
announcer picked the default-route address instead — so the one result a laptop
found when it scanned the network pointed at 192.168.1.72:8000, where nothing
was listening. Clicking it fails, and there is nothing on screen to explain why.
"""

import socket
import threading
import unittest

from features.sync import mdns


class ReachableAddressTests(unittest.TestCase):
    def setUp(self):
        # A real listener on the loopback address only, standing in for a hub
        # bound to one interface out of several.
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.stop.set()
        self.server.close()

    def _accept_loop(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            connection.close()

    def test_the_address_that_answers_is_the_one_announced(self):
        original = mdns._candidate_ipv4s
        mdns._candidate_ipv4s = lambda: ["10.255.255.1", "127.0.0.1"]
        try:
            self.assertEqual(mdns._reachable_ipv4(self.port), "127.0.0.1")
        finally:
            mdns._candidate_ipv4s = original

    def test_a_port_nothing_serves_falls_back_rather_than_going_silent(self):
        """Announcing the old guess beats a hub no one can discover at all."""

        closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        closed.bind(("127.0.0.1", 0))
        free_port = closed.getsockname()[1]
        closed.close()
        self.assertTrue(mdns._reachable_ipv4(free_port))

    def test_an_address_with_no_listener_is_not_accepted(self):
        self.assertFalse(mdns._accepts("10.255.255.1", self.port, timeout=0.2))

    def test_the_default_route_is_tried_first(self):
        self.assertEqual(mdns._candidate_ipv4s()[0], mdns._local_ipv4())


if __name__ == "__main__":
    unittest.main()
