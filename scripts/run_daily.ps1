<#
.SYNOPSIS
    Polite daily runner for ub-prices (works on both Home PC S3 wake and Work Laptop).
.DESCRIPTION
    1. Waits briefly for network after waking from S3 sleep.
    2. Pulls latest Git state (git pull --rebase).
    3. Skips cleanly if an 'ok' or 'partial' scan already completed today (unless -Force).
    4. Runs ub-prices scan, rebuilds data/ub_prices.db and reports/latest.md.
    5. Commits and pushes updated data/ and reports/latest.md to GitHub.
    6. If -SleepIfIdle is passed and no user input has occurred, puts the PC back to S3 Sleep.
#>
[CmdletBinding()]
param(
    [int]$MaxPages = 3,
    [int]$MaxDetails = 120,
    [double]$MinDelay = 3.5,
    [double]$MaxDelay = 6.5,
    [int]$NetworkWaitSeconds = 60,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$SleepIfIdle,
    [int]$IdleThresholdSeconds = 180
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "daily_scan.log"

function Write-Log {
    param([string]$Message)
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "[$ts] $Message"
    Write-Output $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

# Win32 helpers for idle detection & S3 sleep (preserving wake timers!)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class NativePower {
    [StructLayout(LayoutKind.Sequential)]
    public struct LASTINPUTINFO {
        public uint cbSize;
        public uint dwTime;
    }

    [DllImport("user32.dll")]
    public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);

    [DllImport("powrprof.dll", SetLastError = true)]
    public static extern bool SetSuspendState(bool hibernate, bool forceCritical, bool disableWakeEvent);

    public static double GetIdleSeconds() {
        LASTINPUTINFO lii = new LASTINPUTINFO();
        lii.cbSize = (uint)Marshal.SizeOf(typeof(LASTINPUTINFO));
        if (!GetLastInputInfo(ref lii)) return 0;
        uint elapsedMs = unchecked((uint)Environment.TickCount - lii.dwTime);
        return elapsedMs / 1000.0;
    }
}
"@ -ErrorAction SilentlyContinue

function Test-NetworkReady {
    param([int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $null = [System.Net.Dns]::GetHostAddresses("www.unegui.mn")
            return $true
        } catch {
            Start-Sleep -Seconds 3
        }
    }
    return $false
}

function Test-AlreadyScannedToday {
    param([string]$RunsCsvPath)
    if (-not (Test-Path $RunsCsvPath)) { return $false }
    $todayUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
    $rows = Import-Csv -Path $RunsCsvPath
    foreach ($r in $rows) {
        if ($r.started_at -and $r.started_at.StartsWith($todayUtc)) {
            if ($r.status -in @("ok", "partial")) {
                return $true
            }
        }
    }
    return $false
}

function Invoke-SafeReturnToSleep {
    param([int]$ThresholdSec)
    $isLocked = [bool](Get-Process -Name "LogonUI" -ErrorAction SilentlyContinue)
    $idleSec = [NativePower]::GetIdleSeconds()
    Write-Log ("Sleep check: locked={0}, idle={1:N0}s (threshold={2}s)" -f $isLocked, $idleSec, $ThresholdSec)

    # If unlocked and recently active, never interrupt the user
    if (-not $isLocked -and $idleSec -lt $ThresholdSec) {
        Write-Log "User is actively using the PC; skipping return-to-sleep."
        return
    }

    # Brief safety grace period in case user just returned right as scan ended
    if (-not $isLocked) {
        Start-Sleep -Seconds 15
        $idleAfter = [NativePower]::GetIdleSeconds()
        if ($idleAfter -lt $ThresholdSec) {
            Write-Log "User activity detected during grace period; cancelling sleep."
            return
        }
    }

    Write-Log "Returning PC to S3 Standby sleep (wake timers preserved)..."
    # hibernate=$false (S3 Sleep), forceCritical=$false, disableWakeEvent=$false (keep next wake timer!)
    [void][NativePower]::SetSuspendState($false, $false, $false)
}

Write-Log "=== Starting daily ub-prices run (SleepIfIdle=$SleepIfIdle, DryRun=$DryRun, Force=$Force) ==="

try {
    # 1. Wait for Wi-Fi / Ethernet to settle after S3 wake
    Write-Log "Checking network connectivity..."
    if (-not (Test-NetworkReady -TimeoutSec $NetworkWaitSeconds)) {
        Write-Log "ERROR: Network did not become ready within ${NetworkWaitSeconds}s. Aborting."
        exit 1
    }

    # 2. Sync latest state from GitHub (coordinates Home PC + Work Laptop cleanly)
    Write-Log "Syncing repository with origin/main..."
    & git pull --rebase --autostash origin main 2>&1 | ForEach-Object { Write-Log "git: $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "WARNING: git pull returned exit code $LASTEXITCODE; continuing with local state."
    }

    # 3. Idempotent guard: skip if another machine already completed a scan today
    $runsCsv = Join-Path $RepoRoot "data\scrape_runs.csv"
    if (-not $Force -and (Test-AlreadyScannedToday -RunsCsvPath $runsCsv)) {
        Write-Log "A successful scan already ran today (UTC). Skipping duplicate run."
        exit 0
    }

    # 4. Resolve ub-prices CLI from local .venv (or PATH fallback)
    $ubPricesExe = Join-Path $RepoRoot ".venv\Scripts\ub-prices.exe"
    if (-not (Test-Path $ubPricesExe)) {
        $ubPricesExe = "ub-prices"
    }

    $scanArgs = @(
        "scan",
        "--source", "unegui",
        "--max-pages", $MaxPages,
        "--max-details", $MaxDetails,
        "--min-delay", $MinDelay,
        "--max-delay", $MaxDelay
    )
    if ($DryRun) { $scanArgs += "--dry-run" }

    Write-Log ("Running: {0} {1}" -f $ubPricesExe, ($scanArgs -join " "))
    & $ubPricesExe @scanArgs 2>&1 | ForEach-Object { Write-Log $_ }
    $scanExit = $LASTEXITCODE
    Write-Log "ub-prices scan exited with code $scanExit"

    if (-not $DryRun) {
        Write-Log "Building SQLite DB and daily digest..."
        & $ubPricesExe build-db 2>&1 | ForEach-Object { Write-Log $_ }
        & $ubPricesExe report --output reports/latest.md 2>&1 | ForEach-Object { Write-Log $_ }

        # 5. Commit & push changes if any
        & git add data/ reports/latest.md
        & git diff --cached --quiet
        if ($LASTEXITCODE -ne 0) {
            $today = (Get-Date).ToString("yyyy-MM-dd")
            $hostTag = $env:COMPUTERNAME
            & git commit -m "chore(data): daily UB property scan $today ($hostTag)" 2>&1 | ForEach-Object { Write-Log "git: $_" }
            & git push origin main 2>&1 | ForEach-Object { Write-Log "git: $_" }
            if ($LASTEXITCODE -ne 0) {
                Write-Log "WARNING: git push failed (exit $LASTEXITCODE); local commit preserved."
            } else {
                Write-Log "Pushed daily update to GitHub successfully."
            }
        } else {
            Write-Log "No data changes to commit."
        }
    }
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
finally {
    Write-Log "=== Finished daily ub-prices run ==="
    if ($SleepIfIdle) {
        Invoke-SafeReturnToSleep -ThresholdSec $IdleThresholdSeconds
    }
}
