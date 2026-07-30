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

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot "web\.venv\Scripts\python.exe"
$ServerEntry = Join-Path $RepoRoot "scripts\server_entry.py"
$NativeRoot = Join-Path $env:LOCALAPPDATA "Azimuth Photo"

# The launcher writes its own record before any product directory is known, so
# a failure in the first few lines is still recoverable from disk.
$script:LauncherLogPath = Join-Path $NativeRoot "logs\launcher.log"

# How many consecutive ports the launcher may try before it gives up. A busy
# port is an infrastructure detail and is never reported to the user.
$PortSearchWidth = 10

# How long an already-running instance of this checkout may take to answer
# before the launcher treats it as unresponsive and moves to another port.
$ReuseTimeoutSeconds = 60

# Browsers that support Chromium application windows (--app=). The user's own
# default browser wins; this list only decides the order of the fallbacks.
$AppWindowExecutables = @("chrome.exe", "brave.exe", "vivaldi.exe", "opera.exe", "msedge.exe")
$BrowserFallbackPaths = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe",
    "${env:ProgramFiles(x86)}\BraveSoftware\Brave-Browser\Application\brave.exe",
    "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe",
    "$env:ProgramFiles\Vivaldi\Application\vivaldi.exe",
    "${env:ProgramFiles(x86)}\Vivaldi\Application\vivaldi.exe",
    "$env:LOCALAPPDATA\Vivaldi\Application\vivaldi.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
)

function Write-LauncherLog {
    param([string]$Text)
    try {
        $Folder = Split-Path -Parent $script:LauncherLogPath
        if (-not (Test-Path -LiteralPath $Folder -PathType Container)) {
            New-Item -ItemType Directory -Force -Path $Folder | Out-Null
        }
        $Stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        Add-Content -LiteralPath $script:LauncherLogPath -Value "$Stamp  $Text"
    }
    catch {
        # Logging must never stop the launcher or delay the user.
    }
}

