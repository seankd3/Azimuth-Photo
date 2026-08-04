from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.responses import JSONResponse


class RequestBodyTooLarge(ValueError):
    pass


def _folder_scope(request: Request, folder: str = Query("")) -> str | list[str]:
    """Every folder the sidebar selected, not just the last one.

    The tree sends one `folder=` per selected node. A route declaring
    `folder: str` gets the last of them and silently scopes to a third of what
    the person picked — which is how Refine came to say "not enough photos to
    refine" about a 195-photo selection: it was looking at the last folder
    alone, and that one held five.

    A dependency rather than a line each route must remember to write. Six
    routes remembered; two did not.

    The `folder` parameter is declared here, unused, so that FastAPI keeps
    documenting it in the OpenAPI schema — a pure `Depends` reads the query
    string but disappears from the published surface, and the harness caught it
    vanishing from nine routes at once.
    """

    values = [value for value in request.query_params.getlist("folder") if value]
    if not values:
        return ""
    return values[0] if len(values) == 1 else values


#: Declare `folder: FolderScope` and the repeated values arrive on their own.
FolderScope = Annotated[str | list[str], Depends(_folder_scope)]


def positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def parse_exclude_sources(value) -> tuple[int, ...]:
    """Parse optional exclude_sources query values into stable positive IDs."""
    if value is None or value == "" or value == ():
        return ()
    if isinstance(value, (list, tuple)):
        raw_parts = []
        for item in value:
            raw_parts.extend(str(item or "").replace(";", ",").split(","))
    else:
        raw_parts = str(value).replace(";", ",").split(",")
    out: list[int] = []
    seen: set[int] = set()
    for part in raw_parts:
        source_id = positive_int(part.strip()) if isinstance(part, str) else positive_int(part)
        if source_id is None or source_id in seen:
            continue
        seen.add(source_id)
        out.append(source_id)
    return tuple(out)


def clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def repeated_query_values(request: Request | None, name: str, fallback: str = "") -> str | list[str]:
    if request is None:
        return fallback or ""
    try:
        values = [value for value in request.query_params.getlist(name) if value]
    except KeyError:
        return fallback or ""
    if not values:
        return fallback or ""
    if len(values) == 1:
        return values[0]
    return values


async def read_body_limited(request: Request, maximum_bytes: int) -> bytes:
    """Read an ASGI request body without buffering beyond the route's limit."""

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > maximum_bytes:
                raise RequestBodyTooLarge()
        except ValueError as exc:
            raise RequestBodyTooLarge() from exc

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum_bytes:
            raise RequestBodyTooLarge()
        body.extend(chunk)
    return bytes(body)


async def json_object(request: Request):
    try:
        body = await request.json()
    except Exception:
        return None, JSONResponse({"error": "Malformed JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return None, JSONResponse({"error": "JSON body must be an object"}, status_code=400)
    return body, None
