# Runs migrations then the FastAPI backend, teeing logs to logs/backend.log.
# Run from anywhere:  .\scripts\run-backend.ps1
# (No --reload: a file-change auto-restart mid-demo is exactly what we don't want.)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "backend.log"

Set-Location (Join-Path $root "backend")

Write-Host "Applying database migrations (alembic upgrade head)..." -ForegroundColor Cyan
alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Host "alembic upgrade head FAILED — not starting the backend." -ForegroundColor Red
    exit 1
}

Write-Host "Starting backend on http://localhost:8000 (logs -> $log)" -ForegroundColor Green
uvicorn app.main:app --port 8000 2>&1 | Tee-Object -FilePath $log
