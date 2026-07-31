"""Narrow-viewport behaviour: right info drawer and Develop layout."""

from __future__ import annotations


def narrow_right_drawer(qa) -> None:
    qa.goto_desktop()

    qa.mark("narrow the viewport below the 880px breakpoint")
    qa.page.set_viewport_size({"width": 820, "height": 900})
    button = qa.page.locator("#btn-right-panel")
    button.wait_for(state="visible")

    qa.mark("open the info drawer from the context bar button")
    button.click()
    qa.page.wait_for_selector("#shell.right-drawer-open")
    box = qa.page.locator("#panel-right").bounding_box()
    assert box and box["width"] > 0, "right panel has no on-screen box"
    assert box["x"] + box["width"] <= 821, f"drawer off screen: {box}"
    qa.page.locator("#metadata-panel").wait_for(state="visible")

    qa.mark("Escape dismisses the drawer like the left one")
    qa.page.keyboard.press("Escape")
    qa.page.wait_for_selector("#shell:not(.right-drawer-open)")
    assert qa.page.locator("#panel-scrim").is_hidden(), "scrim left behind"

    qa.mark("left and right drawers are mutually exclusive")
    qa.page.locator("#btn-left-drawer").click()
    qa.page.wait_for_selector("#shell.drawer-open")
    qa.page.keyboard.press("]")
    qa.page.wait_for_selector("#shell.right-drawer-open:not(.drawer-open)")
    qa.page.locator("#panel-scrim").click()
    qa.page.wait_for_selector("#shell:not(.right-drawer-open):not(.drawer-open)")
    assert qa.page.locator("#panel-scrim").is_hidden(), "scrim left behind"

    qa.mark("Develop layout drops the fixed 200px/320px rails when narrow")
    columns = qa.page.eval_on_selector(
        ".develop-layout", "el => getComputedStyle(el).gridTemplateColumns"
    )
    assert "320px" not in columns and "200px" not in columns, columns

    qa.mark("restore the wide viewport and the docked panel")
    qa.page.set_viewport_size({"width": 1600, "height": 1000})
    qa.page.wait_for_selector("#shell:not(.right-drawer-open)")
