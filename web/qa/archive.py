"""Durable, per-run QA evidence and trend data."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from qa.config import REPO_ROOT, REPORT_PATH, RUNS_ROOT

MAX_ARCHIVED_RUNS = 50


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class RunArchive:
    """Owns all durable evidence for one invocation of the QA suite."""

    def __init__(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = RUNS_ROOT / f"{stamp}-{_git_sha()}"
        self.path = base
        suffix = 2
        while self.path.exists():
            self.path = RUNS_ROOT / f"{base.name}-{suffix}"
            suffix += 1
        self.path.mkdir(parents=True, exist_ok=False)
        self.server_log = self.path / "server.log"

    def failure_dir(self, scenario_name: str, attempt: int) -> Path:
        return self.path / "failures" / scenario_name / f"attempt-{attempt}"

    def capture_failure(self, result, *, attempt: int, server_log_start: int) -> Path:
        """Write browser and server evidence next to a failed scenario attempt."""

        target = self.failure_dir(result.name, attempt)
        target.mkdir(parents=True, exist_ok=True)
        console = {
            "scenario": result.name,
            "attempt": attempt,
            "step": result.step,
            "error": result.error,
            "console": result.evidence.console_messages,
            "browser_evidence": result.evidence.problems(),
        }
        (target / "browser-console.json").write_text(
            json.dumps(console, indent=2) + "\n", encoding="utf-8"
        )
        server_slice = ""
        if self.server_log.exists():
            with self.server_log.open("rb") as source:
                source.seek(server_log_start)
                server_slice = source.read().decode("utf-8", errors="replace")
        (target / "server.log").write_text(server_slice, encoding="utf-8")
        return target

    def write_report(self, report: dict) -> Path:
        target = self.path / "report.json"
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, REPORT_PATH)
        self._prune()
        return target

    def _prune(self) -> None:
        runs = sorted(path for path in RUNS_ROOT.iterdir() if path.is_dir())
        for stale in runs[:-MAX_ARCHIVED_RUNS]:
            shutil.rmtree(stale)
