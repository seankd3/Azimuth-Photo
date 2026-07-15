"""Probe-port server lifecycle isolated from every long-lived instance."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from qa.config import SERVER_LOG, WEB_ROOT, fixture_environment


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ProbeServer:
    def __init__(self) -> None:
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen | None = None
        self._log_file = None

    def __enter__(self) -> "ProbeServer":
        env = os.environ.copy()
        env.update(fixture_environment())
        env.update({"PHOTOARCHIVE_HOST": "127.0.0.1", "PHOTOARCHIVE_PORT": str(self.port)})
        SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = SERVER_LOG.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(WEB_ROOT),
            env=env,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._wait_ready()
        return self

    def _wait_ready(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                raise RuntimeError(f"QA server exited early with code {self.process.returncode}; see {SERVER_LOG}")
            try:
                with urllib.request.urlopen(f"{self.base_url}/api/dev/status", timeout=1) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = str(exc)
            time.sleep(0.1)
        raise RuntimeError(f"QA server did not become ready on {self.base_url}: {last_error}; see {SERVER_LOG}")

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        if self._log_file is not None:
            self._log_file.close()
