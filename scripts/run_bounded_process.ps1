param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,

    [string[]]$Arguments = @(),

    [string]$WorkingDirectory = (Get-Location).Path,

    [int]$TimeoutSeconds = 300,

    [string]$LogPath = "runtime/validation/bounded-process.log"
)

$ErrorActionPreference = "Stop"

function Stop-ProcessTree {
    param([int]$RootPid)

    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $RootPid" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -RootPid $child.ProcessId
    }

    Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedLogPath = if ([System.IO.Path]::IsPathRooted($LogPath)) {
    $LogPath
} else {
    Join-Path $repoRoot $LogPath
}

$logDir = Split-Path -Parent $resolvedLogPath
if ($logDir) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}

$stdoutPath = "$resolvedLogPath.stdout"
$stderrPath = "$resolvedLogPath.stderr"
Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue

$started = Get-Date
$process = Start-Process `
    -FilePath $FilePath `
    -ArgumentList $Arguments `
    -WorkingDirectory $WorkingDirectory `
    -NoNewWindow `
    -PassThru `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath

while (-not $process.HasExited) {
    if (((Get-Date) - $started).TotalSeconds -ge $TimeoutSeconds) {
        Stop-ProcessTree -RootPid $process.Id
        $elapsed = [int]((Get-Date) - $started).TotalSeconds
        "timeout after ${elapsed}s: $FilePath $($Arguments -join ' ')" | Set-Content -Encoding UTF8 -Path $resolvedLogPath
        if (Test-Path $stdoutPath) {
            Add-Content -Encoding UTF8 -Path $resolvedLogPath -Value "`n--- stdout tail ---"
            Get-Content $stdoutPath -Tail 120 | Add-Content -Encoding UTF8 -Path $resolvedLogPath
        }
        if (Test-Path $stderrPath) {
            Add-Content -Encoding UTF8 -Path $resolvedLogPath -Value "`n--- stderr tail ---"
            Get-Content $stderrPath -Tail 120 | Add-Content -Encoding UTF8 -Path $resolvedLogPath
        }
        Write-Error "process timed out after ${elapsed}s; log: $resolvedLogPath"
        exit 124
    }

    Start-Sleep -Seconds 2
    $process.Refresh()
}

$process.WaitForExit()
$exitCode = $process.ExitCode
if ($null -eq $exitCode) {
    $exitCode = 0
}
$elapsedSeconds = [int]((Get-Date) - $started).TotalSeconds
"exit ${exitCode} after ${elapsedSeconds}s: $FilePath $($Arguments -join ' ')" | Set-Content -Encoding UTF8 -Path $resolvedLogPath
if (Test-Path $stdoutPath) {
    Add-Content -Encoding UTF8 -Path $resolvedLogPath -Value "`n--- stdout tail ---"
    Get-Content $stdoutPath -Tail 120 | Add-Content -Encoding UTF8 -Path $resolvedLogPath
}
if (Test-Path $stderrPath) {
    Add-Content -Encoding UTF8 -Path $resolvedLogPath -Value "`n--- stderr tail ---"
    Get-Content $stderrPath -Tail 120 | Add-Content -Encoding UTF8 -Path $resolvedLogPath
}

Write-Host "exit ${exitCode} after ${elapsedSeconds}s; log: $resolvedLogPath"
exit $exitCode
