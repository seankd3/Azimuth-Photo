"""Saved workspace browsing and restoration."""

from __future__ import annotations

SAVED_VIEW_NAME = "QA Landscape workspace"


def saved_view_workspace(qa) -> None:
    qa.goto_desktop()

    qa.mark("wait for the seeded workspace in Views")
    saved = qa.page.locator("#saved-view-list .saved-view-row", has_text=SAVED_VIEW_NAME)
    saved.wait_for(state="visible")

    qa.mark("leave All Photos, then restore the saved workspace")
    qa.page.locator("#library-list [data-lib='picked']").click()
    qa.poll(
        "the Picked scope",
        lambda: 0 < qa.current_count() < int(qa.manifest["visible_images"]),
    )
    saved.locator("button").first.click()
    qa.page.locator(
        "#ctx-crumbs .chip[data-facet='orientation']", has_text="Landscape"
    ).wait_for(state="visible")
    qa.poll(
        "the saved landscape workspace count",
        lambda: 0 < qa.current_count() < int(qa.manifest["visible_images"]),
    )
    assert qa.page.locator("#sort-select").input_value() == "date_taken"
