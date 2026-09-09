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


FONTS = {
    "inter": ROOT / "node_modules" / "@fontsource" / "inter" / "files",
    "ibm-plex-mono": ROOT / "node_modules" / "@fontsource" / "ibm-plex-mono" / "files",
}


def with_fonts(css: str) -> str:
    """Every `url("font:<file>")` becomes the face itself, base64, so the one
    document carries its type and a fresh machine wears the same face."""

    import base64
    import re

    def inline(match: re.Match) -> str:
        name = match.group(1)
        family = name.rsplit("-latin-", 1)[0]
        data = (FONTS[family] / name).read_bytes()
        return f'url("data:font/woff2;base64,{base64.b64encode(data).decode()}")'

    return re.sub(r'url\("font:([^"]+)"\)', inline, css)


def render(template: str, css: str, javascript: str) -> str:
    css = with_fonts(css)
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
    if any(not folder.is_dir() for folder in FONTS.values()):
        print("the web fonts are missing. Run npm ci before building the desktop UI.", file=sys.stderr)
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
    try:
        OUTPUT.write_text(
            render(
                TEMPLATE.read_text(encoding="utf-8"),
                CSS.read_text(encoding="utf-8"),
                bundle.read_text(encoding="utf-8"),
            ),
            encoding="utf-8",
        )
    finally:
        bundle.unlink(missing_ok=True)
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
