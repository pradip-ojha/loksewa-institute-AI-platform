# Runs the Celery worker + beat in ONE process, teeing logs to logs/worker.log.
# Run from anywhere:  .\scripts\run-worker.ps1
#
# --pool=solo is mandatory on Windows (the worker runtime only supports solo/prefork;
# threaded/gevent/eventlet corrupt the shared event loop). No -Q: with task_queues
# declared in workers/celery_config.py the worker consumes all declared queues, so the
# consumed set can never drift from the declared set. -B runs beat (keepalive + the
# stale-job reaper) in the same process.

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "worker.log"

Set-Location $root

Write-Host "Starting Celery worker + beat (--pool=solo). Logs -> $log" -ForegroundColor Green
Write-Host "Watch this window for: ExternalServiceError, 'connection is closed', 'Reap ... stale job'." -ForegroundColor DarkYellow
celery -A workers.celery_app.celery_app worker -B -l info --pool=solo 2>&1 | Tee-Object -FilePath $log
