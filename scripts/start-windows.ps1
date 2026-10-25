<#
  Agent Desk on Windows. Run from the repo folder in PowerShell:

    powershell -ExecutionPolicy Bypass -File scripts\start-windows.ps1 -Demo     # synthetic prices, password "demo"
    powershell -ExecutionPolicy Bypass -File scripts\start-windows.ps1           # your real Kite data (Test mode)
    powershell -ExecutionPolicy Bypass -File scripts\start-windows.ps1 -Rebuild  # after pulling new code

  Needs Python 3.12, Node.js 20+ and Git (see README, "Run it on Windows").
#>
param([switch]$Demo, [switch]$Rebuild)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$root = (Get-Location).Path

function Need($cmd, $hint) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Write-Host "Missing $cmd. $hint" -ForegroundColor Red; exit 1 }
}
Need py  "Install Python 3.12 from python.org (tick 'Add python.exe to PATH') or: winget install Python.Python.3.12"
Need npm "Install Node.js LTS from nodejs.org or: winget install OpenJS.NodeJS.LTS"

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "Creating Python environment..." -ForegroundColor Cyan
  py -3.12 -m venv .venv
  $Rebuild = $true
}
if ($Rebuild) {
  Write-Host "Installing Python packages..." -ForegroundColor Cyan
  & $py -m pip install -q --upgrade pip
  & $py -m pip install -q -e ml -e backend
}
if ($Rebuild -or -not (Test-Path "frontend\dist\index.html")) {
  Write-Host "Building the dashboard..." -ForegroundColor Cyan
  Push-Location frontend; npm ci; npm run build; Pop-Location
}

if ($Demo) {
  $env:DATA_MODE = "demo"; $env:DASHBOARD_PASSWORD = "demo"; $env:SECRET_KEY = "local-demo"; $env:SCHEDULER_ENABLED = "false"
  Write-Host "Demo mode: synthetic prices. Password: demo" -ForegroundColor Yellow
} elseif (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  $secret = -join (1..48 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
  (Get-Content ".env") -replace '^SECRET_KEY=$', "SECRET_KEY=$secret" -replace '^PUBLIC_URL=.*$', 'PUBLIC_URL=http://localhost:8000' | Set-Content ".env"
  Write-Host "Created .env. Set DASHBOARD_PASSWORD (and ANTHROPIC_API_KEY if you have one), save, then run this again." -ForegroundColor Yellow
  notepad ".env"
  exit 0
}

$env:DATA_DIR = Join-Path $root "data"
Write-Host "Starting Agent Desk at http://localhost:8000  (Ctrl+C to stop)" -ForegroundColor Green
Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", "Start-Sleep 5; Start-Process http://localhost:8000"
Set-Location backend
& $py -m uvicorn app.main:app --port 8000
