"""Shared pytest fixtures and parallel-safety hooks for the unit suite."""

from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "contract: closed-class product guards (tier 0; seconds)",
    )
    config.addinivalue_line(
        "markers",
        "slow: e2e / Playwright / long wall-clock; excluded from gate quick",
    )
    config.addinivalue_line(
        "markers",
        "serial: must not share an xdist worker with other tests",
    )
    config.addinivalue_line(
        "markers",
        "playwright: browser-driven e2e proofs",
    )
    # Unit tests stay in-process unless a test explicitly enables the pool.
    # Spawning demosaic workers per test case is slow and unrelated to most asserts.
    os.environ.setdefault("AZIMUTH_DEMOSAIC_PROCESSES", "0")


def _worker_tag() -> str:
    return os.environ.get("PYTEST_XDIST_WORKER") or "gw0"


def free_port() -> int:
    """Bind an ephemeral local port (safe under xdist)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def worker_scratch(label: str) -> Path:
    """Per-worker scratch under TMPDIR (never a shared fixed path)."""
    root = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "azimuth-pytest"
    path = root / _worker_tag() / label
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(autouse=True)
def model_stood_down(monkeypatch):
    """The suite never loads the 2.4 GB embedding model.

    `embed.ready()` is the product's own seam for "can this machine make a
    vector right now", and during a test run the honest answer is no -- the
    card belongs to whatever real work is running (the live backfill, at the
    time this was written). A test about the embedding kind patches `ready`
    back to True together with a fake `vector`, which exercises every line
    except the forward pass.
    """

    import embed

    monkeypatch.setattr(embed, "ready", lambda: False)


@pytest.fixture
def ephemeral_port() -> int:
    return free_port()


@pytest.fixture
def worker_tmp_path(tmp_path: Path) -> Path:
    """tmp_path already unique; expose under a stable name for clarity."""
    return tmp_path
