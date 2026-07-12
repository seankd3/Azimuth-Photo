#!/usr/bin/env python3
"""Build the frozen photoArchive server (PyInstaller onedir).

Output: dist/photoarchive-server/

Uses a SEPARATE build venv — never the shared web/.venv symlink. Default build
venv: ~/.cache/photoarchive-pkg-venv (needs symlink support; override with
PHOTOARCHIVE_BUILD_VENV). Heavy PyInstaller workpath defaults to
/mnt/expansion/tmp/pkg-pyinstaller.

  python3.12 scripts/build_server.py           # bootstrap venv + build
  python3.12 scripts/build_server.py --build-only   # reuse existing venv

Base deps only (no torch). Ships static/, templates/, develop film stocks and
camera profiles, plus zeroconf/tifffile/imagecodecs.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DIST = ROOT / "dist"
SPEC_WORK = Path(os.environ.get("PHOTOARCHIVE_BUILD_WORK", "/mnt/expansion/tmp/pkg-pyinstaller"))
DEFAULT_VENV = Path(
    os.environ.get(
        "PHOTOARCHIVE_BUILD_VENV",
        str(Path.home() / ".cache" / "photoarchive-pkg-venv"),
    )
)

# Packages that must be present in the frozen artifact (and not pulled via torch).
BASE_PIP = [
    "pyinstaller>=6.3",
    "tifffile",
    "imagecodecs",
    "zeroconf",
]

EXCLUDE_MODULES = [
    "torch",
    "torchvision",
    "torchaudio",
    "sentence_transformers",
    "transformers",
    "insightface",
    "onnxruntime",
    "onnxruntime_gpu",
    "tensorflow",
    "jax",
    "jaxlib",
]

HIDDEN_IMPORTS = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "multipart",
    "aiosqlite",
    "jinja2",
    "rawpy",
    "PIL",
    "numpy",
    "cv2",
    "sklearn",
    "sklearn.utils._cython_blas",
    "zeroconf",
    "tifffile",
    "imagecodecs",
    "email.mime.text",
    "email.mime.multipart",
    # App package surface (dynamic feature imports).
    "app",
    "db",
    "settings",
    "scanner",
    "thumbnails",
    "helpers",
    "pairing",
    "photo_metadata",
    "field_sync",
    "ai_models",
    "embed_cache",
    "elo_propagation",
    "date_inference",
    "caption_worker",
    "embedding_worker",
    "face_worker",
]


def _python_for_venv() -> str:
    preferred = os.environ.get("PHOTOARCHIVE_BUILD_PYTHON")
    if preferred:
        return preferred
    for candidate in ("python3.12", "python3.11", "python3"):
        path = shutil.which(candidate)
        if path:
            return path
    return sys.executable


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def bootstrap_venv(venv: Path) -> Path:
    """Create the build venv and install base requirements + PyInstaller."""
    venv.parent.mkdir(parents=True, exist_ok=True)
    py = _python_for_venv()
    vpy = _venv_python(venv)
    req = WEB / "requirements.txt"

    if not vpy.exists():
        print(f"[build_server] creating venv with {py} at {venv}")
        # Prefer uv when available — handles standalone CPython builds cleanly.
        uv = shutil.which("uv")
        if uv:
            subprocess.check_call([uv, "venv", "-p", py, str(venv)])
        else:
            subprocess.check_call([py, "-m", "venv", str(venv)])

    print(f"[build_server] installing deps into {venv}")
    uv = shutil.which("uv")
    if uv:
        subprocess.check_call([uv, "pip", "install", "-p", str(vpy), "-r", str(req), *BASE_PIP])
    else:
        subprocess.check_call([str(vpy), "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools"])
        subprocess.check_call([str(vpy), "-m", "pip", "install", "-r", str(req), *BASE_PIP])
    return vpy


def _add_data_arg(src: Path, dest: str) -> str:
    # PyInstaller --add-data uses OS path separator between src and dest.
    sep = ";" if os.name == "nt" else ":"
    return f"{src}{sep}{dest}"


def data_files() -> list[tuple[Path, str]]:
    return [
        (WEB / "static", "static"),
        (WEB / "templates", "templates"),
        (WEB / "features" / "develop" / "film_stocks", str(Path("features") / "develop" / "film_stocks")),
        (WEB / "features" / "develop" / "profiles", str(Path("features") / "develop" / "profiles")),
        # schema.py is code; keep a copy under data/ for any tooling that expects it.
        (WEB / "data", "data"),
    ]


def run_pyinstaller(vpy: Path) -> Path:
    SPEC_WORK.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    entry = ROOT / "scripts" / "server_entry.py"
    if not entry.is_file():
        raise SystemExit(f"missing entrypoint: {entry}")

    args: list[str] = [
        str(vpy),
        "-m",
        "PyInstaller",
        str(entry),
        "--name=photoarchive-server",
        "--onedir",
        "--noconfirm",
        "--clean",
        f"--paths={WEB}",
        f"--distpath={DIST}",
        f"--workpath={SPEC_WORK}",
        f"--specpath={SPEC_WORK}",
    ]
    for src, dest in data_files():
        if not src.exists():
            print(f"[build_server] skip missing data: {src}")
            continue
        args.append(f"--add-data={_add_data_arg(src, dest)}")

    for name in HIDDEN_IMPORTS:
        args.append(f"--hidden-import={name}")
    for name in EXCLUDE_MODULES:
        args.append(f"--exclude-module={name}")

    # Binary-heavy packages: collect full distributions so .so/.pyd land in onedir.
    for pkg in ("rawpy", "imagecodecs", "cv2", "zeroconf"):
        args.append(f"--collect-all={pkg}")
    args.append("--collect-submodules=uvicorn")
    args.append("--collect-submodules=features")
    args.append("--collect-submodules=core")
    args.append("--collect-submodules=data")

    print("[build_server] running PyInstaller…")
    subprocess.check_call(args)

    out = DIST / "photoarchive-server"
    exe = out / ("photoarchive-server.exe" if os.name == "nt" else "photoarchive-server")
    if not exe.exists():
        # Some PyInstaller layouts nest the binary one level deeper.
        candidates = list(out.rglob("photoarchive-server*"))
        raise SystemExit(f"build finished but binary missing at {exe}; found: {candidates[:8]}")
    print(f"[build_server] ok → {exe}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build frozen photoArchive server (onedir)")
    parser.add_argument(
        "--venv",
        type=Path,
        default=DEFAULT_VENV,
        help=f"Build venv path (default: {DEFAULT_VENV})",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Skip pip install; use existing venv as-is",
    )
    parser.add_argument(
        "--bootstrap-only",
        action="store_true",
        help="Only create/install the build venv",
    )
    args = parser.parse_args(argv)

    if args.build_only:
        vpy = _venv_python(args.venv)
        if not vpy.exists():
            raise SystemExit(f"build venv missing: {vpy} (run without --build-only first)")
    else:
        vpy = bootstrap_venv(args.venv)

    if args.bootstrap_only:
        print(f"[build_server] venv ready: {vpy}")
        return 0

    run_pyinstaller(vpy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
