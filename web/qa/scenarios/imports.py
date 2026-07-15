"""Import folder selection and preflight scenario."""

from __future__ import annotations

from pathlib import Path


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


def import_commit(qa) -> None:
    """Exercise the currently shipped chooser through its real Import button."""

    qa.goto_desktop()
    all_before = int(qa.page.request.get(f"{qa.base_url}/api/counts").json().get("total") or 0)
    qa.mark("choose the seeded folder and click Import")
    qa.page.locator("#import-view").click()
    modal = qa.page.locator("#import-modal[role='dialog']")
    with qa.page.expect_file_chooser() as chooser_info:
        modal.locator("#import-folder").click()
    chooser_info.value.set_files(qa.manifest["import_source"])
    expected = int(qa.manifest["import_source_images"])
    qa.poll("the selected import files", lambda: modal.locator("#import-selection-summary").inner_text() == f"{expected} items ready")
    with qa.page.expect_response(
        lambda response: response.url.endswith("/api/imports") and response.request.method == "POST"
    ) as response_info:
        modal.locator("#import-start").click()
    response = response_info.value
    assert response.status == 200, f"Import returned HTTP {response.status}"
    payload = response.json()
    assert int(payload.get("imported_files") or 0) == expected, payload
    batch_id = int(payload["batch_id"])
    qa.poll(
        "the completed import batch",
        lambda: qa.page.request.get(f"{qa.base_url}/api/imports/{batch_id}").json().get("batch", {}).get("status") == "complete",
    )
    # The importer first focuses its batch. Let that real grid response render
    # before changing lenses, otherwise the navigation legitimately aborts it.
    qa.poll("the completed batch in the grid", lambda: qa.page.locator("#grid-flow .cell[data-id]").count() > 0)
    with qa.page.expect_response(
        lambda response: "/api/rankings?" in response.url and "import_batch=" not in response.url
    ):
        qa.page.locator("#library-list [data-lib='all']").click()
    qa.poll(
        "new files in the All Photos API count",
        lambda: int(qa.page.request.get(f"{qa.base_url}/api/counts").json().get("total") or 0) == all_before + expected,
    )
    batch = qa.page.request.get(f"{qa.base_url}/api/imports/{batch_id}").json()["batch"]
    destination = Path(batch["destination_path"])
    assert batch.get("destination_mode") == "date_shoot", batch
    assert destination.name == "2026-07-15" and destination.parent.name == "2026", batch
    imported_paths = [Path(image["filepath"]) for image in batch["images"]]
    assert (
        len(imported_paths) == expected
        and all(path.is_file() and path.parent.name == "QA Card" and path.parent.parent == destination for path in imported_paths)
    ), batch


def import_cancel(qa) -> None:
    """Required staged-job contract; XFAIL until p0fix lands on develop.

    The desktop chooser is still wired to the older synchronous endpoint, so this
    intentionally drives the available staged API after opening Import. The
    assertion is the product promise: partial work is retained and its batch is
    truthfully marked cancelled rather than complete.
    """

    qa.goto_desktop()
    qa.mark("open Import and start the seeded cancellable staged job")
    qa.page.locator("#import-view").click()
    scan = qa.page.request.post(
        f"{qa.base_url}/api/import/scan",
        data={"path": qa.manifest["cancellable_import_source"], "include_subfolders": True},
    )
    assert scan.ok, scan.text()
    scan_id = scan.json()["scan_id"]
    qa.poll(
        "the cancellable source scan",
        lambda: qa.page.request.get(f"{qa.base_url}/api/import/scan/{scan_id}").json().get("status") == "done",
    )
    commit = qa.page.request.post(
        f"{qa.base_url}/api/import/commit",
        data={"scan_id": scan_id, "keys": "all_checked_default", "mode": "copy"},
    )
    assert commit.ok, commit.text()
    job_id, batch_id = commit.json()["job_id"], int(commit.json()["batch_id"])
    cancelled = qa.page.request.post(f"{qa.base_url}/api/import/jobs/{job_id}/cancel", data={})
    assert cancelled.ok, cancelled.text()
    qa.poll(
        "the cancelled job terminal state",
        lambda: qa.page.request.get(f"{qa.base_url}/api/import/jobs/{job_id}").json().get("phase") == "cancelled",
    )
    batch = qa.page.request.get(f"{qa.base_url}/api/imports/{batch_id}").json()["batch"]
    assert batch.get("status") == "cancelled", batch
