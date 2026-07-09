"""Date inference for images without embedded capture dates."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime


DATE_RE = re.compile(r"(?<!\d)(?P<year>20\d{2}|19\d{2})[-_ ]?(?P<month>\d{2})[-_ ]?(?P<day>\d{2})(?!\d)")
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


@dataclass(frozen=True)
class InferredDate:
    date_taken: str
    date_source: str


def _format_noon(year: int, month: int, day: int) -> str | None:
    try:
        return datetime(year, month, day, 12, 0, 0).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _date_from_match(match: re.Match) -> str | None:
    return _format_noon(
        int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
    )


def infer_from_filename(filename: str) -> InferredDate | None:
    match = DATE_RE.search(filename or "")
    if not match:
        return None
    date_taken = _date_from_match(match)
    return InferredDate(date_taken, "filename") if date_taken else None


def _path_segments(filepath: str, source_root: str | None = None) -> list[str]:
    path = os.path.normpath(filepath or "")
    root = os.path.normpath(source_root or "")
    if root:
        try:
            common = os.path.commonpath([os.path.abspath(path), os.path.abspath(root)])
            if common == os.path.abspath(root):
                path = os.path.relpath(path, root)
        except ValueError:
            pass
    folder = os.path.dirname(path)
    return [part for part in re.split(r"[\\/]+", folder) if part and part != "."]


def infer_from_folder(filepath: str, source_root: str | None = None) -> InferredDate | None:
    for segment in reversed(_path_segments(filepath, source_root)):
        match = DATE_RE.fullmatch(segment)
        if match:
            date_taken = _date_from_match(match)
            return InferredDate(date_taken, "folder") if date_taken else None
        if _YEAR_RE.fullmatch(segment):
            return InferredDate(f"{segment}-01-01 12:00:00", "folder")
    return None


def infer_from_file_modified(file_modified_at) -> InferredDate | None:
    try:
        timestamp = float(file_modified_at)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return InferredDate(datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S"), "file")


def infer_image_date(
    *,
    filename: str = "",
    filepath: str = "",
    file_modified_at=None,
    source_root: str | None = None,
) -> InferredDate | None:
    return (
        infer_from_filename(filename or os.path.basename(filepath or ""))
        or infer_from_folder(filepath, source_root)
        or infer_from_file_modified(file_modified_at)
    )