function Show-LauncherFailure {
    param([string]$Text)
    # The dialog runs in a separate process. The launcher is normally started
    # with a hidden window, so it must never wait on a modal window: it reports
    # the failure and exits immediately, and the user closes the dialog later.
    try {
        $Payload = Join-Path $env:TEMP ("azimuth-launcher-error-{0}.txt" -f [Guid]::NewGuid())
        Set-Content -LiteralPath $Payload -Value $Text -Encoding UTF8
        $DialogScript = @"
Add-Type -AssemblyName PresentationFramework
`$File = '$Payload'
`$Message = Get-Content -LiteralPath `$File -Raw
Remove-Item -LiteralPath `$File -ErrorAction SilentlyContinue
[System.Windows.MessageBox]::Show(
    `$Message,
    'Azimuth Photo could not start',
    [System.Windows.MessageBoxButton]::OK,
    [System.Windows.MessageBoxImage]::Error
) | Out-Null
"@
        $Encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($DialogScript))
        Start-Process -FilePath "powershell.exe" `
            -ArgumentList "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", $Encoded `
            -WindowStyle Hidden | Out-Null
    }
    catch {
        # Without a dialog, the launcher log is the remaining record.
    }
}

function Test-PortIsBusy {
    param([int]$Number)
    # A raw TCP connect is the only dependable free/busy test here. An HTTP probe
    # of a closed local port reports a timeout instead of a refused connection,
    # which makes free ports look occupied.
    $Client = New-Object System.Net.Sockets.TcpClient
    try {
        $Attempt = $Client.BeginConnect("127.0.0.1", $Number, $null, $null)
        if (-not $Attempt.AsyncWaitHandle.WaitOne(300)) {
            return $false
        }
        $Client.EndConnect($Attempt)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $Client.Close()
    }
}

function Test-PortIsOwnedByThisCheckout {
    param([int]$Number)
    # Process identity, not an HTTP answer, decides ownership. A loaded instance
    # can be slow to reply, and a slow reply must not look like a foreign port.
    try {
        $Owner = Get-NetTCPConnection -LocalPort $Number -State Listen -ErrorAction Stop |
            Select-Object -First 1 -ExpandProperty OwningProcess
    }
    catch {
        return $false
    }
    if (-not $Owner) {
        return $false
    }
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$Owner" -ErrorAction SilentlyContinue
    if (-not $Process -or -not $Process.CommandLine) {
        return $false
    }
    return $Process.CommandLine.IndexOf($ServerEntry, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-AzimuthStatus {
    param([int]$Number, [int]$TimeoutSeconds = 5)
    try {
        $Response = Invoke-RestMethod -Uri "http://127.0.0.1:$Number/api/dev/status" -TimeoutSec $TimeoutSeconds
    }
    catch {
        return $null
    }
    if (-not $Response) {
        return $null
    }
    $Fields = $Response.PSObject.Properties
    if (-not $Fields["pid"] -or -not $Fields["git_commit"] -or -not $Fields["cwd"]) {
        return $null
    }
    if (-not [string]::Equals([string]$Response.cwd, $RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
        return $null
    }
    return $Response
}

function Wait-AzimuthStatus {
    param([int]$Number, [int]$TimeoutSeconds, $Process = $null)
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        $Found = Get-AzimuthStatus -Number $Number
        if ($null -ne $Found) {
            return $Found
        }
        if ($null -ne $Process -and $Process.HasExited) {
            return $null
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

function Get-DefaultBrowserPath {
    # The user's own choice of browser is the correct one to open the app in.
    $Choice = Get-ItemProperty `
        -LiteralPath "HKCU:\SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice" `
        -ErrorAction SilentlyContinue
    if (-not $Choice -or -not $Choice.PSObject.Properties["ProgId"]) {
        return $null
    }
    $ProgId = [string]$Choice.ProgId
    if (-not $ProgId) {
        return $null
    }
    $CommandKeys = @(
        "HKCU:\SOFTWARE\Classes\$ProgId\shell\open\command",
        "Registry::HKEY_CLASSES_ROOT\$ProgId\shell\open\command"
    )
    foreach ($Key in $CommandKeys) {
        $Entry = Get-ItemProperty -LiteralPath $Key -ErrorAction SilentlyContinue
        if (-not $Entry -or -not $Entry.PSObject.Properties["(default)"]) {
            continue
        }
        $Command = [string]$Entry."(default)"
        if (-not $Command) {
            continue
        }
        if ($Command.StartsWith('"')) {
            $Executable = $Command.Split('"')[1]
        }
        else {
            $Executable = $Command.Split(' ')[0]
        }
        if ($Executable -and (Test-Path -LiteralPath $Executable -PathType Leaf)) {
            return $Executable
        }
    }
    return $null
}

function Get-AppWindowBrowser {
    $Preferred = Get-DefaultBrowserPath
    if ($Preferred) {
        $Leaf = (Split-Path -Leaf $Preferred).ToLowerInvariant()
        if ($AppWindowExecutables -contains $Leaf) {
            return $Preferred
        }
    }
    foreach ($Path in $BrowserFallbackPaths) {
        if ($Path -and (Test-Path -LiteralPath $Path -PathType Leaf)) {
            return $Path
        }
    }
    return $null
}

trap {
    $Message = $_.Exception.Message
    Write-LauncherLog "FAILED: $Message"
    Show-LauncherFailure $Message
    Write-Error $Message -ErrorAction Continue
    exit 1
}

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
$script:LauncherLogPath = Join-Path $LogRoot "launcher.log"

Write-LauncherLog "start mode=$Mode dataroot=$DataRoot"

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

# Reuse this checkout's running instance when there is one. Otherwise take the
# first free port. Anything else holding a port -- a foreign application, or an
# instance that stopped answering -- is stepped over without telling the user.
$SelectedPort = 0
$Status = $null
foreach ($Candidate in $Port..($Port + $PortSearchWidth - 1)) {
    if (-not (Test-PortIsBusy $Candidate)) {
        $SelectedPort = $Candidate
        Write-LauncherLog "port $Candidate is free"
        break
    }
    if (Test-PortIsOwnedByThisCheckout $Candidate) {
        $Existing = Wait-AzimuthStatus -Number $Candidate -TimeoutSeconds $ReuseTimeoutSeconds
        if ($null -ne $Existing) {
            $SelectedPort = $Candidate
            $Status = $Existing
            Write-LauncherLog "reusing instance on port $Candidate (pid $($Existing.pid))"
            break
        }
        Write-LauncherLog "port $Candidate holds an unresponsive instance; trying the next port"
        continue
    }
    Write-LauncherLog "port $Candidate is held by another application; trying the next port"
}
if ($SelectedPort -eq 0) {
    throw "Azimuth Photo could not open a local connection on this computer. Restart the computer and try again."
}

$DesktopUrl = "http://127.0.0.1:$SelectedPort/d"

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
$env:AZIMUTH_PORT = [string]$SelectedPort
$env:AZIMUTH_THUMB_CACHE_DIR = $PreviewRoot
Remove-Item Env:AZIMUTH_SMOKE_MODE -ErrorAction SilentlyContinue

if ($null -eq $Status) {
    $StdoutLog = Join-Path $LogRoot "desktop-server.stdout.log"
    $StderrLog = Join-Path $LogRoot "desktop-server.stderr.log"
    $Arguments = @(
        $ServerEntry,
        "--host", "127.0.0.1",
        "--port", [string]$SelectedPort
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
    Write-LauncherLog "started server pid $($StartedProcess.Id) on port $SelectedPort"

    $Status = Wait-AzimuthStatus -Number $SelectedPort -TimeoutSeconds $StartupTimeoutSeconds -Process $StartedProcess
    if ($null -eq $Status -and $StartedProcess.HasExited) {
        $ErrorTail = if (Test-Path -LiteralPath $StderrLog) {
            (Get-Content -LiteralPath $StderrLog -Tail 20) -join [Environment]::NewLine
        }
        else {
            "No server error log was created."
        }
        throw "Azimuth Photo stopped during startup.`n$ErrorTail"
    }
}

if ($null -eq $Status) {
    throw "Azimuth Photo did not become ready within $StartupTimeoutSeconds seconds. See $LogRoot."
}

Write-LauncherLog "ready pid=$($Status.pid) url=$DesktopUrl"
Write-Host "Azimuth Photo is ready (PID $($Status.pid)) in $Mode mode with catalog $Catalog"

if (-not $NoOpen) {
    $AppBrowser = Get-AppWindowBrowser
    if ($AppBrowser) {
        Write-LauncherLog "opening app window with $AppBrowser"
        Start-Process -FilePath $AppBrowser -ArgumentList "--app=$DesktopUrl"
    }
    else {
        Write-LauncherLog "opening $DesktopUrl with the system default handler"
        Start-Process $DesktopUrl
    }
}
