"""Keep the keyboard cheat sheet tied to its frontend bindings."""

import json
import subprocess
from pathlib import Path


WEB_ROOT = Path(__file__).parent
SHEET_SOURCE = WEB_ROOT / "static/js/desktop/shortcut_sheet.js"
KEYBOARD_SOURCE = WEB_ROOT / "static/js/desktop/keyboard.js"
DEVELOP_SOURCE = WEB_ROOT / "static/js/desktop/develop/develop.js"
PAINTER_SOURCE = WEB_ROOT / "static/js/desktop/keywords_panel.js"
DESKTOP_TEMPLATE = WEB_ROOT / "templates/desktop.html"


def load_shortcuts():
    # Import the file where it lives. Reading it into a data: URL used to work
    # and stopped the moment the sheet imported escapeHtml from ../lib.js —
    # a data: URL has no directory, so no relative specifier can resolve.
    script = """
const url = require('url');
(async () => {
  const module = await import(url.pathToFileURL(process.argv[1]).href);
  process.stdout.write(JSON.stringify(module.SHORTCUTS));
})();
"""
    result = subprocess.run(
        ["node", "-e", script, str(SHEET_SOURCE)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_shortcut_sheet_entries_are_backed_by_keyboard_bindings():
    shortcuts = load_shortcuts()
    keyboard = "\n".join(source.read_text(encoding="utf-8") for source in (
        KEYBOARD_SOURCE, DEVELOP_SOURCE, PAINTER_SOURCE,
    ))

    assert {shortcut["area"] for shortcut in shortcuts} == {
        "Library", "Loupe", "Develop", "Culling", "Painter",
    }
    expected_fragments = {
        "/": ["key === '/'"],
        "Ctrl/Cmd K": ["key === 'k'"],
        "?": ["key === '?'"],
        "Esc": ["event.key === 'Escape'"],
        "G": ["key === 'g'"],
        "E": ["key === 'e'"],
        "D": ["event.key.toLowerCase() === 'd'"],
        "C / O / M / H": [
            "key === 'c'", "switchLens('collections')",
            "key === 'o'", "switchLens('people')",
            "key === 'm'", "switchLens('map')",
            "event.key.toLowerCase() === 'h'", "switchLens('shared')",
        ],
        "Arrows": ["event.key === 'ArrowRight'", "event.key === 'ArrowLeft'"],
        "Home / End": ["event.key === 'Home'", "event.key === 'End'"],
        "Page Up / Down": ["event.key === 'PageUp'", "event.key === 'PageDown'"],
        "Enter": ["event.key === 'Enter'"],
        "Space": ["event.key === ' '"],
        "Ctrl/Cmd A": ["key === 'a'"],
        "P / X / U": ["key === 'p'", "key === 'x'", "key === 'u'"],
        "1–5": ["^[1-5]$"],
        "B": ["key === 'b'"],
        "F": ["key === 'f'"],
        "S": ["key === 's'"],
        "J": ["key === 'j'"],
        "Delete": ["event.key === 'Delete'"],
        "[ / ]": ["key === '['", "key === ']'"],
        "Left / Right": ["event.key === 'ArrowLeft'", "event.key === 'ArrowRight'"],
        "Shift Arrows": ["event.shiftKey && event.key === 'ArrowLeft'"],
        "+ / − / 0": ["event.key === '+'", "event.key === '-'", "event.key === '0'"],
        "Z / Space": ["key === 'z'", "event.key === ' '"],
        "L / I / V": ["lk === 'l'", "lk === 'i'", "lk === 'v'"],
        "G / Esc": ["key === 'g'", "event.key === 'Escape'"],
        "Z": ["key === 'z'"],
        "\\": ["event.key === '\\\\'"],
        "R": ["key === 'r'"],
        "O": ["key.toLowerCase() === 'o'", "crop.cycleOverlay"],
        "K": ["key === 'k'", "masking?.togglePanel"],
        "W": ["key === 'w'", "setWbPick"],
        "Y / Alt Y": ["key === 'y'", "event.altKey"],
        "Ctrl/Cmd Z": ["key === 'z'"],
        "Ctrl/Cmd Shift Z": ["key === 'z'", "event.shiftKey"],
        "Ctrl/Cmd Shift C / V": ["key === 'c'", "key === 'v'"],
        "Ctrl/Cmd '": ["event.code === 'Quote'"],
        "1–9 / 0": ["pickByKey(event.key)"],
        "K / Enter": ["key === 'k'", "event.key === 'Enter'"],
        "C": ["key === 'c'"],
        "U": ["key === 'u'"],
    }
    for shortcut in shortcuts:
        fragments = expected_fragments[shortcut["key"]]
        assert all(fragment in keyboard for fragment in fragments), shortcut

    template = DESKTOP_TEMPLATE.read_text(encoding="utf-8")
    for key, lens, label in (
        ("C", "collections", "Collections"),
        ("O", "people", "People"),
        ("M", "map", "Map"),
        ("H", "shared", "Shared"),
    ):
        assert f'data-view="{lens}"' in template
        assert f'data-tip="{label} · {key}"' in template
        assert f'aria-label="{label} lens"' in template
