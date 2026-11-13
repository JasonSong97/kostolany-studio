$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment setup failed.' }
}
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
if (-not (Test-Path -LiteralPath '.venv\installed.marker')) {
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    New-Item -ItemType File -Path '.venv\installed.marker' -Force | Out-Null
}
if (-not (Test-Path -LiteralPath 'node_modules')) {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Node dependency installation failed.' }
}
npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
Write-Host 'Kostolany Studio: http://127.0.0.1:8765  (Ctrl+C to stop)'
& '.\.venv\Scripts\python.exe' -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
