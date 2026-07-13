"""Unit coverage for Reveal-in-file-manager path validation and argv building."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from features.catalog import reveal


class RevealPathValidationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pa-reveal-root-")
        self.nested = os.path.join(self.root, "Trip", "Day")
        os.makedirs(self.nested)
        self.outside = tempfile.mkdtemp(prefix="pa-reveal-out-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)

    def test_in_root_allowed_and_out_of_root_rejected(self):
        roots = [self.root]
        self.assertTrue(reveal.path_is_under_roots(self.root, roots))
        self.assertTrue(reveal.path_is_under_roots(self.nested, roots))
        self.assertFalse(reveal.path_is_under_roots(self.outside, roots))
        self.assertFalse(reveal.path_is_under_roots("/etc", roots))

    def test_reveal_folder_rejects_outside_and_accepts_nested(self):
        calls = []

        def runner(argv):
            calls.append(list(argv))

        denied = reveal.reveal_folder(
            self.outside,
            [self.root],
            platform_name="linux",
            environ={"DISPLAY": ":0"},
            runner=runner,
            which=lambda _name: "/usr/bin/xdg-open",
        )
        self.assertFalse(denied["ok"])
        self.assertIn("outside", denied["error"].lower())
        self.assertEqual(calls, [])

        allowed = reveal.reveal_folder(
            self.nested,
            [self.root],
            platform_name="linux",
            environ={"DISPLAY": ":0"},
            runner=runner,
            which=lambda _name: "/usr/bin/xdg-open",
        )
        self.assertTrue(allowed["ok"])
        self.assertEqual(calls, [["xdg-open", reveal.normalize_path(self.nested)]])


class RevealArgvTests(unittest.TestCase):
    def test_argv_per_platform(self):
        path = "/photos/archive"
        self.assertEqual(reveal.reveal_argv(path, "win32"), ["explorer", reveal.normalize_path(path)])
        self.assertEqual(reveal.reveal_argv(path, "darwin"), ["open", reveal.normalize_path(path)])
        self.assertEqual(reveal.reveal_argv(path, "linux"), ["xdg-open", reveal.normalize_path(path)])

    def test_headless_linux_noops_with_message(self):
        with tempfile.TemporaryDirectory() as root:
            result = reveal.reveal_folder(
                root,
                [root],
                platform_name="linux",
                environ={},
                runner=lambda _argv: (_ for _ in ()).throw(AssertionError("should not run")),
                which=lambda _name: "/usr/bin/xdg-open",
            )
        self.assertFalse(result["ok"])
        self.assertIn("graphical", result["error"].lower())

    def test_mocked_runner_receives_platform_argv(self):
        with tempfile.TemporaryDirectory() as root:
            for platform_name, expected_bin in (
                ("win32", "explorer"),
                ("darwin", "open"),
                ("linux", "xdg-open"),
            ):
                with self.subTest(platform=platform_name):
                    seen = []
                    result = reveal.reveal_folder(
                        root,
                        [root],
                        platform_name=platform_name,
                        environ={"DISPLAY": ":0"},
                        runner=lambda argv: seen.append(list(argv)),
                        which=lambda name: f"/bin/{name}",
                    )
                    self.assertTrue(result["ok"])
                    self.assertEqual(seen, [[expected_bin, reveal.normalize_path(root)]])


if __name__ == "__main__":
    unittest.main()
