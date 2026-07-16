"""The first phone-sized, real-write Azimuth Photo gate."""

from __future__ import annotations


def mobile_smoke(qa) -> None:
    qa.mark("load the phone timeline")
    qa.page.goto(f"{qa.base_url}/m", wait_until="domcontentloaded")
    timeline = qa.page.locator("#m-timeline")
    timeline.wait_for(state="visible")
    ranked = qa.page.request.get(f"{qa.base_url}/api/rankings?stacks=expanded&limit=120&offset=0&sort=date_taken")
    assert ranked.ok
    image_id = next(int(image["id"]) for image in ranked.json().get("images") or [] if image.get("flag") == "unflagged")
    cell = timeline.locator(f".mcell[data-id='{image_id}']")
    cell.wait_for(state="visible")

    qa.mark("open a photo in the mobile viewer")
    cell.click()
    viewer = qa.page.locator("#m-viewer:not([hidden])")
    viewer.wait_for(state="visible")
    qa.page.locator("#mv-img").wait_for(state="visible")

    qa.mark("favorite it from the mobile viewer")
    qa.page.locator("#mv-pick").click()
    qa.poll(
        "the mobile favorite flag API state",
        lambda: image_id in {
            int(image["id"])
            for image in (qa.page.request.get(f"{qa.base_url}/api/rankings?limit=4000&offset=0&flag=picked").json().get("images") or [])
        },
    )
    assert qa.page.locator("#mv-pick").get_attribute("aria-pressed") == "true"
