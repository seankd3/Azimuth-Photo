import asyncio
import uuid
import time
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.requests import FolderScope, json_object, parse_exclude_sources, positive_int
from data import connection as data_connection


import db
from core import cache_events, propagation_queue
from features.compare import service as compare_service


router = APIRouter()
MAX_SCOPED_IMAGE_IDS = 2000
MAX_SCOPED_IDS_LENGTH = 20000
InvalidatePairing = Callable[..., None]
USER_WRITE_TIMEOUT_SECONDS = 5.0
_user_write_lock: asyncio.Lock | None = None
_user_write_lock_loop: asyncio.AbstractEventLoop | None = None


def _user_write_lock_for_loop() -> asyncio.Lock:
    global _user_write_lock, _user_write_lock_loop
    loop = asyncio.get_running_loop()
    if _user_write_lock is None or _user_write_lock_loop is not loop:
        _user_write_lock = asyncio.Lock()
        _user_write_lock_loop = loop
    return _user_write_lock


def _apply_propagated_pairing_updates(*, elo_deltas=None) -> None:
    """Patch the ids propagation touched; clear only when they are unknown.

    A pick schedules its own propagation, so clearing every reservoir on drain
    threw away the rows that same pick had just patched and made the next click
    fully cold.
    """

    if elo_deltas is None:
        cache_events.invalidate_pairing_cache()
        return
    compare_service.patch_propagated_pairing_cache(elo_deltas)


def _schedule_propagation(coro) -> None:
    propagation_queue.schedule(coro, invalidate_callback=_apply_propagated_pairing_updates)


def _parse_scoped_ids(ids: str | None) -> tuple[list[int] | None, JSONResponse | None]:
    if ids is None:
        return None, None
    if len(ids) > MAX_SCOPED_IDS_LENGTH:
        return [], JSONResponse(
            {"error": f"ids is limited to {MAX_SCOPED_IDS_LENGTH} characters"},
            status_code=400,
        )
    scoped_ids = []
    seen = set()
    saw_token = False
    for value in ids.split(","):
        value = value.strip()
        if not value:
            continue
        saw_token = True
        if not value.isdigit():
            return [], JSONResponse({"error": "ids must contain positive integers"}, status_code=400)
        image_id = int(value)
        if image_id <= 0:
            return [], JSONResponse({"error": "ids must contain positive integers"}, status_code=400)
        if image_id in seen:
            continue
        seen.add(image_id)
        scoped_ids.append(image_id)
        if len(scoped_ids) > MAX_SCOPED_IMAGE_IDS:
            return [], JSONResponse(
                {"error": f"ids is limited to {MAX_SCOPED_IMAGE_IDS} images"},
                status_code=400,
            )
    if not saw_token or not scoped_ids:
        return [], JSONResponse({"error": "ids must contain at least one image id"}, status_code=400)
    return scoped_ids, None


