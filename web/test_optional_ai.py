"""Focused proof that local AI is optional and never self-installs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch

from core import capabilities
from core import background
import embedding_worker
import face_worker
from features.ai import routes as ai_routes
from features.captions import routes as caption_routes
from features.people import routes as people_routes


WEB_DIR = Path(__file__).resolve().parent
OPTIONAL_IMPORTS = {
    "torch",
    "sentence_transformers",
    "transformers",
    "huggingface_hub",
    "accelerate",
    "qwen_vl_utils",
    "bitsandbytes",
    "insightface",
    "onnxruntime",
    "cv2",
}


def missing_capability(key: str) -> dict:
    with patch.object(capabilities, "find_spec", return_value=None):
        return capabilities.capability_status(key)


class OptionalAiPackagingTests(unittest.TestCase):
    def test_capabilities_are_dependency_only_and_include_install_guidance(self):
        status = missing_capability("people")
        self.assertFalse(status["available"])
        self.assertEqual(status["requirements_file"], "requirements-ai-people.txt")
        self.assertEqual(
            status["install_command"],
            "python -m pip install -r requirements-ai-people.txt",
        )
        self.assertFalse(status["runtime_install"])
        self.assertEqual(status["missing"], ["insightface", "onnxruntime", "cv2"])

    def test_search_pack_does_not_require_bitsandbytes_but_captions_do(self):
        def finder(name):
            return None if name == "bitsandbytes" else object()

        with patch.object(capabilities, "find_spec", side_effect=finder):
            search = capabilities.capability_status("search")
            captions = capabilities.capability_status("captions")
        self.assertTrue(search["available"])
        self.assertEqual(search["optional_missing"], ["bitsandbytes"])
        self.assertFalse(captions["available"])
        self.assertIn("bitsandbytes", captions["missing"])

    def test_8b_search_cannot_resume_or_load_without_bitsandbytes(self):
        capability = {
            **missing_capability("search"),
            "available": True,
            "missing": [],
            "optional_missing": ["bitsandbytes"],
        }
        config = {
            "model_id": "Qwen/Qwen3-VL-Embedding-8B",
            "model_key": "test",
            "model_dir": "/tmp/test",
            "dimension": 4096,
        }
        with patch.object(ai_routes, "_configured"), patch.object(
            ai_routes.capabilities, "capability_status", return_value=capability
        ), patch.object(ai_routes.settings, "active_embedding_config", return_value=config):
            response = asyncio.run(ai_routes.api_resume_embeddings())
        self.assertEqual(response.status_code, 409)
        self.assertIn("needs bitsandbytes", json.loads(response.body)["error"])

        fake_torch = types.ModuleType("torch")
        fake_torch.float16 = object()
        fake_torch.float32 = object()
        fake_sentence = types.ModuleType("sentence_transformers")
        fake_sentence.SentenceTransformer = Mock()
        with patch.dict(sys.modules, {"torch": fake_torch, "sentence_transformers": fake_sentence}), patch.object(
            embedding_worker.importlib.util, "find_spec", return_value=None
        ):
            with self.assertRaisesRegex(RuntimeError, "needs bitsandbytes"):
                embedding_worker._load_model("/tmp/test", config["model_id"])
        fake_sentence.SentenceTransformer.assert_not_called()

    def test_compact_search_has_deliberate_float32_path_without_bitsandbytes(self):
        fake_torch = types.ModuleType("torch")
        fake_torch.float16 = object()
        fake_torch.float32 = object()
        fake_sentence = types.ModuleType("sentence_transformers")
        constructor = Mock(return_value=object())
        fake_sentence.SentenceTransformer = constructor
        model_id = "Qwen/Qwen3-VL-Embedding-2B"
        with patch.dict(sys.modules, {"torch": fake_torch, "sentence_transformers": fake_sentence}), patch.object(
            embedding_worker.importlib.util, "find_spec", return_value=None
        ), patch("core.ml_device.preferred_device", return_value="cpu"):
            embedding_worker._load_model("/tmp/test", model_id)
        self.assertEqual(constructor.call_args.kwargs["model_kwargs"], {"torch_dtype": fake_torch.float32})
        self.assertEqual(constructor.call_args.kwargs["device"], "cpu")

    def test_minimal_install_can_import_app_without_optional_modules(self):
        script = """
