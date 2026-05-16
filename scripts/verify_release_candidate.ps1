param(
    [string]$BuildDir = "build/windows-msvc-debug",
    [string]$Configuration = "Debug",
    [ValidateSet("debug", "release")]
    [string]$BaselineProfile = "debug",
    [ValidateSet("smoke", "short", "medium")]
    [string]$SoakProfile = "smoke",
    [switch]$SkipBuild,
    [switch]$SkipReleaseBaseline,
    [int]$TimeoutSeconds = 120,
    [string]$SummaryPath = "runtime/validation/release-candidate-summary.json"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

$argsList = @(
    "$scriptDir/verify_release_candidate.py",
    "--build-dir", $BuildDir,
    "--configuration", $Configuration,
    "--baseline-profile", $BaselineProfile,
    "--soak-profile", $SoakProfile,
    "--timeout-seconds", "$TimeoutSeconds",
    "--summary-path", $SummaryPath
)

if ($SkipBuild) {
    $argsList += "--skip-build"
}

if ($SkipReleaseBaseline) {
    $argsList += "--skip-release-baseline"
}

Push-Location $repoRoot
try {
    python @argsList
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
