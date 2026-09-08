$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$envFile = Join-Path $projectDir ".env"
$bufferMinutes = 5

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalIdentity = New-Object Security.Principal.WindowsPrincipal($currentIdentity)

if (-not $principalIdentity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an Administrator PowerShell window."
}

if (-not (Test-Path $docker)) {
    throw "Docker CLI was not found at '$docker'."
}

if (-not (Test-Path $envFile)) {
    throw ".env file was not found at '$envFile'."
}

function Read-EnvTime {
    param(
        [string]$Key,
        [string]$Default
    )

    $line = Get-Content $envFile | Where-Object { $_ -match "^$Key=" } | Select-Object -First 1
    if (-not $line) {
        Write-Host "$Key not found in .env; defaulting to $Default"
        return $Default
    }

    ($line -split "=", 2)[1].Trim()
}

function Register-ContainerTask {
    param(
        [string]$TaskName,
        [string]$ScriptPath,
        [int]$Hour,
        [int]$Minute
    )

    # Encode the invocation as a single base64 token so Task Scheduler
    # cannot mangle the path (which may contain spaces).
    $invocation = "& '$ScriptPath'"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($invocation))

    $action = New-ScheduledTaskAction `
        -Execute "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded"

    $trigger = New-ScheduledTaskTrigger -Daily -At "${Hour}:$($Minute.ToString('D2'))"

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable

    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive `
        -RunLevel Highest

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force `
        -ErrorAction Stop

    Write-Host "Registered: $TaskName (daily at ${Hour}:$($Minute.ToString('D2')), while user is logged in)"
}

$startTimeRaw = Read-EnvTime -Key "START_TIME" -Default "14:00"
$endTimeRaw   = Read-EnvTime -Key "END_TIME"   -Default "18:00"

$startParts = $startTimeRaw -split ":"
$endParts   = $endTimeRaw   -split ":"

$startHour   = [int]$startParts[0]
$startMinute = if ($startParts.Length -gt 1) { [int]$startParts[1] } else { 0 }
$endHour     = [int]$endParts[0]
$endMinute   = if ($endParts.Length -gt 1) { [int]$endParts[1] } else { 0 }

# Container starts a few minutes before the app window opens
$containerStartMinute = $startMinute - $bufferMinutes
$containerStartHour   = $startHour
if ($containerStartMinute -lt 0) {
    $containerStartMinute += 60
    $containerStartHour   -= 1
}
if ($containerStartHour -lt 0) { $containerStartHour = 0 }

# Container stops a few minutes after the app window closes
$containerStopMinute = $endMinute + $bufferMinutes
$containerStopHour   = $endHour
if ($containerStopMinute -ge 60) {
    $containerStopMinute -= 60
    $containerStopHour   += 1
}
if ($containerStopHour -gt 23) { $containerStopHour = 23 }

Write-Host "App window:  $startTimeRaw - $endTimeRaw"
Write-Host "Container:   $($containerStartHour):$($containerStartMinute.ToString('D2')) - $($containerStopHour):$($containerStopMinute.ToString('D2'))"

Register-ContainerTask `
    -TaskName "Afrimillions Start" `
    -ScriptPath "$projectDir\scripts\start-container.ps1" `
    -Hour $containerStartHour `
    -Minute $containerStartMinute

Register-ContainerTask `
    -TaskName "Afrimillions Stop" `
    -ScriptPath "$projectDir\scripts\stop-container.ps1" `
    -Hour $containerStopHour `
    -Minute $containerStopMinute

Write-Host "`nDone. Both tasks run with highest privileges while your Windows account is logged in."
