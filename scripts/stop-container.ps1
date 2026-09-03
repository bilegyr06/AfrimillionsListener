$projectDir = "C:\path\to\Afrimillions"
Set-Location $projectDir

Write-Host "Stopping Afrimillions container..."
docker-compose down

if ($LASTEXITCODE -eq 0) {
    Write-Host "Container stopped successfully."
} else {
    Write-Host "Failed to stop container." -ForegroundColor Red
    exit 1
}
