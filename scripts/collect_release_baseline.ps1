param(
    [string]$BuildDir = "build/windows-ninja-release",
    [string]$Configuration = "Release",
    [switch]$SkipBuild,
    [int]$BaselineTimeoutSeconds = 120,
    [string]$SummaryPath = "runtime/validation/release-baseline-summary.json"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

$argsList = @(
    "$scriptDir/collect_release_baseline.py",
    "--build-dir", $BuildDir,
    "--configuration", $Configuration,
    "--baseline-timeout-seconds", "$BaselineTimeoutSeconds",
    "--summary-path", $SummaryPath
)

if ($SkipBuild) {
    $argsList += "--skip-build"
}

Push-Location $repoRoot
try {
    python @argsList
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
