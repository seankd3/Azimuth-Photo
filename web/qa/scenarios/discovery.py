"""People and Map browsing scenarios."""

from __future__ import annotations


def people_browse(qa) -> None:
    qa.goto_desktop()

    qa.mark("open People and wait for named and unnamed faces")
    qa.page.locator("#view-switch [data-view='people']").click()
    flow = qa.page.locator("#view-people.active #people-flow")
    named = flow.locator(".person-card.is-named", has_text="Ada QA")
    unnamed = flow.locator(".person-card.is-unnamed")
    named.wait_for(state="visible")
    unnamed.wait_for(state="visible")
    assert "3 photos" in named.locator(".person-count").inner_text()
    assert "2 photos" in unnamed.locator(".person-count").inner_text()
    qa.page.wait_for_function(
        """() => [...document.querySelectorAll('#people-flow .person-face img')]
            .filter(image => image.offsetParent !== null)
            .every(image => image.complete && image.naturalWidth > 0)"""
    )

    qa.mark("browse the named person's available face actions")
    named.locator("[data-act='menu']").click()
    menu = named.locator(".person-menu[role='menu']")
    menu.wait_for(state="visible")
    assert menu.get_by_text("Rename", exact=True).count() == 1
    assert menu.get_by_text("Merge with...", exact=True).count() == 1
    assert menu.get_by_text("Hide person", exact=True).count() == 1
    qa.page.keyboard.press("Escape")
    qa.poll("the person actions to close", lambda: menu.is_hidden())


def map_geo_browse(qa) -> None:
    qa.goto_desktop()

    qa.mark("open Map and render deterministic geo clusters")
    qa.page.locator("#view-switch [data-view='map']").click()
    stage = qa.page.locator("#view-map.active #map-stage")
    stage.locator("#map-svg[aria-label='Photo map']").wait_for(state="visible")
    pins = stage.locator(".map-pin[data-cluster]")
    qa.poll("both seeded map clusters", lambda: pins.count() == 2)
    assert f"{qa.manifest['located_images']} located photos shown" in stage.locator("#map-info").inner_text()

    qa.mark("open a multi-photo location and browse into Loupe")
    cluster = pins.filter(has_text="4").first
    cluster.click()
    popover = stage.locator("#map-popover.on")
    popover.wait_for(state="visible")
    assert "4 PHOTOS HERE" in popover.inner_text().upper()
    first = popover.locator("button[data-id]").first
    image_id = int(first.get_attribute("data-id"))
    first.click()
    qa.page.wait_for_function(
        "expected => Number(document.querySelector('#loupe-img')?.dataset.imageId) === expected",
        arg=image_id,
    )
    qa.page.locator("#view-loupe.active #loupe:not([hidden])").wait_for(state="visible")
