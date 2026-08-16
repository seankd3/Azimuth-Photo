import asyncio
import threading

import work
import logging
import os
import time

from core import capabilities
from core.catalog_path import catalog_path
from core.user_activity import IDLE_ACTIVITY_EXCLUDED_PATHS, marks_user_activity


log = logging.getLogger(__name__)
INTERACTION_CACHE_WARMUP_DELAY_SECONDS = 0.05


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
    return os.environ.get("AZIMUTH_SMOKE_MODE") == "1"


# A launch is the moment someone is most impatient, and it is exactly when the
# app used to start every warmer at once: a 150k-image satellite spent minutes
# serving thumbnails in 5-50s because its own boot work held the disk and the
# worker threads. Warmers now wait for a gap in real traffic instead.
STARTUP_WARM_IDLE_SECONDS = float(os.environ.get("AZIMUTH_WARM_IDLE_SECONDS", "1.5"))
STARTUP_WARM_MAX_WAIT_SECONDS = float(os.environ.get("AZIMUTH_WARM_MAX_WAIT", "180"))
# Idle alone is not enough at launch: nobody has browsed yet, so the app looks
# idle at the exact moment someone is opening it. Hold for this long first, so
# the shell, its counts and its first screen of tiles get the machine.
STARTUP_WARM_GRACE_SECONDS = float(os.environ.get("AZIMUTH_WARM_GRACE", "20"))
_process_started_at = time.monotonic()


async def wait_for_user_gap(
    idle_seconds: float = STARTUP_WARM_IDLE_SECONDS,
    max_wait: float = STARTUP_WARM_MAX_WAIT_SECONDS,
) -> None:
    """Hold until the person using the app pauses, or the ceiling is reached.

    The ceiling matters: a library left open all day still deserves its caches
    warmed, so patience is bounded rather than infinite.
    """
    deadline = time.monotonic() + max(0.0, max_wait)
    while time.monotonic() < deadline:
        uptime = time.monotonic() - _process_started_at
        if uptime < STARTUP_WARM_GRACE_SECONDS:
            await asyncio.sleep(min(1.0, STARTUP_WARM_GRACE_SECONDS - uptime))
            continue
        try:
            import work

            idle = 0.0 if work.busy() else idle_seconds
        except Exception:
            return
        if idle >= idle_seconds:
            return
        await asyncio.sleep(min(0.5, max(0.1, idle_seconds - idle)))


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


def schedule_optional_workers(*, track_background_task, settings, face_worker=None, caption_worker=None) -> dict:
    """The AI derivation fleet is gone (2026-08-14 gutting order).

    Search, People, and captions serve what has already been derived; the
    hub was the batch engine and its return is where new derivation lives.
    The capability statuses survive so the UI can say what is served.
    """

    del track_background_task, settings, face_worker, caption_worker
    return {
        key: capabilities.capability_status(key)
        for key in ("search", "people", "captions")
    }


async def track_idle_activity(
    request,
    call_next,
    *,
    excluded_paths: set[str] | None = None,
    activity_classifier=None,
):
    """Note browsing only for genuine media traffic.

    The central classifier in ``core.user_activity`` is the source of truth.
    ``excluded_paths`` is an optional extra deny-list (tests / specialized shells).
    A failed request is not someone browsing, so it must never clamp background
    work to the activity-burst budget.
    """
    path = request.url.path
    classify = activity_classifier or marks_user_activity
    response = await call_next(request)
    served = int(getattr(response, "status_code", 200) or 200) < 400
    if served and classify(path) and (excluded_paths is None or path not in excluded_paths):
        work.touched()
    return response


def install_idle_activity_middleware(app, *, excluded_paths=None):
    extra_excludes = set(excluded_paths) if excluded_paths is not None else None

    @app.middleware("http")
    async def track_idle_activity_middleware(request, call_next):
        return await track_idle_activity(
            request,
            call_next,
            excluded_paths=extra_excludes,
        )

    return track_idle_activity_middleware


async def run_shutdown(
    *,
    background_task_tracker: BackgroundTaskTracker,
) -> None:
    from data import connection as data_connection

    # First stop everything that can touch a database. Releasing handles while
    # warm, embedding or caption work was still winding down let those reopen
    # the catalog a moment after it had been let go — which on Windows means
    # the app still holds the library file after saying it was finished with
    # it, and in the test suite meant a temporary catalog that would not
    # delete, failing a different test each run.
    await background_task_tracker.cancel_all()
    await _fire_and_forget.cancel_all()
    # media_warm's cancel stood here. Nothing schedules prefetch or memory-warm
    # tasks any more -- the chore loop is the only background work -- so the
    # trackers above are the whole of it. The *ordering* is the lesson and it
    # is unchanged: stop everything database-touching before releasing handles.
    # Only now can nothing reopen what we are about to release.
    await data_connection.close_shared_readers()

    # Last act: earn the next boot its instant start.
    from features.system import backups
    import db

    await asyncio.to_thread(backups.mark_clean_shutdown, db.DB_PATH)


