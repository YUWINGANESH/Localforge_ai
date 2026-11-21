# Start LocalForge-AI 2.0 Backend on Windows
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$backendDir = Join-Path $rootDir "backend"
$pythonExe = Join-Path $backendDir ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    Write-Host "Virtual environment not found at $pythonExe" -ForegroundColor Red
    Write-Host "Run: python -m venv backend\.venv && backend\.venv\Scripts\pip install -r backend\requirements.txt"
    exit 1
}

Write-Host "Starting LocalForge-AI 2.0 Backend on http://127.0.0.1:8765 ..." -ForegroundColor Green
& $pythonExe -m uvicorn app.main:app --app-dir $backendDir --host 127.0.0.1 --port 8765
