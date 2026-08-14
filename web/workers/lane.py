"""The shared state machine of a derive-worker lane.

Captions, People, and Search embeddings are one worker shape: a status dict
the routes read, a manual pause that mirrors a saved settings flag, OOM and
model-load cooldowns, and a turn in the coordination queue. Each worker
module keeps its model and its derive step; a Lane owns everything the
three used to copy — the state, the transitions, and the copy strings are
supplied per lane, the machine is written once.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import settings
from core import work_coordination

OOM_COOLDOWN_BASE_SECONDS = 15 * 60
OOM_COOLDOWN_MAX_SECONDS = 2 * 60 * 60
MODEL_LOAD_FAILURE_PAUSE_THRESHOLD = 3
MODEL_LOAD_FAILURE_COOLDOWN_SECONDS = 900


class Lane:
    """One derive lane: its status surface and its pause/cooldown/turn state.

    ``release`` drops whatever the lane holds beyond its manual turn (model
    residency, GPU ownership). ``on_resume`` restores per-lane eagerness
    (circuit resets, an immediate-scan request).
    """

    def __init__(
        self,
        *,
        key: str,
        settings_flag: str,
        noun: str,
        status_seed: dict[str, Any],
        startup_stopped_message: str,
        stopped_message: str,
        resume_message: str,
        uses_gpu: bool = False,
        release: Callable[[], None] = lambda: None,
        on_resume: Callable[[], None] = lambda: None,
    ) -> None:
        self.key = key
        self.settings_flag = settings_flag
        self.noun = noun
        self.stopped_message = stopped_message
        self.resume_message = resume_message
        self.uses_gpu = uses_gpu
        self._release_extra = release
        self._on_resume = on_resume
        self._lock = threading.Lock()
        self._status = dict(status_seed)
        self.manual_pause = self._initial_manual_pause()
        self.manual_pause_message = startup_stopped_message
        self.oom_cooldown_until = 0.0
        self.oom_cooldown_count = 0
        self.load_failure_cooldown_until = 0.0
        self.model_load_failure_count = 0

    def _initial_manual_pause(self) -> bool:
        # Auto-resume: when the lane is enabled in settings, it starts after
        # a service restart instead of waiting for a manual click — a 47k
        # backfill must survive routine deploy restarts unattended.
        try:
            return not bool(settings.get_settings()[self.settings_flag])
        except Exception:
            return True

    # -- status surface -----------------------------------------------------

    def set_status(self, **updates: Any) -> None:
        with self._lock:
            self._status.update(updates)

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            status = dict(self._status)
        status["manual_pause"] = self.manual_pause
        return status

    def status_value(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._status.get(key, default)

    def mark_dependencies_unavailable(self, capability: dict[str, Any]) -> None:
        """Publish one stable missing-pack state without starting the loop."""

        self.set_status(
            state="unavailable",
            ready=False,
            running=False,
            message=capability["message"],
            last_error="",
        )

    # -- pause / resume ------------------------------------------------------

    def release_owners(self) -> None:
        work_coordination.release_manual_owner(self.key)
        self._release_extra()

    def enter_paused(self, message: str) -> None:
        self.release_owners()
        self.set_status(state="paused", ready=False, message=message, last_error="")

    def pause(self, message: str | None = None, *, persist: bool = True) -> dict[str, Any]:
        if persist:
            config = settings.get_settings()
            if bool(config[self.settings_flag]):
                settings.save_settings({**config, self.settings_flag: False})
        self.manual_pause = True
        self.manual_pause_message = message or self.stopped_message
        self.enter_paused(self.manual_pause_message)
        return self.get_status()

    def resume(self, *, persist: bool = True) -> dict[str, Any]:
        if persist:
            config = settings.get_settings()
            if not bool(config[self.settings_flag]):
                settings.save_settings({**config, self.settings_flag: True})
        self.manual_pause = False
        self.manual_pause_message = ""
        self.model_load_failure_count = 0
        self.load_failure_cooldown_until = 0.0
        self.oom_cooldown_until = 0.0
        self.oom_cooldown_count = 0
        self._on_resume()
        self.set_status(
            state="idle",
            message=self.resume_message,
            model_load_failures=0,
            retry_at=None,
        )
        return self.get_status()

    # -- cooldown circuits ---------------------------------------------------

    def cooldown_until(self) -> float:
        return max(self.oom_cooldown_until, self.load_failure_cooldown_until)

    def _cooldown_copy(self, seconds: float) -> str:
        minutes = max(1, round(seconds / 60))
        return (
            f"{self.noun} making room for other work and will retry "
            f"automatically in about {minutes} minutes."
        )

    def publish_cooldown_wait(self, retry_at: float) -> int:
        """Announce an active cooldown; returns whole seconds remaining."""

        remaining = max(1, int(retry_at - time.time()))
        self.release_owners()
        self.set_status(
            state="cooldown",
            ready=False,
            message=self._cooldown_copy(remaining),
            retry_at=retry_at,
            last_error="",
        )
        return remaining

    def enter_oom_cooldown(self) -> None:
        """Defer after repeated OOMs without forgetting the saved intent."""

        self.oom_cooldown_count += 1
        seconds = min(
            OOM_COOLDOWN_MAX_SECONDS,
            OOM_COOLDOWN_BASE_SECONDS * (2 ** max(0, self.oom_cooldown_count - 1)),
        )
        self.oom_cooldown_until = time.time() + seconds
        self.release_owners()
        self.set_status(
            state="cooldown",
            ready=False,
            message=self._cooldown_copy(seconds),
            retry_at=self.oom_cooldown_until,
            last_error="",
        )

    def clear_oom_streak(self) -> None:
        if self.oom_cooldown_count:
            self.oom_cooldown_count = 0
            self.set_status(retry_at=None)

    def reset_model_load_failures(self) -> None:
        if self.model_load_failure_count:
            self.model_load_failure_count = 0
            self.set_status(model_load_failures=0)

    def record_model_load_failure(self, error: Exception) -> bool:
        """Count a load failure; True when the lane should cool down.

        Cools down instead of pausing permanently: transient GPU contention
        (another worker holding VRAM) must not end a backfill.
        """

        self.model_load_failure_count += 1
        self.set_status(
            model_load_failures=self.model_load_failure_count, last_error=str(error)
        )
        if self.model_load_failure_count < MODEL_LOAD_FAILURE_PAUSE_THRESHOLD:
            return False
        self.load_failure_cooldown_until = time.time() + MODEL_LOAD_FAILURE_COOLDOWN_SECONDS
        self.model_load_failure_count = 0
        self.set_status(
            state="cooldown",
            message=(
                f"{self.noun.split(' ')[0]} model failed to load 3 times — "
                "retrying in 15 minutes."
            ),
            model_load_failures=0,
        )
        return True

    # -- the coordination turn ----------------------------------------------

    async def wait_for_turn(self) -> None:
        if work_coordination.manual_turn_blocked(self.key):
            self.set_status(
                state="waiting_for_turn",
                ready=False,
                message=f"{self.noun} waiting for other background work.",
            )
        await work_coordination.wait_for_manual_turn(self.key)
        if not self.uses_gpu:
            return
        if work_coordination.gpu_turn_blocked(self.key):
            self.set_status(
                state="waiting_for_gpu",
                ready=False,
                message=f"{self.noun} waiting for the GPU.",
            )
        await work_coordination.wait_for_gpu_turn(self.key)

    async def renew_turn(self) -> None:
        if work_coordination.lost_ownership(self.key, gpu=self.uses_gpu):
            self._release_extra()
        await self.wait_for_turn()
