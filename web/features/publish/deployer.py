"""Publish static gallery bundles to a configured directory."""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import signal
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import settings
from features.publish.builder import BundleSummary


HOOK_TIMEOUT_SECONDS = 15 * 60
HOOK_OUTPUT_LINES = 40
HOOK_ENV = "PHOTOARCHIVE_PUBLISH_HOOK"


def configured_publish_hook() -> str:
    """Server-side-only: env var wins, then the on-disk settings file. The
    hook executes shell after publish, so it is never writable via the API."""
    return (
        (os.environ.get(HOOK_ENV) or "").strip()
        or str(settings.get_settings().get("publish_hook") or "").strip()
    )

_deploy_lock = asyncio.Lock()


@dataclass(frozen=True)
class PublishConfig:
    publish_dir: str = ""
    publish_hook: str = ""
    hook_timeout_seconds: int = HOOK_TIMEOUT_SECONDS

    @classmethod
    def from_settings(cls) -> "PublishConfig":
        config = settings.get_settings()
        return cls(
            publish_dir=str(config.get("publish_dir") or "").strip(),
            publish_hook=configured_publish_hook(),
        )

    @property
    def public_g_dir(self) -> Path:
        return Path(self.publish_dir).expanduser()

    @property
    def enabled(self) -> bool:
        return bool(str(self.publish_dir or "").strip())


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def text(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


@dataclass(frozen=True)
class HookStatus:
    configured: bool
    command: str = ""
    returncode: int | None = None
    output: str = ""
    ran_at: float | None = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return (not self.configured) or self.returncode == 0

    def payload(self) -> dict:
        return {
            "configured": self.configured,
            "command": self.command,
            "returncode": self.returncode,
            "output": self.output,
            "ran_at": self.ran_at,
            "timed_out": self.timed_out,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class DeployResult:
    summary: BundleSummary | None
    last_commit: str | None = None
    push_error: str | None = None
    hook: HookStatus | None = None
    publish_row: dict | None = None


class PublishConflict(Exception):
    status_code = 409

    def __init__(self, message: str, *, paths: list[str] | None = None):
        super().__init__(message)
        self.paths = paths or []


class PublishDeployError(Exception):
    status_code = 502

    def __init__(self, message: str, *, tail: str = ""):
        super().__init__(message)
        self.tail = tail


class PublishSetupError(Exception):
    status_code = 409


CommandRunner = Callable[[str, Path, int], CommandResult]
BundleWriter = Callable[[Path], BundleSummary]
PublishPersister = Callable[[BundleSummary, HookStatus | None], dict]
RevokePersister = Callable[[HookStatus | None], bool]
PublishedRows = list[dict] | Callable[[], list[dict]]
ConfigFactory = Callable[[], PublishConfig]


def default_command_runner(command: str, cwd: Path, timeout_seconds: int) -> CommandResult:
    process: subprocess.Popen | None = None
    try:
        process = subprocess.Popen(
            _resolve_relative_command(command, cwd),
            cwd=str(cwd),
            env=os.environ.copy(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            **(
                {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                if os.name == "nt"
                else {"start_new_session": True}
            ),
        )
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return CommandResult(process.returncode or 0, stdout, stderr)
    except subprocess.TimeoutExpired:
        if process is not None:
            _kill_process_group(process)
            stdout, stderr = process.communicate()
        else:
            stdout, stderr = "", ""
        return CommandResult(124, stdout, stderr, timed_out=True)


class GalleryDeployer:
    def __init__(
        self,
        config: PublishConfig | None = None,
        *,
        command_runner: CommandRunner = default_command_runner,
        config_factory: ConfigFactory | None = None,
    ):
        self.config = config
        self.command_runner = command_runner
        self.config_factory = config_factory or PublishConfig.from_settings

    async def publish(
        self,
        *,
        slug: str,
        title: str,
        collection_id: int,
        published_rows: PublishedRows,
        write_bundle: BundleWriter,
        persist_publish: PublishPersister | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> DeployResult:
        async with _deploy_lock:
            return await asyncio.to_thread(
                self._publish_sync,
                slug,
                title,
                int(collection_id),
                published_rows,
                write_bundle,
                persist_publish,
                progress,
            )

    async def revoke(
        self,
        *,
        slug: str,
        collection_id: int,
        published_rows: PublishedRows,
        persist_revoke: RevokePersister | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> DeployResult:
        async with _deploy_lock:
            return await asyncio.to_thread(
                self._revoke_sync,
                slug,
                int(collection_id),
                published_rows,
                persist_revoke,
                progress,
            )

    async def retry_hook(self) -> HookStatus:
        """Run only the configured confirmation hook for a pending publish."""
        async with _deploy_lock:
            config = self._active_config()
            return await asyncio.to_thread(self._run_hook, config)

    def _publish_sync(
        self,
        slug: str,
        title: str,
        collection_id: int,
        published_rows: PublishedRows,
        write_bundle: BundleWriter,
        persist_publish: PublishPersister | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> DeployResult:
        config = self._active_config()
        public_g_dir = _publish_root(config)
        target = public_g_dir / slug
        now = time.time()
        if progress:
            progress("building")
        summary = write_bundle(target)
        manifest_rows = _rows_with_pending(
            _published_rows(published_rows),
            {
                "collection_id": collection_id,
                "slug": slug,
                "title": title,
                "published_at": now,
                "updated_at": now,
                "image_count": summary.photo_count,
            },
        )
        write_manifest(public_g_dir, manifest_rows)
        if progress:
            progress("hook")
        hook = self._run_hook(config)
        publish_row = persist_publish(summary, hook) if persist_publish else None
        return DeployResult(summary=summary, hook=hook, publish_row=publish_row)

    def _revoke_sync(
        self,
        slug: str,
        collection_id: int,
        published_rows: PublishedRows,
        persist_revoke: RevokePersister | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> DeployResult:
        config = self._active_config()
        public_g_dir = _publish_root(config)
        if progress:
            progress("building")
        try:
            shutil.rmtree(public_g_dir / slug)
        except OSError as exc:
            raise PublishDeployError(f"Could not remove live gallery '{slug}'. It is still published.") from exc
        manifest_rows = [
            row
            for row in _published_rows(published_rows)
            if int(row.get("collection_id") or 0) != collection_id and row.get("slug") != slug
        ]
        write_manifest(public_g_dir, manifest_rows)
        if progress:
            progress("hook")
        hook = self._run_hook(config)
        if persist_revoke:
            persist_revoke(hook)
        return DeployResult(summary=None, hook=hook)

    def _active_config(self) -> PublishConfig:
        config = self.config or self.config_factory()
        if not config.enabled:
            raise PublishSetupError("Choose a publishing folder before publishing this gallery.")
        return config

    def _run_hook(self, config: PublishConfig) -> HookStatus:
        command = str(config.publish_hook or "").strip()
        if not command:
            return HookStatus(configured=False)
        result = self.command_runner(command, config.public_g_dir, int(config.hook_timeout_seconds or HOOK_TIMEOUT_SECONDS))
        return HookStatus(
            configured=True,
            command=command,
            returncode=int(result.returncode),
            output=_tail(result.text),
            ran_at=time.time(),
            timed_out=bool(result.timed_out),
        )


def write_manifest(public_g_dir: Path, rows: list[dict]) -> None:
    public_g_dir.mkdir(parents=True, exist_ok=True)
    galleries = []
    for row in rows:
        slug = row.get("slug") or ""
        if not slug:
            continue
        meta = gallery_meta_from_index(public_g_dir / slug / "index.html")
        cover = meta.get("cover") or (f"/g/{slug}/thumb/sm/{meta['first_id']}.jpg" if meta.get("first_id") else "")
        galleries.append(
            {
                "slug": slug,
                "title": row.get("title") or slug,
                "photo_count": int(row.get("image_count") or meta.get("photo_count") or 0),
                "date_range": meta.get("date_range") or "",
                "cover": cover,
                "published_at": float(row.get("published_at") or row.get("updated_at") or 0),
            }
        )
    galleries.sort(key=lambda item: item["published_at"], reverse=True)
    manifest_path = public_g_dir / "manifest.json"
    temp_path = public_g_dir / f".manifest.tmp-{os.getpid()}-{time.time_ns()}.json"
    try:
        temp_path.write_text(
            json.dumps({"galleries": galleries}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp_path, manifest_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def gallery_meta_from_index(index_path: Path) -> dict:
    if not index_path.exists():
        return {}
    text = index_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r'<script[^>]*id=["\']gallery-data["\'][^>]*>(.*?)</script>',
        text,
        flags=re.DOTALL,
    )
    if not match:
        return {}
    try:
        data = json.loads(html.unescape(match.group(1)).strip() or "{}")
    except json.JSONDecodeError:
        return {}
    images = data.get("images") if isinstance(data, dict) else []
    first_id = None
    if images:
        try:
            first_id = int(images[0].get("id"))
        except (TypeError, ValueError):
            first_id = None
    return {
        "photo_count": int(data.get("photo_count") or len(images or [])),
        "date_range": data.get("date_range") or "",
        "first_id": first_id,
    }


def _publish_root(config: PublishConfig) -> Path:
    root = config.public_g_dir
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise PublishDeployError("Publishing folder is not a directory.")
    return root


def _rows_with_pending(rows: list[dict], pending: dict) -> list[dict]:
    collection_id = int(pending.get("collection_id") or 0)
    slug = pending.get("slug")
    kept = [
        row
        for row in rows
        if int(row.get("collection_id") or 0) != collection_id and row.get("slug") != slug
    ]
    return [pending, *kept]


def _published_rows(rows: PublishedRows) -> list[dict]:
    if callable(rows):
        return list(rows())
    return list(rows)


def _kill_process_group(process: subprocess.Popen) -> None:
    if os.name == "nt":
        # No killpg on Windows: taskkill /T fells the whole hook process tree.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _resolve_relative_command(command: str, cwd: Path) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if not parts:
        return command
    first = parts[0]
    if first.startswith("/") or "/" not in first or (cwd / first).exists():
        return command
    for parent in (cwd, *cwd.parents):
        candidate = parent / first
        if candidate.exists():
            parts[0] = str(candidate)
            return " ".join(shlex.quote(part) for part in parts)
    return command


def _tail(text: str, *, lines: int = HOOK_OUTPUT_LINES) -> str:
    return "\n".join((text or "").splitlines()[-lines:])
