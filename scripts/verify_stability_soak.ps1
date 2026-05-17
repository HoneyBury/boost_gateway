param(
    [string]$BuildDir = "build/windows-msvc-debug",
    [string]$Configuration = "Debug",
    [string]$BaselineProfile = "debug",
    [ValidateSet("smoke", "short", "medium")]
    [string]$SoakProfile = "smoke",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$argsList = @(
    "scripts/verify_stability_soak.py",
    "--build-dir", $BuildDir,
    "--configuration", $Configuration,
    "--baseline-profile", $BaselineProfile,
    "--soak-profile", $SoakProfile
)

if ($SkipBuild) {
    $argsList += "--skip-build"
}

python @argsList
