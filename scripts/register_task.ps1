<#
.SYNOPSIS
    Register (or update/remove) the Windows Scheduled Task for ub-prices daily scans.
.DESCRIPTION
    Configures a daily task at the specified local time (default 09:00 AM) that:
    - Wakes the PC from S3 Standby Sleep (WakeToRun = true)
    - Runs scripts/run_daily.ps1 -SleepIfIdle silently in the background
    - Catches up automatically if a scheduled run was missed (StartWhenAvailable = true)
    - Returns the PC to S3 Sleep only if no user is actively using it
#>
[CmdletBinding()]
param(
    [string]$TaskName = "UB-Property-Prices-Daily",
    [string]$RunAt = "09:00",
    [switch]$NoSleepAfter,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunnerScript = Join-Path $RepoRoot "scripts\run_daily.ps1"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Unregistered scheduled task '$TaskName'."
    exit 0
}

$argList = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunnerScript`""
if (-not $NoSleepAfter) {
    $argList += " -SleepIfIdle"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argList -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $RunAt
$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 40)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Daily polite Ulaanbaatar property price scan (wakes from S3 sleep, updates GitHub, returns to sleep if idle)." `
    -Force | Out-Null

Write-Output "Registered scheduled task '$TaskName' to run daily at $RunAt (WakeToRun=True, SleepIfIdle=$(-not $NoSleepAfter))."
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object NextRunTime
