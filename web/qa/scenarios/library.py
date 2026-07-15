"""Library grid, scope, filter, and scrubber scenarios."""

from __future__ import annotations

from qa.browser import number_from_text


def library_grid_virtualization(qa) -> None:
    qa.goto_desktop()
    assert qa.current_count() == int(qa.manifest["active_images"])

    qa.mark("scroll through multiple virtualized grid chunks")
    canvas = qa.page.locator("#canvas")

    def loaded_later_chunk():
        canvas.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        starts = qa.page.locator("#grid-flow .grid-chunk").evaluate_all(
            "nodes => nodes.map(node => Number(node.dataset.start || 0))"
        )
        return max(starts or [0]) >= 100

    qa.poll("a later virtualized grid chunk", loaded_later_chunk, timeout=20)
    assert qa.page.locator("#grid-flow .grid-chunk").count() >= 2


def scope_source_switch(qa) -> None:
    qa.goto_desktop()
    qa.mark("switch from All Photos to the hub mirror source")
    source = qa.page.locator("#source-list [data-source]").first
    source_count = number_from_text(source.locator(".nr-count").inner_text())
    source.click()
    qa.page.wait_for_function(
        """expected => {
            const text = document.querySelector('#ctx-count')?.textContent || '';
            const count = Number(text.replace(/[^0-9]/g, ''));
            return count === expected || !!document.querySelector('#grid-flow .grid-empty');
        }""",
        arg=source_count,
    )
    assert qa.current_count() == source_count, (
        f"source advertised {source_count} photos but rendered {qa.current_count()}"
    )
    assert 0 < source_count < int(qa.manifest["active_images"])


def scope_folder_switch(qa) -> None:
    qa.goto_desktop()
    qa.mark("switch from All Photos to a real nested folder")
    folder = qa.page.locator("#folder-tree [data-folder-path$='/Develop']")
    folder.wait_for(state="visible")
    folder_count = number_from_text(folder.locator(".folder-count").inner_text())
    folder.locator(".folder-main").click()
    qa.wait_count(folder_count)
    assert folder_count > 0

    qa.mark("return from the folder to All Photos")
    qa.all_photos()


def scope_collection_switch(qa) -> None:
    qa.goto_desktop()
    qa.mark("switch from All Photos to a collection")
    collection = qa.page.locator("#collection-list .coll-row").first
    collection_count = number_from_text(collection.locator(".nr-count").inner_text())
    collection.locator(".coll-main").click()
    qa.wait_count(collection_count)
    assert collection_count == int(qa.manifest["collection_images"])

    qa.mark("return from the collection to All Photos")
    qa.all_photos()


def sort_filter_date_jump(qa) -> None:
    qa.goto_desktop()

    qa.mark("sort by date and wait for the scrubber")
    qa.page.locator("#sort-select").select_option("date_taken")
    qa.page.locator("#date-scrubber.on").wait_for(state="visible")

    qa.mark("filter to landscape photos")
    qa.page.locator("#btn-filter").click()
    landscape = qa.page.locator(
        "#filter-popover [data-filter-section='orientation'] [data-toggle-key='orientation'][data-value='landscape']"
    )
    landscape.wait_for(state="visible")
    landscape.click()
    qa.poll(
        "a non-empty filtered landscape count",
        lambda: 0 < qa.current_count() < int(qa.manifest["active_images"]),
    )

    qa.mark("clear the landscape filter")
    if qa.page.locator("#filter-popover").is_hidden():
        qa.page.locator("#btn-filter").click()
    landscape = qa.page.locator(
        "#filter-popover [data-filter-section='orientation'] [data-toggle-key='orientation'][data-value='landscape']"
    )
    landscape.click()
    qa.wait_count(int(qa.manifest["active_images"]))

    qa.mark("jump to January 2018 with the date scrubber")
    target = qa.page.locator("#date-scrubber .ds-tick[data-month='2018-01']")
    target.wait_for(state="visible")
    target.click()
    qa.page.wait_for_function(
        """() => document.querySelector('#date-scrubber [data-month="2018-01"]')?.classList.contains('current')
            && document.querySelector('#date-scrubber')?.getAttribute('aria-busy') === 'false'"""
    )
    qa.poll(
        "the grid window to move to the scrubbed date",
        lambda: max(
            qa.page.locator("#grid-flow .cell[data-idx]").evaluate_all(
                "nodes => nodes.map(node => Number(node.dataset.idx || 0))"
            )
            or [0]
        )
        > 1_000,
    )
