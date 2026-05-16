$ErrorActionPreference = "Stop"

param(
    [string]$BuildDir = "D:\Program\boost-github\BoostAsioDemo\build\windows-ninja-release",
    [ValidateSet("smoke", "baseline")]
    [string]$RunPreset = "smoke",
    [int]$GatewayPort = 9201,
    [int]$LoginPort = 9202,
    [int]$RoomPort = 9302,
    [int]$BattlePort = 9303,
    [int]$HttpPort = 9080,
    [int]$IoCores = 4,
    [string]$OutputRoot = ""
)

$root = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $root "scripts\collect_v2_perf_baseline.py"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "python not found in PATH"
}

$args = @(
    $scriptPath,
    "--build-dir", $BuildDir,
    "--run-preset", $RunPreset,
    "--gateway-port", [string]$GatewayPort,
    "--login-port", [string]$LoginPort,
    "--room-port", [string]$RoomPort,
    "--battle-port", [string]$BattlePort,
    "--http-port", [string]$HttpPort,
    "--io-cores", [string]$IoCores
)

if (-not [string]::IsNullOrWhiteSpace($OutputRoot)) {
    $args += @("--output-root", $OutputRoot)
}

& python @args
exit $LASTEXITCODE
