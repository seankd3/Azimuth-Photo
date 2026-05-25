import asyncio
import os


IDLE_ACTIVITY_EXCLUDED_PATHS = frozenset(
    {
        "/api/ai/status",
        "/api/cache/status",
        "/api/cache/pregen/status",
        "/api/dev/status",
        "/api/people/status",
        "/api/scan/status",
        "/api/settings",
        "/api/ui/settings",
    }
)


class BackgroundTaskTracker:
    """Track fire-and-forget app tasks so shutdown can cancel them cleanly."""

    def __init__(self):
        self.tasks: set[asyncio.Task] = set()

    def track(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def cancel_all(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()


def smoke_mode_enabled() -> bool:
    return os.environ.get("PHOTOARCHIVE_SMOKE_MODE") == "1"


async def track_idle_activity(request, call_next, *, thumbnails, excluded_paths: set[str]):
    path = request.url.path
    if not path.startswith("/static") and path not in excluded_paths:
        thumbnails.note_user_activity()
    return await call_next(request)


def install_idle_activity_middleware(app, *, thumbnails, excluded_paths=None):
    excluded = set(excluded_paths or IDLE_ACTIVITY_EXCLUDED_PATHS)

    @app.middleware("http")
    async def track_idle_activity_middleware(request, call_next):
        return await track_idle_activity(
            request,
            call_next,
            thumbnails=thumbnails,
            excluded_paths=excluded,
        )

    return track_idle_activity_middleware


async def run_shutdown(*, thumbnails, background_task_tracker: BackgroundTaskTracker) -> None:
    thumbnails.stop_prefetch()
    await background_task_tracker.cancel_all()


async def run_startup(
    *,
    smoke_mode_enabled,
    warm_templates,
    thumbnails,
    settings,
    face_worker,
    track_background_task,
    init_db,
    get_filter_options,
    get_date_groups,
    get_catalog_image_counts,
    get_stats,
    get_ai_status_counts,
    get_visible_orientation_pairing_pool_counts,
    get_catalog_summary,
    cache_root,
    build_ai_status,
    build_cache_status,
    api_rankings,
    api_folders,
    api_map_markers,
    api_date_groups,
    api_settings,
    mosaic_next,
    compare_next,
    default_visible_pairing_candidates,
    warm_filtered_visible_ranked_candidates,
    get_visible_past_matchups,
    classify_orientations_background,
    scan_metadata_background,
    swiss_pair_window: int,
    filtered_swiss_pair_window: int,
    filtered_mosaic_window: int,
    mosaic_explore_window: int,
    mosaic_diverse_window: int,
    interaction_cache_warmup_delay_seconds: float,
) -> None:
    if smoke_mode_enabled():
        await asyncio.to_thread(warm_templates)
        return
    await init_db()
    thumbnails.configure(settings.load_settings())

    async def _cleanup_stale_cache_temps_when_quiet():
        await asyncio.to_thread(thumbnails.cleanup_stale_cache_temps)

    async def _warm_common_filter_caches():
        options = await get_filter_options()
        file_types = [
            str(item.get("ext") or "")
            for item in (options.get("file_types") or [])[:3]
            if item.get("ext")
        ]
        await asyncio.gather(
            *(
                api_rankings(limit=60, file_type=file_type)
                for file_type in file_types
            ),
            *(
                api_rankings(limit=60, q=file_type)
                for file_type in file_types
            ),
            *(
                get_date_groups(
                    file_type=file_type,
                    visible_thumb_size="sm",
                    cache_root=cache_root(),
                )
                for file_type in file_types
            ),
            return_exceptions=True,
        )

    async def _warm_light_startup_caches():
        await asyncio.sleep(0.1)
        await asyncio.gather(
            get_catalog_image_counts(),
            get_stats(),
            get_ai_status_counts(),
            get_filter_options(),
            build_ai_status(),
            get_date_groups(visible_thumb_size="sm", cache_root=cache_root()),
            api_rankings(limit=60),
            mosaic_next(n=12, strategy="explore"),
            api_folders(max_depth=0),
            api_folders(max_depth=1),
            api_folders(max_depth=2),
            api_map_markers(),
            api_settings(),
            get_visible_orientation_pairing_pool_counts("md", cache_root(), "landscape"),
            get_visible_orientation_pairing_pool_counts("md", cache_root(), "portrait"),
            get_visible_orientation_pairing_pool_counts("sm", cache_root(), "landscape"),
            get_visible_orientation_pairing_pool_counts("sm", cache_root(), "portrait"),
            _warm_common_filter_caches(),
            warm_filtered_visible_ranked_candidates(
                "md",
                limit=filtered_swiss_pair_window,
                orientation="landscape",
                warm_matchups=True,
            ),
            warm_filtered_visible_ranked_candidates(
                "md",
                limit=filtered_swiss_pair_window,
                orientation="portrait",
                warm_matchups=True,
            ),
            warm_filtered_visible_ranked_candidates(
                "sm",
                limit=filtered_mosaic_window,
                orientation="landscape",
            ),
            warm_filtered_visible_ranked_candidates(
                "sm",
                limit=filtered_mosaic_window,
                orientation="portrait",
            ),
            asyncio.to_thread(warm_templates),
            return_exceptions=True,
        )

    track_background_task(_warm_light_startup_caches())

    async def _warm_priority_interaction_caches():
        await asyncio.sleep(0.5)
        await asyncio.gather(
            get_stats(),
            get_filter_options(),
            default_visible_pairing_candidates(
                "md",
                limit=swiss_pair_window,
                include_card_metadata=True,
            ),
            default_visible_pairing_candidates(
                "sm",
                copy_rows=True,
                limit=mosaic_explore_window,
                order="cache",
            ),
            default_visible_pairing_candidates(
                "sm",
                limit=mosaic_diverse_window,
                order="least_compared",
                include_card_metadata=False,
            ),
            get_visible_past_matchups("md"),
            api_folders(max_depth=1),
            api_rankings(limit=50),
            api_rankings(limit=50, sort="resolution"),
            api_settings(),
            return_exceptions=True,
        )
        await mosaic_next(n=12, strategy="explore")
        await mosaic_next(n=12, strategy="diverse")
        await compare_next(n=5)

    track_background_task(_warm_priority_interaction_caches())

    async def _start_background_daemon(coro_factory, delay: float = 5.0):
        await asyncio.sleep(delay)
        await coro_factory()

    track_background_task(_start_background_daemon(thumbnails.run_prefetch_worker))
    track_background_task(_start_background_daemon(_cleanup_stale_cache_temps_when_quiet, delay=20.0))
    track_background_task(_start_background_daemon(classify_orientations_background))
    track_background_task(_start_background_daemon(scan_metadata_background))
    try:
        import embedding_worker
        embedding_worker.pause_embedding_worker("Search is stopped until you start it from Background Work.")
        track_background_task(_start_background_daemon(embedding_worker.run_embedding_worker))
    except ImportError:
        pass  # AI features disabled - missing dependencies

    track_background_task(_start_background_daemon(face_worker.run_face_worker, delay=25.0))

    async def _warm_interaction_caches():
        await asyncio.sleep(interaction_cache_warmup_delay_seconds)
        await asyncio.gather(
            get_ai_status_counts(),
            get_visible_orientation_pairing_pool_counts("md", cache_root(), "landscape"),
            get_visible_orientation_pairing_pool_counts("md", cache_root(), "portrait"),
            get_visible_orientation_pairing_pool_counts("sm", cache_root(), "landscape"),
            get_visible_orientation_pairing_pool_counts("sm", cache_root(), "portrait"),
            warm_filtered_visible_ranked_candidates(
                "md",
                limit=filtered_swiss_pair_window,
                orientation="landscape",
                warm_matchups=True,
            ),
            warm_filtered_visible_ranked_candidates(
                "md",
                limit=filtered_swiss_pair_window,
                orientation="portrait",
                warm_matchups=True,
            ),
            warm_filtered_visible_ranked_candidates(
                "sm",
                limit=filtered_mosaic_window,
                orientation="landscape",
            ),
            warm_filtered_visible_ranked_candidates(
                "sm",
                limit=filtered_mosaic_window,
                orientation="portrait",
            ),
            build_cache_status(ahead=0),
            get_catalog_summary(),
            api_date_groups(),
            api_map_markers(),
            api_rankings(limit=50, sort="newest"),
            api_rankings(limit=50, sort="camera"),
            return_exceptions=True,
        )
        await asyncio.gather(
            mosaic_next(n=6, orientation="landscape"),
            mosaic_next(n=12, strategy="diverse"),
            mosaic_next(n=12, strategy="diverse", orientation="landscape"),
            mosaic_next(n=12, strategy="diverse", orientation="portrait"),
            compare_next(n=5, mode="topn"),
            return_exceptions=True,
        )
    track_background_task(_warm_interaction_caches())