async def run_startup(
    *,
    warm_templates,
    track_background_task,
) -> None:
    # The startup warmers reach straight for what they warm. These used to be
    # 36 keyword parameters, threaded from a 29-field dataclass in app_factory,
    # so that this module would not import them.
    import db
    import settings
    from features.ai import routes as ai_routes
    from features.catalog import metadata as catalog_metadata
    from features.catalog import routes as catalog_routes
    from features.settings import routes as settings_routes

    init_db = db.init_db
    get_ai_status_counts = db.get_ai_status_counts
    get_catalog_summary = db.get_catalog_summary
    build_ai_status = ai_routes.build_ai_status
    api_folders = catalog_routes.api_folders
    classify_orientations_background = catalog_metadata.classify_orientations_background
    scan_metadata_background = catalog_metadata.scan_metadata_background

    def cache_root() -> str:
        import tiles

        return tiles.CACHE_DIR

    from core import on_the_loop

    on_the_loop.remember_the_loop()
    # Automatic house manners: bulk seats until serve is proven.
    try:
        from core import memory_pressure as _memory_pressure

        _memory_pressure.note_process_start()
    except Exception:
        log.exception("worker=memory_pressure startup calm failed to arm")

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
    import tiles

    tiles.configure(settings.load_settings())

    async def _warm_tile_row_reader():
        # The first tile after launch pays the reader's first-touch page
        # faults (~400ms measured) unless someone else pays them first.
        from data import connection as data_connection

        if data_connection.is_ephemeral_db_path(db.DB_PATH):
            # Temp catalogs never hold an inline reader (Windows deletability).
            return
        try:
            await asyncio.to_thread(
                lambda: data_connection.inline_reader(db.DB_PATH)
                .execute("SELECT id FROM images ORDER BY id LIMIT 1")
                .fetchone()
            )
        except Exception:
            log.debug("tile row reader warm skipped", exc_info=True)

    track_background_task(_warm_tile_row_reader())

    # The bulk-HDD sequencer stood here, asking whether previews were holding
    # the disk so a second bulk consumer could wait its turn. There is one
    # consumer now -- the chore loop -- so the question has no second asker.

    async def _warm_collection_suggestions():
        # Populate the suggestions cache off the request path so the first user
        # after a boot gets it instantly instead of paying the multi-second build.
        await wait_for_user_gap()
        try:
            import db as _db
            from features.collections import suggestions as _suggestions
            await _suggestions.collection_suggestions(_db.DB_PATH)
        except Exception:
            log.debug("collection suggestions warmup skipped", exc_info=True)

    track_background_task(_warm_collection_suggestions())

    async def _reconcile_stored_stars():
        # Stars are a stored projection of Elo; reconcile once per boot so
        # catalogs ranked before the column existed (or while the app was
        # down) filter and sort correctly without waiting for the next pick.
        await wait_for_user_gap()
        try:
            import db as _db
            from features.sync import elo_stars as _elo_stars
            await _elo_stars.refresh_stored_stars(_db.DB_PATH)
        except Exception:
            log.debug("stored star reconcile skipped", exc_info=True)

    # The daemon delay keeps short-lived processes (tests, --help launches)
    # from touching the catalog: cancellation lands in the sleep, not mid-query.
    track_background_task(_start_background_daemon(_reconcile_stored_stars, delay=10.0))

    # A disk-path index warmer stood here, because the first request that
    # gated a tile on cache truth paid a 771 ms whole-table index build. The
    # tile route asks for one row by primary key now -- (hash, kind, recipe)
    # -- so there is no index to build and nothing to warm.

    async def _reconcile_folders():
        # The catalog follows the folders on its own: renaming or reorganising a
        # tree outside the app is not damage to repair, it is a folder that has
        # not been reconciled yet. Cheap by construction — a pass that finds
        # nothing stats the directories and reads none of them.
        import db as app_db
        from data import connection as data_connection
        from features.catalog import synchronize

        if data_connection.is_ephemeral_db_path(app_db.DB_PATH):
            # A temp catalog belongs to a test, and a worker still holding it
            # when the fixture tears down is a Windows deletability failure.
            return
        await synchronize.run_reconcile_worker(lambda: app_db.DB_PATH)

    track_background_task(_start_background_daemon(_reconcile_folders, delay=30.0))

    async def _hold_develop_cache_to_budget():
        # A decode cache with no ceiling is a slow leak: it reached 77GB on the
        # hub, which is also why it ended up on the archive disk. Oldest out
        # first, the same law as the laptop cache.
        import db as app_db
        from data import connection as data_connection
        from features.develop import base_cache_budget

        if data_connection.is_ephemeral_db_path(app_db.DB_PATH):
            return
        await base_cache_budget.run_base_cache_budget_worker()

    track_background_task(_start_background_daemon(_hold_develop_cache_to_budget, delay=45.0))

    # One chore loop, in a thread with its own connection. It replaces a
    # prefetch worker, a pregen worker and a warm worker that arbitrated
    # between themselves through a governor: owed is a query, so there is
    # nothing to divide up.
    def _chores() -> None:
        import work
        from core.catalog_path import catalog_path
        from data import connection as _conn

        work.run(lambda: _conn.open_sync(catalog_path()))

    threading.Thread(target=_chores, name="chores", daemon=True).start()
    # A separate identity backfill ran here on a five-minute daemon, with its
    # own INDEXED BY hint and its own skip cursor. Identity is the first thing
    # work.step() asks for -- it has to be, since every cache row is keyed on
    # the hash -- so the chore loop already does it, cursor-free.
    track_background_task(_start_background_daemon(classify_orientations_background))
    track_background_task(_start_background_daemon(scan_metadata_background))
    schedule_optional_workers(
        track_background_task=track_background_task,
        settings=settings,
    )

    # Auto-resume bulk workers that were running before the last shutdown.
    try:
        from core import bulk_scheduler as _bulk_scheduler
    except Exception:
        log.exception("worker=pregen auto-resume failed")

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

