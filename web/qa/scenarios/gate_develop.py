"""Batch Develop gates: queue export and sync a deliberate edit."""

from __future__ import annotations

from qa.scenarios.develop import _settings


def develop_batch_export_sync(qa) -> None:
    qa.goto_desktop()
    cells = qa.page.locator("#grid-flow .cell[data-id]")
    ids = [int(cells.nth(index).get_attribute("data-id")) for index in range(3)]

    qa.mark("select three photos and queue a Develop batch export")
    cells.first.locator(".c-check").click(force=True)
    cells.nth(2).locator(".c-check").click(modifiers=["Shift"], force=True)
    with qa.page.expect_response(
        lambda response: response.url.endswith("/api/develop/export/batch") and response.request.method == "POST"
    ) as response_info:
        cells.first.locator(".c-menu").click(force=True)
        qa.page.locator("#grid-pop-menu [data-act='export-develop']").click()
    assert response_info.value.status == 202
    qa.poll(
        "the three-file Develop export to finish",
        lambda: (status := qa.page.request.get(f"{qa.base_url}/api/develop/export/batch/status").json()).get("state") == "complete"
        and status.get("done") == len(ids),
        timeout=45,
    )
    status = qa.page.request.get(f"{qa.base_url}/api/develop/export/batch/status").json()
    assert len(status.get("results") or []) == len(ids), status
    assert not status.get("errors"), status
    assert all(int(result.get("bytes") or 0) > 0 for result in status["results"]), status

    qa.mark("set white balance and tone on one photo, then sync them to the other two")
    # Selection remains active for Sync, so use the keyboard Loupe command on
    # the focused first cell instead of treating a selected cell as a click.
    source_id = ids[0]
    qa.page.keyboard.press("e")
    qa.page.locator("#view-loupe.active #loupe:not([hidden])").wait_for(state="visible")
    qa.page.wait_for_function(
        "expected => Number(document.querySelector('#loupe-img')?.dataset.imageId) === expected",
        arg=source_id,
    )
    qa.page.locator("#view-switch [data-view='develop']").click()
    qa.page.locator("#view-develop.active").wait_for(state="visible")
    qa.page.wait_for_function(
        """() => {
            const canvas = document.querySelector('#develop-canvas');
            return canvas?.classList.contains('ready') && canvas.width > 0 && canvas.height > 0;
        }"""
    )
    temperature = qa.page.locator("#develop-panels .develop-slider[data-setting='Temperature'] input")
    exposure = qa.page.locator("#develop-panels .develop-slider[data-setting='Exposure2012'] input")
    temperature.fill("6100")
    temperature.press("Tab")
    exposure.fill("0.7")
    exposure.press("Tab")
    qa.poll(
        "the source settings to persist",
        lambda: _settings(qa, source_id).get("Temperature") == 6100 and _settings(qa, source_id).get("Exposure2012") == 0.7,
    )
    with qa.page.expect_response(
        lambda response: response.url.endswith("/api/develop/sync") and response.request.method == "POST"
    ) as response_info:
        qa.page.locator("#develop-toolbar [data-action='sync']").click()
        dialog = qa.page.locator(".develop-sync-dialog")
        dialog.wait_for(state="visible")
        for checkbox in dialog.locator("[data-sync-group]").all():
            if checkbox.get_attribute("data-sync-group") not in {"wb", "tone"}:
                checkbox.uncheck()
        dialog.locator("[data-sync-confirm]").click()
    payload = response_info.value.json()
    assert response_info.value.status == 200 and len(payload.get("synced") or []) == 2, payload
    for image_id in ids[1:]:
        settings = _settings(qa, image_id)
        assert settings.get("Temperature") == 6100 and settings.get("Exposure2012") == 0.7, settings
