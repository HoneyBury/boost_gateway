#!/usr/bin/env bash
#
# Download all third-party dependencies for offline/internal-network builds.
#
# Usage:
#   bash third_party/download_deps.sh
#
# This script fetches the exact versions declared in cmake/Dependencies.cmake
# and stores them as compressed archives under third_party/.
#
# After running this script, run:
#   bash third_party/package.sh
# to create a single distributable archive for your internal repository.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Downloading third-party dependencies ==="

# --- fmt 11.2.0 ---
FMT_ARCHIVE="fmt-11.2.0.tar.gz"
if [ -f "$FMT_ARCHIVE" ]; then
    echo "[skip] $FMT_ARCHIVE already exists"
else
    echo "[fetch] $FMT_ARCHIVE"
    curl -fSL -o "$FMT_ARCHIVE" \
        "https://github.com/fmtlib/fmt/archive/refs/tags/11.2.0.tar.gz"
fi

# --- googletest v1.17.0 ---
GTEST_ARCHIVE="googletest-1.17.0.tar.gz"
if [ -f "$GTEST_ARCHIVE" ]; then
    echo "[skip] $GTEST_ARCHIVE already exists"
else
    echo "[fetch] $GTEST_ARCHIVE"
    curl -fSL -o "$GTEST_ARCHIVE" \
        "https://github.com/google/googletest/archive/refs/tags/v1.17.0.tar.gz"
fi

# --- spdlog v1.15.3 ---
SPDLOG_ARCHIVE="spdlog-1.15.3.tar.gz"
if [ -f "$SPDLOG_ARCHIVE" ]; then
    echo "[skip] $SPDLOG_ARCHIVE already exists"
else
    echo "[fetch] $SPDLOG_ARCHIVE"
    curl -fSL -o "$SPDLOG_ARCHIVE" \
        "https://github.com/gabime/spdlog/archive/refs/tags/v1.15.3.tar.gz"
fi

# --- nlohmann_json v3.12.0 ---
JSON_ARCHIVE="nlohmann_json-3.12.0.tar.gz"
if [ -f "$JSON_ARCHIVE" ]; then
    echo "[skip] $JSON_ARCHIVE already exists"
else
    echo "[fetch] $JSON_ARCHIVE"
    curl -fSL -o "$JSON_ARCHIVE" \
        "https://github.com/nlohmann/json/archive/refs/tags/v3.12.0.tar.gz"
fi

# --- Boost 1.90.0 ---
BOOST_ARCHIVE="boost_1_90_0.zip"
if [ -f "$BOOST_ARCHIVE" ]; then
    echo "[skip] $BOOST_ARCHIVE already exists"
else
    echo "[fetch] $BOOST_ARCHIVE"
    curl -fSL -o "$BOOST_ARCHIVE" \
        "https://archives.boost.io/release/1.90.0/source/boost_1_90_0.zip"
fi

# --- hiredis v1.2.0 ---
HIREDIS_ARCHIVE="hiredis-1.2.0.tar.gz"
if [ -f "$HIREDIS_ARCHIVE" ]; then
    echo "[skip] $HIREDIS_ARCHIVE already exists"
else
    echo "[fetch] $HIREDIS_ARCHIVE"
    curl -fSL -o "$HIREDIS_ARCHIVE" \
        "https://github.com/redis/hiredis/archive/refs/tags/v1.2.0.tar.gz"
fi

cat <<'EOF'

[info] OpenSSL is not downloaded as source by this script. Provide it via one of:
  - Conan: BOOST_DEPENDENCY_PROVIDER=conan with the repository conanfile/lockfile
  - system package: libssl-dev / openssl-devel
  - local install: third_party/openssl/{include,lib} or OPENSSL_ROOT_DIR
EOF

echo ""
echo "=== All dependencies downloaded to third_party/ ==="
echo "Next: run 'bash third_party/package.sh' to create a distributable archive."
