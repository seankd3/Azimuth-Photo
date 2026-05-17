import os
import time

from fastapi import APIRouter


router = APIRouter()
_started_at: float | None = None
_git_commit = "unknown"


def configure(*, started_at: float, git_commit: str) -> None:
    global _started_at, _git_commit
    _started_at = float(started_at)
    _git_commit = git_commit


@router.get("/api/dev/status")
async def dev_status():
    """Lightweight process/version probe for local server management."""
    if _started_at is None:
        raise RuntimeError("Dev routes are not configured")
    return {
        "pid": os.getpid(),
        "started_at": _started_at,
        "uptime_seconds": round(time.time() - _started_at, 3),
        "git_commit": _git_commit,
        "cwd": os.getcwd(),
    }
