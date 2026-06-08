# One-shot demo launcher: runs preflight, and only if every dependency passes,
# launches backend + worker + frontend each in its own window.
# Run from anywhere:  .\scripts\run-all.ps1
# Skip the gate (NOT recommended) with:  .\scripts\run-all.ps1 -SkipPreflight

param([switch]$SkipPreflight)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

if (-not $SkipPreflight) {
    Write-Host "=== Running preflight (go/no-go) ===" -ForegroundColor Cyan
    python (Join-Path $PSScriptRoot "preflight.py")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Preflight FAILED — not starting anything. Fix the items above, then re-run." -ForegroundColor Red
        exit 1
    }
    Write-Host "Preflight passed." -ForegroundColor Green
}

function Start-Window([string]$title, [string]$script) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$script'"
    Write-Host "Launched: $title" -ForegroundColor Green
}

Start-Window "Backend"  (Join-Path $PSScriptRoot "run-backend.ps1")
Start-Sleep -Seconds 3   # give the backend a head start (worker shares the DB; frontend proxies to it)
Start-Window "Worker"   (Join-Path $PSScriptRoot "run-worker.ps1")
Start-Window "Frontend" (Join-Path $PSScriptRoot "run-frontend.ps1")

Write-Host ""
Write-Host "All three started in separate windows." -ForegroundColor Cyan
Write-Host "  Backend : http://localhost:8000  (readiness: http://localhost:8000/health/ready)" -ForegroundColor Gray
Write-Host "  Frontend: http://localhost:5173" -ForegroundColor Gray
