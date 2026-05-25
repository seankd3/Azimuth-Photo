import os
import subprocess
import time


class StaticAssetContext:
    def __init__(self, *, app_dir: str, repo_dir: str | None = None, started_at: float | None = None):
        self.app_dir = app_dir
        self.repo_dir = repo_dir or os.path.dirname(app_dir)
        self.started_at = started_at if started_at is not None else time.time()
        self.git_commit = self._git_commit()

    def _git_commit(self) -> str | None:
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self.repo_dir,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1,
            ).strip()
        except Exception:
            return None

    def static_version(self) -> str:
        static_dir = os.path.join(self.app_dir, "static")
        try:
            mtimes = [os.path.getmtime(os.path.join(static_dir, "style.css"))]
            for root, _dirs, files in os.walk(static_dir):
                for filename in files:
                    if filename.endswith(".js"):
                        mtimes.append(os.path.getmtime(os.path.join(root, filename)))
            return str(int(max(mtimes)))
        except OSError:
            return str(int(self.started_at))

    def template_context(self, request) -> dict:
        return {"request": request, "static_version": self.static_version()}


def warm_templates(templates, template_names: tuple[str, ...]) -> None:
    for template_name in template_names:
        templates.env.get_template(template_name)
