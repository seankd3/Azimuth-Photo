# Windows entry point for the same isolated, local-only QA harness.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "web\.venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "QA requires a virtualenv at web/.venv (run the setup from docs/development.md)."
}

Push-Location (Join-Path $Root "web")
try {
    & $Python -m qa.run @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
