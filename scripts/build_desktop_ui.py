#!/usr/bin/env python3
"""Bundle the modular V2 UI into one serverless desktop document."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "web" / "templates" / "v2.html"
CSS = ROOT / "web" / "static" / "v2" / "index.css"
ENTRY = ROOT / "web" / "static" / "v2" / "index.js"
OUTPUT = ROOT / "build" / "desktop" / "index.html"
ESBUILD = ROOT / "node_modules" / "esbuild" / "bin" / "esbuild"


def render(template: str, css: str, javascript: str) -> str:
    style = f"<style>{css.replace('</style', '<\\/style')}</style>"
    script = f"<script>{javascript.replace('</script', '<\\/script')}</script>"
    document = template.replace('<link rel="stylesheet" href="/static/index.css">', style)
    document = document.replace(
        '<script type="module" src="/static/index.js"></script>', script
    )
    if document == template or 'href="/static/' in document or 'src="/static/' in document:
        raise ValueError("desktop document still refers to served assets")
    return document


def main() -> int:
    if not ESBUILD.is_file():
        print("esbuild is missing. Run npm ci before building the desktop UI.", file=sys.stderr)
        return 2
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    bundle = OUTPUT.with_suffix(".js")
    subprocess.run(
        [
            "node",
            str(ESBUILD),
            str(ENTRY),
            "--bundle",
            "--format=iife",
            "--platform=browser",
            "--target=chrome120",
            f"--outfile={bundle}",
        ],
        cwd=ROOT,
        check=True,
    )
    OUTPUT.write_text(
        render(
            TEMPLATE.read_text(encoding="utf-8"),
            CSS.read_text(encoding="utf-8"),
            bundle.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    bundle.unlink()
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
