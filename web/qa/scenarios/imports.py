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
    selected_count = modal.locator("#import-folder-input").evaluate("input => input.files.length")
    assert selected_count == expected, f"file chooser selected {selected_count} files, expected {expected}"

    qa.mark("verify selected photos before importing")
    previews = modal.locator("#import-preview-grid .import-preview-item")
    qa.poll("a thumbnail for every selected photo", lambda: previews.count() == expected)
    image_count = previews.locator("img").count()
    assert image_count == expected, f"preview grid rendered {image_count} images, expected {expected}"
    qa.page.wait_for_function(
        """() => [...document.querySelectorAll('#import-preview-grid .import-preview-item img')]
            .every(image => image.complete && image.naturalWidth > 0)"""
    )
    names = previews.locator(".import-preview-name").all_inner_texts()
    expected_names = [f"qa-import-{index}.jpg" for index in range(1, expected + 1)]
    assert sorted(names) == expected_names, f"unexpected import previews: {names!r}"

    qa.mark("verify the populated pre-import options grid")
    preflight = modal.locator(".import-grid")
    preflight.wait_for(state="visible")
    assert preflight.locator("#import-shoot-date").input_value(), "Import date was not populated"
    destination_mode = preflight.locator("#import-destination-mode").input_value()
    assert destination_mode == "date_shoot", f"expected Date + shoot destination, got {destination_mode!r}"
    assert preflight.locator("#import-root").input_value(), "Import root was not populated"
    assert modal.locator("#import-start").is_enabled(), "Import button remained disabled after a valid folder selection"
    status = modal.locator("#import-status").inner_text()
    assert status == "Ready to import.", f"unexpected import status: {status!r}"

    qa.mark("close Import without writing to the fixture library")
    modal.locator("#import-close").click()
    qa.page.locator("#import-scrim").wait_for(state="hidden")
