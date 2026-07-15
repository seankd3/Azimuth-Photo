"""Import folder selection and preflight scenario."""

from __future__ import annotations


def import_folder_preflight(qa) -> None:
    qa.goto_desktop()

    qa.mark("open Import and choose the seeded source folder")
    qa.page.locator("#import-view").click()
    modal = qa.page.locator("#import-modal[role='dialog']")
    modal.wait_for(state="visible")
    with qa.page.expect_file_chooser() as chooser_info:
        modal.locator("#import-folder").click()
    chooser_info.value.set_files(qa.manifest["import_source"])

    expected = int(qa.manifest["import_source_images"])
    qa.poll(
        "the selected folder to reach Import preflight",
        lambda: modal.locator("#import-selection-summary").inner_text() == f"{expected} items ready",
    )
    assert modal.locator("#import-folder-input").evaluate("input => input.files.length") == expected

    qa.mark("verify selected photos before importing")
    previews = modal.locator("#import-preview-grid .import-preview-item")
    qa.poll("a thumbnail for every selected photo", lambda: previews.count() == expected)
    assert previews.locator("img").count() == expected
    qa.page.wait_for_function(
        """() => [...document.querySelectorAll('#import-preview-grid .import-preview-item img')]
            .every(image => image.complete && image.naturalWidth > 0)"""
    )
    assert previews.locator(".import-preview-name").all_inner_texts() == [
        f"qa-import-{index}.jpg" for index in range(1, expected + 1)
    ]

    qa.mark("verify the populated pre-import options grid")
    preflight = modal.locator(".import-grid")
    preflight.wait_for(state="visible")
    assert preflight.locator("#import-shoot-date").input_value(), "Import date was not populated"
    assert preflight.locator("#import-destination-mode").input_value() == "date_shoot"
    assert preflight.locator("#import-root").input_value(), "Import root was not populated"
    assert modal.locator("#import-start").is_enabled()
    assert modal.locator("#import-status").inner_text() == "Ready to import."

    qa.mark("close Import without writing to the fixture library")
    modal.locator("#import-close").click()
    qa.page.locator("#import-scrim").wait_for(state="hidden")
