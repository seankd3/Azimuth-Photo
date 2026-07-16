from test_support import *  # noqa: F401,F403


class UiContractsTests(BackendTestCase):
    async def test_living_shell_templates_use_living_assets_only(self):
        base_dir = os.path.dirname(__file__)
        deleted_names = tuple(
            "".join(parts)
            for parts in (
                ("base", ".html"),
                ("index", ".html"),
                ("library", ".html"),
                ("compare", ".html"),
                ("people", ".html"),
                ("settings", ".html"),
                ("_filters", ".html"),
                ("_bottom", "_bar_nav", ".html"),
                ("_bottom", "_bar_search", ".html"),
                ("_bottom", "_bar_work", ".html"),
                ("_background", "_work_panel", ".html"),
                ("/static/app", ".js"),
                ("/static/style", ".css"),
            )
        )
        for template_name in ("desktop.html", "mobile.html", "share_gallery.html"):
            with open(os.path.join(base_dir, "templates", template_name), encoding="utf-8") as fh:
                template = fh.read()
            self.assertNotIn("{% extends", template)
            self.assertNotIn("{% include", template)
            for deleted_name in deleted_names:
                self.assertNotIn(deleted_name, template)

        with open(os.path.join(base_dir, "templates", "desktop.html"), encoding="utf-8") as fh:
            desktop_template = fh.read()
        with open(os.path.join(base_dir, "templates", "mobile.html"), encoding="utf-8") as fh:
            mobile_template = fh.read()

        self.assertIn('id="topbar"', desktop_template)
        self.assertIn('id="grid-flow"', desktop_template)
        self.assertIn('id="quick-guide"', desktop_template)
        self.assertIn('id="help-restart-guide"', desktop_template)
        self.assertIn('href="/static/desktop.css', desktop_template)
        self.assertIn('src="/static/js/desktop/bootstrap.js', desktop_template)
        self.assertIn('id="m-timeline"', mobile_template)
        self.assertIn('id="m-tabbar"', mobile_template)
        self.assertIn('href="/static/mobile.css', mobile_template)
        self.assertIn('src="/static/js/mobile/bootstrap.js', mobile_template)

    async def test_living_frontend_modules_share_api_facade(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "static", "js", "desktop", "bootstrap.js"), encoding="utf-8") as fh:
            desktop_bootstrap = fh.read()
        with open(os.path.join(base_dir, "static", "js", "mobile", "bootstrap.js"), encoding="utf-8") as fh:
            mobile_bootstrap = fh.read()
        with open(os.path.join(base_dir, "static", "js", "desktop", "api.js"), encoding="utf-8") as fh:
            desktop_api = fh.read()
        with open(os.path.join(base_dir, "static", "js", "desktop", "library_health.js"), encoding="utf-8") as fh:
            library_health = fh.read()
        with open(os.path.join(base_dir, "static", "js", "mobile", "api.js"), encoding="utf-8") as fh:
            mobile_api = fh.read()
        with open(os.path.join(base_dir, "static", "js", "api.js"), encoding="utf-8") as fh:
            shared_api = fh.read()

        self.assertIn("initGridContextMenu", desktop_bootstrap)
        self.assertIn("initQuickGuide", desktop_bootstrap)
        self.assertIn("initTimeline", mobile_bootstrap)
        self.assertIn("from '../api.js'", desktop_api)
        self.assertIn("renderLibraryHealth", library_health)
        self.assertIn("Check 50 originals", library_health)
        self.assertIn("from '../api.js'", mobile_api)
        self.assertIn("export async function fetchJson", shared_api)

    async def test_desktop_api_notifies_when_post_helpers_fail(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "static", "js", "desktop", "api.js"), encoding="utf-8") as fh:
            desktop_api = fh.read()

        self.assertIn("function reportApiFailure", desktop_api)
        self.assertIn("if (!response.ok) reportApiFailure({ status: response.status });", desktop_api)
        self.assertIn("const result = await requestWithStatus(url, jsonRequestOptions('POST', body, options));", desktop_api)
        self.assertIn('showToast("The library isn\'t responding.");', desktop_api)
        self.assertIn("return result.ok ? result.data : null;", desktop_api)

    async def test_service_worker_precaches_mobile_shell_only(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "static", "sw.js"), encoding="utf-8") as fh:
            service_worker = fh.read()

        self.assertIn("'/m'", service_worker)
        self.assertIn("'/static/mobile.css'", service_worker)
        self.assertIn("'/static/js/api.js'", service_worker)
        self.assertIn("'/static/js/mobile/bootstrap.js'", service_worker)
        self.assertIn("'/static/js/mobile/write_queue.js'", service_worker)
        self.assertIn("searchParams.get('v')", service_worker)
        self.assertIn("pa-write-queue", service_worker)
        self.assertNotIn("style" + ".css", service_worker)
        self.assertNotIn("app" + ".js", service_worker)
        self.assertNotIn("/static/js/" + "legacy/", service_worker)
        self.assertNotIn("/static/js/" + "desktop/", service_worker)

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

    async def test_browser_smoke_targets_living_shells(self):
        base_dir = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(base_dir, "scripts", "photoarchive-browser-smoke"), encoding="utf-8") as fh:
            browser_smoke = fh.read()

        self.assertIn('const PAGE_PATHS = ["/", "/d", "/m"]', browser_smoke)
        self.assertIn('"#topbar"', browser_smoke)
        self.assertIn('"#grid-flow"', browser_smoke)
        self.assertIn('"#m-timeline"', browser_smoke)
        for route in ("settings", "catalog", "people", "library", "rankings", "compare"):
            self.assertNotIn(f'"/{route}"', browser_smoke)

    async def test_cull_brief_uses_large_previews_and_scoped_reversible_review(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "static", "js", "desktop", "cull_brief.js"), encoding="utf-8") as fh:
            cull_brief = fh.read()

        self.assertIn("thumbUrl('lg', member.id)", cull_brief)
        self.assertIn("data-cull-preview", cull_brief)
        self.assertIn("preloadNext", cull_brief)
        self.assertIn("writeFlags", cull_brief)
        self.assertIn("scopeParams", cull_brief)
        self.assertIn("getRankings", cull_brief)
        self.assertIn("key === 'z'", cull_brief)

    async def test_desktop_smart_collection_id_reaches_all_scoped_consumers(self):
        base_dir = os.path.dirname(__file__)
        desktop_dir = os.path.join(base_dir, "static", "js", "desktop")
        with open(os.path.join(desktop_dir, "state.js"), encoding="utf-8") as fh:
            state = fh.read()
        with open(os.path.join(desktop_dir, "timeline.js"), encoding="utf-8") as fh:
            timeline = fh.read()
        with open(os.path.join(desktop_dir, "cull_brief.js"), encoding="utf-8") as fh:
            cull_brief = fh.read()
        with open(os.path.join(desktop_dir, "filters.js"), encoding="utf-8") as fh:
            filters = fh.read()

        self.assertIn("if (scope.collectionId) params.set('collection_id', scope.collectionId);", state)
        self.assertNotIn("scope.collectionId && !scope.collectionSmart", state)
        self.assertIn("scopeParams({ limit: MONTH_SAMPLE_LIMIT", timeline)
        self.assertIn("scopeParams({ limit: SCOPE_PAGE_LIMIT", cull_brief)
        self.assertIn("getFilterOptions(scopeParams())", filters)

    async def test_map_uses_the_server_resolved_scope(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "static", "js", "desktop", "map.js"), encoding="utf-8") as fh:
            map_module = fh.read()

        self.assertIn("await getMapMarkers(scopeParams())", map_module)
        self.assertIn("marker.preview_ready !== false && marker.thumb_url", map_module)
        self.assertNotIn("loadCollectionMarkers", map_module)
        self.assertNotIn("getCollection,", map_module)

    async def test_develop_history_refresh_bypasses_the_empty_pre_save_cache(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "static", "js", "desktop", "develop", "history_panel.js"), encoding="utf-8") as fh:
            history_panel = fh.read()

        self.assertIn("cache: 'no-store'", history_panel)

    async def test_source_rows_offer_the_complete_scope_menu(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "static", "js", "desktop", "panel.js"), encoding="utf-8") as fh:
            panel = fh.read()
        with open(os.path.join(base_dir, "static", "js", "desktop", "source_reveal_menu.js"), encoding="utf-8") as fh:
            source_reveal_menu = fh.read()
        with open(os.path.join(base_dir, "static", "js", "desktop", "file_manager.js"), encoding="utf-8") as fh:
            file_manager = fh.read()

        self.assertIn("openSourceRevealMenu", panel)
        self.assertIn("row.addEventListener('contextmenu'", panel)
        self.assertIn("openSourceRevealMenu(row.dataset.source, row, count, {", panel)
        self.assertIn("revealFolder(path, sourceId)", source_reveal_menu)
        self.assertIn('data-act="scope"', source_reveal_menu)
        self.assertIn('data-act="refine"', source_reveal_menu)
        self.assertIn('data-act="reveal"', source_reveal_menu)
        self.assertIn('data-act="export"', source_reveal_menu)
        self.assertIn("fileManagerMenuLabel()", source_reveal_menu)
        self.assertIn("revealAvailable", source_reveal_menu)
        self.assertIn("revealAvailable ?", source_reveal_menu)
        self.assertIn("node.reveal_available !== false", (open(os.path.join(base_dir, "static", "js", "desktop", "folders.js"), encoding="utf-8")).read())
        self.assertIn("'Open in Explorer'", file_manager)
        self.assertNotIn("Reveal in Explorer", file_manager)

    async def test_template_context_versions_static_assets(self):
        context = app_module.app.state.photoarchive_shell.template_context(HeaderRequest())

        self.assertIn("static_version", context)
        self.assertTrue(str(context["static_version"]).isdigit())

        with tempfile.TemporaryDirectory() as tempdir:
            static_dir = os.path.join(tempdir, "static")
            js_dir = os.path.join(static_dir, "js", "desktop")
            os.makedirs(js_dir)
            css_path = os.path.join(static_dir, "desktop.css")
            js_path = os.path.join(js_dir, "bootstrap.js")
            with open(css_path, "w", encoding="utf-8") as fh:
                fh.write("body {}\n")
            with open(js_path, "w", encoding="utf-8") as fh:
                fh.write("export default {};\n")
            os.utime(css_path, (1000, 1000))
            os.utime(js_path, (1000, 1000))
            assets = StaticAssetContext(app_dir=tempdir, repo_dir=tempdir, started_at=1)

            self.assertEqual(assets.static_version(), "1000")

            os.utime(js_path, (1005, 1005))
            self.assertEqual(assets.static_version(), "1005")
