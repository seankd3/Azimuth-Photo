"""Lightroom-style import destination and copy helpers."""

import os
import re
from datetime import date

import scanner


CHUNK_SIZE = 1024 * 1024
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def safe_name(value: str, fallback: str = "Import") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "-", str(value or "")).strip(" .-_")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80] or fallback


def normalize_server_dir(path: str, fallback: str) -> str:
    value = os.path.expanduser(str(path or "").strip())
    if not value:
        value = fallback
    if not os.path.isabs(value):
        value = os.path.join(os.path.expanduser("~"), value)
    value = os.path.abspath(value)
    if value == os.path.sep:
        return fallback
    return value


def clean_relative_path(path: str, filename: str) -> str:
    raw = str(path or filename or "").replace("\\", "/").strip()
    parts = [safe_name(part, "") for part in raw.split("/") if part and part not in (".", "..")]
    parts = [part for part in parts if part]
    if not parts:
        parts = [safe_name(os.path.basename(filename or "upload"), "upload")]
    return os.path.join(*parts[-16:])


def supported_extension(filename: str) -> bool:
    return os.path.splitext(filename or "")[1].lower() in scanner.SUPPORTED_EXTENSIONS


def import_date(value: str) -> str:
    candidate = str(value or "").strip()
    return candidate if DATE_RE.match(candidate) else date.today().isoformat()


def destination_plan(
    *,
    mode: str,
    import_root: str,
    manual_destination: str = "",
    preset_path: str = "",
    shoot_date: str = "",
    shoot_name: str = "",
) -> dict:
    normalized_mode = mode if mode in {"date_shoot", "manual", "preset"} else "date_shoot"
    root = normalize_server_dir(import_root, os.path.join(os.path.expanduser("~"), "Pictures", "Azimuth Imports"))
    date_label = import_date(shoot_date)
    shoot_label = safe_name(shoot_name, "")
    batch_name = f"{date_label} - {shoot_label}" if shoot_label else date_label

    if normalized_mode == "manual":
        destination = normalize_server_dir(manual_destination, root)
        return {
            "mode": normalized_mode,
            "name": safe_name(os.path.basename(destination), batch_name),
            "root": destination,
            "destination": destination,
        }

    base = normalize_server_dir(preset_path, root) if normalized_mode == "preset" else root
    destination = os.path.join(base, date_label[:4], batch_name)
    return {
        "mode": normalized_mode,
        "name": batch_name,
        "root": base,
        "destination": destination,
    }


def unique_destination(path: str) -> tuple[str, bool]:
    if not os.path.exists(path):
        return path, False
    stem, ext = os.path.splitext(path)
    for index in range(2, 10000):
        candidate = f"{stem}-{index}{ext}"
        if not os.path.exists(candidate):
            return candidate, True
    raise RuntimeError("Could not create a unique filename")


async def copy_upload(upload, destination_path: str) -> int:
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    bytes_written = 0
    with open(destination_path, "wb") as handle:
        while True:
            chunk = await upload.read(CHUNK_SIZE)
            if not chunk:
                break
            handle.write(chunk)
            bytes_written += len(chunk)
    return bytes_written


async def copy_import_files(
    *,
    uploads,
    relative_paths: list[str],
    destination_path: str,
    preserve_structure: bool,
) -> dict:
    copied = []
    skipped = []
    collision_count = 0
    os.makedirs(destination_path, exist_ok=True)

    for index, upload in enumerate(uploads):
        original_name = os.path.basename(upload.filename or f"upload-{index + 1}")
        relative_path = relative_paths[index] if index < len(relative_paths) else original_name
        if not supported_extension(original_name):
            skipped.append({"name": original_name, "reason": "unsupported"})
            continue

        cleaned_relative = clean_relative_path(relative_path, original_name)
        if not preserve_structure:
            cleaned_relative = os.path.basename(cleaned_relative)
        target_path, collided = unique_destination(os.path.join(destination_path, cleaned_relative))
        if collided:
            collision_count += 1
        await copy_upload(upload, target_path)
        stat = os.stat(target_path)
        copied.append({
            "filename": os.path.basename(target_path),
            "filepath": target_path,
            "file_ext": os.path.splitext(target_path)[1].lower(),
            "file_size": int(stat.st_size),
            "file_modified_at": float(stat.st_mtime),
            "original_name": original_name,
        })

    return {"copied": copied, "skipped": skipped, "collision_count": collision_count}
