"""Playwright lifecycle, polling, and failure evidence."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from qa.config import DEFAULT_ACTION_TIMEOUT_MS, SCREENSHOT_DIR


def number_from_text(value: str) -> int:
    match = re.search(r"([\d,]+)", value or "")
    if not match:
        raise AssertionError(f"expected a numeric count, got {value!r}")
    return int(match.group(1).replace(",", ""))


@dataclass
class BrowserEvidence:
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    server_errors: list[str] = field(default_factory=list)

    def attach(self, page) -> None:
        page.on("console", self._console)
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        page.on("requestfailed", self._request_failed)
        page.on("response", self._response)

    def _console(self, message) -> None:
        if message.type == "error":
            self.console_errors.append(f"console.error: {message.text}")

    def _request_failed(self, request) -> None:
        failure = request.failure or "unknown failure"
        # Grid virtualization deliberately clears offscreen <img> sources. Chromium
        # reports those cancelled thumbnail transfers as ERR_ABORTED, not a failed app request.
        if "/api/thumb/" in request.url and "ERR_ABORTED" in failure:
            return
        self.failed_requests.append(f"{request.method} {request.url} — {failure}")

    def _response(self, response) -> None:
        if response.status >= 500:
            self.server_errors.append(f"HTTP {response.status} {response.request.method} {response.url}")

    def problems(self) -> list[str]:
        return self.console_errors + self.page_errors + self.failed_requests + self.server_errors


@dataclass
class ScenarioResult:
    name: str
    surface: str
    status: str
    duration_seconds: float
    step: str
    error: str = ""
    screenshot: str = ""
    evidence: BrowserEvidence = field(default_factory=BrowserEvidence)

    def as_dict(self) -> dict:
        return asdict(self)


class ScenarioContext:
    def __init__(self, page, base_url: str, manifest: dict, old_hub=None) -> None:
        self.page = page
        self.base_url = base_url
        self.manifest = manifest
        self.old_hub = old_hub
        self.current_step = "starting"

    def mark(self, step: str) -> None:
        self.current_step = step

    def goto_desktop(self) -> None:
        self.mark("load /d and wait for the first usable grid")
        self.page.goto(f"{self.base_url}/d", wait_until="domcontentloaded", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        self.page.locator("#library-list [data-lib='all']").wait_for(state="visible")
        self.page.locator("#grid-flow .cell[data-id]").first.wait_for(state="visible")
        self.page.wait_for_function(
            """() => {
                const text = document.querySelector('#ctx-count')?.textContent || '';
                const value = Number(text.replace(/[^0-9]/g, ''));
                return value > 0;
            }"""
        )

    def wait_count(self, expected: int, *, timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS) -> None:
        self.page.wait_for_function(
            """expected => {
                const text = document.querySelector('#ctx-count')?.textContent || '';
                return Number(text.replace(/[^0-9]/g, '')) === expected;
            }""",
            arg=expected,
            timeout=timeout_ms,
        )

    def current_count(self) -> int:
        return number_from_text(self.page.locator("#ctx-count").inner_text())

    def all_photos(self) -> None:
        self.page.locator("#library-list [data-lib='all']").click()
        self.wait_count(int(self.manifest["visible_images"]))

    def poll(self, description: str, predicate: Callable[[], object], *, timeout: float = 20.0):
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                value = predicate()
                if value:
                    return value
            except Exception as exc:  # The DOM may be between renders; retry until the named deadline.
                last_error = exc
            self.page.wait_for_timeout(100)
        detail = f": {last_error}" if last_error else ""
        raise AssertionError(f"timed out polling for {description}{detail}")


class BrowserHarness:
    def __init__(self, browser, base_url: str, manifest: dict, old_hub=None) -> None:
        self.browser = browser
        self.base_url = base_url
        self.manifest = manifest
        self.old_hub = old_hub

    def run(self, scenario) -> ScenarioResult:
        started = time.monotonic()
        context = self.browser.new_context(
            viewport={"width": 1600, "height": 1000},
            reduced_motion="reduce",
            service_workers="block",
        )
        context.add_init_script(
            "Object.defineProperty(Navigator.prototype, 'platform', { configurable: true, get: () => 'Win32' });"
        )
        page = context.new_page()
        page.set_default_timeout(DEFAULT_ACTION_TIMEOUT_MS)
        evidence = BrowserEvidence()
        evidence.attach(page)
        qa = ScenarioContext(page, self.base_url, self.manifest, self.old_hub)
        error = ""
        screenshot = ""
        status = "PASS"
        try:
            scenario.run(qa)
            problems = evidence.problems()
            if problems:
                raise AssertionError("browser emitted forbidden errors:\n" + "\n".join(problems))
        except Exception as exc:
            status = "FAIL"
            error = f"{type(exc).__name__}: {exc}"
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            target = SCREENSHOT_DIR / f"{scenario.name}.png"
            try:
                page.screenshot(path=str(target), full_page=True)
                screenshot = str(target)
            except Exception as screenshot_error:
                error += f"; screenshot failed: {screenshot_error}"
        finally:
            context.close()
        return ScenarioResult(
            name=scenario.name,
            surface=scenario.surface,
            status=status,
            duration_seconds=round(time.monotonic() - started, 3),
            step=qa.current_step,
            error=error,
            screenshot=screenshot,
            evidence=evidence,
        )