import importlib.util
original = importlib.util.find_spec
blocked = %r
importlib.util.find_spec = lambda name, package=None: None if name.split('.')[0] in blocked else original(name, package)
import app
assert app.app.title == 'Azimuth Photo'
""" % OPTIONAL_IMPORTS
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=WEB_DIR,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_people_dependency_check_never_runs_pip(self):
        self.assertFalse(hasattr(face_worker, "subprocess"))
        config = {"face_model_id": "buffalo_l", "face_model_dir": "/tmp/unused", "people_auto_install": True}
        with patch.object(face_worker, "find_spec", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Runtime package installation is disabled"):
                face_worker._load_face_app(config)

    def test_startup_does_not_schedule_missing_inference_workers(self):
        unavailable = {key: missing_capability(key) for key in ("search", "people", "captions")}
        tracked = []

        class Worker:
            def __init__(self):
                self.unavailable = None

            def mark_dependencies_unavailable(self, status):
                self.unavailable = status

        people_worker = Worker()
        captions_worker = Worker()
        with patch.object(
            background.capabilities,
            "capability_status",
            side_effect=lambda key: unavailable[key],
        ):
            statuses = background.schedule_optional_workers(
                track_background_task=tracked.append,
                settings=object(),
                face_worker=people_worker,
                caption_worker=captions_worker,
            )
        self.assertEqual(tracked, [])
        self.assertEqual(people_worker.unavailable, unavailable["people"])
        self.assertEqual(captions_worker.unavailable, unavailable["captions"])
        self.assertFalse(statuses["search"]["available"])

    def test_missing_pack_resume_routes_return_409_before_starting_work(self):
        people = missing_capability("people")
        captions = missing_capability("captions")
        search = missing_capability("search")

        with patch.object(people_routes.capabilities, "capability_status", return_value=people), patch.object(
            people_routes.thumbnails, "start_pregeneration"
        ) as pregen, patch.object(people_routes.face_worker, "resume_face_worker") as resume:
            response = asyncio.run(people_routes.api_people_scan_resume())
            self.assertEqual(response.status_code, 409)
            pregen.assert_not_called()
            resume.assert_not_called()

        with patch.object(caption_routes, "_configured"), patch.object(
            caption_routes.capabilities, "capability_status", return_value=captions
        ), patch.object(caption_routes.caption_worker, "resume_caption_worker") as resume:
            response = asyncio.run(caption_routes.api_resume_captions())
            self.assertEqual(response.status_code, 409)
            resume.assert_not_called()

        with patch.object(ai_routes, "_configured"), patch.object(
            ai_routes.capabilities, "capability_status", return_value=search
        ), patch.object(ai_routes.ai_models, "start_model_install") as install:
            response = asyncio.run(ai_routes.api_install_ai_model())
            self.assertEqual(response.status_code, 409)
            install.assert_not_called()
            payload = json.loads(response.body)
            self.assertEqual(payload["install_command"], search["install_command"])

    def test_statuses_keep_stored_counts_visible_when_inference_is_missing(self):
        search = missing_capability("search")
        get_ai_counts = lambda: asyncio.sleep(
            0, result={"embedded": 7, "total_images": 11, "rated_images": 3}
        )
        model_status = {
            "installed": False,
            "install": {"running": False, "status": "idle", "message": ""},
            "model_id": "test",
            "model_dir": "/tmp/test",
            "model_key": "test",
            "dimension": 4,
        }
        with patch.object(ai_routes.capabilities, "capability_status", return_value=search), patch.object(
            ai_routes, "_get_ai_status_counts", get_ai_counts
        ), patch.object(ai_routes, "_count_embeddings_for_model", lambda **_kwargs: asyncio.sleep(0, result=7)), patch.object(
            ai_routes, "_invalidate_settings_response_cache", lambda: None
        ):
            status = asyncio.run(ai_routes.build_ai_status(model_status, force=True))
        self.assertEqual(status["embedded"], 7)
        self.assertEqual(status["worker_state"], "unavailable")
        self.assertEqual(status["capability"], search)

        people = missing_capability("people")
        with patch.object(people_routes.capabilities, "capability_status", return_value=people):
            status = asyncio.run(people_routes.people_status_payload({"counts": {"people": 4}}))
        self.assertEqual(status["counts"]["people"], 4)
        self.assertFalse(status["active"])
        self.assertEqual(status["worker"]["state"], "unavailable")
        self.assertFalse(status["auto_install"])
        self.assertTrue(status["auto_install_legacy"])

        captions = missing_capability("captions")
        with patch.object(caption_routes.capabilities, "capability_status", return_value=captions), patch.object(
            caption_routes,
            "_get_caption_status_counts",
            lambda **_kwargs: asyncio.sleep(0, result={"captioned": 9}),
        ), patch.object(caption_routes, "_invalidate_settings_response_cache", lambda: None):
            status = asyncio.run(caption_routes.caption_status_payload())
        self.assertEqual(status["counts"]["captioned"], 9)
        self.assertFalse(status["active"])
        self.assertEqual(status["worker"]["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
