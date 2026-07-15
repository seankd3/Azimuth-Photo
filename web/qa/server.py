"""Probe-port server lifecycle isolated from every long-lived instance."""

from __future__ import annotations

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

from qa.config import SERVER_LOG, WEB_ROOT, fixture_environment


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
    def __init__(self, *, old_hub: bool = False) -> None:
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen | None = None
        self._log_file = None
        self.old_hub = OldHubStub() if old_hub else None

    def __enter__(self) -> "ProbeServer":
        env = os.environ.copy()
        env.update(fixture_environment())
        if self.old_hub is not None:
            self.old_hub.start()
            env.update({"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": self.old_hub.base_url})
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
        if self.old_hub is not None:
            self.old_hub.stop()
