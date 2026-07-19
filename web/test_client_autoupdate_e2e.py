"""Client auto-update acceptance — four frozen e2e scenarios (scratch pattern)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient

from core.version import API_REV, CAPABILITIES
from data.schema import SCHEMA_VERSION
from features.sync import client_update, contract, launcher
from features.sync.client_update import UI_RETRY, UI_ROLLED_BACK, UI_UPDATING, atomic_write_text
from features.sync.sync_worker import SyncWorker
from features.system import client_bundle


def _tar_gz(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _seed_version(root: Path, sha: str, *, requirements: bytes = b"fastapi\n") -> Path:
    version = root / "versions" / sha
    web = version / "web"
    web.mkdir(parents=True)
    (web / "requirements.txt").write_bytes(requirements)
    (version / ".client_sha").write_text(sha + "\n", encoding="utf-8")
    return version


class ClientAutoUpdateE2ETests(unittest.TestCase):
    def setUp(self):
        contract.reset_hub_contract_cache()
        client_bundle.reset_hub_client_identity_for_tests()
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.install = self.root / "install"
        self.install.mkdir()
        self.sha_a = "a" * 40
        self.sha_b = "b" * 40
        self.req_bytes = b"fastapi\nuvicorn\n"
        self.bundle_a = _tar_gz(
            {
                "web/requirements.txt": self.req_bytes,
                "web/app.py": b"# sha A\n",
                "VERSION": b"0.1.0\n",
            }
        )
        self.bundle_sha = hashlib.sha256(self.bundle_a).hexdigest()
        _seed_version(self.install, self.sha_b, requirements=self.req_bytes)
        atomic_write_text(self.install / "current.txt", str(self.install / "versions" / self.sha_b) + "\n")
        self.bundle_bytes = self.bundle_a
        self.hub_files: dict[str, bytearray] = {}

        app = FastAPI()

        @app.get("/api/version")
        async def version():
            return {
                "app_version": "0.1.0",
                "api_rev": API_REV,
                "capabilities": sorted(CAPABILITIES),
                "sha": self.sha_a,
                "bundle_sha256": self.bundle_sha,
                "schema_version": SCHEMA_VERSION,
            }

        @app.get("/api/client/bundle")
        async def bundle():
            return Response(self.bundle_bytes, media_type="application/gzip")

        @app.post("/api/sync/manifest")
        async def manifest(request: Request):
            payload = await request.json()
            items = payload.get("items") or []
            missing, known = [], []
            for item in items:
                content_hash = item["content_hash"]
                if content_hash in self.hub_files and self.hub_files[content_hash]:
                    known.append({"content_hash": content_hash, "image_id": 1})
                else:
                    missing.append(content_hash)
            return {"missing": missing, "known": known}

        @app.get("/api/sync/upload/{content_hash}/status")
        async def upload_status(content_hash: str):
            return {"offset": len(self.hub_files.get(content_hash, b""))}

        @app.post("/api/sync/upload/{content_hash}")
        async def upload(content_hash: str, request: Request):
            body = await request.body()
            offset = int(request.headers.get("x-offset") or 0)
            target = self.hub_files.setdefault(content_hash, bytearray())
            if len(target) != offset:
                del target[offset:]
            target.extend(body)
            return {"image_id": 1}

        @app.post("/api/sync/metadata")
        async def metadata():
            return {"applied": [], "skipped": []}

        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        contract.reset_hub_contract_cache()
        client_bundle.reset_hub_client_identity_for_tests()
        self.tempdir.cleanup()

    async def _request(self, method, url, *, body=None, headers=None):
        parsed = urlsplit(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        response = self.client.request(method, path, content=body, headers=headers)
        return response.status_code, dict(response.headers), response.content

    def _fake_build(self, builds: list[str]):
        def fake_build(path, requirements):
            builds.append(path.name)
            path.mkdir(parents=True, exist_ok=True)
            target = client_update._venv_python(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("new", encoding="utf-8")
            (path / ".deps_hash").write_text(path.name + "\n", encoding="utf-8")
            return path

        return fake_build

    def test_1_handshake_downloads_flips_restarts_and_resumes_upload(self):
        async def scenario():
            restarts: list[str] = []
            updater = client_update.ClientUpdater(
                install_root=self.install,
                hub="http://hub",
                local_sha=self.sha_b,
                request=self._request,
                restart=lambda: restarts.append("restart"),
                ui_busy=lambda: False,
            )
            digest = client_update.deps_hash(
                self.install / "versions" / self.sha_b / "web" / "requirements.txt"
            )
            venv = self.install / "venvs" / digest
            venv.mkdir(parents=True)
            py = client_update._venv_python(venv)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("marker", encoding="utf-8")
            (venv / ".deps_hash").write_text(digest + "\n", encoding="utf-8")

            builds: list[str] = []
            with mock.patch.object(client_update, "build_venv", side_effect=self._fake_build(builds)):
                await updater.consider_hub_version(
                    {
                        "sha": self.sha_a,
                        "bundle_sha256": self.bundle_sha,
                        "schema_version": SCHEMA_VERSION,
                    }
                )

            self.assertTrue((self.install / "versions" / self.sha_a).is_dir())
            self.assertEqual(
                (self.install / "current.txt").read_text(encoding="utf-8").strip(),
                str(self.install / "versions" / self.sha_a),
            )
            self.assertEqual(updater.status.message, UI_UPDATING)
            self.assertEqual(builds, [])  # requirements unchanged → venv reuse

            content_hash = "1" * 32
            payload = b"original-bytes-under-B"
            self.assertFalse(updater.request_restart_if_safe(uploading=True))
            self.assertEqual(restarts, [])
            # In-flight resumable upload completes under B, then safe-point restart under A.
            self.hub_files[content_hash] = bytearray(payload)
            self.assertTrue(updater.request_restart_if_safe(uploading=False))
            self.assertEqual(restarts, ["restart"])
            self.assertEqual(bytes(self.hub_files[content_hash]), payload)
            self.assertEqual(
                (self.install / "versions" / self.sha_a / ".client_sha").read_text(encoding="utf-8").strip(),
                self.sha_a,
            )

        asyncio.run(scenario())

    def test_2_corrupt_bundle_stays_on_b_with_retry_status(self):
        async def scenario():
            self.bundle_bytes = self.bundle_a[:-1] + bytes([self.bundle_a[-1] ^ 0xFF])
            updater = client_update.ClientUpdater(
                install_root=self.install,
                hub="http://hub",
                local_sha=self.sha_b,
                request=self._request,
            )
            await updater.consider_hub_version(
                {"sha": self.sha_a, "bundle_sha256": self.bundle_sha}
            )
            self.assertEqual(updater.status.state, client_update.STATUS_RETRY)
            self.assertEqual(updater.status.message, UI_RETRY)
            self.assertEqual(
                (self.install / "current.txt").read_text(encoding="utf-8").strip(),
                str(self.install / "versions" / self.sha_b),
            )

            self.bundle_bytes = self.bundle_a
            builds: list[str] = []
            with mock.patch.object(client_update, "build_venv", side_effect=self._fake_build(builds)):
                await updater.consider_hub_version(
                    {"sha": self.sha_a, "bundle_sha256": self.bundle_sha}
                )
            self.assertEqual(
                (self.install / "current.txt").read_text(encoding="utf-8").strip(),
                str(self.install / "versions" / self.sha_a),
            )
            self.assertTrue(updater._pending_restart)

        asyncio.run(scenario())

    def test_3_crash_looping_new_version_rolls_back_and_sync_continues(self):
        old = self.install / "versions" / self.sha_b
        new = _seed_version(self.install, self.sha_a, requirements=self.req_bytes)
        atomic_write_text(self.install / "previous.txt", str(old) + "\n")
        atomic_write_text(self.install / "current.txt", str(new) + "\n")

        class FakeProcess:
            def __init__(self, *, dead: bool):
                self._code = 1 if dead else None

            def poll(self):
                return self._code

            def terminate(self):
                self._code = 1

            def kill(self):
                self._code = 1

            def wait(self, timeout=None):
                if self._code is None:
                    self._code = 0
                return self._code

        seen: list[str] = []

        def spawn(*, web_dir, **kwargs):
            sha = Path(web_dir).parent.name if Path(web_dir).name == "web" else Path(web_dir).name
            seen.append(sha)
            return FakeProcess(dead=(sha == self.sha_a))

        def wait_ready(*, process, **kwargs):
            return process.poll() is None

        code = launcher.run_supervised(
            install_root=self.install,
            ready_timeout=0.01,
            spawn=spawn,
            wait_ready=wait_ready,
        )
        self.assertEqual(code, 0)
        self.assertGreaterEqual(seen.count(self.sha_a), 2)
        self.assertIn(self.sha_b, seen)
        self.assertEqual(
            (self.install / "current.txt").read_text(encoding="utf-8").strip(),
            str(old),
        )
        self.assertEqual(launcher.consume_rollback_notice(self.install), UI_ROLLED_BACK)

        launcher.mark_rollback_notice(self.install)
        updater = client_update.ClientUpdater(
            install_root=self.install,
            hub="http://hub",
            local_sha=self.sha_b,
        )
        client_update.consume_rollback_notice_into_status(updater, self.install)
        worker = SyncWorker(db_path=":memory:", hub="http://hub", request=self._request, updater=updater)
        self.assertEqual(worker.status()["update_message"], UI_ROLLED_BACK)

    def test_4_requirements_unchanged_reuses_venv_changed_builds(self):
        async def scenario():
            digest = client_update.deps_hash(
                self.install / "versions" / self.sha_b / "web" / "requirements.txt"
            )
            venv = self.install / "venvs" / digest
            venv.mkdir(parents=True)
            py = client_update._venv_python(venv)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("marker", encoding="utf-8")
            (venv / ".deps_hash").write_text(digest + "\n", encoding="utf-8")
            mtime_before = py.stat().st_mtime

            builds: list[str] = []
            updater = client_update.ClientUpdater(
                install_root=self.install,
                hub="http://hub",
                local_sha=self.sha_b,
                request=self._request,
            )
            with mock.patch.object(client_update, "build_venv", side_effect=self._fake_build(builds)):
                await updater.consider_hub_version(
                    {"sha": self.sha_a, "bundle_sha256": self.bundle_sha}
                )
            self.assertEqual(builds, [])
            self.assertEqual(py.read_text(encoding="utf-8"), "marker")
            self.assertEqual(py.stat().st_mtime, mtime_before)

            changed = _tar_gz(
                {
                    "web/requirements.txt": b"fastapi\nuvicorn\nPillow\n",
                    "web/app.py": b"# sha A changed deps\n",
                }
            )
            self.bundle_bytes = changed
            self.bundle_sha = hashlib.sha256(changed).hexdigest()
            atomic_write_text(
                self.install / "current.txt",
                str(self.install / "versions" / self.sha_b) + "\n",
            )
            updater.local_sha = self.sha_b
            updater._prepared_sha = None
            updater._pending_restart = False
            shutil.rmtree(self.install / "versions" / self.sha_a, ignore_errors=True)
            with mock.patch.object(client_update, "build_venv", side_effect=self._fake_build(builds)):
                await updater.consider_hub_version(
                    {"sha": self.sha_a, "bundle_sha256": self.bundle_sha}
                )
            new_digest = client_update.deps_hash(
                self.install / "versions" / self.sha_a / "web" / "requirements.txt"
            )
            self.assertEqual(builds, [new_digest])
            self.assertTrue((self.install / "venvs" / new_digest).is_dir())

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
