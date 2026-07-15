"""Named desktop UI scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from qa.scenarios.chrome import (
    folder_context_menu,
    import_entry,
    search,
    settings_panel,
    source_context_menu,
)
from qa.scenarios.library import (
    library_grid_virtualization,
    scope_collection_switch,
    scope_folder_switch,
    scope_source_switch,
    sort_filter_date_jump,
)
from qa.scenarios.photo import loupe_and_develop
from qa.scenarios.trash import empty_trash_and_leave


@dataclass(frozen=True)
class Scenario:
    name: str
    surface: str
    run: Callable


SCENARIOS = [
    Scenario("library_grid_virtualization", "Library grid", library_grid_virtualization),
    Scenario("scope_source_switch", "Source scope", scope_source_switch),
    Scenario("scope_folder_switch", "Folder scope", scope_folder_switch),
    Scenario("scope_collection_switch", "Collection scope", scope_collection_switch),
    Scenario("sort_filter_date_jump", "Sort, filter, date scrubber", sort_filter_date_jump),
    Scenario("source_context_menu", "Source context menu", source_context_menu),
    Scenario("folder_context_menu", "Folder context menu", folder_context_menu),
    Scenario("loupe_and_develop", "Loupe and Develop", loupe_and_develop),
    Scenario("settings_panel", "Settings panel", settings_panel),
    Scenario("import_entry", "Import entry", import_entry),
    Scenario("search", "Search", search),
    # Destructive by design; keep last so read-only scenarios never depend on its state.
    Scenario("empty_trash_and_leave", "Trash", empty_trash_and_leave),
]
