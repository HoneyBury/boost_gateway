param(
    [string]$BuildDir = "build/windows-ninja-debug",
    [string]$Configuration = "Debug",
    [switch]$SkipBuild,
    [switch]$IncludeRedisLive,
    [switch]$IncludeOperatorKind,
    [int]$BuildTimeoutSeconds = 180,
    [int]$TestTimeoutSeconds = 120,
    [int]$OperatorTimeoutSeconds = 600,
    [string]$SummaryPath = "runtime/validation/specialized-e2e-summary.json"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

$argsList = @(
    "$scriptDir/verify_specialized_e2e.py",
    "--build-dir", $BuildDir,
    "--configuration", $Configuration,
    "--build-timeout-seconds", "$BuildTimeoutSeconds",
    "--test-timeout-seconds", "$TestTimeoutSeconds",
    "--operator-timeout-seconds", "$OperatorTimeoutSeconds",
    "--summary-path", $SummaryPath
)

if ($SkipBuild) {
    $argsList += "--skip-build"
}
if ($IncludeRedisLive) {
    $argsList += "--include-redis-live"
}
if ($IncludeOperatorKind) {
    $argsList += "--include-operator-kind"
}

Push-Location $repoRoot
try {
    python @argsList
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
