"""Manual keyword and IPTC persistence through the photo panel."""

from __future__ import annotations

from urllib.parse import urlparse


KEYWORD_PATH = "QA > Portfolio > Night"
IPTC = {
    "title": "QA night study",
    "caption": "Deterministic IPTC persistence check",
    "copyright": "Azimuth Photo QA",
    "creator": "QA Harness",
}


def _image_metadata(qa, image_id: int, kind: str) -> dict:
    response = qa.page.request.get(f"{qa.base_url}/api/images/{image_id}/{kind}")
    assert response.ok, f"{kind} returned HTTP {response.status}"
    return response.json()


def _mount_iptc_editor(qa, image_id: int) -> None:
    qa.page.evaluate(
        """async imageId => {
            const panel = await import('/static/js/desktop/keywords_panel.js');
            await panel.renderIptcMetadata({ id: imageId });
        }""",
        image_id,
    )


def keyword_and_iptc_persistence(qa) -> None:
    qa.goto_desktop()

    qa.mark("focus a photo and assign a hierarchical keyword")
    first = qa.page.locator("#grid-flow .cell[data-id]").first
    image_id = int(first.get_attribute("data-id"))
    first.click()
    qa.page.wait_for_function(
        "expected => Number(document.querySelector('#loupe-img')?.dataset.imageId) === expected",
        arg=image_id,
    )
    def keyword_panel_is_stable() -> bool:
        input_ = qa.page.locator("#keyword-input")
        if input_.count() != 1:
            return False
        return input_.evaluate(
            """input => {
                if (input.dataset.qaStable === '1') return true;
                input.dataset.qaStable = '1';
                return false;
            }"""
        )

    qa.poll("the keyword panel to finish loading", keyword_panel_is_stable)
    keyword_input = qa.page.locator("#keyword-input")
    keyword_input.fill(KEYWORD_PATH)
    with qa.page.expect_response(
        lambda response: urlparse(response.url).path == "/api/keywords/resolve"
        and response.request.method == "POST"
    ) as response_info:
        keyword_input.press("Enter")
    assert response_info.value.status == 201
    qa.page.locator("#keywords-panel .keyword-token", has_text=KEYWORD_PATH).wait_for(state="visible")
    keywords = _image_metadata(qa, image_id, "keywords").get("keywords", [])
    assert any(keyword.get("path") == KEYWORD_PATH and int(keyword.get("direct") or 0) for keyword in keywords), keywords

    qa.mark("edit every IPTC field and save it")
    # The component is real, but its missing production mount is reported in
    # qa-expand-findings.md. Exercise the existing editor without product edits.
    _mount_iptc_editor(qa, image_id)
    editor = qa.page.locator("#iptc-fields .iptc-editor")
    editor.wait_for(state="attached")
    editor.locator("summary").click()
    for field, value in IPTC.items():
        editor.locator(f"[data-iptc='{field}']").fill(value)
    with qa.page.expect_response(
        lambda response: urlparse(response.url).path == f"/api/images/{image_id}/iptc"
        and response.request.method == "PUT"
    ) as response_info:
        editor.locator("[data-iptc-save]").click()
    assert response_info.value.status == 200
    saved_iptc = _image_metadata(qa, image_id, "iptc").get("iptc") or {}
    assert all(saved_iptc.get(field) == value for field, value in IPTC.items()), saved_iptc

    qa.mark("leave the photo, return, and verify keyword and IPTC persistence in the UI")
    qa.page.locator("#loupe-next").click()
    qa.page.wait_for_function(
        "expected => Number(document.querySelector('#loupe-img')?.dataset.imageId) !== expected",
        arg=image_id,
    )
    qa.page.locator("#loupe-prev").click()
    qa.page.wait_for_function(
        "expected => Number(document.querySelector('#loupe-img')?.dataset.imageId) === expected",
        arg=image_id,
    )
    qa.page.locator("#keywords-panel .keyword-token", has_text=KEYWORD_PATH).wait_for(state="visible")
    _mount_iptc_editor(qa, image_id)
    reopened = qa.page.locator("#iptc-fields .iptc-editor")
    reopened.wait_for(state="attached")
    for field, value in IPTC.items():
        qa.poll(
            f"persisted IPTC {field}",
            lambda field=field, value=value: reopened.locator(f"[data-iptc='{field}']").input_value() == value,
        )
