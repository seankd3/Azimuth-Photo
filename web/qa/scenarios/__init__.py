"""Named desktop UI scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from qa.scenarios.develop import develop_raw_workflow
from qa.scenarios.discovery import map_geo_browse, people_browse
from qa.scenarios.imports import import_cancel, import_commit, import_folder_preflight
from qa.scenarios.gate_cull import (
    collection_dnd,
    duplicates_review,
    flag_pick_reject,
    people_merge_rename,
    trash_selected_restore,
)
from qa.scenarios.gate_develop import develop_batch_export_sync
from qa.scenarios.mobile import mobile_smoke
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
from qa.scenarios.responsive import narrow_right_drawer
from qa.scenarios.trash import empty_trash_and_leave, trash_empty_offline_hub
from qa.scenarios.handshake import handshake_skew
from qa.scenarios.offline import grid_offline_thumbs


@dataclass(frozen=True)
class Scenario:
    name: str
    surface: str
    run: Callable
    expected_failure: str = ""
    mobile_viewport: bool = False


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
    Scenario("flag_pick_reject", "Cull flags", flag_pick_reject),
    Scenario("collection_dnd", "Collections", collection_dnd),
    Scenario("people_merge_rename", "People", people_merge_rename),
    Scenario("develop_batch_export_sync", "Develop batch", develop_batch_export_sync),
    Scenario("mobile_smoke", "Mobile", mobile_smoke, mobile_viewport=True),
    Scenario("search", "Search", search),
    Scenario("narrow_right_drawer", "Responsive panels", narrow_right_drawer),
    # Changes the seeded stack representative; keep after RAW workflows that expect image 1 first.
    Scenario("stack_promote_cover", "Stacks cover", stack_promote_cover),
    # Existing empty-Trash coverage needs the untouched seeded Trash rows.
    Scenario("empty_trash_and_leave", "Trash", empty_trash_and_leave),
    Scenario("import_commit", "Import commit", import_commit),
    Scenario("import_cancel", "Import cancel", import_cancel),
    # Destructive by design; keep after every scenario that opens active originals.
    Scenario("duplicates_review", "Stacks", duplicates_review),
    Scenario("trash_selected_restore", "Trash and restore", trash_selected_restore),
    Scenario("grid_offline_thumbs", "Offline media", grid_offline_thumbs),
    Scenario("trash_empty_offline_hub", "Trash", trash_empty_offline_hub),
    Scenario("handshake_skew", "Satellite contract", handshake_skew),
]
