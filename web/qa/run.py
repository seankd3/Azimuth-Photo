"""Desktop E2E runner with durable evidence and one-shot flake confirmation."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

from qa.archive import RunArchive
from qa.browser import BrowserHarness
from qa.config import WAIT_MULTIPLIER
from qa.fixture import ensure_fixture, reset_fixture
from qa.scenarios import SCENARIOS
from qa.server import ProbeServer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic desktop Playwright QA")
    parser.add_argument("--list", action="store_true", help="list scenario names and exit")
    parser.add_argument("--scenario", action="append", default=[], help="run only this named scenario (repeatable)")
    return parser


def _print_result(result) -> None:
    print(f"{result.status:<5} {result.name:<30} {result.duration_seconds:>6.2f}s  [{result.surface}]", flush=True)
    if result.status == "FAIL":
        print(f"      step: {result.step}", flush=True)
        print(f"      error: {result.error}", flush=True)
        for item in result.evidence.problems():
            print(f"      evidence: {item}", flush=True)
        if result.screenshot:
            print(f"      screenshot: {result.screenshot}", flush=True)


def _log_offset(path) -> int:
    return path.stat().st_size if path.exists() else 0


def _record(scenario, status: str, attempts: list, artifacts: list[str]) -> dict:
    return {
        "name": scenario.name,
        "surface": scenario.surface,
        "status": status,
        "attempts": [result.as_dict() for result in attempts],
        "artifacts": artifacts,
        "expected_failure": scenario.expected_failure,
    }


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

    archive = RunArchive()
    _, built = ensure_fixture()
    manifest = reset_fixture()
    print(
        f"QA fixture: {'built' if built else 'reused'} · "
        f"{manifest['active_images']:,} active / {manifest['visible_images']:,} collapsed-grid + "
        f"{manifest['trash_images']} trash rows · wait x{WAIT_MULTIPLIER:g}",
        flush=True,
    )

    started = time.monotonic()
    records: list[dict] = []
    retry_queue: list[tuple[object, dict, str, object, list[str]]] = []
    offline_names = {"trash_empty_offline_hub", "grid_offline_thumbs"}
    offline = [scenario for scenario in selected if scenario.name in offline_names]
    old_hub = [scenario for scenario in selected if scenario.name == "handshake_skew"]
    special = offline_names | {"handshake_skew"}
    standard = [scenario for scenario in selected if scenario.name not in special]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            groups = (
                (standard, {}, "direct owner"),
                (offline, {"offline_hub": True}, "offline satellite"),
                (old_hub, {"old_hub": True}, "old-hub satellite"),
            )
            for scenarios, server_options, label in groups:
                if not scenarios:
                    continue
                scenario_groups = [[scenario] for scenario in scenarios] if server_options.get("offline_hub") else [scenarios]
                for scenario_group in scenario_groups:
                    if server_options:
                        manifest = reset_fixture()
                    with ProbeServer(log_path=archive.server_log, **server_options) as server:
                        print(f"Probe server: {server.base_url} ({label}, isolated AZIMUTH_HOME)", flush=True)
                        harness = BrowserHarness(browser, server.base_url, manifest, server.old_hub)
                        for scenario in scenario_group:
                            start = _log_offset(archive.server_log)
                            primary = harness.run(scenario, failure_dir=archive.failure_dir(scenario.name, 1))
                            _print_result(primary)
                            if primary.status == "PASS" and not scenario.expected_failure:
                                records.append(_record(scenario, "PASS", [primary], []))
                                continue
                            if primary.status == "FAIL" and scenario.expected_failure:
                                print(f"XFAIL {scenario.name}: {scenario.expected_failure}", flush=True)
                                records.append(_record(scenario, "XFAIL", [primary], []))
                                continue
                            if primary.status == "PASS" and scenario.expected_failure:
                                primary.status = "XPASS"
                                records.append(_record(scenario, "XPASS", [primary], []))
                                continue
                            artifact = str(archive.capture_failure(primary, attempt=1, server_log_start=start))
                            retry_queue.append((scenario, server_options, label, primary, [artifact]))

            for scenario, server_options, label, primary, artifacts in retry_queue:
                print(f"FLAKY? {scenario.name}: retrying once in a fresh ProbeServer", flush=True)
                manifest = reset_fixture()
                start = _log_offset(archive.server_log)
                with ProbeServer(log_path=archive.server_log, **server_options) as server:
                    print(f"Probe server: {server.base_url} ({label}, fresh retry)", flush=True)
                    retry = BrowserHarness(browser, server.base_url, manifest, server.old_hub).run(
                        scenario, failure_dir=archive.failure_dir(scenario.name, 2)
                    )
                _print_result(retry)
                if retry.status == "PASS":
                    print(f"FLAKY  {scenario.name}: failed first attempt and passed fresh retry", flush=True)
                    records.append(_record(scenario, "FLAKY", [primary, retry], artifacts))
                else:
                    artifacts.append(str(archive.capture_failure(retry, attempt=2, server_log_start=start)))
                    records.append(_record(scenario, "FAIL", [primary, retry], artifacts))
        finally:
            browser.close()

    elapsed = round(time.monotonic() - started, 3)
    failed = [record for record in records if record["status"] in {"FAIL", "XPASS"}]
    flaky = [record for record in records if record["status"] == "FLAKY"]
    expected = [record for record in records if record["status"] == "XFAIL"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": elapsed,
        "wait_multiplier": WAIT_MULTIPLIER,
        "fixture": manifest,
        "summary": {
            "passed": len(records) - len(failed) - len(expected),
            "failed": len(failed),
            "flaky": len(flaky),
            "expected_failures": len(expected),
            "total": len(records),
        },
        "server_log": str(archive.server_log),
        "scenarios": records,
    }
    report_path = archive.write_report(report)
    verdict = "FAIL" if failed else "PASS"
    print(
        f"\n{verdict}: {len(records) - len(failed) - len(expected)} passed, {len(expected)} expected-fail, "
        f"{len(failed)} failed, {len(flaky)} flaky, "
        f"{elapsed:.2f}s total · report {report_path}",
        flush=True,
    )
    if failed:
        print("\nFAILED ASSERTIONS AND ARTIFACTS (final output):", flush=True)
        for record in failed:
            final = record["attempts"][-1]
            print(f"FAIL {record['name']} at {final['step']}: {final['error']}", flush=True)
            for artifact in record["artifacts"]:
                print(f"  artifacts: {artifact}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
