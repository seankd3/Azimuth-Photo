"""Named desktop UI scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from qa.scenarios.develop import develop_raw_workflow
from qa.scenarios.discovery import map_geo_browse, people_browse
from qa.scenarios.imports import import_folder_preflight
from qa.scenarios.metadata import keyword_and_iptc_persistence
from qa.scenarios.publishing import collections_and_publishing
from qa.scenarios.saved_views import saved_view_workspace
from qa.scenarios.stacks import stack_expand_and_collapse, stack_promote_cover
from qa.scenarios.chrome import (
    folder_context_menu,
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
    Scenario("stack_expand_and_collapse", "Grid stacks", stack_expand_and_collapse),
    Scenario("people_browse", "People", people_browse),
    Scenario("map_geo_browse", "Map", map_geo_browse),
    Scenario("saved_view_workspace", "Saved views", saved_view_workspace),
    Scenario("source_context_menu", "Source context menu", source_context_menu),
    Scenario("folder_context_menu", "Folder context menu", folder_context_menu),
    Scenario("loupe_and_develop", "Loupe and Develop", loupe_and_develop),
    Scenario("develop_raw_workflow", "Develop RAW workflow", develop_raw_workflow),
    Scenario("collections_and_publishing", "Collections and Publishing", collections_and_publishing),
    Scenario("keyword_and_iptc_persistence", "Keywording and metadata", keyword_and_iptc_persistence),
    Scenario("settings_panel", "Settings panel", settings_panel),
    Scenario("import_folder_preflight", "Import preflight", import_folder_preflight),
    Scenario("search", "Search", search),
    # Changes the seeded stack representative; keep after RAW workflows that expect image 1 first.
    Scenario("stack_promote_cover", "Stacks cover", stack_promote_cover),
    # Destructive by design; keep last so read-only scenarios never depend on its state.
    Scenario("empty_trash_and_leave", "Trash", empty_trash_and_leave),
]
