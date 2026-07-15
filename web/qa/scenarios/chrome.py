"""Navigation chrome, context menus, settings, import, and search."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


EXPECTED_SCOPE_MENU_ITEMS = (
    "Show in scope with subfolders",
    "Open in Refine",
    "Open in Explorer",
    "Export view",
)


def _assert_scope_menu(qa, anchor) -> None:
    anchor.click(button="right")
    menu = qa.page.locator("[role='menu']:visible").last
    menu.wait_for(state="visible", timeout=5_000)
    text = menu.inner_text()
    missing = [item for item in EXPECTED_SCOPE_MENU_ITEMS if item not in text]
    assert not missing, f"context menu missing {missing}; rendered items were: {text!r}"


def source_context_menu(qa) -> None:
    qa.goto_desktop()
    qa.mark("right-click a source and assert the complete Windows menu")
    _assert_scope_menu(qa, qa.page.locator("#source-list [data-source]").first)


def folder_context_menu(qa) -> None:
    qa.goto_desktop()
    qa.mark("right-click a folder and assert the complete Windows menu")
    _assert_scope_menu(qa, qa.page.locator("#folder-tree [data-folder-path]").first)


def settings_panel(qa) -> None:
    qa.goto_desktop()
    qa.mark("open Settings and wait for real settings content")
    qa.page.locator("#system-btn").click()
    qa.page.locator("#drawer[aria-hidden='false']").wait_for(state="visible")
    qa.page.locator("#drawer-save-state").wait_for(state="visible")
    qa.page.locator("#drawer-close").click()
    qa.page.locator("#drawer[aria-hidden='true']").wait_for(state="attached")


def search(qa) -> None:
    qa.goto_desktop()
    qa.mark("run a live metadata search")
    query = "qa-nebula-00042"

    def is_search_response(response) -> bool:
        parsed = urlparse(response.url)
        return parsed.path == "/api/rankings" and parse_qs(parsed.query).get("q") == [query]

    with qa.page.expect_response(is_search_response, timeout=DEFAULT_SEARCH_TIMEOUT_MS) as response_info:
        qa.page.locator("#scope-input").fill(query)
    response = response_info.value
    assert response.status == 200, f"search returned HTTP {response.status}"
    payload = response.json()
    assert int(payload.get("visible_images") or 0) >= 1, f"search found no results: {payload}"
    qa.poll("the search result count in the open omnibox", lambda: "1 results" in qa.page.locator("#scope-drop").inner_text())


DEFAULT_SEARCH_TIMEOUT_MS = 30_000
