"""Deploy static gallery bundles into the portfolio site repo."""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from features.publish.builder import BundleSummary


MAX_PAGES_FILE_BYTES = 25 * 1024 * 1024
DEFAULT_SITE_REPO = "/home/sean/Projects/sean-kenneth-doherty"

_deploy_lock = asyncio.Lock()


@dataclass(frozen=True)
class PublishConfig:
    site_repo: str = DEFAULT_SITE_REPO
    project_name: str = "seankennethdoherty"
    branch: str = "master"

    @property
    def app_dir(self) -> Path:
        return Path(self.site_repo) / "app"

    @property
    def public_g_dir(self) -> Path:
        return self.app_dir / "public" / "g"

    @property
    def build_out_dir(self) -> Path:
        return self.app_dir / "out"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def text(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


@dataclass(frozen=True)
class DeployResult:
    summary: BundleSummary | None
    last_commit: str | None
    push_error: str | None = None


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


CommandRunner = Callable[[list[str], Path, dict[str, str] | None], CommandResult]
BundleWriter = Callable[[Path], BundleSummary]


def default_command_runner(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class GalleryDeployer:
    def __init__(
        self,
        config: PublishConfig | None = None,
        *,
        command_runner: CommandRunner = default_command_runner,
    ):
        self.config = config or PublishConfig(
            site_repo=os.environ.get("PHOTOARCHIVE_PORTFOLIO_REPO", DEFAULT_SITE_REPO)
        )
        self.command_runner = command_runner

    async def publish(
        self,
        *,
        slug: str,
        title: str,
        collection_id: int,
        published_rows: list[dict],
        write_bundle: BundleWriter,
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
                progress,
            )

    async def revoke(
        self,
        *,
        slug: str,
        collection_id: int,
        published_rows: list[dict],
        progress: Callable[[str], None] | None = None,
    ) -> DeployResult:
        async with _deploy_lock:
            return await asyncio.to_thread(
                self._revoke_sync,
                slug,
                int(collection_id),
                published_rows,
                progress,
            )

    def _publish_sync(
        self,
        slug: str,
        title: str,
        collection_id: int,
        published_rows: list[dict],
        write_bundle: BundleWriter,
        progress: Callable[[str], None] | None,
    ) -> DeployResult:
        self._preflight()
        self.config.public_g_dir.mkdir(parents=True, exist_ok=True)
        target = self.config.public_g_dir / slug
        if progress:
            progress("building")
        summary = write_bundle(target)
        manifest_rows = _rows_with_pending(
            published_rows,
            {
                "collection_id": collection_id,
                "slug": slug,
                "title": title,
                "published_at": time.time(),
                "updated_at": time.time(),
                "image_count": summary.photo_count,
            },
        )
        write_manifest(self.config.public_g_dir, manifest_rows)
        commit, did_commit = self._commit(f"Publish gallery {slug} ({summary.photo_count} photos)")
        if progress:
            progress("deploying")
        self._build_or_rollback(did_commit)
        push_error = self._deploy_and_push()
        return DeployResult(summary=summary, last_commit=commit, push_error=push_error)

    def _revoke_sync(
        self,
        slug: str,
        collection_id: int,
        published_rows: list[dict],
        progress: Callable[[str], None] | None,
    ) -> DeployResult:
        self._preflight()
        if progress:
            progress("building")
        shutil.rmtree(self.config.public_g_dir / slug, ignore_errors=True)
        manifest_rows = [
            row
            for row in published_rows
            if int(row.get("collection_id") or 0) != collection_id and row.get("slug") != slug
        ]
        write_manifest(self.config.public_g_dir, manifest_rows)
        commit, did_commit = self._commit(f"Revoke gallery {slug}")
        if progress:
            progress("deploying")
        self._build_or_rollback(did_commit)
        push_error = self._deploy_and_push()
        return DeployResult(summary=None, last_commit=commit, push_error=push_error)

    def _preflight(self) -> None:
        self.config.public_g_dir.mkdir(parents=True, exist_ok=True)
        dirty = self.command_runner(
            ["git", "status", "--porcelain", "--", "app/public/g"],
            Path(self.config.site_repo),
            None,
        )
        dirty_paths = [line[3:] if len(line) > 3 else line for line in dirty.stdout.splitlines() if line.strip()]
        if dirty_paths:
            raise PublishConflict("Portfolio gallery files have uncommitted changes.", paths=dirty_paths)
        pulled = self.command_runner(["git", "pull", "--ff-only"], Path(self.config.site_repo), None)
        if pulled.returncode != 0:
            raise PublishConflict("Portfolio repo diverged; git pull --ff-only failed.")

    def _commit(self, message: str) -> tuple[str | None, bool]:
        self.command_runner(["git", "add", "app/public/g"], Path(self.config.site_repo), None)
        diff = self.command_runner(
            ["git", "diff", "--cached", "--quiet", "--", "app/public/g"],
            Path(self.config.site_repo),
            None,
        )
        if diff.returncode == 0:
            head = self.command_runner(["git", "rev-parse", "HEAD"], Path(self.config.site_repo), None)
            return (head.stdout.strip() if head.returncode == 0 else None), False
        committed = self.command_runner(["git", "commit", "-m", message], Path(self.config.site_repo), None)
        if committed.returncode != 0:
            raise PublishDeployError("Portfolio gallery commit failed.", tail=_tail(committed.text))
        head = self.command_runner(["git", "rev-parse", "HEAD"], Path(self.config.site_repo), None)
        return (head.stdout.strip() if head.returncode == 0 else None), True

    def _build_or_rollback(self, did_commit: bool) -> None:
        env = {**os.environ, "NEXT_PUBLIC_SITE_BASE_PATH": ""}
        built = self.command_runner(["npm", "run", "build"], self.config.app_dir, env)
        if built.returncode == 0:
            return
        if did_commit:
            self.command_runner(["git", "reset", "--hard", "HEAD~1"], Path(self.config.site_repo), None)
        raise PublishDeployError("Portfolio build failed.", tail=_tail(built.text))

    def _deploy_and_push(self) -> str | None:
        with tempfile.TemporaryDirectory(prefix="pa-gallery-pages-") as temp_name:
            temp_path = Path(temp_name)
            shutil.copytree(self.config.build_out_dir, temp_path, dirs_exist_ok=True)
            _delete_large_files(temp_path)
            deployed = self.command_runner(
                [
                    "npx",
                    "wrangler",
                    "pages",
                    "deploy",
                    str(temp_path),
                    "--project-name",
                    self.config.project_name,
                    "--branch",
                    self.config.branch,
                ],
                self.config.app_dir,
                None,
            )
            if deployed.returncode != 0:
                raise PublishDeployError("Portfolio deploy failed.", tail=_tail(deployed.text))
        pushed = self.command_runner(["git", "push", "origin", self.config.branch], Path(self.config.site_repo), None)
        if pushed.returncode != 0:
            return _tail(pushed.text)
        return None


def write_manifest(public_g_dir: Path, rows: list[dict]) -> None:
    public_g_dir.mkdir(parents=True, exist_ok=True)
    galleries = []
    for row in rows:
        slug = row.get("slug") or ""
        if not slug:
            continue
        meta = gallery_meta_from_index(public_g_dir / slug / "index.html")
        cover = meta.get("cover") or f"/g/{slug}/thumb/sm/{meta['first_id']}.jpg" if meta.get("first_id") else ""
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
    (public_g_dir / "manifest.json").write_text(
        json.dumps({"galleries": galleries}, separators=(",", ":")),
        encoding="utf-8",
    )


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


def _rows_with_pending(rows: list[dict], pending: dict) -> list[dict]:
    collection_id = int(pending.get("collection_id") or 0)
    slug = pending.get("slug")
    kept = [
        row
        for row in rows
        if int(row.get("collection_id") or 0) != collection_id and row.get("slug") != slug
    ]
    return [pending, *kept]


def _delete_large_files(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and path.stat().st_size > MAX_PAGES_FILE_BYTES:
            path.unlink()


def _tail(text: str, *, lines: int = 40) -> str:
    return "\n".join((text or "").splitlines()[-lines:])
