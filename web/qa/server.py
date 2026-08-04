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

from qa.config import WEB_ROOT, fixture_environment, scaled_seconds


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ProbeServer:
    def __init__(self, *, log_path: Path) -> None:
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen | None = None
        self._log_file = None
        self.log_path = log_path

    def __enter__(self) -> "ProbeServer":
        env = os.environ.copy()
        env.update(fixture_environment())
        env.update({"AZIMUTH_MODE": "hub"})
        env.update({"AZIMUTH_HOST": "127.0.0.1", "AZIMUTH_PORT": str(self.port)})
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = self.log_path.open("a", encoding="utf-8")
        self._log_file.write(f"\n--- ProbeServer {self.base_url} starting ---\n")
        self._log_file.flush()
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
        deadline = time.monotonic() + scaled_seconds(timeout)
        last_error = ""
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                raise RuntimeError(f"QA server exited early with code {self.process.returncode}; see {self.log_path}")
            try:
                with urllib.request.urlopen(f"{self.base_url}/api/dev/status", timeout=scaled_seconds(1)) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = str(exc)
            time.sleep(0.1)
        raise RuntimeError(f"QA server did not become ready on {self.base_url}: {last_error}; see {self.log_path}")

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            self._stop_process(signal.SIGTERM)
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._stop_process(signal.SIGKILL)
                process.wait(timeout=5)
        if self._log_file is not None:
            self._log_file.close()

    def _stop_process(self, sig: signal.Signals) -> None:
        """Use process groups on POSIX and native process termination on Windows."""

        if self.process is None:
            return
        try:
            if os.name == "nt":
                # signal.SIGKILL does not exist on Windows; escalate anything
                # beyond SIGTERM to a hard kill.
                if sig == signal.SIGTERM:
                    self.process.terminate()
                else:
                    self.process.kill()
            else:
                os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass
