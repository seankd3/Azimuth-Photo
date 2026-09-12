[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$DataRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BundledPython = Join-Path $RepoRoot "web\.venv\Scripts\python.exe"
if (-not $Python) {
    $Python = if (Test-Path -LiteralPath $BundledPython -PathType Leaf) {
        $BundledPython
    }
    else {
        "python"
    }
}

if ($env:OS -ne "Windows_NT") {
    throw "This launcher is for the Azimuth Photo Windows app."
}
if ($DataRoot) {
    $env:AZIMUTH_HOME = [IO.Path]::GetFullPath($DataRoot)
}

Push-Location $RepoRoot
try {
    & $Python "scripts\build_desktop_ui.py"
    if ($LASTEXITCODE -ne 0) {
        throw "The Azimuth Photo interface could not be built. Run npm ci first."
    }

    & $Python "web\desktop.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Azimuth Photo exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
