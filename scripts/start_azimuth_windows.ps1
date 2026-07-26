[CmdletBinding()]
param(
    [string]$DataRoot = $env:AZIMUTH_HOME,
    [ValidateSet("standalone", "satellite", "hub")]
    [string]$Mode = $(if ($env:AZIMUTH_MODE) { $env:AZIMUTH_MODE } else { "standalone" }),
    [string]$PreviewRoot = $env:AZIMUTH_THUMB_CACHE_DIR,
    [int]$Port = 8010,
    [int]$StartupTimeoutSeconds = 180,
    [switch]$RequireExistingCatalog,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

trap {
    $Message = $_.Exception.Message
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            $Message,
            "Azimuth Photo could not start",
            [System.Windows.MessageBoxButton]::OK,
            [System.Windows.MessageBoxImage]::Error
        ) | Out-Null
    }
    catch {
        # The terminal still receives the original error if the dialog API is
        # unavailable (for example, in a non-interactive maintenance session).
    }
    Write-Error $Message -ErrorAction Continue
    exit 1
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot "web\.venv\Scripts\python.exe"
$ServerEntry = Join-Path $RepoRoot "scripts\server_entry.py"
$NativeRoot = Join-Path $env:LOCALAPPDATA "Azimuth Photo"
if ($DataRoot) {
    $DataRoot = [IO.Path]::GetFullPath($DataRoot)
    $Catalog = Join-Path $DataRoot "data\catalog\azimuth.db"
    $StateRoot = Join-Path $DataRoot "state"
    if (-not $PreviewRoot) {
        $PreviewRoot = Join-Path $DataRoot "cache\previews"
    }
}
else {
    $Catalog = Join-Path $NativeRoot "catalog\azimuth.db"
    $StateRoot = Join-Path $NativeRoot "state"
    if (-not $PreviewRoot) {
        $PreviewRoot = Join-Path $NativeRoot "cache\previews"
    }
}
$PreviewRoot = [IO.Path]::GetFullPath($PreviewRoot)
$LogRoot = Join-Path $StateRoot "logs"
$RunRoot = Join-Path $StateRoot "run"
$StatusUrl = "http://127.0.0.1:$Port/api/dev/status"
$DesktopUrl = "http://127.0.0.1:$Port/d"

if ($env:OS -ne "Windows_NT") {
    throw "This launcher is for the Azimuth Photo Windows satellite."
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Azimuth Photo's Python environment is missing at $Python. Run: python -m venv `"$($RepoRoot)\web\.venv`""
}
if (-not (Test-Path -LiteralPath $ServerEntry -PathType Leaf)) {
    throw "Azimuth Photo's server entrypoint is missing at $ServerEntry."
}
if ($RequireExistingCatalog -and -not (Test-Path -LiteralPath $Catalog -PathType Leaf)) {
    throw "The required Azimuth Photo catalog is missing at $Catalog. The launcher stopped before creating a new library."
}

New-Item -ItemType Directory -Force -Path $PreviewRoot, $LogRoot, $RunRoot | Out-Null

# An explicit data root supports portable or multi-machine installations.
# Without one, the application uses the current user's platform-native paths.
if ($DataRoot) {
    $env:AZIMUTH_HOME = $DataRoot
}
else {
    Remove-Item Env:AZIMUTH_HOME -ErrorAction SilentlyContinue
}
$env:AZIMUTH_MODE = $Mode
$env:AZIMUTH_HOST = "127.0.0.1"
$env:AZIMUTH_PORT = [string]$Port
$env:AZIMUTH_THUMB_CACHE_DIR = $PreviewRoot
Remove-Item Env:AZIMUTH_SMOKE_MODE -ErrorAction SilentlyContinue

function Get-AzimuthStatus {
    try {
        $Response = Invoke-RestMethod -Uri $StatusUrl -TimeoutSec 2
    }
    catch {
        return $null
    }
    if (-not $Response.pid -or -not $Response.git_commit) {
        throw "Port $Port is already in use by another application."
    }
    if (-not [string]::Equals(
        [string]$Response.cwd,
        $RepoRoot,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Another Azimuth Photo checkout is already using port $Port. Stop it before opening the canonical app."
    }
    return $Response
}

$Status = Get-AzimuthStatus
$StartedProcess = $null
if ($null -eq $Status) {
    $StdoutLog = Join-Path $LogRoot "desktop-server.stdout.log"
    $StderrLog = Join-Path $LogRoot "desktop-server.stderr.log"
    $Arguments = @(
        $ServerEntry,
        "--host", "127.0.0.1",
        "--port", [string]$Port
    )
    $StartedProcess = Start-Process `
        -FilePath $Python `
        -ArgumentList $Arguments `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog `
        -PassThru
    Set-Content -LiteralPath (Join-Path $RunRoot "desktop-server.pid") -Value $StartedProcess.Id

    $Deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        Start-Sleep -Milliseconds 500
        $Status = Get-AzimuthStatus
        if ($null -ne $Status) {
            break
        }
        if ($StartedProcess.HasExited) {
            $ErrorTail = if (Test-Path -LiteralPath $StderrLog) {
                (Get-Content -LiteralPath $StderrLog -Tail 20) -join [Environment]::NewLine
            }
            else {
                "No server error log was created."
            }
            throw "Azimuth Photo stopped during startup.`n$ErrorTail"
        }
    }
}

if ($null -eq $Status) {
    throw "Azimuth Photo did not become ready within $StartupTimeoutSeconds seconds. See $LogRoot."
}

Write-Host "Azimuth Photo is ready (PID $($Status.pid)) in $Mode mode with catalog $Catalog"

if (-not $NoOpen) {
    $AppBrowser = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
        Select-Object -First 1

    if ($AppBrowser) {
        Start-Process -FilePath $AppBrowser -ArgumentList "--app=$DesktopUrl"
    }
    else {
        Start-Process $DesktopUrl
    }
}
