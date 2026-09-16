$ErrorActionPreference = "Stop"

$deployDir = $PSScriptRoot
$dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"

Set-Location $deployDir

if (-not (Test-Path $dockerDesktop)) {
    throw "Docker Desktop was not found at '$dockerDesktop'."
}
if (-not (Test-Path $docker)) {
    throw "Docker CLI was not found at '$docker'."
}
if (-not (Test-Path (Join-Path $deployDir "docker-compose.yml"))) {
    throw "docker-compose.yml was not found in '$deployDir'."
}
if (-not (Test-Path (Join-Path $deployDir ".env"))) {
    throw ".env was not found in '$deployDir'. Copy it from the project before starting."
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
    & $docker info 2>&1 | Out-Null
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

Write-Host "Checking ghcr.io login..."
$dockerConfig = Join-Path $env:USERPROFILE ".docker\config.json"
$ghcrLoggedIn = $false
if (Test-Path $dockerConfig) {
    try {
        $config = Get-Content $dockerConfig -Raw | ConvertFrom-Json
        if ($config.auths.PSObject.Properties.Name -contains "ghcr.io") {
            $ghcrLoggedIn = $true
        }
    } catch {
        $ghcrLoggedIn = $false
    }
}
if (-not $ghcrLoggedIn) {
    throw "Not logged in to ghcr.io. Run: docker login ghcr.io -u <github-username> first, then re-run this script."
}

Write-Host "Starting Afrimillions from container images..."
& $docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to start Afrimillions." -ForegroundColor Red
    exit 1
}

Write-Host "Afrimillions started successfully."
Write-Host "Console: http://localhost:3000"