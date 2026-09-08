$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"

Set-Location $projectDir

if (-not (Test-Path $dockerDesktop)) {
    throw "Docker Desktop was not found at '$dockerDesktop'."
}

if (-not (Test-Path $docker)) {
    throw "Docker CLI was not found at '$docker'."
}

if (-not (Test-Path (Join-Path $projectDir "docker-compose.yml"))) {
    throw "docker-compose.yml was not found in '$projectDir'."
}

if (-not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue)) {
    Write-Host "Starting Docker Desktop..."
    Start-Process $dockerDesktop
}

Write-Host "Waiting for Docker engine..."
$dockerReady = $false

for ($attempt = 1; $attempt -le 30; $attempt++) {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $dockerProbeOutput = & $docker info 2>&1
    $dockerExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference

    if ($dockerExitCode -eq 0) {
        $dockerReady = $true
        break
    }

    Start-Sleep -Seconds 10
}

if (-not $dockerReady) {
    throw "Docker engine did not become ready within five minutes."
}

Write-Host "Building and starting Afrimillions..."
& $docker compose up -d --build

if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to start Afrimillions." -ForegroundColor Red
    exit 1
}

Write-Host "Afrimillions started successfully."
