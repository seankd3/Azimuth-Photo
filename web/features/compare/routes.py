import asyncio
import uuid
import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import elo_propagation
from core.requests import json_object, positive_int
from data import connection as data_connection


router = APIRouter()
MAX_SCOPED_IMAGE_IDS = 2000
MAX_SCOPED_IDS_LENGTH = 20000
PatchPairingCache = Callable[[list[tuple[int, float, int]]], None]
AddPastMatchups = Callable[[list[tuple[int, int]]], None]
SchedulePropagation = Callable[[object], None]
InvalidatePairing = Callable[..., None]
NextHandler = Callable[..., object]
RecordMosaicPick = Callable[[int, list[int], str], Awaitable[dict]]
RecordComparison = Callable[..., Awaitable[dict | None]]
UndoComparison = Callable[[], Awaitable[dict | None]]
_patch_pairing_cache: PatchPairingCache | None = None
_add_past_matchups: AddPastMatchups | None = None
_schedule_pairing_propagation: SchedulePropagation | None = None
_invalidate_pairing_cache: InvalidatePairing | None = None
_mosaic_next_handler: NextHandler | None = None
_compare_next_handler: NextHandler | None = None
_record_active_mosaic_pick: RecordMosaicPick | None = None
_record_active_comparison: RecordComparison | None = None
_undo_last_comparison: UndoComparison | None = None
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


def configure(
    *,
    patch_pairing_cache: PatchPairingCache,
    add_past_matchups: AddPastMatchups,
    schedule_pairing_propagation: SchedulePropagation,
    invalidate_pairing_cache: InvalidatePairing,
    record_active_mosaic_pick: RecordMosaicPick,
    record_active_comparison: RecordComparison,
    undo_last_comparison: UndoComparison,
    mosaic_next_handler: NextHandler | None = None,
    compare_next_handler: NextHandler | None = None,
) -> None:
    global _patch_pairing_cache, _add_past_matchups
    global _schedule_pairing_propagation, _invalidate_pairing_cache
    global _mosaic_next_handler, _compare_next_handler
    global _record_active_mosaic_pick, _record_active_comparison, _undo_last_comparison
    _patch_pairing_cache = patch_pairing_cache
    _add_past_matchups = add_past_matchups
    _schedule_pairing_propagation = schedule_pairing_propagation
    _invalidate_pairing_cache = invalidate_pairing_cache
    _record_active_mosaic_pick = record_active_mosaic_pick
    _record_active_comparison = record_active_comparison
    _undo_last_comparison = undo_last_comparison
    _mosaic_next_handler = mosaic_next_handler
    _compare_next_handler = compare_next_handler


def _configured() -> None:
    if (
        _patch_pairing_cache is None
        or _add_past_matchups is None
        or _schedule_pairing_propagation is None
        or _invalidate_pairing_cache is None
        or _record_active_mosaic_pick is None
        or _record_active_comparison is None
        or _undo_last_comparison is None
    ):
        raise RuntimeError("Compare routes are not configured")


def _parse_scoped_ids(ids: str | None) -> tuple[list[int], JSONResponse | None]:
    if ids is None:
        return [], None
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
    orientation: str = "", compared: str = "", min_stars: int = 0, folder: str = "",
    flag: str = "", date_taken: str = "", file_type: str = "", camera: str = "", lens: str = "",
    q: str = "", deep: bool = False, people: str = "", ids: str | None = None, collection_id: int = 0,
):
    if _mosaic_next_handler is None:
        raise RuntimeError("Compare routes are not configured")
    scoped_ids, id_error = _parse_scoped_ids(ids)
    if id_error is not None:
        return id_error
    started = time.perf_counter()
    try:
        with data_connection.sqlite_timeout(0.25):
            response = await _mosaic_next_handler(
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
                q=q,
                deep=deep,
                people=people,
                ids=scoped_ids,
                collection_id=collection_id,
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
    _configured()
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
                result = await _record_active_mosaic_pick(picked_id, other_ids, action_id)
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
        _patch_pairing_cache(
            [(picked_id, picked_elo, pairs_recorded)]
            + [(image_id, new_elo, 1) for image_id, new_elo in loser_updates]
        )
    _add_past_matchups([(picked_id, loser_id) for loser_id in other_ids])

    _schedule_pairing_propagation(
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

    return {
        "count": elo_propagation.last_propagation_count,
        "queue": propagation_queue.status(),
    }


@router.post("/api/propagation/predict")
async def propagation_predict(request: Request):
    """Precompute propagation counts for each possible winner in a grid."""
    body, error = await json_object(request)
    if error:
        return error
    grid_ids = body.get("grid_ids", [])
    if not grid_ids:
        return {"counts": {}}
    counts = await elo_propagation.predict_propagation(grid_ids)
    return {"counts": {str(k): v for k, v in counts.items()}}


@router.get("/api/compare/next")
async def compare_next(
    n: int = 5, mode: str = "swiss",
    orientation: str = "", compared: str = "", min_stars: int = 0, folder: str = "",
    flag: str = "", date_taken: str = "", file_type: str = "", camera: str = "", lens: str = "",
    q: str = "", deep: bool = False, people: str = "", ids: str | None = None, collection_id: int = 0,
):
    if _compare_next_handler is None:
        raise RuntimeError("Compare routes are not configured")
    scoped_ids, id_error = _parse_scoped_ids(ids)
    if id_error is not None:
        return id_error
    started = time.perf_counter()
    try:
        with data_connection.sqlite_timeout(0.25):
            response = await _compare_next_handler(
                n=n,
                mode=mode,
                orientation=orientation,
                compared=compared,
                min_stars=min_stars,
                folder=folder,
                flag=flag,
                date_taken=date_taken,
                file_type=file_type,
                camera=camera,
                lens=lens,
                q=q,
                deep=deep,
                people=people,
                ids=scoped_ids,
                collection_id=collection_id,
            )
    except Exception as exc:
        if not data_connection.is_sqlite_locked_error(exc):
            raise
        return {
            "pairs": [],
            "total_images": 0,
            "visible_images": 0,
            "hidden_pending_thumbnails": 0,
            "total_kept": 0,
            "stats": {"filtered_pool": 0, "filtered_pool_visible": 0, "filtered_pool_total": 0},
            "status_stale": True,
            "counts_stale": True,
            "candidate_source": "sqlite_busy",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    if isinstance(response, dict):
        response.setdefault("status_stale", False)
        response.setdefault("latency_ms", round((time.perf_counter() - started) * 1000, 1))
    return response


@router.post("/api/compare")
async def submit_comparison(request: Request):
    _configured()
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
                result = await _record_active_comparison(winner_id, loser_id, mode, action_id=action_id)
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
    _patch_pairing_cache([(winner_id, new_winner_elo, 1), (loser_id, new_loser_elo, 1)])
    _add_past_matchups([(winner_id, loser_id)])
    _schedule_pairing_propagation(
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
    _configured()
    result = await _undo_last_comparison()
    if result:
        _invalidate_pairing_cache(matchups=True)
        return {"ok": True, **result}
    return JSONResponse({"error": "Nothing to undo"}, status_code=400)
