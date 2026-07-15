"""Loupe and Develop click path."""

from __future__ import annotations


def loupe_and_develop(qa) -> None:
    qa.goto_desktop()

    qa.mark("open the highest-rated real JPEG in Loupe")
    first = qa.page.locator("#grid-flow .cell[data-id]").first
    first_id = int(first.get_attribute("data-id"))
    assert first_id <= 6, f"fixture expected a real Develop JPEG first, got image {first_id}"
    first.click()
    qa.page.locator("#view-loupe.active #loupe:not([hidden])").wait_for(state="visible")
    qa.page.wait_for_function(
        """expected => {
            const image = document.querySelector('#loupe-img');
            return Number(image?.dataset.imageId) === expected && image.complete && image.naturalWidth > 0;
        }""",
        arg=first_id,
    )

    qa.mark("click Develop and wait for a rendered canvas")
    qa.page.locator("#view-switch [data-view='develop']").click()
    qa.page.locator("#view-develop.active").wait_for(state="visible")
    qa.page.wait_for_function(
        """() => {
            const canvas = document.querySelector('#develop-canvas');
            return canvas?.classList.contains('ready') && canvas.width > 0 && canvas.height > 0;
        }""",
        timeout=30_000,
    )
    status = qa.page.locator("#develop-status")
    assert "error" not in (status.get_attribute("class") or ""), status.inner_text()
