param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Version = (Get-Content (Join-Path $RepoRoot "VERSION") -Raw).Trim()
$TauriConfig = Get-Content (Join-Path $RepoRoot "desktop/src-tauri/tauri.conf.json") -Raw | ConvertFrom-Json
$CargoVersion = (
    Select-String -Path (Join-Path $RepoRoot "desktop/src-tauri/Cargo.toml") `
        -Pattern '^version\s*=\s*"([^"]+)"' |
    Select-Object -First 1
).Matches.Groups[1].Value

if ($env:OS -ne "Windows_NT") {
    throw "The Azimuth Photo Windows installer must be built on Windows."
}
if ($TauriConfig.version -ne $Version -or $CargoVersion -ne $Version) {
    throw "Version mismatch: VERSION=$Version, Tauri=$($TauriConfig.version), Cargo=$CargoVersion"
}

# `cargo tauri` is supplied by the Tauri CLI rather than the Rust toolchain.
# Bootstrap the pinned major line on a clean Windows builder so the documented
# installer command works without a separate developer-only setup step.
& cargo tauri --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing the Tauri build CLI..."
    & cargo install tauri-cli --version 2.11.4 --locked
    if ($LASTEXITCODE -ne 0) {
        throw "The Tauri build CLI could not be installed."
    }
}

Push-Location $RepoRoot
try {
    & $Python "scripts/build_server.py"
    if ($LASTEXITCODE -ne 0) {
        throw "The bundled Azimuth Photo engine build failed."
    }

    $Engine = Join-Path $RepoRoot "dist/photoarchive-server/photoarchive-server.exe"
    if (-not (Test-Path $Engine -PathType Leaf)) {
        throw "The bundled Azimuth Photo engine was not created at $Engine"
    }

    Push-Location (Join-Path $RepoRoot "desktop/src-tauri")
    try {
        & cargo tauri build --bundles nsis
        if ($LASTEXITCODE -ne 0) {
            throw "The Azimuth Photo desktop installer build failed."
        }
    }
    finally {
        Pop-Location
    }

    $BundleDir = Join-Path $RepoRoot "desktop/src-tauri/target/release/bundle/nsis"
    $Installer = Get-ChildItem $BundleDir -Filter "*-setup.exe" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $Installer) {
        throw "The NSIS installer was not found in $BundleDir"
    }
    Write-Host "Azimuth Photo test installer: $($Installer.FullName)"
}
finally {
    Pop-Location
}
