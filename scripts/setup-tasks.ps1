$projectDir = "C:\path\to\Afrimillions"
$dockerCompose = "docker compose"

function Register-ContainerTask {
    param(
        [string]$TaskName,
        [string]$ScriptPath,
        [int]$Hour
    )

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-ExecutionPolicy Bypass -File `"$ScriptPath`""

    $trigger = New-ScheduledTaskTrigger -Daily -At "${Hour}:00"

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -LogonType S4U `
        -Force

    Write-Host "Registered: $TaskName (daily at ${Hour}:00, runs without login)"
}

# Stop task first so start-task doesn't conflict
Register-ContainerTask `
    -TaskName "Afrimillions Stop" `
    -ScriptPath "$projectDir\scripts\stop-container.ps1" `
    -Hour 20

Register-ContainerTask `
    -TaskName "Afrimillions Start" `
    -ScriptPath "$projectDir\scripts\start-container.ps1" `
    -Hour 8

Write-Host "`nDone. Both tasks registered with 'Run whether user is logged on or not'."
