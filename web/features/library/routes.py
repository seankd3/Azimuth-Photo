import time

from fastapi import APIRouter, Request

from core.requests import FolderScope, RankingSort, parse_exclude_sources
from data import connection as data_connection


from features.library import service as library_service

router = APIRouter()
@router.get("/api/storage/overview")
async def api_storage_overview():
    from features.library import storage  # deferred: keeps quality-scoring numpy off boot until storage is requested

    return await storage.overview_payload()


@router.get("/api/rankings")
async def api_rankings(
    limit: int = 100, offset: int = 0, sort: RankingSort = "elo",
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: FolderScope = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", q: str = "", deep: bool = False, people: str = "",
    import_batch: int = 0, stacks: str = "expanded", ids: str = "", collection_id: int = 0,
    exclude_sources: str = "",
    request: Request = None,
):
    excluded = parse_exclude_sources(exclude_sources)
    started = time.perf_counter()
    try:
        with data_connection.sqlite_timeout(0.25):
            response = await library_service.api_rankings_impl(
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
                ids=ids,
                collection_id=collection_id,
                exclude_sources=excluded,
                request=request,
            )
    except Exception as exc:
        if not data_connection.is_sqlite_locked_error(exc):
            raise
        return {
            "images": [],
            "total_images": 0,
            "visible_images": 0,
            "pending_thumbnails": 0,
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
    folder: FolderScope = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", people: str = "", q: str = "", deep: bool = False,
    import_batch: int = 0, stacks: str = "expanded", collection_id: int = 0, exclude_sources: str = "",
):
    """Return date groups with counts for the scrubber, respecting active filters."""
    from features.library import service as library_service  # deferred: keeps numpy off boot until a Library metadata request

    excluded = parse_exclude_sources(exclude_sources)
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
        collection_id=collection_id,
        exclude_sources=excluded,)


@router.get("/api/date-histogram")
async def api_date_histogram(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: FolderScope = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", people: str = "", q: str = "", deep: bool = False,
    import_batch: int = 0, stacks: str = "expanded", collection_id: int = 0,
    exclude_sources: str = "",
):
    """Return whole-scope month counts for the timeline scrubber and month view."""
    from features.library import service as library_service  # deferred: keeps numpy off boot until a Library metadata request

    excluded = parse_exclude_sources(exclude_sources)
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
        collection_id=collection_id,
        exclude_sources=excluded,)


@router.get("/api/counts")
async def api_counts(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: FolderScope = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", people: str = "", q: str = "", deep: bool = False,
    import_batch: int = 0, stacks: str = "expanded", exclude_sources: str = "",
):
    """Return cheap total/picked/rejected counts for the scope in one call."""
    from features.library import service as library_service  # deferred: keeps numpy off boot until a Library metadata request

    excluded = parse_exclude_sources(exclude_sources)
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
        exclude_sources=excluded,)


@router.get("/api/map/markers")
async def api_map_markers(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: FolderScope = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", people: str = "", q: str = "", deep: bool = False,
    import_batch: int = 0, collection_id: int = 0, exclude_sources: str = "",
):
    """Return images with GPS data for map display."""
    from features.library import service as library_service  # deferred: keeps numpy off boot until a Library metadata request

    excluded = parse_exclude_sources(exclude_sources)
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
        tag=tag,
        people=people,
        q=q,
        import_batch=import_batch,
        deep=deep,
        collection_id=collection_id,
        exclude_sources=excluded,)


@router.get("/api/filter-options")
async def api_filter_options(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: FolderScope = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", people: str = "", q: str = "", deep: bool = False,
    import_batch: int = 0, stacks: str = "expanded", collection_id: int = 0, exclude_sources: str = "",
):
    """Return metadata-backed filter choices for the bottom bar."""
    from features.library import service as library_service  # deferred: keeps numpy off boot until a Library metadata request

    excluded = parse_exclude_sources(exclude_sources)
    return await library_service.filter_options_payload(
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
        deep=deep,
        import_batch=import_batch,
        stacks=stacks,
        collection_id=collection_id,
        exclude_sources=excluded,)


@router.get("/api/stats")
async def api_stats():
    from features.library import service as library_service  # deferred: keeps numpy off boot until a Library metadata request

    return await library_service.stats_payload()
