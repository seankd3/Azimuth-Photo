from fastapi import Request
from fastapi.responses import JSONResponse


class RequestBodyTooLarge(ValueError):
    pass


def positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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
