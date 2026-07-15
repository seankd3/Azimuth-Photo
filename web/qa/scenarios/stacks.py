"""Collapsed Grid stacks and cover promotion."""

from __future__ import annotations

from urllib.parse import urlparse


def stack_expand_and_collapse(qa) -> None:
    qa.goto_desktop()
    stack_id = int(qa.manifest["stack_id"])
    member_count = len(qa.manifest["stack_members"])

    qa.mark("expand the seeded collapsed stack in Grid")
    badge = qa.page.locator(f"#grid-flow .c-stack[data-stack-id='{stack_id}']")
    badge.wait_for(state="visible")
    badge.click()
    tray = qa.page.locator(f"#grid-flow .stack-tray[data-stack-id='{stack_id}']")
    tray.wait_for(state="visible")
    assert badge.get_attribute("aria-expanded") == "true"
    assert tray.locator(".cell.stack-member").count() == member_count - 1

    qa.mark("collapse the expanded stack back to one cover")
    tray.locator(".stack-tray-collapse").click()
    qa.poll(
        "the stack tray to collapse",
        lambda: qa.page.locator(f"#grid-flow .stack-tray[data-stack-id='{stack_id}']").count() == 0,
    )
    assert badge.get_attribute("aria-expanded") == "false"


def stack_promote_cover(qa) -> None:
    qa.goto_desktop()
    stack_id = int(qa.manifest["stack_id"])

    qa.mark("open Stacks and promote a different cover")
    qa.page.locator("#find-duplicates").click()
    row = qa.page.locator(f"#duplicates-body .stack-row[data-stack='{stack_id}']")
    row.wait_for(state="visible")
    promote = row.locator("[data-set-cover][data-stack-id]").first
    promoted_id = int(promote.get_attribute("data-set-cover"))
    assert promoted_id != int(qa.manifest["stack_members"][0])

    def is_cover_response(response) -> bool:
        return (
            urlparse(response.url).path == f"/api/stacks/{stack_id}/representative"
            and response.request.method == "POST"
        )

    with qa.page.expect_response(is_cover_response) as response_info:
        promote.click()
    assert response_info.value.status == 200
    qa.poll(
        "the promoted stack cover to repaint",
        lambda: row.locator(f".stack-photo.is-cover [data-open-id='{promoted_id}']").count() == 1,
    )

    saved = qa.page.request.get(f"{qa.base_url}/api/stacks/{stack_id}")
    assert saved.ok, f"stack lookup returned HTTP {saved.status}"
    assert int(saved.json()["representative"]["id"]) == promoted_id
