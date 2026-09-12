#!/usr/bin/env python3
"""Make `Azimuth Photo.exe` and point the shortcuts at it.

    web\\.venv\\Scripts\\python.exe scripts\\make_launcher.py [--no-shortcuts]

The exe is the interpreter itself -- a copy of the base pythonw.exe placed
beside the venv's pyvenv.cfg, with its DLLs, wearing the compass and a
version resource -- so the process is named Azimuth Photo in Task Manager
and on the taskbar, runs the checkout in-process, and a relaunch runs the
latest code. (The venv's own pythonw.exe is a redirector that starts the
base interpreter as a child, which is why Task Manager said python.) The
Desktop and Start Menu shortcuts named Azimuth Photo are rewritten to it
unless --no-shortcuts is given.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ICON = ROOT / "desktop" / "icon.ico"
SCRIPTS = WEB / ".venv" / "Scripts"
NAME = "Azimuth Photo"
EXE = SCRIPTS / f"{NAME}.exe"
DLLS = ("python3.dll", f"python{sys.version_info.major}{sys.version_info.minor}.dll",
        "vcruntime140.dll", "vcruntime140_1.dll")


def base_home() -> Path:
    """Where the venv's interpreter lives, by the venv's own word."""

    for line in (WEB / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
        if line.startswith("home"):
            return Path(line.split("=", 1)[1].strip())
    raise SystemExit("web/.venv/pyvenv.cfg names no home")


def version() -> tuple[tuple[int, int, int, int], str]:
    """The build's date as its number, with the commit as its word."""

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 - a build outside git still has a date
        commit = "local"
    now = time.localtime()
    # The exe runs whatever the checkout is, so its version is the day it
    # was made; the commit is the log's to say.
    del commit
    return (now.tm_year, now.tm_mon, now.tm_mday, 0), f"{now.tm_year}.{now.tm_mon}.{now.tm_mday}"


def dress(exe: Path) -> None:
    """The compass and the version resource, written into the exe: what
    Task Manager, the taskbar and the file's Properties say the program is.
    Writing resources leaves the interpreter's own signature invalid; a
    locally made file carries no mark of the web, so Windows runs it, and a
    scanner that objects is answered with an exclusion for the venv."""

    from PyInstaller.utils.win32.icon import CopyIcons_FromIco
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
        write_version_info_to_executable,
    )

    numbers, word = version()
    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Sean Kenneth Doherty"),
                StringStruct("FileDescription", NAME),
                StringStruct("FileVersion", word),
                StringStruct("InternalName", NAME),
                StringStruct("OriginalFilename", f"{NAME}.exe"),
                StringStruct("ProductName", NAME),
                StringStruct("ProductVersion", word),
                StringStruct("LegalCopyright", "© Sean Kenneth Doherty"),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )
    CopyIcons_FromIco(str(exe), [str(ICON)])
    write_version_info_to_executable(str(exe), info)


def make() -> Path:
    home = base_home()
    shutil.copyfile(home / "pythonw.exe", EXE)
    for dll in DLLS:
        source = home / dll
        if source.is_file():
            shutil.copyfile(source, SCRIPTS / dll)   # always the base's own, never a stale one
    dress(EXE)
    return EXE


def smoke(exe: Path) -> str:
    """The exe imports the product in-process and says what it found."""

    report = Path(tempfile.mkdtemp()) / "smoke.txt"
    code = (
        "import sys, os\n"
        f"os.chdir({str(WEB)!r}); sys.path.insert(0, {str(WEB)!r})\n"
        "found = [f'exe {sys.executable}', f'prefix {sys.prefix}']\n"
        "for name in ('numpy', 'PIL', 'rawpy', 'onnxruntime', 'webview', 'boot', 'faces', 'embed'):\n"
        "    try:\n"
        "        __import__(name); found.append(f'{name} ok')\n"
        "    except Exception as error:\n"
        "        found.append(f'{name} FAILED {error!r}')\n"
        f"open({str(report)!r}, 'w', encoding='utf-8').write(chr(10).join(found))\n"
    )
    subprocess.run([str(exe), "-c", code], check=True, timeout=300)
    return report.read_text(encoding="utf-8")


def shortcuts(exe: Path) -> list[str]:
    """Every shortcut named Azimuth Photo on the Desktop and in the Start
    Menu now opens the app through the exe. Made if none is there. Through
    the shell's own scripting, so no package is needed for it."""

    entry = WEB / "desktop.py"
    script = f"""
$sh = New-Object -ComObject WScript.Shell
$links = @()
foreach ($folder in @($sh.SpecialFolders('Desktop'), $sh.SpecialFolders('Programs'))) {{
  $found = Get-ChildItem -LiteralPath $folder -Recurse -Filter '{NAME}.lnk' -ErrorAction SilentlyContinue | ForEach-Object {{ $_.FullName }}
  if ($found) {{ $links += $found }} else {{ $links += (Join-Path $folder '{NAME}.lnk') }}
}}
foreach ($link in $links) {{
  $s = $sh.CreateShortcut($link)
  $s.TargetPath = '{exe}'
  $s.Arguments = '"{entry}"'
  $s.WorkingDirectory = '{WEB}'
  $s.IconLocation = '{exe},0'
  $s.Description = '{NAME}'
  $s.Save()
  Write-Output $link
}}
"""
    made = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, check=True)
    return [line.strip() for line in made.stdout.splitlines() if line.strip()]


def main() -> int:
    exe = make()
    report = smoke(exe)
    print(report)
    if "FAILED" in report:
        print("the exe could not run the product; shortcuts left alone", file=sys.stderr)
        return 1
    if "--no-shortcuts" not in sys.argv:
        for link in shortcuts(exe):
            print(f"shortcut: {link}")
    print(f"made: {exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