@router.get("/api/mosaic/next")
async def mosaic_next(
    n: int = 12, exclude: str = "", strategy: str = "explore", grid_elo: float = 0,
    orientation: str = "", compared: str = "", min_stars: int = 0, folder: FolderScope = "",
    flag: str = "", date_taken: str = "", file_type: str = "", camera: str = "", lens: str = "",
    tag: str = "", q: str = "", deep: bool = False, people: str = "", ids: str | None = None,
    collection_id: int = 0, import_batch: int = 0,
    exclude_sources: str = "",
):
    if compare_service.mosaic_next_impl is None:
        raise RuntimeError("Compare routes are not configured")
    scoped_ids, id_error = _parse_scoped_ids(ids)
    if id_error is not None:
        return id_error
    excluded = parse_exclude_sources(exclude_sources)
    started = time.perf_counter()
    try:
        with data_connection.sqlite_timeout(0.25):
            response = await compare_service.mosaic_next_impl(
                n=n,
                exclude=exclude,
                strategy=strategy,
                grid_elo=grid_elo,
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
                ids=scoped_ids,
                collection_id=collection_id,
                import_batch=import_batch,
                exclude_sources=excluded,
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
            "stats": {"filtered_pool": 0, "filtered_pool_visible": 0, "filtered_pool_total": 0},
            "status_stale": True,
            "counts_stale": True,
            "candidate_source": "sqlite_busy",
            "pairing": "strategy",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    if isinstance(response, dict):
        response.setdefault("status_stale", False)
        response.setdefault("latency_ms", round((time.perf_counter() - started) * 1000, 1))
    return response


@router.post("/api/mosaic/pick")
async def mosaic_pick(request: Request):
    """
    User picked the best image from the visible mosaic.
    Body: { "winner_id": int, "loser_ids": [int, ...] }
    K=12 per pair.
    """
    import elo_propagation  # deferred: keeps numpy off boot until a comparison propagation is queued
    body, error = await json_object(request)
    if error:
        return error
    picked_id = positive_int(body.get("winner_id"))
    raw_other_ids = body.get("loser_ids", [])

    if picked_id is None or not isinstance(raw_other_ids, list) or not raw_other_ids:
        return JSONResponse({"error": "Need winner_id and loser_ids"}, status_code=400)

    other_ids = []
    seen_losers = set()
    for value in raw_other_ids:
        loser_id = positive_int(value)
        if loser_id is None:
            return JSONResponse({"error": "loser_ids must contain valid image ids"}, status_code=400)
        if loser_id == picked_id:
            return JSONResponse({"error": "Winner cannot also be a loser"}, status_code=400)
        if loser_id in seen_losers:
            return JSONResponse({"error": "Duplicate loser_ids are not allowed"}, status_code=400)
        seen_losers.add(loser_id)
        other_ids.append(loser_id)

    action_id = uuid.uuid4().hex
    try:
        async with _user_write_lock_for_loop():
            with data_connection.sqlite_timeout(USER_WRITE_TIMEOUT_SECONDS):
                result = await db.record_active_mosaic_pick(picked_id, other_ids, action_id)
    except Exception as exc:
        if not data_connection.is_sqlite_locked_error(exc):
            raise
        return JSONResponse(
            {
                "error": "Database is busy; pick was not saved",
                "status_stale": True,
            },
            status_code=503,
        )
    if not result.get("ok"):
        return JSONResponse(
            {
                "error": "Images must exist in an active catalog",
                "image_ids": result.get("missing_ids", []),
            },
            status_code=400,
        )

    picked_elo = result["new_elo"]
    pairs_recorded = result["pairs_recorded"]
    loser_updates = result["loser_updates"]

    if pairs_recorded:
        compare_service.patch_pairing_cache(
            [(picked_id, picked_elo, pairs_recorded)]
            + [(image_id, new_elo, 1) for image_id, new_elo in loser_updates]
        )
    compare_service.add_past_matchups([(picked_id, loser_id) for loser_id in other_ids])

    _schedule_propagation(
        elo_propagation.propagate_mosaic(picked_id, other_ids, k=12.0, action_id=action_id)
    )

    return {
        "ok": True,
        "new_elo": round(picked_elo, 1),
        "pairs_recorded": pairs_recorded,
        "action_id": action_id,
    }


@router.get("/api/propagation/last")
async def propagation_last():
    """Return the number of images affected by the last Elo propagation."""
    from core import propagation_queue
    import elo_propagation  # deferred: keeps numpy off boot until propagation status is requested

    return {
        "count": elo_propagation.last_propagation_count,
        "queue": propagation_queue.status(),
    }


@router.post("/api/compare")
async def submit_comparison(request: Request):
    import elo_propagation  # deferred: keeps numpy off boot until a comparison propagation is queued
    body, error = await json_object(request)
    if error:
        return error
    winner_id = positive_int(body.get("winner_id"))
    loser_id = positive_int(body.get("loser_id"))
    mode = body.get("mode", "swiss")

    if winner_id is None or loser_id is None:
        return JSONResponse({"error": "winner_id and loser_id are required"}, status_code=400)
    if winner_id == loser_id:
        return JSONResponse({"error": "Winner and loser must be different images"}, status_code=400)

    action_id = uuid.uuid4().hex
    try:
        async with _user_write_lock_for_loop():
            with data_connection.sqlite_timeout(USER_WRITE_TIMEOUT_SECONDS):
                result = await db.record_active_comparison(winner_id, loser_id, mode, action_id=action_id)
    except Exception as exc:
        if not data_connection.is_sqlite_locked_error(exc):
            raise
        return JSONResponse(
            {
                "error": "Database is busy; comparison was not saved",
                "status_stale": True,
            },
            status_code=503,
        )
    if not result:
        return JSONResponse(
            {"error": "Images must exist in an active catalog"},
            status_code=400,
        )

    new_winner_elo = result["winner_elo"]
    new_loser_elo = result["loser_elo"]
    compare_service.patch_pairing_cache([(winner_id, new_winner_elo, 1), (loser_id, new_loser_elo, 1)])
    compare_service.add_past_matchups([(winner_id, loser_id)])
    _schedule_propagation(
        elo_propagation.propagate_comparison(winner_id, loser_id, result["k"], action_id=action_id)
    )

    return {
        "ok": True,
        "winner_elo": round(new_winner_elo, 1),
        "loser_elo": round(new_loser_elo, 1),
        "action_id": action_id,
    }


@router.post("/api/compare/undo")
async def compare_undo():
    result = await db.undo_last_comparison()
    if result:
        if result.get("skipped_drift"):
            return {"ok": False, "partial": True, **result}
        cache_events.invalidate_pairing_cache(matchups=True)
        return {"ok": True, **result}
    return JSONResponse({"error": "Nothing to undo"}, status_code=400)
