$projectDir = "C:\path\to\Afrimillions"
Set-Location $projectDir

Write-Host "Starting Afrimillions container..."
docker-compose up -d

if ($LASTEXITCODE -eq 0) {
    Write-Host "Container started successfully."
} else {
    Write-Host "Failed to start container." -ForegroundColor Red
    exit 1
}
