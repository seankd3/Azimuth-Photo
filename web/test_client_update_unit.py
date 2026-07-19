"""Unit coverage for satellite client auto-update primitives."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from features.sync import client_update


def _tar_gz_with(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class Sha256RejectionTests(unittest.TestCase):
    def test_corrupt_bundle_is_discarded_and_status_retries(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "versions" / "bbb").mkdir(parents=True)
                client_update.atomic_write_text(root / "current.txt", str(root / "versions" / "bbb") + "\n")
                good = _tar_gz_with({"web/requirements.txt": b"fastapi\n"})
                expected = hashlib.sha256(good).hexdigest()
                corrupt = good[:-1] + bytes([(good[-1] ^ 0xFF)])

                async def request(method, url, *, body=None, headers=None):
                    self.assertIn("/api/client/bundle", url)
                    return 200, {}, corrupt

                restarts: list[str] = []
                updater = client_update.ClientUpdater(
                    install_root=root,
                    hub="http://hub",
                    local_sha="bbb",
                    request=request,
                    restart=lambda: restarts.append("go"),
                )
                await updater.consider_hub_version({"sha": "aaa", "bundle_sha256": expected})
                self.assertEqual(updater.status.state, client_update.STATUS_RETRY)
                self.assertEqual(updater.status.message, client_update.UI_RETRY)
                self.assertFalse((root / "versions" / "aaa").exists())
                self.assertEqual(
                    (root / "current.txt").read_text(encoding="utf-8").strip(),
                    str(root / "versions" / "bbb"),
                )
                self.assertEqual(restarts, [])

        asyncio.run(scenario())


class PointerFlipAtomicityTests(unittest.TestCase):
    def test_crash_between_write_and_rename_leaves_old_pointer_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "versions" / "oldsha"
            new = root / "versions" / "newsha"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            pointer = root / "current.txt"
            client_update.atomic_write_text(pointer, str(old) + "\n")

            real_replace = os.replace

            def crash_before_replace(src, dst):
                raise OSError("simulated crash before rename")

            with mock.patch("features.sync.client_update.os.replace", side_effect=crash_before_replace):
                with self.assertRaises(OSError):
                    client_update.atomic_write_text(pointer, str(new) + "\n")

            self.assertEqual(pointer.read_text(encoding="utf-8").strip(), str(old))
            # Partial may exist; canonical pointer must still be the old target.
            self.assertTrue(pointer.is_file())
            # A subsequent successful flip replaces atomically.
            with mock.patch("features.sync.client_update.os.replace", side_effect=real_replace):
                client_update.atomic_write_text(pointer, str(new) + "\n")
            self.assertEqual(pointer.read_text(encoding="utf-8").strip(), str(new))


class DepsHashReuseTests(unittest.TestCase):
    def test_unchanged_requirements_reuses_venv_changed_builds_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            req = root / "requirements.txt"
            req.write_text("fastapi\n", encoding="utf-8")
            digest = client_update.deps_hash(req)
            venv = root / "venvs" / digest
            venv.mkdir(parents=True)
            python = client_update._venv_python(venv)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            (venv / ".deps_hash").write_text(digest + "\n", encoding="utf-8")

            self.assertTrue(client_update.should_reuse_venv(venv, req))

            req.write_text("fastapi\nuvicorn\n", encoding="utf-8")
            self.assertFalse(client_update.should_reuse_venv(venv, req))
            new_digest = client_update.deps_hash(req)
            self.assertNotEqual(digest, new_digest)

            builds: list[str] = []

            def fake_build(path, requirements):
                builds.append(path.name)
                path.mkdir(parents=True, exist_ok=True)
                py = client_update._venv_python(path)
                py.parent.mkdir(parents=True, exist_ok=True)
                py.write_text("", encoding="utf-8")
                (path / ".deps_hash").write_text(path.name + "\n", encoding="utf-8")
                return path

            version = root / "versions" / "abc"
            (version / "web").mkdir(parents=True)
            (version / "web" / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
            # Seed matching old venv under old hash name; ensure_venv should build new.
            updater = client_update.ClientUpdater(install_root=root, hub="http://hub", local_sha="old")
            with mock.patch.object(client_update, "build_venv", side_effect=fake_build):
                # Old hash venv exists but requirements changed → new hash path built.
                chosen = updater._ensure_venv_for_version("abc")
            self.assertEqual(chosen.name, new_digest)
            self.assertEqual(builds, [new_digest])

            # Unchanged → reuse, no build.
            builds.clear()
            with mock.patch.object(client_update, "build_venv", side_effect=fake_build) as build:
                again = updater._ensure_venv_for_version("abc")
            self.assertEqual(again.name, new_digest)
            build.assert_not_called()

    def test_partial_venv_without_marker_is_rebuilt_not_reused(self):
        """Mid-pip crash leaves python but no .deps_hash → must rebuild, never reuse."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            version = root / "versions" / "abc"
            (version / "web").mkdir(parents=True)
            (version / "web" / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            digest = client_update.deps_hash(version / "web" / "requirements.txt")
            partial = root / "venvs" / digest
            partial.mkdir(parents=True)
            python = client_update._venv_python(partial)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            # No .deps_hash marker — looks reusable to the old python-only check.
            self.assertFalse(
                client_update.should_reuse_venv(
                    partial, version / "web" / "requirements.txt", expected_hash=digest
                )
            )

            builds: list[str] = []

            def fake_build(path, requirements):
                builds.append(path.name)
                path.mkdir(parents=True, exist_ok=True)
                py = client_update._venv_python(path)
                py.parent.mkdir(parents=True, exist_ok=True)
                py.write_text("rebuilt", encoding="utf-8")
                (path / ".deps_hash").write_text(path.name + "\n", encoding="utf-8")
                return path

            updater = client_update.ClientUpdater(install_root=root, hub="http://hub", local_sha="old")
            with mock.patch.object(client_update, "build_venv", side_effect=fake_build):
                chosen = updater._ensure_venv_for_version("abc")
            self.assertEqual(chosen.name, digest)
            self.assertEqual(builds, [digest])
            self.assertTrue((chosen / ".deps_hash").is_file())

    def test_build_venv_stages_into_building_then_replaces(self):
        """Crash-safe build: work in <hash>.building, flip only after python + marker."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "venvs" / "deadbeef"
            building = target.with_name(target.name + ".building")
            created: list[str] = []
            replaced: list[tuple[str, str]] = []

            def fake_create(path, with_pip=True):
                created.append(path)
                Path(path).mkdir(parents=True, exist_ok=True)
                py = client_update._venv_python(Path(path))
                py.parent.mkdir(parents=True, exist_ok=True)
                py.write_text("", encoding="utf-8")

            real_replace = os.replace

            def tracking_replace(src, dst):
                replaced.append((str(src), str(dst)))
                return real_replace(src, dst)

            with mock.patch("venv.create", side_effect=fake_create):
                with mock.patch("features.sync.client_update.os.replace", side_effect=tracking_replace):
                    result = client_update.build_venv(target, None)
            self.assertEqual(result, target)
            self.assertEqual(created, [os.fspath(building)])
            self.assertEqual(replaced, [(str(building), str(target))])
            self.assertTrue(target.is_dir())
            self.assertFalse(building.exists())
            self.assertTrue((target / ".deps_hash").is_file())
            self.assertEqual((target / ".deps_hash").read_text(encoding="utf-8").strip(), "deadbeef")


if __name__ == "__main__":
    unittest.main()
