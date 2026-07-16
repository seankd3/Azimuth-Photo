"""HTTP behavior gates for AI control mutations."""

from core import capabilities as _capabilities
from unittest import mock
from copy import deepcopy

from fastapi.testclient import TestClient

import ai_models
import app as app_module
import settings
from test_support import BackendTestCase


class AiModelRouteTests(BackendTestCase):
    async def test_install_existing_model_keeps_active_embedding_pointer(self):
        if not _capabilities.capability_status("search")["available"]:
            self.skipTest("AI search capability unavailable — matches AI-optional installs")
        before = dict(settings.active_embedding_config())
        installed = deepcopy(ai_models.get_model_status(before))
        installed["installed"] = True
        installed["install"] = {
            **installed.get("install", {}),
            "running": False,
            "status": "ready",
            "message": "already present",
            "model_dir": installed.get("model_dir"),
        }
        with mock.patch.object(ai_models, "get_model_status", return_value=installed):
            def install():
                with TestClient(app_module.app) as client:
                    return client.post("/api/ai/model/install?role=fast")
            response = await __import__("asyncio").to_thread(install)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["already_installed"])
        self.assertEqual(settings.active_embedding_config(), before)
