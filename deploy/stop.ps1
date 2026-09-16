$ErrorActionPreference = "Stop"

$deployDir = $PSScriptRoot
$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"

if (-not (Test-Path $docker)) {
    throw "Docker CLI was not found at '$docker'."
}

Set-Location $deployDir

Write-Host "Stopping Afrimillions..."
& $docker compose down --remove-orphans
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to stop Afrimillions." -ForegroundColor Red
    exit 1
}

Write-Host "Afrimillions stopped."