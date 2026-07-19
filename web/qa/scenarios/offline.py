"""Offline media states must settle honestly instead of shimmering forever."""

from __future__ import annotations

from qa.config import SCREENSHOT_DIR, scaled_timeout_ms


def grid_offline_thumbs(qa) -> None:
    """A dead satellite hub resolves cells and preserves the Loupe fallback frame."""

    qa.goto_desktop()

    qa.mark("keep the medium Loupe frame when its large original is unavailable")
    qa.page.route("**/api/thumb/lg/**", lambda route: route.fulfill(status=204))
    qa.page.locator("#grid-flow .cell[data-id]").first.click()
    qa.page.locator("#view-loupe.active #loupe:not([hidden])").wait_for(state="visible")
    qa.page.wait_for_function("() => document.querySelector('#loupe-img')?.naturalWidth > 0")
    qa.page.keyboard.press("Space")
    qa.page.locator("#loupe-offline-chip").wait_for(state="visible")
    assert qa.page.locator("#loupe-img").evaluate("image => image.naturalWidth > 0"), "Loupe lost its medium frame"
    qa.page.locator("#loupe-close").click()
    qa.page.unroute("**/api/thumb/lg/**")

    qa.mark("open the dead-hub source and resolve thumbnail cells to offline placeholders")
    qa.page.route("**/api/thumb/sm/**", lambda route: route.fulfill(status=204))
    qa.page.locator("#folder-tree [data-folder-source-path='hub://'] .folder-main").click()
    qa.page.locator("#grid-flow .cell[data-id]").first.wait_for(state="visible")
    qa.page.wait_for_function(
        """() => {
            const cells = [...document.querySelectorAll('#grid-flow .grid-chunk:not(.ghost) .cell[data-id]')].slice(0, 12);
            return cells.length === 12 && cells.every(cell => !cell.classList.contains('skel'))
                && cells.every(cell => cell.classList.contains('thumb-offline'));
        }""",
        timeout=scaled_timeout_ms(10_000),
    )
    assert qa.page.locator("#grid-flow .grid-chunk:not(.ghost) .cell.skel").count() == 0
    assert qa.page.locator("#grid-flow .grid-chunk:not(.ghost) .cell.thumb-offline").count() >= 12
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    qa.page.screenshot(path=str(SCREENSHOT_DIR / "grid_offline_thumbs.png"), full_page=True)
