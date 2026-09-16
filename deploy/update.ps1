$ErrorActionPreference = "Stop"

$deployDir = Split-Path -Parent $PSScriptRoot
$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"

if (-not (Test-Path $docker)) {
    throw "Docker CLI was not found at '$docker'."
}

Set-Location $deployDir

Write-Host "Pulling newest images (set \$env:AFRI_VERSION to pin a release)..."
& $docker compose pull
if ($LASTEXITCODE -ne 0) {
    Write-Host "Image pull failed." -ForegroundColor Red
    exit 1
}

Write-Host "Restarting containers..."
& $docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to restart Afrimillions." -ForegroundColor Red
    exit 1
}

Write-Host "Afrimillions updated successfully."
Write-Host "Console: http://localhost:3000"