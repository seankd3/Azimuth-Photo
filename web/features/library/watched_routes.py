"""HTTP routes for watched folders."""

from __future__ import annotations

from core.catalog_path import catalog_path


from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.requests import json_object
from features.library import watched_folders


router = APIRouter()


def _optional_bool(body: dict, key: str) -> bool | None:
    return bool(body[key]) if key in body else None


@router.get('/api/watched-folders')
async def api_watched_folders():
    return {'folders': await watched_folders.list_folders(catalog_path())}


@router.post('/api/watched-folders')
async def api_add_watched_folder(request: Request):
    body, error = await json_object(request)
    if error:
        return error
    try:
        folder = await watched_folders.add_folder(
            catalog_path(), body.get('path', ''), recursive=bool(body.get('recursive', True)), enabled=bool(body.get('enabled', True))
        )
    except ValueError as exc:
        return JSONResponse({'error': str(exc)}, status_code=400)
    return {'ok': True, 'folder': folder}


@router.patch('/api/watched-folders/{folder_id}')
async def api_update_watched_folder(folder_id: int, request: Request):
    body, error = await json_object(request)
    if error:
        return error
    folder = await watched_folders.update_folder(
        catalog_path(), folder_id, enabled=_optional_bool(body, 'enabled'), recursive=_optional_bool(body, 'recursive')
    )
    if not folder:
        return JSONResponse({'error': 'Watched folder not found'}, status_code=404)
    return {'ok': True, 'folder': folder}


@router.delete('/api/watched-folders/{folder_id}')
async def api_remove_watched_folder(folder_id: int):
    if not await watched_folders.remove_folder(catalog_path(), folder_id):
        return JSONResponse({'error': 'Watched folder not found'}, status_code=404)
    return {'ok': True, 'folder_id': folder_id}


@router.post('/api/watched-folders/{folder_id}/scan')
async def api_scan_watched_folder(folder_id: int):
    try:
        result = await watched_folders.scan_folder(catalog_path(), folder_id)
    except LookupError as exc:
        return JSONResponse({'error': str(exc)}, status_code=404)
    return JSONResponse(result, status_code=200 if result.get('ok') else 409)
