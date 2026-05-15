#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

print_status() {
    local category="$1"
    local path="$2"
    if [ -e "$path" ]; then
        echo "$category: $path"
    else
        echo "$category: missing ($path)"
    fi
}

echo "Dependency layout report"
echo "root: $ROOT_DIR"
echo

print_status "vendored-source" "$ROOT_DIR/third_party/hiredis-src"
print_status "vendored-source" "$ROOT_DIR/third_party/openssl"
print_status "cached-archive" "$ROOT_DIR/third_party/fmt-11.2.0.tar.gz"
print_status "cached-archive" "$ROOT_DIR/third_party/googletest-1.17.0.tar.gz"
print_status "cached-archive" "$ROOT_DIR/third_party/spdlog-1.15.3.tar.gz"
print_status "cached-archive" "$ROOT_DIR/third_party/nlohmann_json-3.12.0.tar.gz"
print_status "cached-archive" "$ROOT_DIR/third_party/boost_1_90_0.zip"
print_status "toolchain-cache" "$ROOT_DIR/build/go-modcache"
print_status "toolchain-cache" "$ROOT_DIR/build/_deps"
print_status "toolchain-cache" "$ROOT_DIR/build/windows-msvc-debug/_deps"

echo
echo "Suggested restore commands"
echo "  bash third_party/download_deps.sh"
echo "  bash third_party/bootstrap_from_build_cache.sh"
echo "  cmake --preset windows-msvc-debug"
