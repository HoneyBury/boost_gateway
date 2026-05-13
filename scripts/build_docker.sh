#!/usr/bin/env bash
# Build all Boost Gateway Docker images.
# Usage:
#   ./scripts/build_docker.sh              # build all images
#   ./scripts/build_docker.sh gateway      # build only gateway
#   ./scripts/build_docker.sh --no-cache   # force rebuild

set -euo pipefail

cd "$(dirname "$0")/.."

NO_CACHE=""
TARGET="${1:-all}"

if [ "${1:-}" = "--no-cache" ]; then
    NO_CACHE="--no-cache"
    TARGET="${2:-all}"
fi

build_image() {
    local name="$1"
    local dockerfile="$2"
    shift 2
    echo "=== Building ${name} ==="
    docker build ${NO_CACHE} -f "${dockerfile}" "$@" -t "${name}:latest" .
}

build_all() {
    build_image boost-gateway           env/docker/Dockerfile.gateway
    build_image boost-login-backend      env/docker/Dockerfile.backend --build-arg SERVICE_BINARY=v2_login_backend
    build_image boost-room-backend       env/docker/Dockerfile.backend --build-arg SERVICE_BINARY=v2_room_backend
    build_image boost-battle-backend     env/docker/Dockerfile.backend --build-arg SERVICE_BINARY=v2_battle_backend
    build_image boost-matchmaking-backend env/docker/Dockerfile.backend --build-arg SERVICE_BINARY=v2_match_backend
    build_image boost-leaderboard-backend env/docker/Dockerfile.backend --build-arg SERVICE_BINARY=v2_leaderboard_backend
}

case "${TARGET}" in
    all)
        build_all
        ;;
    gateway)
        build_image boost-gateway env/docker/Dockerfile.gateway
        ;;
    login)
        build_image boost-login-backend env/docker/Dockerfile.backend --build-arg SERVICE_BINARY=v2_login_backend
        ;;
    room)
        build_image boost-room-backend env/docker/Dockerfile.backend --build-arg SERVICE_BINARY=v2_room_backend
        ;;
    battle)
        build_image boost-battle-backend env/docker/Dockerfile.backend --build-arg SERVICE_BINARY=v2_battle_backend
        ;;
    matchmaking)
        build_image boost-matchmaking-backend env/docker/Dockerfile.backend --build-arg SERVICE_BINARY=v2_match_backend
        ;;
    leaderboard)
        build_image boost-leaderboard-backend env/docker/Dockerfile.backend --build-arg SERVICE_BINARY=v2_leaderboard_backend
        ;;
    *)
        echo "Usage: $0 [all|gateway|login|room|battle|matchmaking|leaderboard] [--no-cache]"
        exit 1
        ;;
esac

echo ""
echo "Done. Use 'docker compose up -d' to start the full stack."
