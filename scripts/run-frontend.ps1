# Runs the Vite dev server (frontend) on http://localhost:5173.
# Run from anywhere:  .\scripts\run-frontend.ps1
# The dev server proxies /api -> http://localhost:8000, so start the backend first.

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location (Join-Path $root "frontend")

if (-not (Test-Path "node_modules")) {
    Write-Host "node_modules missing — running npm install..." -ForegroundColor Cyan
    npm install
}

Write-Host "Starting frontend on http://localhost:5173" -ForegroundColor Green
npm run dev
