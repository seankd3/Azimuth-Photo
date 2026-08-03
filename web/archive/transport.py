"""One way to talk to the hub.

Six copies of the same ten lines had drifted into eight different timeouts.
Nothing about a request to the hub varies except how long the caller can afford
to wait, so that is the only knob, and it is named rather than a number typed at
the call site.

An HTTP error is a reply, not an exception. A 404 from the hub means "I do not
have that", which is information the caller wants; raising on it forced every
caller to unwrap an exception to read a status code they had asked for.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import NamedTuple

#: Reachability and version checks. Short enough that a hub which is asleep
#: cannot stall a page render.
CONTRACT = 3.0

#: A person is waiting for this. The laptop must stay responsive, so a hub that
#: has gone away costs a visible pause, not a hang.
INTERACTIVE = 10.0

#: Moving originals or preview packs. Slow by nature, never on a render path.
BULK = 300.0


class HubUnavailable(RuntimeError):
    """The hub is there but cannot serve this now — 5xx, or asked us to wait."""


def is_transient(error: BaseException) -> bool:
    """Is retrying later worth anything?

    The sync worker used to answer this by searching the error message for six
    English words. A 503 matched none of them and hot-looped every fifteen
    seconds; a French locale would have matched none of them either. Ask what
    the failure is, not how it was spelled: anything the network raises is worth
    retrying, and a rejection with a status code is not.
    """

    if isinstance(error, HubUnavailable):
        return True
    return isinstance(error, (urllib.error.URLError, TimeoutError, OSError))


class Reply(NamedTuple):
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _outbound(method: str, url: str, body: bytes | None, headers: dict | None):
    # Imported here, not at module scope, because the hub URL and device token
    # still live in features/sync/satellite.py. archive/ importing features/ is
    # backwards and the lazy import only hides it: the state belongs here, and
    # moving it is 47 call sites through the pairing path. Recorded in
    # docs/SIMPLIFY_LOG.md rather than left looking deliberate.
    from features.sync import satellite

    merged = dict(headers or {})
    merged.update(satellite.hub_request_headers())
    return urllib.request.Request(url, data=body, headers=merged, method=method)


def request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict | None = None,
    timeout: float = INTERACTIVE,
) -> Reply:
    """Blocking hub request. Callers on a request path want request_async."""

    outbound = _outbound(method, url, body, headers)
    try:
        with urllib.request.urlopen(outbound, timeout=timeout) as response:  # noqa: S310 - configured hub URL.
            return Reply(
                int(response.status),
                {key.lower(): value for key, value in response.headers.items()},
                response.read(),
            )
    except urllib.error.HTTPError as error:
        return Reply(
            int(error.code),
            {key.lower(): value for key, value in (error.headers or {}).items()},
            error.read(),
        )


async def request_async(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict | None = None,
    timeout: float = INTERACTIVE,
    runner=None,
) -> Reply:
    """The same request, off the event loop.

    `runner` exists because sync work runs on a bounded pool that the rest of
    the app shares; the default keeps hub traffic inside it.
    """

    def work() -> Reply:
        return request(method, url, body=body, headers=headers, timeout=timeout)

    if runner is not None:
        return await runner(work)
    from features.sync.executor import run_sync_work

    return await run_sync_work(work)


def open_stream(
    url: str,
    *,
    headers: dict | None = None,
    timeout: float = BULK,
):
    """Open a hub response without reading it.

    `request` reads the whole body, which is right for JSON and wrong for a
    50 MB original. The caller owns the returned file-like object and must
    close it. Returns None if the hub cannot be reached — a streamed original
    has a fallback, so this is a miss rather than an error.
    """

    try:
        return urllib.request.urlopen(  # noqa: S310 - configured hub URL.
            _outbound("GET", url, None, headers), timeout=timeout
        )
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
