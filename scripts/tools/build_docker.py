#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from prepare_docker_runtime_context import prepare


TARGETS = {
    "gateway": ("boost-gateway", "env/docker/Dockerfile.gateway", []),
    "login": ("boost-login-backend", "env/docker/Dockerfile.backend", ["--build-arg", "SERVICE_BINARY=v2_login_backend"]),
    "room": ("boost-room-backend", "env/docker/Dockerfile.backend", ["--build-arg", "SERVICE_BINARY=v2_room_backend"]),
    "battle": ("boost-battle-backend", "env/docker/Dockerfile.backend", ["--build-arg", "SERVICE_BINARY=v2_battle_backend"]),
    "matchmaking": ("boost-matchmaking-backend", "env/docker/Dockerfile.backend", ["--build-arg", "SERVICE_BINARY=v2_match_backend"]),
    "leaderboard": ("boost-leaderboard-backend", "env/docker/Dockerfile.backend", ["--build-arg", "SERVICE_BINARY=v2_leaderboard_backend"]),
}


def build_image(root: Path, name: str, dockerfile: str, extra_args: list[str], no_cache: bool) -> None:
    print(f"=== Building {name} ===")
    cmd = ["docker", "build"]
    if no_cache:
        cmd.append("--no-cache")
    cmd.extend(["-f", dockerfile, *extra_args, "-t", f"{name}:latest", "."])
    subprocess.run(cmd, cwd=root, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Boost Gateway Docker images.")
    parser.add_argument("target", nargs="?", default="all",
                        choices=["all", *TARGETS.keys()])
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--build-dir", type=Path, default=Path("build/release"),
                        help="existing strict-Conan CMake build containing release binaries")
    parser.add_argument("--configuration", help="multi-config build configuration, for example Release")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    build_dir = args.build_dir if args.build_dir.is_absolute() else root / args.build_dir
    prepare(
        build_dir,
        root / "runtime/docker-rootfs",
        root / "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock",
        args.configuration,
    )
    targets = TARGETS.keys() if args.target == "all" else [args.target]
    for target in targets:
        name, dockerfile, extra_args = TARGETS[target]
        build_image(root, name, dockerfile, extra_args, args.no_cache)

    print("\nDone. Use 'docker compose up -d' to start the full stack.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
