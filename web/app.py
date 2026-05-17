import asyncio
import copy
import json
import os
import time
import uuid

from fastapi import BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse

import ai_models
import db
import embed_cache
import embedding_worker
import elo_propagation
import face_worker
import pairing
import resource_governor
import scanner
import settings
import thumbnails
from core.app_factory import (
    INTERACTION_CACHE_WARMUP_DELAY_SECONDS,
    create_app_shell,
)
from core import background as background_runtime


APP_DIR = os.path.dirname(__file__)
_app_shell = create_app_shell(base_dir=APP_DIR, started_at=time.time())
_runtime_services = _app_shell.runtime_services
if _runtime_services is None:
    raise RuntimeError("App runtime services were not configured")
app = _app_shell.app
templates = _app_shell.templates

_IDLE_ACTIVITY_EXCLUDED_PATHS = _app_shell.idle_activity_excluded_paths
_background_task_tracker = _app_shell.background_task_tracker
_BACKGROUND_TASKS = _app_shell.background_tasks
_track_background_task = _app_shell.track_background_task


_static_assets = _app_shell.static_assets
_GIT_COMMIT = _app_shell.git_commit
_static_version = _app_shell.static_version
_template_context = _app_shell.template_context
_warm_templates = _app_shell.warm_templates
_smoke_mode_enabled = background_runtime.smoke_mode_enabled


track_idle_activity = _app_shell.idle_activity_middleware
if track_idle_activity is None:
    raise RuntimeError("App idle activity middleware was not configured")


_schedule_pairing_propagation = _runtime_services.schedule_pairing_propagation


_resolve_text_search = _runtime_services.resolve_text_search


_resolve_library_constraints = _runtime_services.resolve_library_constraints


_lifecycle = _app_shell.lifecycle
if _lifecycle is None:
    raise RuntimeError("App lifecycle was not configured")
startup = _lifecycle.startup
shutdown = _lifecycle.shutdown


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True, access_log=False)
