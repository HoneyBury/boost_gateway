param(
    [string]$BuildDir = "build/windows-ninja-debug",
    [string]$Configuration = "Debug",
    [switch]$SkipBuild,
    [switch]$IncludeRedisLive,
    [switch]$IncludeSettlementReplay,
    [int]$BuildTimeoutSeconds = 180,
    [int]$TestTimeoutSeconds = 120,
    [string]$SummaryPath = "runtime/validation/data-recovery-summary.json"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

$argsList = @(
    "$scriptDir/verify_data_recovery_gate.py",
    "--build-dir", $BuildDir,
    "--configuration", $Configuration,
    "--build-timeout-seconds", "$BuildTimeoutSeconds",
    "--test-timeout-seconds", "$TestTimeoutSeconds",
    "--summary-path", $SummaryPath
)

if ($SkipBuild) {
    $argsList += "--skip-build"
}
if ($IncludeRedisLive) {
    $argsList += "--include-redis-live"
}
if ($IncludeSettlementReplay) {
    $argsList += "--include-settlement-replay"
}

Push-Location $repoRoot
try {
    python @argsList
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
