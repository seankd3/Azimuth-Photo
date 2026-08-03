"""Probe-port server lifecycle isolated from every long-lived instance."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qa.config import WEB_ROOT, fixture_environment, scaled_seconds


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class OldHubStub:
    """A deliberately pre-handshake hub; records requests without serving APIs."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(("GET", self.path))
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                owner.requests.append(("POST", self.path))
                self.send_response(404)
                self.end_headers()

            def log_message(self, _format, *_args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3)


class ProbeServer:
    def __init__(
        self,
        *,
        offline_hub: bool = False,
        old_hub: bool = False,
        log_path: Path,
    ) -> None:
        if offline_hub and old_hub:
            raise ValueError("ProbeServer hub modes are mutually exclusive")
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen | None = None
        self._log_file = None
        self.offline_hub = offline_hub
        self.old_hub = OldHubStub() if old_hub else None
        self.log_path = log_path

    def __enter__(self) -> "ProbeServer":
        env = os.environ.copy()
        env.update(fixture_environment())
        if self.old_hub is not None:
            self.old_hub.start()
            env.update({"AZIMUTH_MODE": "satellite", "AZIMUTH_HUB_URL": self.old_hub.base_url})
        elif self.offline_hub:
            # This is intentionally a dead local port, never a real paired hub.
            env.update({"AZIMUTH_MODE": "satellite", "AZIMUTH_HUB_URL": "http://127.0.0.1:1"})
        else:
            # The general desktop matrix exercises direct-owner behavior. The
            # offline satellite flow below gets its own isolated process.
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
        if self.offline_hub or self.old_hub is not None:
            self._wait_for_initial_sync_scan()
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

    def _wait_for_initial_sync_scan(self, timeout: float = 90.0) -> None:
        """Keep satellite scenarios out of the fixture's startup write batch."""

        deadline = time.monotonic() + scaled_seconds(timeout)
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base_url}/api/sync/status", timeout=scaled_seconds(1)) as response:
                    payload = json.loads(response.read() or b"{}")
                if int(payload.get("queue_depth") or 0) or payload.get("recent_errors"):
                    return
            except (json.JSONDecodeError, urllib.error.URLError, TimeoutError, OSError):
                pass
            time.sleep(0.1)
        raise RuntimeError(f"QA satellite did not finish its initial sync scan; see {self.log_path}")

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
        if self.old_hub is not None:
            self.old_hub.stop()

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
