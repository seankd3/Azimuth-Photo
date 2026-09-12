param([string]$Python = "")

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BundledPython = Join-Path $RepoRoot "web/.venv/Scripts/python.exe"
if (-not $Python) {
    $Python = if (Test-Path $BundledPython -PathType Leaf) { $BundledPython } else { "python" }
}

if ($env:OS -ne "Windows_NT") {
    throw "The Azimuth Photo desktop app must be built on Windows."
}

Push-Location $RepoRoot
try {
    & $Python -m pip install -r "web/requirements-v2.txt"
    if ($LASTEXITCODE -ne 0) {
        throw "The V2 desktop dependencies could not be installed."
    }

    & npm ci
    if ($LASTEXITCODE -ne 0) {
        throw "The desktop UI build dependencies could not be installed."
    }

    & $Python "scripts/build_desktop.py"
    if ($LASTEXITCODE -ne 0) {
        throw "The Azimuth Photo desktop build failed."
    }

    $Executable = Join-Path $RepoRoot "dist/azimuth-photo/azimuth-photo.exe"
    if (-not (Test-Path $Executable -PathType Leaf)) {
        throw "The Azimuth Photo executable was not created at $Executable"
    }
    Write-Host "Azimuth Photo desktop app: $Executable"
}
finally {
    Pop-Location
}
