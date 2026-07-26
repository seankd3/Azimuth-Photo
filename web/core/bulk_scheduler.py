"""One bulk HDD stream at a time: preview pregen before cloud vault.

Spinning disks collapse when preview backfill and rclone vault upload seek-thrash
each other. This module owns the sequencing policy; the HDD governor's single-flight
read gate is unchanged.

Desired-running flags persist in the existing runtime state_dir (same place as
cloud_backup_status.json) so an app restart can re-kick workers that were live
before shutdown.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

VAULT_WAIT_MESSAGE = "yielding disk to preview build"
VAULT_OVERRIDE_WARNING = "warning: vault and preview build both share the disk"

DESIRED_FILENAME = "bulk_desired.json"

# States where preview backfill is entitled to the spindle.
_PREVIEW_HOLD_STATES = frozenset({"running", "waiting"})

_previews_pending_fn: Callable[[], bool] | None = None
_desired_path_override: Path | None = None


def sequencing_enabled() -> bool:
    """AZIMUTH_BULK_SEQUENCING — default on."""
    raw = str(os.environ.get("AZIMUTH_BULK_SEQUENCING", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def configure(*, previews_pending: Callable[[], bool] | None) -> None:
    """Wire the live pregen probe (called once at app startup)."""
    global _previews_pending_fn
    _previews_pending_fn = previews_pending


def reset_for_tests(*, desired_path: Path | str | None = None) -> None:
    """Clear the live probe between unit tests; optional temp desired-file path."""
    global _previews_pending_fn, _desired_path_override
    _previews_pending_fn = None
    _desired_path_override = Path(desired_path) if desired_path is not None else None


def desired_path() -> Path:
    if _desired_path_override is not None:
        return _desired_path_override
    from core.runtime_paths import resolve_runtime_paths

    return Path(resolve_runtime_paths().state_dir) / DESIRED_FILENAME


def previews_hold_disk(
    *,
    manual_mode: bool,
    manual_pause: bool,
    state: str,
) -> bool:
    """True when preview backfill is actively entitled to the spindle."""
    if not manual_mode or manual_pause:
        return False
    return str(state or "") in _PREVIEW_HOLD_STATES


def previews_pending() -> bool:
    """Live probe: True when previews currently hold the disk."""
    if _previews_pending_fn is None:
        return False
    try:
        return bool(_previews_pending_fn())
    except Exception:
        log.exception("bulk_scheduler previews_pending probe failed")
        return False


def vault_decision(
    *,
    sequencing: bool,
    previews_pending: bool,
    vault_desired: bool,
    manual_override: bool,
) -> str:
    """Return the vault action: ``run``, ``wait``, or ``idle``.

    Behavior table (sequencing on):
      previews pending + vault desired + no override → wait
      previews done/idle + vault desired               → run
      manual override (explicit start)                 → run
      vault not desired                                → idle
    """
    if manual_override:
        return "run"
    if not vault_desired:
        return "idle"
    if sequencing and previews_pending:
        return "wait"
    return "run"


def decide_vault_start(*, manual_override: bool = False) -> dict[str, Any]:
    """Live decision for a vault start attempt."""
    pending = previews_pending()
    sequencing = sequencing_enabled()
    desired = vault_desired()
    # An explicit start always implies desire for this attempt.
    effective_desired = True if manual_override else desired
    action = vault_decision(
        sequencing=sequencing,
        previews_pending=pending,
        vault_desired=effective_desired,
        manual_override=manual_override,
    )
    message = ""
    if action == "wait":
        message = VAULT_WAIT_MESSAGE
    elif action == "run" and manual_override and sequencing and pending:
        message = VAULT_OVERRIDE_WARNING
    return {
        "action": action,
        "message": message,
        "sequencing": sequencing,
        "previews_pending": pending,
        "vault_desired": effective_desired,
        "manual_override": manual_override,
    }


def _load_desired() -> dict[str, bool]:
    path = desired_path()
    if not path.is_file():
        return {"pregen": False, "vault": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"pregen": False, "vault": False}
    if not isinstance(data, dict):
        return {"pregen": False, "vault": False}
    return {
        "pregen": bool(data.get("pregen")),
        "vault": bool(data.get("vault")),
    }


def _save_desired(data: dict[str, bool]) -> None:
    path = desired_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pregen": bool(data.get("pregen")), "vault": bool(data.get("vault"))}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def pregen_desired() -> bool:
    return _load_desired()["pregen"]


def vault_desired() -> bool:
    return _load_desired()["vault"]


def set_pregen_desired(value: bool) -> None:
    current = _load_desired()
    wanted = bool(value)
    if current["pregen"] == wanted:
        return
    current["pregen"] = wanted
    _save_desired(current)
    log.info("bulk_scheduler pregen_desired=%s", wanted)


def set_vault_desired(value: bool) -> None:
    current = _load_desired()
    wanted = bool(value)
    if current["vault"] == wanted:
        return
    current["vault"] = wanted
    _save_desired(current)
    log.info("bulk_scheduler vault_desired=%s", wanted)
