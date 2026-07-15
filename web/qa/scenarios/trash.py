"""Destructive Trash regression scenario."""

from __future__ import annotations


def empty_trash_and_leave(qa) -> None:
    qa.goto_desktop()
    expected = int(qa.manifest["trash_images"])

    qa.mark("open Trash and wait for seeded rows")
    qa.page.locator("#library-list [data-lib='trash']").click()
    qa.page.locator("#view-trash.active #trash:not([hidden])").wait_for(state="visible")
    qa.page.wait_for_function(
        r"""expected => {
            const text = document.querySelector('#trash-count')?.textContent || '';
            return Number((text.match(/[\d,]+/) || ['0'])[0].replaceAll(',', '')) === expected;
        }""",
        arg=expected,
    )
    assert qa.page.locator("#trash-body .trash-cell").count() == expected

    qa.mark("empty Trash through the typed confirmation")
    qa.page.locator("#trash-empty").click()
    dialog = qa.page.locator(".typed-confirm")
    dialog.wait_for(state="visible")
    dialog.locator("input").fill(str(expected))
    with qa.page.expect_response(
        lambda response: response.url.endswith("/api/trash/empty") and response.request.method == "POST"
    ) as response_info:
        dialog.locator("[data-confirm]").click()
    response = response_info.value
    assert response.status == 200, f"empty-trash returned HTTP {response.status}"
    payload = response.json()
    assert payload.get("deleted_count") == expected, f"empty-trash did not delete every row: {payload}"

    qa.mark("assert Trash actually empties in both UI and API")
    qa.page.locator("#trash-body").get_by_text("Trash is empty", exact=True).wait_for(state="visible")
    api_response = qa.page.request.get(f"{qa.base_url}/api/trash?limit=10&offset=0")
    assert api_response.ok, f"trash verification returned HTTP {api_response.status}"
    api_payload = api_response.json()
    assert api_payload.get("total") == 0 and api_payload.get("images") == [], api_payload

    qa.mark("navigate from emptied Trash back to All Photos promptly")
    started = __import__("time").monotonic()
    qa.page.locator("#library-list [data-lib='all']").click()
    qa.page.locator("#view-grid.active").wait_for(state="visible", timeout=10_000)
    qa.wait_count(int(qa.manifest["visible_images"]), timeout_ms=10_000)
    qa.page.locator("#grid-flow .cell[data-id]").first.wait_for(state="visible", timeout=10_000)
    elapsed = __import__("time").monotonic() - started
    assert elapsed < 10, f"All Photos took {elapsed:.2f}s after emptying Trash"
