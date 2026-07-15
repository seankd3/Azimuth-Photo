"""High-frequency cull and organization gates."""

from __future__ import annotations

from qa.browser import number_from_text


def _flagged_ids(qa, flag: str) -> set[int]:
    response = qa.page.request.get(f"{qa.base_url}/api/rankings?limit=4000&offset=0&flag={flag}")
    assert response.ok, f"rankings returned HTTP {response.status}"
    return {int(image["id"]) for image in response.json().get("images") or []}


def _library_count(qa, key: str) -> int:
    return number_from_text(qa.page.locator(f"#library-list [data-lib='{key}'] .nr-count").inner_text())


def _people(qa) -> list[dict]:
    payload = qa.page.request.get(f"{qa.base_url}/api/people?limit=500").json()
    sections = payload.get("sections") or {}
    rows = [
        *(sections.get("named_people") or []),
        *(sections.get("most_seen") or []),
        *(sections.get("other_faces") or []),
    ]
    return list({int(person["id"]): person for person in rows}.values())


def flag_pick_reject(qa) -> None:
    qa.goto_desktop()
    cells = qa.page.locator("#grid-flow .cell[data-id]")
    picked_id = int(cells.first.get_attribute("data-id"))
    rejected_id = int(cells.nth(1).get_attribute("data-id"))
    picked_before = _library_count(qa, "picked")
    rejected_before = _library_count(qa, "rejected")

    qa.mark("pick a selected grid cell with P")
    cells.first.locator(".c-check").click(force=True)
    qa.page.locator("#sel-count").get_by_text("1 selected", exact=True).wait_for(state="visible")
    qa.page.keyboard.press("p")
    qa.poll("the picked flag API state", lambda: picked_id in _flagged_ids(qa, "picked"))
    picked_cell = qa.page.locator(f"#grid-flow .cell[data-id='{picked_id}']")
    qa.poll("the picked grid glyph", lambda: "picked" in (picked_cell.locator(".c-flag").get_attribute("class") or ""))
    qa.poll("Picked library count", lambda: _library_count(qa, "picked") == picked_before + 1)

    qa.mark("reject a second photo from Loupe with X")
    cells.nth(1).click()
    qa.page.locator("#view-loupe.active #loupe:not([hidden])").wait_for(state="visible")
    qa.page.keyboard.press("x")
    qa.poll("the rejected flag API state", lambda: rejected_id in _flagged_ids(qa, "rejected"))
    qa.poll("Rejected library count", lambda: _library_count(qa, "rejected") == rejected_before + 1)
    qa.page.keyboard.press("g")
    rejected_cell = qa.page.locator(f"#grid-flow .cell[data-id='{rejected_id}']")
    qa.poll("the rejected grid glyph", lambda: "rejected" in (rejected_cell.locator(".c-flag").get_attribute("class") or ""))

    qa.mark("clear both cull flags with U")
    picked_cell.locator(".c-check").click(force=True)
    rejected_cell.locator(".c-check").click(force=True)
    qa.page.keyboard.press("u")
    qa.poll("both flags to clear through the API", lambda: (
        picked_id not in _flagged_ids(qa, "picked")
        and rejected_id not in _flagged_ids(qa, "rejected")
    ))
    qa.poll("library counts to return", lambda: (
        _library_count(qa, "picked") == picked_before
        and _library_count(qa, "rejected") == rejected_before
    ))


def trash_selected_restore(qa) -> None:
    qa.goto_desktop()
    cells = qa.page.locator("#grid-flow .cell[data-id]")
    ids = [int(cells.nth(index).get_attribute("data-id")) for index in (40, 41)]
    all_before = qa.current_count()

    qa.mark("select two photos and move them to Trash with Delete")
    cells.nth(40).locator(".c-check").click(force=True)
    cells.nth(41).locator(".c-check").click(force=True)
    qa.page.locator("#sel-count").get_by_text("2 selected", exact=True).wait_for(state="visible")
    qa.page.keyboard.press("Delete")
    qa.poll("the two selected photos in Trash", lambda: {
        int(image["id"]) for image in qa.page.request.get(f"{qa.base_url}/api/trash?limit=500&offset=0").json()["images"]
    }.issuperset(ids))
    # Deleting while the grid is mounted already reloads All Photos. Waiting
    # for that real reload avoids cancelling it with a redundant lens click.
    qa.wait_count(all_before - 2)

    qa.mark("open Trash and restore one selected photo")
    qa.page.locator("#library-list [data-lib='trash']").click()
    restored_id = ids[0]
    restored_cell = qa.page.locator(f"#trash-body .cell[data-id='{restored_id}']")
    restored_cell.wait_for(state="visible")
    restored_cell.locator(".c-check").click(force=True)
    with qa.page.expect_response(
        lambda response: response.url.endswith("/api/images/restore") and response.request.method == "POST"
    ) as response_info:
        qa.page.locator("#trash-restore").click()
    assert response_info.value.status == 200
    qa.poll("one restored row to leave Trash", lambda: qa.page.locator(f"#trash-body .cell[data-id='{restored_id}']").count() == 0)
    payload = qa.page.request.get(f"{qa.base_url}/api/trash?limit=500&offset=0").json()
    remaining = {int(image["id"]) for image in payload["images"]}
    assert restored_id not in remaining and ids[1] in remaining, remaining

    qa.mark("verify restored status back in All Photos")
    with qa.page.expect_response(lambda response: "/api/rankings?" in response.url):
        qa.page.locator("#library-list [data-lib='all']").click()
    qa.wait_count(all_before - 1)


