"""Single command desktop E2E runner with a triaged terminal report."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from qa.browser import BrowserHarness
from qa.config import REPORT_PATH, SERVER_LOG
from qa.fixture import ensure_fixture, reset_fixture
from qa.scenarios import SCENARIOS
from qa.server import ProbeServer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic desktop Playwright QA")
    parser.add_argument("--list", action="store_true", help="list scenario names and exit")
    parser.add_argument("--scenario", action="append", default=[], help="run only this named scenario (repeatable)")
    return parser


def _print_result(result) -> None:
    print(f"{result.status:<4}  {result.name:<30} {result.duration_seconds:>6.2f}s  [{result.surface}]", flush=True)
    if result.status == "FAIL":
        print(f"      step: {result.step}", flush=True)
        print(f"      error: {result.error}", flush=True)
        for item in result.evidence.problems():
            print(f"      evidence: {item}", flush=True)
        if result.screenshot:
            print(f"      screenshot: {result.screenshot}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.list:
        for scenario in SCENARIOS:
            print(f"{scenario.name}\t{scenario.surface}")
        return 0

    selected = SCENARIOS
    if args.scenario:
        requested = set(args.scenario)
        selected = [scenario for scenario in SCENARIOS if scenario.name in requested]
        missing = requested - {scenario.name for scenario in selected}
        if missing:
            print(f"Unknown scenario(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        print(f"Playwright is unavailable in web/.venv: {exc}", file=sys.stderr)
        return 2

    _, built = ensure_fixture()
    manifest = reset_fixture()
    print(
        f"QA fixture: {'built' if built else 'reused'} · "
        f"{manifest['active_images']:,} active / {manifest['visible_images']:,} collapsed-grid + "
        f"{manifest['trash_images']} trash rows",
        flush=True,
    )

    started = time.monotonic()
    results = []
    base_url = ""
    offline_names = {"trash_empty_offline_hub", "grid_offline_thumbs"}
    offline = [scenario for scenario in selected if scenario.name in offline_names]
    standard = [scenario for scenario in selected if scenario.name not in offline_names]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            for scenarios, offline_hub in ((standard, False), (offline, True)):
                if not scenarios:
                    continue
                if offline_hub:
                    # Satellite scenarios start a worker that may be retrying a dead
                    # hub. Give each destructive/offline proof a fresh fixture so that
                    # retry work from one cannot lock the next scenario's catalog.
                    scenario_groups = [[scenario] for scenario in scenarios]
                else:
                    scenario_groups = [scenarios]
                for scenario_group in scenario_groups:
                    if offline_hub:
                        manifest = reset_fixture()
                    with ProbeServer(offline_hub=offline_hub) as server:
                        base_url = server.base_url
                        label = "offline satellite" if offline_hub else "direct owner"
                        print(f"Probe server: {base_url} ({label}, isolated PHOTOARCHIVE_HOME)", flush=True)
                        harness = BrowserHarness(browser, server.base_url, manifest)
                        for scenario in scenario_group:
                            result = harness.run(scenario)
                            results.append(result)
                            _print_result(result)
        finally:
            browser.close()

    elapsed = round(time.monotonic() - started, 3)
    failed = [result for result in results if result.status == "FAIL"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "duration_seconds": elapsed,
        "fixture": manifest,
        "summary": {"passed": len(results) - len(failed), "failed": len(failed), "total": len(results)},
        "server_log": str(SERVER_LOG),
        "scenarios": [result.as_dict() for result in results],
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    verdict = "FAIL" if failed else "PASS"
    print(
        f"\n{verdict}: {len(results) - len(failed)} passed, {len(failed)} failed, "
        f"{elapsed:.2f}s total · report {REPORT_PATH}",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
