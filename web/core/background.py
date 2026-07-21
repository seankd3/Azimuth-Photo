import asyncio
import logging
import os

from core import capabilities
from core.user_activity import IDLE_ACTIVITY_EXCLUDED_PATHS, marks_user_activity


log = logging.getLogger(__name__)


# Re-export for app_factory / tests that still import the frozenset name.
__all__ = (
    "IDLE_ACTIVITY_EXCLUDED_PATHS",
    "BackgroundTaskTracker",
    "install_idle_activity_middleware",
    "marks_user_activity",
    "run_shutdown",
    "track_background_task",
    "track_idle_activity",
)


class BackgroundTaskTracker:
    """Track fire-and-forget app tasks so shutdown can cancel them cleanly."""

    def __init__(self):
        self.tasks: set[asyncio.Task] = set()

    def track(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error(
                "worker=%s background task failed",
                task.get_coro().__qualname__,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    async def cancel_all(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()


# Shared tracker for layers that spawn SWR/refresh work without DI (repositories,
# route helpers). Same exception logging as AppShell.track_background_task.
_fire_and_forget = BackgroundTaskTracker()


def track_background_task(coro) -> asyncio.Task:
    """Spawn a fire-and-forget task; exceptions are logged, not left unretrieved."""
    return _fire_and_forget.track(coro)


def smoke_mode_enabled() -> bool:
    return os.environ.get("PHOTOARCHIVE_SMOKE_MODE") == "1"


async def _gather_logged(worker_name: str, *awaitables) -> None:
    results = await asyncio.gather(*awaitables, return_exceptions=True)
    for task_index, result in enumerate(results):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, Exception):
            log.error(
                "worker=%s task_index=%s startup warmup failed",
                worker_name,
                task_index,
                exc_info=(type(result), result, result.__traceback__),
            )


async def _start_background_daemon(coro_factory, delay: float = 5.0):
    await asyncio.sleep(delay)
    await coro_factory()


def schedule_optional_workers(*, track_background_task, settings, face_worker, caption_worker) -> dict:
    """Arm hub inference workers whose explicit dependency packs are present."""

    statuses = {
        key: capabilities.capability_status(key)
        for key in ("search", "people", "captions")
    }
    from features.sync import satellite

    if satellite.is_satellite_mode():
        log.info("worker=optional_ai skipped reason=satellite_mode")
        return statuses

    if statuses["search"]["available"]:
        try:
            import embedding_worker

            embedding_worker.pause_embedding_worker(
                "Search is stopped until you start it from Background Work."
            )
            track_background_task(_start_background_daemon(embedding_worker.run_embedding_worker))
        except ImportError:
            log.exception("worker=embedding startup import failed")

    if statuses["people"]["available"]:
        track_background_task(_start_background_daemon(face_worker.run_face_worker, delay=25.0))
    else:
        face_worker.mark_dependencies_unavailable(statuses["people"])

    if statuses["captions"]["available"]:
        try:
            if not settings.get_settings().get("caption_scan_enabled"):
                caption_worker.pause_caption_worker(
                    "Captions are stopped until you start them from Background Work."
                )
            else:
                caption_worker.resume_caption_worker()
            track_background_task(_start_background_daemon(caption_worker.run_caption_worker, delay=30.0))
        except Exception:
            log.exception("worker=caption startup failed; caption worker was not scheduled")
    else:
        caption_worker.mark_dependencies_unavailable(statuses["captions"])
    return statuses


async def track_idle_activity(
    request,
    call_next,
    *,
    thumbnails,
    excluded_paths: set[str] | None = None,
    activity_classifier=None,
):
    """Note browsing only for genuine media traffic.

    The central classifier in ``core.user_activity`` is the source of truth.
    ``excluded_paths`` is an optional extra deny-list (tests / specialized shells).
    """
    path = request.url.path
    classify = activity_classifier or marks_user_activity
    if classify(path) and (excluded_paths is None or path not in excluded_paths):
        thumbnails.note_user_activity()
    return await call_next(request)


def install_idle_activity_middleware(app, *, thumbnails, excluded_paths=None):
    extra_excludes = set(excluded_paths) if excluded_paths is not None else None

    @app.middleware("http")
    async def track_idle_activity_middleware(request, call_next):
        return await track_idle_activity(
            request,
            call_next,
            thumbnails=thumbnails,
            excluded_paths=extra_excludes,
        )

    return track_idle_activity_middleware


async def run_shutdown(
    *,
    thumbnails,
    background_task_tracker: BackgroundTaskTracker,
    caption_worker=None,
) -> None:
    thumbnails.stop_prefetch()
    await background_task_tracker.cancel_all()
    await _fire_and_forget.cancel_all()
    await thumbnails.cancel_background_tasks()

    from features.media import warm as media_warm
    await media_warm.cancel_background_tasks()

    try:
        import embedding_worker

        await embedding_worker.shutdown_embedding_worker()
    except ImportError:
        pass

    if caption_worker is not None:
        caption_worker.shutdown_caption_worker()

    # Last act: earn the next boot its instant start.
    from features.system import backups
    import db
    await asyncio.to_thread(backups.mark_clean_shutdown, db.DB_PATH)


async def run_startup(
    *,
    smoke_mode_enabled,
    warm_templates,
    thumbnails,
    settings,
    face_worker,
    caption_worker,
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
    from features.system import backups
    import db

    # PRAGMA quick_check costs ~1s/GB and used to block every boot (~64s on a
    # 139k catalog). fsck pattern: a clean shutdown earns an instant boot with a
    # background verification; anything else (crash, kill, power loss) still
    # pays the full blocking check before serving.
    if backups.consume_clean_shutdown(db.DB_PATH):
        async def _verify_catalog_in_background():
            result = await asyncio.to_thread(backups.catalog_quick_check, db.DB_PATH)
            if not result["ok"]:
                log.error(
                    "background catalog check FAILED after clean-shutdown boot "
                    "state=%s error=%s", result["state"], result.get("error"),
                )
        track_background_task(_verify_catalog_in_background())
    else:
        catalog = await asyncio.to_thread(backups.catalog_quick_check, db.DB_PATH)
        if not catalog["ok"]:
            log.error("catalog startup blocked state=%s error=%s", catalog["state"], catalog.get("error"))
            await asyncio.to_thread(warm_templates)
            return
    await init_db()
    thumbnails.configure(settings.load_settings())

    # Bulk HDD sequencing: one spindle consumer at a time (previews before vault).
    try:
        from core import bulk_scheduler as _bulk_scheduler

        def _previews_hold_disk() -> bool:
            status = getattr(thumbnails, "_pregen_status", {}) or {}
            return _bulk_scheduler.previews_hold_disk(
                manual_mode=bool(status.get("manual_mode")),
                manual_pause=bool(status.get("manual_pause")),
                state=str(status.get("state") or ""),
            )

        _bulk_scheduler.configure(previews_pending=_previews_hold_disk)
    except Exception:
        log.exception("worker=bulk_scheduler failed to configure")

    async def _cleanup_stale_cache_temps_when_quiet():
        await asyncio.to_thread(thumbnails.cleanup_stale_cache_temps)

    async def _sweep_phantom_cache_entries():
        # Once per process start; low priority after interactive warmers settle.
        await asyncio.sleep(45.0)
        result = await asyncio.to_thread(thumbnails.sweep_missing_cache_entries)
        log.info(
            "worker=cache_phantom_sweep scanned=%s removed=%s batches=%s",
            result.get("scanned"),
            result.get("removed"),
            result.get("batches"),
        )

    async def _warm_common_filter_caches():
        options = await get_filter_options()
        file_types = [
            str(item.get("ext") or "")
            for item in (options.get("file_types") or [])[:3]
            if item.get("ext")
        ]
        await _gather_logged(
            "common_filter_cache_warmup",
            *(
                api_rankings(limit=100, file_type=file_type, stacks="collapsed")
                for file_type in file_types
            ),
            *(
                api_rankings(limit=100, q=file_type, stacks="collapsed")
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
        )

    async def _warm_light_startup_caches():
        await asyncio.sleep(0.1)
        await _gather_logged(
            "light_startup_cache_warmup",
            get_catalog_image_counts(),
            get_stats(),
            get_ai_status_counts(),
            get_filter_options(),
            build_ai_status(),
            get_date_groups(visible_thumb_size="sm", cache_root=cache_root()),
            api_rankings(limit=100, stacks="collapsed"),
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
        )

    track_background_task(_warm_light_startup_caches())

    async def _warm_priority_interaction_caches():
        await asyncio.sleep(0.5)
        await _gather_logged(
            "priority_interaction_cache_warmup",
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
            api_rankings(limit=100, stacks="collapsed"),
            api_rankings(limit=50, sort="resolution"),
            api_settings(),
        )
        await mosaic_next(n=12, strategy="explore")
        await mosaic_next(n=12, strategy="diverse")
        await compare_next(n=5)

    track_background_task(_warm_priority_interaction_caches())

    async def _warm_collection_suggestions():
        # Populate the suggestions cache off the request path so the first user
        # after a boot gets it instantly instead of paying the multi-second build.
        await asyncio.sleep(2.0)
        try:
            import db as _db
            from features.collections import suggestions as _suggestions
            await _suggestions.collection_suggestions(_db.DB_PATH)
        except Exception:
            log.debug("collection suggestions warmup skipped", exc_info=True)

    track_background_task(_warm_collection_suggestions())

    track_background_task(_start_background_daemon(thumbnails.run_prefetch_worker))
    track_background_task(_start_background_daemon(_cleanup_stale_cache_temps_when_quiet, delay=20.0))
    track_background_task(_sweep_phantom_cache_entries())
    track_background_task(_start_background_daemon(classify_orientations_background))
    track_background_task(_start_background_daemon(scan_metadata_background))
    schedule_optional_workers(
        track_background_task=track_background_task,
        settings=settings,
        face_worker=face_worker,
        caption_worker=caption_worker,
    )

    from features.sync import satellite as _satellite

    # Auto-resume bulk workers that were running before the last shutdown.
    # Pregen and cloud vault own the hub's archive disk. Satellites keep only
    # interactive/on-demand previews and receive generated work through sync.
    if not _satellite.is_satellite_mode():
        try:
            from core import bulk_scheduler as _bulk_scheduler

            if _bulk_scheduler.pregen_desired():
                log.info("bulk_scheduler resuming preview pregen from prior desired state")
                thumbnails.start_pregeneration()
        except Exception:
            log.exception("worker=pregen auto-resume failed")
    else:
        log.info("worker=pregen auto-resume skipped reason=satellite_mode")

    try:
        import db as _db
        from features.system import backups as _catalog_backups

        track_background_task(
            _start_background_daemon(
                lambda: _catalog_backups.run_daily_backup_scheduler(lambda: _db.DB_PATH),
                delay=15.0,
            )
        )
    except Exception:
        log.exception("worker=catalog_backup scheduler failed to arm")

    if not _satellite.is_satellite_mode():
        try:
            import db as _db
            from core import bulk_scheduler as _bulk_scheduler
            from features.backup import cloud as _cloud_backup

            async def _resume_vault_if_desired() -> None:
                if not _bulk_scheduler.vault_desired():
                    return
                log.info("bulk_scheduler resuming cloud vault from prior desired state")
                try:
                    await asyncio.to_thread(
                        _cloud_backup.start_sync,
                        _db.DB_PATH,
                        manual_override=False,
                    )
                except Exception:
                    log.exception("cloud_backup auto-resume failed to start")

            track_background_task(
                _start_background_daemon(
                    lambda: _cloud_backup.run_nightly_scheduler(lambda: _db.DB_PATH),
                    delay=25.0,
                )
            )
            track_background_task(
                _start_background_daemon(_resume_vault_if_desired, delay=3.0)
            )
        except Exception:
            log.exception("worker=cloud_backup scheduler failed to arm")
    else:
        log.info("worker=cloud_backup skipped reason=satellite_mode")

    try:
        import db as _db
        from features.library import watched_folders
        track_background_task(
            _start_background_daemon(
                lambda: watched_folders.run_poller(lambda: _db.DB_PATH),
                delay=35.0,
            )
        )
    except Exception:
        log.exception("worker=watched_folder_poller failed to arm")

    async def _warm_interaction_caches():
        await asyncio.sleep(interaction_cache_warmup_delay_seconds)
        await _gather_logged(
            "interaction_cache_warmup",
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
        )
        await _gather_logged(
            "interaction_pairing_warmup",
            mosaic_next(n=6, orientation="landscape"),
            mosaic_next(n=12, strategy="diverse"),
            mosaic_next(n=12, strategy="diverse", orientation="landscape"),
            mosaic_next(n=12, strategy="diverse", orientation="portrait"),
            compare_next(n=5, mode="topn"),
        )
    track_background_task(_warm_interaction_caches())
