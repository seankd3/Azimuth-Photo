import pytest
import os
import unittest



class MobileOfflineContractsTests(unittest.TestCase):
    def setUp(self):
        self.base_dir = os.path.dirname(__file__)

    def read(self, *parts):
        with open(os.path.join(self.base_dir, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_flag_writes_use_the_durable_mobile_queue(self):
        api = self.read("static", "js", "mobile", "api.js")
        queue = self.read("static", "js", "mobile", "write_queue.js")

        self.assertIn("enqueueWrite(`/api/image/${imageId}/flag`, { flag })", api)
        self.assertIn("enqueueWrite('/api/images/flag', { image_ids: imageIds, flag })", api)
        self.assertIn("localStorage.setItem(STORAGE_KEY", queue)
        self.assertIn("BASE_RETRY_MS", queue)
        self.assertIn("window.addEventListener('online'", queue)
        self.assertIn("registration.sync.register(WRITE_SYNC_TAG)", queue)
        self.assertIn("drain-write-queue", queue)
        self.assertNotIn("write-outcome", queue)
        self.assertIn("emit('flag-write'", api)
        # Stars are Elo's computed projection; the manual rating write path is
        # retired and must not return.
        self.assertNotIn("rating-write", api)
        self.assertNotIn("'#mv-pick'", self.read("static", "js", "mobile", "bootstrap.js"))

    def test_offline_pins_and_queued_writes_survive_upgrades(self):
        queue = self.read("static", "js", "mobile", "write_queue.js")
        offline = self.read("static", "js", "mobile", "offline.js")
        service_worker = self.read("static", "sw.js")

        # Pre-rebrand localStorage keys are adopted once, then retired —
        # writes the UI already confirmed must still reach the server.
        self.assertIn("'pa-m-write-queue-v1'", queue)
        self.assertIn("localStorage.removeItem(LEGACY_STORAGE_KEY)", queue)
        self.assertIn("'pa-m-offline-photos-v1'", offline)
        self.assertIn("localStorage.removeItem(LEGACY_STORAGE_KEY)", offline)

        # Pinned photos live in a version-independent cache the SW never
        # rotates away or trims, and the local index is reconciled against
        # what the cache actually holds instead of trusted blindly.
        self.assertIn("'azimuth-mobile-pinned-thumbs'", offline)
        self.assertIn("'azimuth-mobile-pinned-thumbs'", service_worker)
        self.assertIn("name !== PIN_CACHE && !name.startsWith(CACHE_VERSION)", service_worker)
        self.assertIn("const cache = pinned ? pinCache : await caches.open(THUMB_CACHE);", service_worker)
        self.assertIn("if (!pinned) trimThumbCache();", service_worker)
        self.assertIn("async function reconcileIndex()", offline)
        self.assertIn("void reconcileIndex();", offline)

    def test_mobile_shell_preloads_match_import_urls_and_banner_is_honest(self):
        template = self.read("templates", "mobile.html")

        # Relative imports do not inherit ?v — a versioned preload never
        # matches the module loader's request and double-fetches the graph.
        self.assertIn('<link rel="modulepreload" href="/static/js/{{ mod }}">', template)
        self.assertNotIn('modulepreload" href="/static/js/{{ mod }}?v=', template)
        # Flag writes queue and sync offline; the banner must not deny it.
        self.assertIn("Offline — favorites and rejects will sync", template)
        self.assertNotIn("changes need a connection", template)

    def test_service_worker_is_secure_only_and_versioned(self):
        template = self.read("templates", "mobile.html")
        service_worker = self.read("static", "sw.js")
        register = self.read("static", "js", "sw_register.js")
        manifest = self.read("static", "manifest.webmanifest")

        self.assertIn("scheduleServiceWorkerRegistration", template)
        self.assertIn("sw_register.js", template)
        self.assertIn("isSecureContext", register)
        self.assertIn("pa_sw", register)
        self.assertIn("data-static-version", template)
        self.assertIn("searchParams.get('v')", service_worker)
        self.assertIn("thumbStaleWhileRevalidate", service_worker)
        self.assertIn("'/static/js/mobile/write_queue.js'", service_worker)
        self.assertIn("azimuth-write-queue", service_worker)
        self.assertIn('"display": "standalone"', manifest)
        self.assertIn('"theme_color": "#141517"', manifest)
        self.assertIn("/static/icons/icon.svg", manifest)

    def test_https_origin_helper_keeps_http_usable(self):
        helper = self.read("static", "js", "mobile", "https_origin.js")
        bootstrap = self.read("static", "js", "mobile", "bootstrap.js")
        self.assertIn("buildHttpsOrigin", helper)
        self.assertIn("/api/remote-access", helper)
        self.assertIn("resolveSecureAppUrl", bootstrap)
        self.assertIn("fallbackSecureAppUrl", bootstrap)

    def test_loupe_swipes_favorite_dismiss_and_keep_horizontal_navigation(self):
        viewer = self.read("static", "js", "mobile", "viewer.js")

        self.assertIn("function favoriteSwipe()", viewer)
        self.assertIn("void applyFlags([image.id], 'picked');", viewer)
        self.assertIn("settleDismissSwipe();", viewer)
        self.assertIn("favoriteSwipe();", viewer)
        self.assertIn("const dir = dx < 0 ? 1 : -1", viewer)
        self.assertIn("flagBadge.textContent = flag === 'picked' ? 'Favorited' : 'Rejected'", viewer)

    def test_search_flattens_grouped_people_before_rendering(self):
        search = self.read("static", "js", "mobile", "search.js")

        self.assertIn("function flattenPeople(data)", search)
        self.assertIn("sections.named_people", search)
        self.assertIn("people = { people: flattenPeople(peopleData) }", search)

    @pytest.mark.contract
    def test_background_workers_default_new_productive_states_to_running_on_mobile(self):
        library = self.read("static", "js", "mobile", "library.js")

        self.assertIn("const INACTIVE_WORKER_STATES = new Set([", library)
        for state in ("idle", "ready", "paused", "complete", "caught_up", "error", "disabled", "unavailable", "stale"):
            self.assertIn(f"'{state}'", library)
        self.assertIn("return Boolean(state) && !INACTIVE_WORKER_STATES.has(state);", library)
        self.assertNotIn("const ACTIVE_WORKER_STATES", library)
        self.assertIn("running: workerStateIsActive(ai && ai.worker_state)", library)
        self.assertIn("running: !cachePregen.manual_pause && workerStateIsActive(cachePregen.state)", library)
        self.assertIn("running: workerStateIsActive(peopleWorker.state)", library)

    def test_pending_previews_render_as_real_mobile_cards_and_month_placeholders(self):
        timeline = self.read("static", "js", "mobile", "timeline.js")
        preparing = timeline[
            timeline.index("function renderPreparingState"):
            timeline.index("function appendImages")
        ]
        months = timeline[
            timeline.index("function renderMonths"):
            timeline.index("/* ---------- zoom levels ---------- */")
        ]

        self.assertIn("Preparing your photos", preparing)
        self.assertNotIn("hiddenPendingThumbnails", preparing)
        self.assertIn("preview-pending", timeline)
        self.assertIn("c-placeholder-name", timeline)
        self.assertIn("coverId ? 'has-cover' : 'preview-pending'", months)
        self.assertNotIn("renderPreparingState", months)

    def test_pending_photo_share_preview_does_not_synthesize_a_thumbnail(self):
        sharing = self.read("static", "js", "mobile", "sharing.js")
        photo_share = sharing[
            sharing.index("export async function openPhotoShareSheet"):
            sharing.index("function sharedUrl")
        ]

        self.assertIn("previewThumbUrl(image)", photo_share)
        self.assertIn("m-share-photo-placeholder", photo_share)
        self.assertNotIn("thumbUrl('sm', image.id)", photo_share)

    def test_histogram_rejects_a_stale_scope_before_mutating_timeline_state(self):
        timeline = self.read("static", "js", "mobile", "timeline.js")
        loader = timeline[
            timeline.index("async function loadHistogram"):
            timeline.index("export async function reload()")
        ]

        self.assertIn("async function loadHistogram(requestGeneration = generation)", loader)
        guard = "if (!data || requestGeneration !== generation) return;"
        self.assertIn(guard, loader)
        self.assertLess(loader.index(guard), loader.index("histogram = data;"))
        self.assertLess(loader.index(guard), loader.index("monthOffsets = [];"))
        self.assertIn("loadHistogram(gen)", timeline)

    def test_info_sheet_stars_stay_bound_to_their_displayed_photo(self):
        viewer = self.read("static", "js", "mobile", "viewer.js")
        info_sheet = viewer[viewer.index("function infoSheet()") :]
        stars_region = info_sheet[
            info_sheet.index("syncStarsRow(sheet, imageStars(image));"):
            info_sheet.index("sheet.querySelector('#mv-similar').addEventListener")
        ]

        # Stars are read-only (computed from Elo). The async refresh must stay
        # bound to the photo the sheet was opened for, never the swiped-to one,
        # and the retired manual write path must not return.
        self.assertNotIn("current()", stars_region)
        self.assertNotIn("writeRating", info_sheet)
        self.assertNotIn("[data-rating]", info_sheet)
        self.assertIn("viewerRequestCurrent(image.id, ratingGeneration)", stars_region)
        self.assertIn("const known = byId.get(Number(image.id));", stars_region)
        self.assertIn("if (sheet.isConnected) syncStarsRow(sheet, imageStars(image)", stars_region)

    def test_smart_collection_scopes_hide_membership_actions(self):
        state = self.read("static", "js", "mobile", "state.js")
        library = self.read("static", "js", "mobile", "library.js")
        selection = self.read("static", "js", "mobile", "selection.js")

        self.assertIn("collectionSmart: false", state)
        self.assertIn("smartCollectionId: String(coll.id)", library)
        self.assertIn("scope.collectionSmart ? ''", selection)
        self.assertIn(".filter((collection) => !collection.smart)", selection)


if __name__ == "__main__":
    unittest.main()
