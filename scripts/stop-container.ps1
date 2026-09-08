$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"

Set-Location $projectDir

if (-not (Test-Path $docker)) {
    throw "Docker CLI was not found at '$docker'."
}

if (-not (Test-Path (Join-Path $projectDir "docker-compose.yml"))) {
    throw "docker-compose.yml was not found in '$projectDir'."
}

Write-Host "Stopping Afrimillions..."
& $docker compose down --remove-orphans

if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to stop Afrimillions." -ForegroundColor Red
    exit 1
}

Write-Host "Afrimillions stopped successfully."
