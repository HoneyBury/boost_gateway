param(
    [string]$BuildDir = "build/windows-ninja-debug",
    [string]$Configuration = "Debug",
    [switch]$SkipBuild,
    [switch]$IncludeOtelCollector,
    [switch]$IncludeRuntimeHttp,
    [int]$BuildTimeoutSeconds = 180,
    [int]$TestTimeoutSeconds = 120,
    [string]$SummaryPath = "runtime/validation/observability-gate-summary.json"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

$argsList = @(
    "$scriptDir/verify_observability_gate.py",
    "--build-dir", $BuildDir,
    "--configuration", $Configuration,
    "--build-timeout-seconds", "$BuildTimeoutSeconds",
    "--test-timeout-seconds", "$TestTimeoutSeconds",
    "--summary-path", $SummaryPath
)

if ($SkipBuild) {
    $argsList += "--skip-build"
}
if ($IncludeOtelCollector) {
    $argsList += "--include-otel-collector"
}
if ($IncludeRuntimeHttp) {
    $argsList += "--include-runtime-http"
}

Push-Location $repoRoot
try {
    python @argsList
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
