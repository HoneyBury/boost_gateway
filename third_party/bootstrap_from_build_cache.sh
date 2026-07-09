#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_DIR="${1:-"${ROOT_DIR}/../build/_deps"}"

if [[ ! -d "${CACHE_DIR}" ]]; then
    echo "ERROR: cache directory not found: ${CACHE_DIR}" >&2
    exit 1
fi

echo "=== Bootstrapping third_party source cache from ${CACHE_DIR} ==="

copy_dep() {
    local name="$1"
    local src="${CACHE_DIR}/${name}-src"
    local dst="${ROOT_DIR}/${name}-src"
    if [[ ! -d "${src}" ]]; then
        echo "[skip] ${name}-src not found in cache"
        return 0
    fi
    if [[ -d "${dst}" ]]; then
        echo "[skip] ${name}-src already exists in third_party"
        return 0
    fi
    echo "[copy] ${name}-src"
    cp -R "${src}" "${dst}"
}

copy_dep fmt
copy_dep googletest
copy_dep spdlog
copy_dep nlohmann_json
copy_dep boost
copy_dep hiredis

if [[ -d "${CACHE_DIR}/openssl-src" ]]; then
    copy_dep openssl
else
    echo "[info] openssl-src not found in cache; provide OpenSSL via Conan, system package, or third_party/openssl"
fi

echo
echo "=== Done ==="
echo "CMake will now prefer third_party/*-src before remote fetches."
