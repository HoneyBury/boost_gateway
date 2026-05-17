param(
    [string]$OperatorDir = "operator/boostgateway-operator",
    [switch]$IncludeEnvtest,
    [switch]$IncludeKind,
    [int]$GoTestTimeoutSeconds = 180,
    [int]$EnvtestTimeoutSeconds = 240,
    [int]$KindTimeoutSeconds = 900,
    [string]$SummaryPath = "runtime/validation/control-plane-gate-summary.json"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

$argsList = @(
    "$scriptDir/verify_control_plane_gate.py",
    "--operator-dir", $OperatorDir,
    "--go-test-timeout-seconds", "$GoTestTimeoutSeconds",
    "--envtest-timeout-seconds", "$EnvtestTimeoutSeconds",
    "--kind-timeout-seconds", "$KindTimeoutSeconds",
    "--summary-path", $SummaryPath
)

if ($IncludeEnvtest) {
    $argsList += "--include-envtest"
}
if ($IncludeKind) {
    $argsList += "--include-kind"
}

Push-Location $repoRoot
try {
    python @argsList
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
