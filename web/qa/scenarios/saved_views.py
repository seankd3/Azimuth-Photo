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

    qa.mark("create the current workspace without a post-save error")
    created_name = "QA Created workspace"
    qa.page.locator("#save-view-btn").click()
    form = qa.page.locator("#saved-view-form")
    form.wait_for(state="visible")
    form.locator("#saved-view-name").fill(created_name)
    form.locator("button[type='submit']").click()
    form.wait_for(state="hidden")
    qa.page.locator("#saved-view-list .saved-view-row", has_text=created_name).wait_for(state="visible")
