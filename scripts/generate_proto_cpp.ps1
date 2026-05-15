$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$protoDir = Join-Path $root "proto\v3"
$outDir = Join-Path $root "src\v3\proto"

if (-not (Get-Command protoc -ErrorAction SilentlyContinue)) {
    throw "protoc not found in PATH"
}

New-Item -ItemType Directory -Force -Path $outDir | Out-Null

& protoc "--cpp_out=$outDir" (Join-Path $protoDir "common.proto") (Join-Path $protoDir "login.proto") (Join-Path $protoDir "room.proto") (Join-Path $protoDir "battle.proto") (Join-Path $protoDir "match.proto") (Join-Path $protoDir "leaderboard.proto")
if ($LASTEXITCODE -ne 0) {
    throw "protoc generation failed"
}

Write-Host "Generated C++ proto files in $outDir"
