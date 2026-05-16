$ErrorActionPreference = "Stop"

param(
    [string]$BuildDir = "D:\Program\boost-github\BoostAsioDemo\build\windows-msvc-debug"
)

$root = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $root "scripts\p4_validate.py"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "python not found in PATH"
}

& python $scriptPath --build-dir $BuildDir
exit $LASTEXITCODE
