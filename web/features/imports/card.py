"""Removable-card discovery and byte-safe staged copy primitives."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable

from features.sync.hashing import HASH_PREFIX_BYTES, compute_full_hash


VIDEO_EXTENSIONS = {".mp4", ".mov"}
COPY_CHUNK_BYTES = 1024 * 1024
CARD_POLL_SECONDS = 5.0
_cached_cards: dict[str, object] = {"expires": 0.0, "rows": []}
WINDOWS_DRIVE_REMOVABLE = 2
WINDOWS_DRIVE_FIXED = 3
_HARDLINK_FALLBACK_ERRNOS = {
    errno.EPERM,
    errno.EXDEV,
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EPERM),
    getattr(errno, "EOPNOTSUPP", errno.EPERM),
}


def _linux_volumes() -> Iterable[Path]:
    user = os.environ.get("USER", "")
    for root in (Path("/run/media") / user, Path("/media")):
        if not root.is_dir():
            continue
        try:
            yield from (child for child in root.iterdir() if child.is_dir())
        except OSError:
            continue


def _windows_volumes() -> Iterable[Path]:
    import ctypes

    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
        has_dcim = drive_type in {WINDOWS_DRIVE_REMOVABLE, WINDOWS_DRIVE_FIXED} and os.path.isdir(
            f"{root}DCIM"
        )
        if _is_windows_card_drive(drive_type, has_dcim=has_dcim):
            yield Path(root)


def _is_windows_card_drive(drive_type: int, *, has_dcim: bool) -> bool:
    return drive_type == WINDOWS_DRIVE_REMOVABLE or (
        drive_type == WINDOWS_DRIVE_FIXED and has_dcim
    )


def removable_volumes() -> list[Path]:
    """Small, stdlib-only platform seam; tests pass their own enumerator."""

    candidates = _windows_volumes() if sys.platform.startswith("win") else _linux_volumes()
    return [path for path in candidates if (path / "DCIM").is_dir()]


def card_stats(root: Path, supported: set[str]) -> tuple[int, int]:
    count = total = 0
    try:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in supported:
                continue
            count += 1
            total += path.stat().st_size
    except OSError:
        pass
    return count, total


def detected_cards(
    *,
    supported: set[str],
    volume_enumerator: Callable[[], Iterable[Path]] = removable_volumes,
) -> list[dict]:
    cards = []
    for root in volume_enumerator():
        root = Path(root).resolve()
        dcim = root / "DCIM"
        if not dcim.is_dir():
            continue
        count, total = card_stats(dcim, supported)
        cards.append({
            "id": f"card:{root}",
            "kind": "card",
            "label": f"Card · {root.name or root.drive}",
            "path": str(root),
            "photo_count": count,
            "bytes": total,
        })
    return cards


def polled_cards(*, supported: set[str]) -> list[dict]:
    """Five-second removable-volume poll used by the staged source endpoint."""

    now = time.monotonic()
    if now < float(_cached_cards["expires"]):
        return list(_cached_cards["rows"])
    rows = detected_cards(supported=supported)
    _cached_cards.update(expires=now + CARD_POLL_SECONDS, rows=rows)
    return list(rows)


def is_card_path(path: str, cards: Iterable[dict]) -> bool:
    candidate = Path(path).resolve()
    for card in cards:
        try:
            candidate.relative_to(Path(card["path"]).resolve())
            return True
        except ValueError:
            continue
    return False


def content_hash_from_stream(path: Path) -> tuple[str, str, int]:
    """Return the sync identity, full identity, and byte count in one read."""

    full = hashlib.blake2b(digest_size=16)
    prefix = hashlib.blake2b(digest_size=16)
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(COPY_CHUNK_BYTES):
            full.update(chunk)
            if total < HASH_PREFIX_BYTES:
                prefix.update(chunk[: HASH_PREFIX_BYTES - total])
            total += len(chunk)
    prefix.update(total.to_bytes(8, byteorder="little", signed=False))
    return prefix.hexdigest(), full.hexdigest(), total


def _fsync_file(path: Path) -> None:
    # Windows FlushFileBuffers needs a writable handle; an "rb" fsync EBADFs.
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    if sys.platform.startswith("win"):
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _destination_candidates(directory: Path, filename: str) -> Iterable[Path]:
    preferred = directory / filename
    yield preferred
    for index in range(2, 10_000):
        yield preferred.with_name(f"{preferred.stem}-{index}{preferred.suffix}")


def _finalize_partial(partial: Path, candidate: Path) -> None:
    """Claim a collision-safe destination even when hardlinks are unavailable."""

    try:
        os.link(partial, candidate)
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in _HARDLINK_FALLBACK_ERRNOS:
            raise
        try:
            with partial.open("rb") as incoming, candidate.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=COPY_CHUNK_BYTES)
                outgoing.flush()
                os.fsync(outgoing.fileno())
        except FileExistsError:
            raise
        except Exception:
            candidate.unlink(missing_ok=True)
            raise
    partial.unlink(missing_ok=True)


def copy_verified(source: str, directory: str, *, retry_count: int = 1) -> dict:
    """Copy one file without overwriting, then verify the final destination.

    The returned file is fsynced and has passed a second full-file hash.  It is
    deliberately still *unregistered*: callers must register it before they may
    delete a card original.
    """

    source_path = Path(source)
    destination_dir = Path(directory)
    destination_dir.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for _attempt in range(retry_count + 1):
        partial = destination_dir / f".{source_path.name}.{uuid.uuid4().hex}.importing"
        try:
            content_hash, source_full_hash, byte_count = _copy_stream(source_path, partial)
            _fsync_file(partial)
            destination = None
            for candidate in _destination_candidates(destination_dir, source_path.name):
                if candidate.exists():
                    if compute_full_hash(candidate) == source_full_hash:
                        partial.unlink(missing_ok=True)
                        return {"duplicate_destination": str(candidate), "content_hash": content_hash,
                                "full_hash": source_full_hash, "bytes": byte_count}
                    continue
                try:
                    _finalize_partial(partial, candidate)
                    destination = candidate
                    break
                except FileExistsError:
                    continue
            if destination is None:
                raise RuntimeError("Could not create a collision-safe destination")
            _fsync_file(destination)
            _fsync_directory(destination_dir)
            if compute_full_hash(destination) != source_full_hash:
                destination.unlink(missing_ok=True)
                raise ArithmeticError("Destination full hash did not match copied source")
            return {"destination": str(destination), "content_hash": content_hash,
                    "full_hash": source_full_hash, "bytes": byte_count}
        except Exception as exc:
            partial.unlink(missing_ok=True)
            last_error = exc
    raise last_error or RuntimeError("Copy verification failed")


def _copy_stream(source: Path, partial: Path) -> tuple[str, str, int]:
    full = hashlib.blake2b(digest_size=16)
    prefix = hashlib.blake2b(digest_size=16)
    total = 0
    with source.open("rb") as incoming, partial.open("xb") as outgoing:
        while chunk := incoming.read(COPY_CHUNK_BYTES):
            outgoing.write(chunk)
            full.update(chunk)
            if total < HASH_PREFIX_BYTES:
                prefix.update(chunk[: HASH_PREFIX_BYTES - total])
            total += len(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    prefix.update(total.to_bytes(8, byteorder="little", signed=False))
    return prefix.hexdigest(), full.hexdigest(), total


def remove_verified_card_file(path: str) -> None:
    """The caller has already verified, fsynced, and registered its destination."""

    Path(path).unlink()
