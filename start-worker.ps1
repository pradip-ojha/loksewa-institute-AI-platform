# Starts Celery worker + beat as two separate Windows Terminal tabs (or cmd windows).
# Run from the project root:  .\start-worker.ps1

$root = $PSScriptRoot

# No -Q: with task_queues declared in workers/celery_config.py the worker
# consumes ALL declared queues automatically, so the consumed set can never
# drift from the declared set (that drift is what silently dropped tasks).
$workerCmd = "celery -A workers.celery_app.celery_app worker -l info --pool=solo"
$beatCmd   = "celery -A workers.celery_app.celery_app beat -l info"

# Try Windows Terminal first (wt.exe), fall back to separate cmd windows
if (Get-Command wt -ErrorAction SilentlyContinue) {
    wt --title "Celery Worker" --startingDirectory $root powershell -NoExit -Command $workerCmd `; `
       new-tab --title "Celery Beat"   --startingDirectory $root powershell -NoExit -Command $beatCmd
} else {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; $workerCmd"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; $beatCmd"
}

Write-Host "Worker and beat started in separate windows." -ForegroundColor Green
