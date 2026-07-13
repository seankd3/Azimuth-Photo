"""Semver compatibility policy for a satellite talking to its hub."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering


# Raise this only when a satellite starts depending on a newer hub sync contract.
MIN_COMPATIBLE_HUB = "0.1.0"
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


@total_ordering
@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if core != other_core:
            return core < other_core
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            left_number, right_number = left.isdigit(), right.isdigit()
            if left_number != right_number:
                return left_number
            if left_number:
                return int(left) < int(right)
            return left < right
        return len(self.prerelease) < len(other.prerelease)


def parse_semver(value: object) -> SemVer | None:
    """Parse strict SemVer 2.0.0; callers can safely handle unknown peers."""

    match = _SEMVER.match(str(value or "").strip())
    if match is None:
        return None
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease):
        return None
    return SemVer(int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease)


def compare_semver(left: object, right: object) -> int | None:
    """Compare two versions, returning -1/0/1 or None when either is malformed."""

    parsed_left, parsed_right = parse_semver(left), parse_semver(right)
    if parsed_left is None or parsed_right is None:
        return None
    return (parsed_left > parsed_right) - (parsed_left < parsed_right)


def hub_compatibility(hub_version: object, *, minimum: str = MIN_COMPATIBLE_HUB) -> dict[str, object]:
    """Return status fields consumed by /api/sync/status and the desktop drawer."""

    version = str(hub_version or "").strip() or None
    comparison = compare_semver(version, minimum)
    return {
        "hub_version": version,
        "minimum_compatible_hub": minimum,
        "server_update_available": comparison == -1,
        "server_incompatible": comparison is None,
    }
