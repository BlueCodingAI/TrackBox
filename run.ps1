# TrackBox — one-command launcher (Windows PowerShell)
# Creates a venv, installs deps, and starts the server on http://localhost:8000

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from template." -ForegroundColor Yellow
}

# Scrape mode needs a browser. Prefer system Edge (no download); otherwise
# fetch Playwright's bundled Chromium once.
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$usingScrape = (Get-Content ".env" -ErrorAction SilentlyContinue) -match 'PROVIDER_MODE\s*=\s*scrape'
if ($usingScrape -and -not (Test-Path $edge)) {
    Write-Host "Edge not found - installing Playwright Chromium (one-time ~130MB)..." -ForegroundColor Cyan
    & $py -m playwright install chromium
}

Write-Host "`nStarting TrackBox on http://localhost:8000  (Ctrl+C to stop)`n" -ForegroundColor Green
& $py -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
