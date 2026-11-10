$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$appPython = "$PSScriptRoot\venv\Scripts\python.exe"
& $appPython -m scripts.setup_local
if ($LASTEXITCODE -ne 0) { throw 'Local configuration setup failed.' }
docker compose up -d --wait db
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop, then run this script again.' }
& $appPython -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
$env:WORKER_STEP1_ONLY = 'true'
$appWorker = Start-Process -FilePath $appPython -ArgumentList @('-m', 'app.worker') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "$PSScriptRoot/worker.stdout.log" -RedirectStandardError "$PSScriptRoot/worker.stderr.log"
try {
    & $appPython -m uvicorn main:app --host 127.0.0.1 --port 8000
} finally {
    if (-not $appWorker.HasExited) { Stop-Process -Id $appWorker.Id }
}
