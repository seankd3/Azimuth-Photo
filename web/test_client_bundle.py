"""Hub client-bundle identity and /api/client/bundle coverage."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.version import version_payload
from data.schema import SCHEMA_VERSION
from features.sync import device_auth, hub_routes
from features.system import client_bundle


class ClientBundleIdentityTests(unittest.TestCase):
    def setUp(self):
        client_bundle.reset_hub_client_identity_for_tests()

    def tearDown(self):
        client_bundle.reset_hub_client_identity_for_tests()

    def test_identity_computed_once_and_cached_under_runtime_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            sha = "a" * 40
            bundle = cache / "client-bundles" / f"{sha}.tar.gz"
            bundle.parent.mkdir(parents=True)
            payload = b"fake-tar-gz-bytes"
            bundle.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()

            with mock.patch.object(client_bundle, "read_git_sha", return_value=sha), mock.patch.object(
                client_bundle, "archive_head_bundle", return_value=(bundle, digest)
            ) as archive:
                first = client_bundle.init_hub_client_identity(cache_dir=str(cache), sha=sha)
                second = client_bundle.init_hub_client_identity(cache_dir=str(cache), sha=sha)

            self.assertEqual(first.sha, sha)
            self.assertEqual(first.bundle_sha256, digest)
            self.assertEqual(first.schema_version, SCHEMA_VERSION)
            self.assertEqual(first, second)
            archive.assert_called_once()

    def test_dirty_tree_logs_and_still_archives_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            sha = "b" * 40

            def fake_archive(args, **kwargs):
                # git archive --output=<path>
                output = next(part.split("=", 1)[1] for part in args if str(part).startswith("--output="))
                Path(output).write_bytes(b"head-archive")

            with mock.patch.object(client_bundle, "working_tree_dirty", return_value=True), mock.patch.object(
                client_bundle, "read_git_sha", return_value=sha
            ), mock.patch("features.system.client_bundle.subprocess.check_call", side_effect=fake_archive) as check_call, mock.patch(
                "features.system.client_bundle.log.warning"
            ) as warning:
                path, digest = client_bundle.archive_head_bundle(sha=sha, cache_dir=str(cache))

            self.assertTrue(path.is_file())
            self.assertEqual(digest, hashlib.sha256(b"head-archive").hexdigest())
            warning.assert_called()
            self.assertIn("--output=", " ".join(str(part) for part in check_call.call_args[0][0]))

    def test_version_payload_includes_frozen_identity_fields(self):
        identity = client_bundle.ClientIdentity(
            sha="c" * 40,
            bundle_sha256="d" * 64,
            schema_version=SCHEMA_VERSION,
            bundle_path="/tmp/x.tar.gz",
        )
        with mock.patch.object(client_bundle, "get_hub_client_identity", return_value=identity), mock.patch.object(
            client_bundle, "_identity", identity
        ):
            # identity_payload reads module _identity; set it via init path
            client_bundle._identity = identity
            payload = version_payload()
        self.assertEqual(payload["sha"], "c" * 40)
        self.assertEqual(payload["bundle_sha256"], "d" * 64)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertIn("api_rev", payload)
        self.assertIn("capabilities", payload)


class ClientBundleRouteTests(unittest.TestCase):
    def setUp(self):
        client_bundle.reset_hub_client_identity_for_tests()
        self.tempdir = tempfile.TemporaryDirectory()
        self.cache = Path(self.tempdir.name) / "cache"
        self.sha = "e" * 40
        self.bundle = self.cache / "client-bundles" / f"{self.sha}.tar.gz"
        self.bundle.parent.mkdir(parents=True)
        self.bundle.write_bytes(b"route-bundle-bytes")
        digest = hashlib.sha256(b"route-bundle-bytes").hexdigest()
        client_bundle._identity = client_bundle.ClientIdentity(
            sha=self.sha,
            bundle_sha256=digest,
            schema_version=SCHEMA_VERSION,
            bundle_path=str(self.bundle),
        )
        self.auth_patch = mock.patch.object(
            device_auth, "require_device_token_enabled", return_value=False
        )
        self.auth_patch.start()
        hub_routes.configure(db_path=lambda: str(Path(self.tempdir.name) / "hub.db"))
        app = FastAPI()
        app.include_router(hub_routes.router)
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.auth_patch.stop()
        client_bundle.reset_hub_client_identity_for_tests()
        self.tempdir.cleanup()

    def test_bundle_route_uses_sync_auth_dependency_and_serves_cached_bytes(self):
        # Router-level Depends(device_auth.enforce_device_token) — same as /api/sync/*.
        self.assertTrue(
            any(
                getattr(dep, "dependency", None) is device_auth.enforce_device_token
                for dep in hub_routes.router.dependencies
            )
        )
        response = self.client.get("/api/client/bundle")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"route-bundle-bytes")
        self.assertEqual(response.headers.get("x-content-sha256"), hashlib.sha256(b"route-bundle-bytes").hexdigest())
        self.assertEqual(response.headers.get("x-content-sha"), self.sha)

    def test_bundle_requires_device_token_when_auth_enabled(self):
        from features.sync import pairing

        self.auth_patch.stop()
        with mock.patch.object(device_auth, "require_device_token_enabled", return_value=True):
            denied = self.client.get("/api/client/bundle")
            self.assertEqual(denied.status_code, 401)
            with mock.patch.object(
                pairing,
                "authenticate_device_token",
                new=mock.AsyncMock(return_value={"device_id": "sat-1"}),
            ):
                allowed = self.client.get(
                    "/api/client/bundle",
                    headers={"X-Device-Token": "live-token"},
                )
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed.content, b"route-bundle-bytes")
        self.auth_patch = mock.patch.object(
            device_auth, "require_device_token_enabled", return_value=False
        )
        self.auth_patch.start()


if __name__ == "__main__":
    unittest.main()
