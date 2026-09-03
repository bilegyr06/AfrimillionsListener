$projectDir = "C:\path\to\Afrimillions"
$dockerCompose = "docker compose"

Set-Location $projectDir

Write-Host "Stopping Afrimillions container..."
& $dockerCompose down

if ($LASTEXITCODE -eq 0) {
    Write-Host "Container stopped successfully."
} else {
    Write-Host "Failed to stop container." -ForegroundColor Red
    exit 1
}
