#!/usr/bin/env bash
#
# Package all third-party archives into a single distributable archive.
#
# Usage:
#   bash third_party/package.sh
#
# Prerequisites: run download_deps.sh first.
#
# Output: third_party.zip (in the project root)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Packaging third-party dependencies ==="

# Verify archives exist
REQUIRED=(
    "fmt-11.2.0.tar.gz"
    "googletest-1.17.0.tar.gz"
    "spdlog-1.15.3.tar.gz"
    "nlohmann_json-3.12.0.tar.gz"
    "boost_1_90_0.zip"
    "hiredis-1.2.0.tar.gz"
)

MISSING=()
for f in "${REQUIRED[@]}"; do
    if [ ! -f "$SCRIPT_DIR/$f" ]; then
        MISSING+=("$f")
    fi
done

if [ ! -d "$SCRIPT_DIR/openssl" ] && [ ! -d "$SCRIPT_DIR/openssl-src" ]; then
    echo "WARN: no local OpenSSL install found under third_party/openssl or third_party/openssl-src"
    echo "      Consumers must provide OpenSSL via Conan, system packages, or OPENSSL_ROOT_DIR."
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "ERROR: Missing archives:"
    for f in "${MISSING[@]}"; do
        echo "  - $f"
    done
    echo "Run 'bash third_party/download_deps.sh' first."
    exit 1
fi

# Create the distributable archive (zip format for cross-platform compatibility)
OUTPUT="$PROJECT_DIR/third_party.zip"

# Remove old archive if exists
rm -f "$OUTPUT"

# Create zip containing the third_party directory
cd "$PROJECT_DIR"
zip -r "$OUTPUT" third_party/ \
    -x "third_party/.gitignore" \
    -x "third_party/download_deps.sh" \
    -x "third_party/download_deps.bat" \
    -x "third_party/package.sh" \
    -x "third_party/package.bat"

echo ""
echo "=== Package created: third_party.zip ==="
echo "Upload this file to your internal repository or file server."
echo ""
echo "Other developers should:"
echo "  1. Download third_party.zip"
echo "  2. Extract it to the project root"
echo "  3. Run cmake --preset <preset> as normal"