def collection_dnd(qa) -> None:
    qa.goto_desktop()
    cells = qa.page.locator("#grid-flow .cell[data-id]")
    ids = [int(cells.nth(index).get_attribute("data-id")) for index in (40, 41)]
    row = qa.page.locator("#collection-list .coll-row[data-coll-id='1']")
    before = number_from_text(row.locator(".nr-count").inner_text())

    qa.mark("drag two selected photos onto QA Favorites")
    cells.nth(40).locator(".c-check").click(force=True)
    cells.nth(41).locator(".c-check").click(force=True)
    with qa.page.expect_response(
        lambda response: response.url.endswith("/api/user-collections/1/images") and response.request.method == "POST"
    ) as response_info:
        cells.nth(40).drag_to(row)
    assert response_info.value.status == 200
    qa.poll("the collection count to increase by two", lambda: number_from_text(row.locator(".nr-count").inner_text()) == before + 2)

    qa.mark("open the collection and verify both dropped photos")
    row.locator(".coll-main").click()
    qa.wait_count(before + 2)
    response = qa.page.request.get(f"{qa.base_url}/api/user-collections/1")
    assert response.ok
    actual = {int(image["id"]) for image in response.json()["collection"]["images"]}
    assert set(ids).issubset(actual), actual


def people_merge_rename(qa) -> None:
    qa.goto_desktop()
    people = qa.manifest["people"]
    source_id, target_id, hide_id = (people["merge_source"], people["merge_target"], people["hide"])

    qa.mark("merge the two seeded unnamed people")
    qa.page.locator("#view-switch [data-view='people']").click()
    source = qa.page.locator(f"#people-flow .person-card[data-person-id='{source_id}']")
    target = qa.page.locator(f"#people-flow .person-card[data-person-id='{target_id}']")
    source.wait_for(state="visible")
    source.locator("[data-act='menu']").click()
    source.locator("[data-act='merge-start']").click()
    target.click()
    with qa.page.expect_response(
        lambda response: response.url.endswith("/api/people/merge") and response.request.method == "POST"
    ) as response_info:
        qa.page.locator("#people-merge-pop [data-act='confirm-drop-merge']").click()
    assert response_info.value.status == 200
    qa.poll("the source person to disappear from the API", lambda: not any(
        int(person["id"]) == source_id for person in _people(qa)
    ))

    qa.mark("rename the merged person")
    target = qa.page.locator(f"#people-flow .person-card[data-person-id='{target_id}']")
    target.wait_for(state="visible")
    target.locator("[data-act='menu']").click()
    target.locator("button[role='menuitem'][data-act='rename']").click()
    rename = target.locator(".person-rename input")
    rename.fill("Merged QA")
    with qa.page.expect_response(
        lambda response: response.url.endswith(f"/api/people/{target_id}/label") and response.request.method == "POST"
    ) as response_info:
        rename.press("Enter")
    assert response_info.value.status == 200

    qa.mark("hide a separate person and wait for the durable write")
    hide = qa.page.locator(f"#people-flow .person-card[data-person-id='{hide_id}']")
    hide.locator("[data-act='menu']").click()
    hide.locator("[data-act='ignore']").click()
    qa.poll(
        "the hidden person API state",
        lambda: not any(int(person["id"]) == hide_id for person in _people(qa)),
        timeout=12,
    )
    merged = next(person for person in _people(qa) if int(person["id"]) == target_id)
    assert merged.get("name") == "Merged QA", merged


def duplicates_review(qa) -> None:
    qa.goto_desktop()
    stack_id = int(qa.manifest["exact_duplicate_stack_id"])
    keep_id, loser_id = [int(value) for value in qa.manifest["exact_duplicate_ids"]]

    qa.mark("keep the best seeded exact duplicate and trash its loser")
    qa.page.locator("#find-duplicates").click()
    row = qa.page.locator(f"#duplicates-body .stack-row[data-stack='{stack_id}']")
    row.wait_for(state="visible")
    with qa.page.expect_response(
        lambda response: response.url.endswith("/api/images/trash") and response.request.method == "POST"
    ) as response_info:
        row.locator(f"[data-stack-keep='{stack_id}']").click()
    assert response_info.value.status == 200
    qa.poll("the loser in Trash", lambda: loser_id in {
        int(image["id"]) for image in qa.page.request.get(f"{qa.base_url}/api/trash?limit=500&offset=0").json()["images"]
    })
    assert keep_id not in {
        int(image["id"]) for image in qa.page.request.get(f"{qa.base_url}/api/trash?limit=500&offset=0").json()["images"]
    }
