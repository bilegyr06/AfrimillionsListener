$projectDir = "C:\path\to\Afrimillions"
$dockerCompose = "docker compose"

Set-Location $projectDir

Write-Host "Starting Afrimillions container..."
& $dockerCompose up -d

if ($LASTEXITCODE -eq 0) {
    Write-Host "Container started successfully."
} else {
    Write-Host "Failed to start container." -ForegroundColor Red
    exit 1
}
