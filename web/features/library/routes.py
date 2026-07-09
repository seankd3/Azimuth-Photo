from collections.abc import Awaitable, Callable
import time

from fastapi import APIRouter, Request

from data import connection as data_connection
from features.library import service as library_service


router = APIRouter()
RankingsHandler = Callable[..., Awaitable[dict]]
_rankings_handler: RankingsHandler | None = None


def configure(
    *,
    rankings_handler: RankingsHandler,
) -> None:
    global _rankings_handler
    _rankings_handler = rankings_handler


@router.get("/api/rankings")
async def api_rankings(
    limit: int = 100, offset: int = 0, sort: str = "elo",
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", q: str = "", deep: bool = False, people: str = "",
    import_batch: int = 0, stacks: str = "expanded", request: Request = None,
):
    if _rankings_handler is None:
        raise RuntimeError("Library routes are not configured")
    started = time.perf_counter()
    try:
        with data_connection.sqlite_timeout(0.25):
            response = await _rankings_handler(
                limit=limit,
                offset=offset,
                sort=sort,
                orientation=orientation,
                compared=compared,
                min_stars=min_stars,
                folder=folder,
                flag=flag,
                date_taken=date_taken,
                file_type=file_type,
                camera=camera,
                lens=lens,
                tag=tag,
                q=q,
                deep=deep,
                people=people,
                import_batch=import_batch,
                stacks=stacks,
                request=request,
            )
    except Exception as exc:
        if not data_connection.is_sqlite_locked_error(exc):
            raise
        return {
            "images": [],
            "total_images": 0,
            "visible_images": 0,
            "hidden_pending_thumbnails": 0,
            "total_kept": 0,
            "status_stale": True,
            "counts_stale": True,
            "candidate_source": "sqlite_busy",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    if isinstance(response, dict):
        response.setdefault("status_stale", False)
        response.setdefault("latency_ms", round((time.perf_counter() - started) * 1000, 1))
    return response


@router.get("/api/date-groups")
async def api_date_groups(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", people: str = "", q: str = "", deep: bool = False,
    import_batch: int = 0, stacks: str = "expanded",
):
    """Return date groups with counts for the scrubber, respecting active filters."""
    return await library_service.date_groups_payload(
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        tag=tag,
        people=people,
        q=q,
        import_batch=import_batch,
        deep=deep,
        stacks=stacks,
    )


@router.get("/api/date-histogram")
async def api_date_histogram(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", people: str = "", q: str = "", deep: bool = False,
    import_batch: int = 0, stacks: str = "expanded",
):
    """Return whole-scope month counts for the timeline scrubber and month view."""
    return await library_service.date_histogram_payload(
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        tag=tag,
        people=people,
        q=q,
        import_batch=import_batch,
        deep=deep,
        stacks=stacks,
    )


@router.get("/api/counts")
async def api_counts(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", people: str = "", q: str = "", deep: bool = False,
    import_batch: int = 0, stacks: str = "expanded",
):
    """Return cheap total/picked/rejected counts for the scope in one call."""
    return await library_service.scope_counts_payload(
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        tag=tag,
        people=people,
        q=q,
        import_batch=import_batch,
        deep=deep,
        stacks=stacks,
    )


@router.get("/api/map/markers")
async def api_map_markers(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", people: str = "", q: str = "", deep: bool = False,
    import_batch: int = 0,
):
    """Return images with GPS data for map display."""
    return await library_service.map_markers_payload(
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        people=people,
        q=q,
        import_batch=import_batch,
        deep=deep,
    )


@router.get("/api/filter-options")
async def api_filter_options():
    """Return metadata-backed filter choices for the bottom bar."""
    return await library_service.filter_options_payload()


@router.get("/api/stats")
async def api_stats():
    return await library_service.stats_payload()
