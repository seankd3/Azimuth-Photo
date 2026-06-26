from test_support import *  # noqa: F401,F403


class UiContractsTests(BackendTestCase):
    async def test_compare_page_uses_shared_search_controls_without_deep_search_button(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "templates", "library.html"), encoding="utf-8") as fh:
            library_template = fh.read()
        with open(os.path.join(base_dir, "templates", "compare.html"), encoding="utf-8") as fh:
            compare_template = fh.read()
        with open(os.path.join(base_dir, "templates", "_bottom_bar_search.html"), encoding="utf-8") as fh:
            bottom_bar_search_template = fh.read()
        with open(os.path.join(base_dir, "templates", "_filters.html"), encoding="utf-8") as fh:
            filters_template = fh.read()
        with open(os.path.join(base_dir, "templates", "_background_work_panel.html"), encoding="utf-8") as fh:
            background_work_template = fh.read()
        with open(os.path.join(base_dir, "static", "js", "legacy", "app.js"), encoding="utf-8") as fh:
            script = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "pagination.js"), encoding="utf-8") as fh:
            pagination = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "load_more.js"), encoding="utf-8") as fh:
            load_more = fh.read()
        with open(os.path.join(base_dir, "static", "js", "bottom_bar_controls.js"), encoding="utf-8") as fh:
            bottom_bar_controls = fh.read()
        with open(os.path.join(base_dir, "static", "js", "legacy", "library_init_bridge.js"), encoding="utf-8") as fh:
            library_init_bridge = fh.read()
        with open(os.path.join(base_dir, "static", "js", "legacy", "search_action_bridge.js"), encoding="utf-8") as fh:
            search_action_bridge = fh.read()
        with open(os.path.join(base_dir, "static", "js", "compare", "page_controller.js"), encoding="utf-8") as fh:
            compare_page_controller = fh.read()

        self.assertIn('data-command-layout="culling-workbench"', library_template)
        self.assertIn('data-command-layout="culling-workbench"', compare_template)
        for template in (library_template, compare_template):
            self.assertLess(template.index("{% include '_bottom_bar_search.html' %}"), template.index('data-command-zone="status"'))
            self.assertLess(template.index('data-command-zone="status"'), template.index("{% include '_bottom_bar_nav.html' %}"))
            self.assertLess(template.rindex('data-command-zone="mode-tools"'), template.index("{% include '_bottom_bar_nav.html' %}"))
            self.assertLess(template.index("{% include '_bottom_bar_nav.html' %}"), template.index("{% include '_bottom_bar_work.html' %}"))
            self.assertNotIn("PhotoArchive.", template)
        self.assertLess(library_template.index('id="sort-toggles"'), library_template.index("{% include '_bottom_bar_search.html' %}"))
        self.assertLess(library_template.index('data-action="set-library-view"'), library_template.index("{% include '_bottom_bar_nav.html' %}"))
        self.assertLess(compare_template.index('id="bar-strategies"'), compare_template.index("{% include '_bottom_bar_search.html' %}"))
        self.assertLess(compare_template.index('class="bar-section bar-coverage"'), compare_template.index("{% include '_bottom_bar_nav.html' %}"))
        self.assertIn("{% include '_bottom_bar_search.html' %}", compare_template)
        self.assertIn('data-mosaic-strategy="explore"', compare_template)
        self.assertIn('data-action="mosaic-shuffle"', compare_template)
        self.assertIn('data-action="set-sort-field"', library_template)
        self.assertIn('<option value="taste">Taste</option>', library_template)
        self.assertIn('data-action="toggle-sort-dir"', library_template)
        self.assertIn('data-action="set-library-view"', library_template)
        self.assertIn('id="library-load-more"', library_template)
        self.assertIn('id="library-load-more-btn"', library_template)
        self.assertIn('bindLibraryLoadMoreButton', library_init_bridge)
        self.assertIn("SEARCH_RANKINGS_PAGE_SIZE = 5000", pagination)
        self.assertIn("hasActiveSearch: hasActiveTextSearch(searchQuery)", script)
        self.assertIn("return false;", load_more[load_more.index("function shouldShowLibraryLoadMore"):])
        with open(os.path.join(base_dir, "templates", "_bottom_bar_work.html"), encoding="utf-8") as fh:
            bottom_bar_work_template = fh.read()
        self.assertIn('bar-work-wrap', bottom_bar_work_template)
        self.assertIn('data-action="toggle-background-work-panel"', bottom_bar_work_template)
        bar_work_close = bottom_bar_work_template.index('</div>', bottom_bar_work_template.index('id="bar-work"'))
        panel_include = bottom_bar_work_template.index("{% include '_background_work_panel.html' %}")
        self.assertLess(bar_work_close, panel_include)
        self.assertIn('id="search-input"', bottom_bar_search_template)
        self.assertNotIn("Deep Search", bottom_bar_search_template)
        self.assertNotIn("PhotoArchive.runDeepSearch()", bottom_bar_search_template)
        self.assertNotIn("deep-search", bottom_bar_search_template)
        self.assertNotIn("PhotoArchive.", bottom_bar_search_template)
        self.assertNotIn("PhotoArchive.", filters_template)
        self.assertNotIn("PhotoArchive.", background_work_template)
        self.assertIn("function initSearchInputControls()", script)
        self.assertIn("initSearchInputControls,", script)
        self.assertIn("bindSharedBottomBarControls", bottom_bar_controls)
        self.assertIn("data-action=\"clear-search\"", bottom_bar_search_template)
        self.assertIn("data-filter-control=\"select\"", filters_template)
        self.assertIn("initSearchInputControls();", compare_page_controller)
        self.assertIn("initSearchInputControls();", library_init_bridge)
        self.assertIn("bindSharedBottomBarControlsImpl({", compare_page_controller)
        self.assertIn("bindSharedBottomBarControlsImpl({", library_init_bridge)
        self.assertNotIn("runDeepSearch", search_action_bridge)

    async def test_taste_sort_frontend_contract(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "static", "js", "library", "sort.js"), encoding="utf-8") as fh:
            sort_js = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "sort_controller.js"), encoding="utf-8") as fh:
            sort_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "display.js"), encoding="utf-8") as fh:
            display = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "shell.js"), encoding="utf-8") as fh:
            shell = fh.read()
        with open(os.path.join(base_dir, "static", "js", "legacy", "app.js"), encoding="utf-8") as fh:
            app_js = fh.read()

        self.assertIn("taste: { desc: 'taste', asc: 'taste', defaultDesc: true }", sort_js)
        self.assertIn("sortField === 'similarity' || sortField === 'taste'", sort_controller)
        self.assertIn("% taste", display)
        self.assertIn("Taste sorting needs more signal", shell)
        self.assertIn("data.taste_available === false", app_js)

    async def test_library_selection_reveals_collection_action(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "static", "js", "library", "batch.js"), encoding="utf-8") as fh:
            batch_js = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "batch_controller.js"), encoding="utf-8") as fh:
            batch_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "library", "collection_sheet.js"), encoding="utf-8") as fh:
            collection_sheet = fh.read()
        with open(os.path.join(base_dir, "static", "style.css"), encoding="utf-8") as fh:
            styles = fh.read()

        self.assertIn('data-batch-action="collection"', batch_js)
        self.assertIn("createCollectionSheetController", batch_controller)
        self.assertIn("/api/user-collections", collection_sheet)
        self.assertIn("Add to collection", collection_sheet)
        self.assertIn(".collection-sheet", styles)

    async def test_loupe_filmstrip_uses_visible_library_pool_total(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "static", "js", "loupe", "controller.js"), encoding="utf-8") as fh:
            loupe_controller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "loupe", "filmstrip.js"), encoding="utf-8") as fh:
            filmstrip = fh.read()
        with open(os.path.join(base_dir, "static", "js", "loupe", "navigation.js"), encoding="utf-8") as fh:
            navigation = fh.read()
        with open(os.path.join(base_dir, "static", "js", "legacy", "app.js"), encoding="utf-8") as fh:
            script = fh.read()

        self.assertIn("getLibraryPoolTotal", loupe_controller)
        self.assertIn("poolTotal", filmstrip)
        self.assertIn("${lightboxIndex + 1} / ${total}", filmstrip)
        self.assertIn("getLibraryPoolTotal: () => Math.max", script)
        self.assertIn("filtered_pool_visible", script)
        self.assertIn("while (index >= getLibraryImages().length && !getRankingsExhausted())", navigation)
        self.assertIn("loadRankings(false)", navigation)

    async def test_public_docs_do_not_advertise_deep_search(self):
        base_dir = os.path.dirname(os.path.dirname(__file__))
        for relpath in (
            "README.md",
            "docs/features.md",
            "docs/getting-started.md",
            "docs/data-and-privacy.md",
        ):
            with open(os.path.join(base_dir, relpath), encoding="utf-8") as fh:
                content = fh.read()
            self.assertNotRegex(content, r"(?i)deep[- ]search")

    async def test_background_work_panel_replaces_work_mode_picker(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "templates", "settings.html"), encoding="utf-8") as fh:
            settings_template = fh.read()
        with open(os.path.join(base_dir, "static", "js", "work", "status_panel.js"), encoding="utf-8") as fh:
            status_panel = fh.read()
        with open(os.path.join(base_dir, "static", "js", "bottom_bar_controls.js"), encoding="utf-8") as fh:
            bottom_bar_controls = fh.read()
        with open(os.path.join(os.path.dirname(base_dir), "scripts", "photoarchive-browser-smoke"), encoding="utf-8") as fh:
            browser_smoke = fh.read()
        with open(os.path.join(base_dir, "static", "js", "people", "labels.js"), encoding="utf-8") as fh:
            people_labels = fh.read()

        self.assertNotIn("Computer Work Mode", settings_template)
        self.assertNotIn("work-mode-section", settings_template)
        self.assertIn("label: 'Search'", status_panel)
        self.assertIn("label: 'Previews'", status_panel)
        self.assertNotIn("label: 'Full-Size Cache'", status_panel)
        self.assertIn("People", status_panel)
        self.assertNotIn("Work Governor", status_panel)
        self.assertNotIn("Deep Search", status_panel)
        self.assertNotIn("throttled scan", people_labels)
        self.assertIn("data-work-kind", status_panel)
        self.assertIn("background-work-action-group", status_panel)
        self.assertIn("data-work-command", status_panel)
        self.assertIn('[data-action="toggle-background-work-panel"]', status_panel)
        self.assertNotIn('[data-action="toggle-background-work-panel"]', bottom_bar_controls)
        self.assertNotIn("Stop Background Work", status_panel)
        self.assertNotIn("Resume Background Work", status_panel)
        self.assertIn("/api/ai/embeddings/pause", status_panel)
        self.assertIn("/api/ai/embeddings/resume", status_panel)
        self.assertIn("/api/cache/pregen/stop", status_panel)
        self.assertIn("/api/cache/pregen/start", status_panel)
        self.assertIn("/api/people/scan/pause", status_panel)
        self.assertIn("/api/people/scan/resume", status_panel)
        self.assertIn('"/people"', browser_smoke)
        self.assertIn('"/catalog"', browser_smoke)
        self.assertIn("Background Work close failed", browser_smoke)
        self.assertIn("Background Work forced status refresh", browser_smoke)
        self.assertIn("Background Work stale fallback status", browser_smoke)

    async def test_background_work_timeout_fallbacks_do_not_render_fake_zero_jobs(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "static", "js", "ai", "poller.js"), encoding="utf-8") as fh:
            poller = fh.read()
        with open(os.path.join(base_dir, "static", "js", "settings", "page.js"), encoding="utf-8") as fh:
            settings_page = fh.read()
        with open(os.path.join(base_dir, "static", "js", "work", "status_panel.js"), encoding="utf-8") as fh:
            status_panel = fh.read()
        with open(os.path.join(os.path.dirname(base_dir), "scripts", "photoarchive-browser-smoke"), encoding="utf-8") as fh:
            browser_smoke = fh.read()

        self.assertIn("const DEFAULT_STATUS_TIMEOUT_MS = 5000", poller)
        self.assertIn("const SETTINGS_STATUS_TIMEOUT_MS = 5000", settings_page)
        self.assertIn("function stalePeopleStatus", poller)
        self.assertIn("active: false", poller[poller.index("function stalePeopleStatus"):poller.index("function staleCacheStatus")])
        self.assertIn("function unknownStaleRow(kind, label, incoming = {})", status_panel)
        self.assertIn("fallbackOnly: true", status_panel)
        self.assertIn("return unknownStaleRow('faces', 'People', peopleStatus)", status_panel)
        self.assertIn("unknownStaleRow('embedding', 'Search', aiStatus)", status_panel)
        self.assertIn("unknownStaleRow('preview', 'Previews', cacheStatus)", status_panel)
        self.assertIn("hideProgress: true", status_panel)
        self.assertIn("control: null", status_panel[status_panel.index("function unknownStaleRow"):status_panel.index("function isTransientDatabaseLock")])
        self.assertIn("const realRows = rows.filter((row) => !isFallbackOnlyRow(row));", status_panel)
        self.assertIn("Background Work · Updating", status_panel)
        self.assertIn('!progressTexts.some((text) => text.includes("0 / 0"))', browser_smoke)

    async def test_photoarchive_check_exposes_agent_friendly_areas(self):
        base_dir = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(base_dir, "scripts", "photoarchive-check"), encoding="utf-8") as fh:
            check_script = fh.read()

        self.assertIn("--list-areas", check_script)
        self.assertIn('[background-work]="test_ui_contracts"', check_script)
        self.assertIn('[ai-search]="test_search test_embedding_worker"', check_script)
        self.assertIn('[previews]="test_cache_status test_thumbnails"', check_script)
        self.assertIn('[people-work]="test_people"', check_script)
        self.assertIn('[frontend]="test_ui_contracts"', check_script)
        self.assertIn('[search]="test_search test_embedding_worker"', check_script)
        self.assertIn('[cache]="test_cache_status test_thumbnails"', check_script)
        self.assertIn('[people]="test_people"', check_script)
        self.assertIn("run_background_work_smoke_if_available", check_script)

    async def test_template_context_versions_static_assets(self):
        context = app_module.app.state.photoarchive_shell.template_context(HeaderRequest())

        self.assertIn("static_version", context)
        self.assertTrue(str(context["static_version"]).isdigit())

        with tempfile.TemporaryDirectory() as tempdir:
            static_dir = os.path.join(tempdir, "static")
            js_dir = os.path.join(static_dir, "js")
            os.makedirs(js_dir)
            style_path = os.path.join(static_dir, "style.css")
            js_path = os.path.join(js_dir, "app.js")
            with open(style_path, "w", encoding="utf-8") as fh:
                fh.write("body {}\n")
            with open(js_path, "w", encoding="utf-8") as fh:
                fh.write("export default {};\n")
            os.utime(style_path, (1000, 1000))
            os.utime(js_path, (1000, 1000))
            assets = StaticAssetContext(app_dir=tempdir, repo_dir=tempdir, started_at=1)

            self.assertEqual(assets.static_version(), "1000")

            os.utime(js_path, (1005, 1005))
            self.assertEqual(assets.static_version(), "1005")
