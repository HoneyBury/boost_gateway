param(
    [string]$BuildDir = "build/windows-ninja-release",
    [string]$Configuration = "Release",
    [switch]$SkipBuild,
    [int]$BaselineTimeoutSeconds = 120,
    [ValidateSet("smoke", "baseline", "capacity")]
    [string]$PerfPreset = "baseline",
    [int]$PerfRepetitions = 3,
    [int]$PerfTimeoutSeconds = 600,
    [switch]$SkipR4,
    [switch]$SkipPerf,
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
    "--perf-preset", $PerfPreset,
    "--perf-repetitions", "$PerfRepetitions",
    "--perf-timeout-seconds", "$PerfTimeoutSeconds",
    "--summary-path", $SummaryPath
)

if ($SkipBuild) {
    $argsList += "--skip-build"
}
if ($SkipR4) {
    $argsList += "--skip-r4"
}
if ($SkipPerf) {
    $argsList += "--skip-perf"
}

Push-Location $repoRoot
try {
    python @argsList
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
