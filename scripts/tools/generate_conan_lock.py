#!/usr/bin/env python3
"""Generate repository-local Conan lockfiles for a selected profile/build type."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Conan profile path relative to repo root.")
    parser.add_argument("--build-type", default="Release")
    parser.add_argument("--with-grpc", action="store_true")
    parser.add_argument("--without-sqlite", action="store_true")
    parser.add_argument("--lockfile-out", type=Path)
    parser.add_argument("--conan-home", type=Path)
    parser.add_argument("--allow-public", action="store_true")
    parser.add_argument("--no-remote", action="store_true")
    args = parser.parse_args()

    profile = Path(args.profile)
    profile_path = profile if profile.is_absolute() else ROOT / profile
    if not profile_path.exists():
        raise SystemExit(f"profile not found: {profile_path}")

    lockfile_out = args.lockfile_out
    if lockfile_out is None:
        stem = profile_path.stem
        suffix = "grpc" if args.with_grpc else "nogrpc"
        sqlite_suffix = "nosqlite" if args.without_sqlite else "sqlite"
        lockfile_out = ROOT / "conan" / "locks" / f"{stem}-{args.build_type.lower()}-{suffix}-{sqlite_suffix}.lock"
    elif not lockfile_out.is_absolute():
        lockfile_out = ROOT / lockfile_out

    lockfile_out.parent.mkdir(parents=True, exist_ok=True)

    if args.conan_home is None:
        grpc_suffix = "grpc" if args.with_grpc else "nogrpc"
        sqlite_suffix = "nosqlite" if args.without_sqlite else "sqlite"
        default_home_name = (
            f".conan2-lock-{profile_path.stem}-{args.build_type.lower()}-"
            f"{grpc_suffix}-{sqlite_suffix}"
        )
        conan_home = ROOT / default_home_name
    else:
        conan_home = args.conan_home if args.conan_home.is_absolute() else ROOT / args.conan_home

    env = os.environ.copy()
    env["CONAN_HOME"] = str(conan_home)

    bootstrap_cmd = [sys.executable, str(ROOT / "scripts" / "bootstrap_conan.py")]
    if args.allow_public:
        bootstrap_cmd.append("--allow-public")
    if args.no_remote:
        bootstrap_cmd.append("--no-remote")
    subprocess.run(bootstrap_cmd, cwd=ROOT, env=env, check=True)

    cmd = [
        "conan",
        "lock",
        "create",
        str(ROOT),
        "--profile:host",
        str(profile_path),
        "--profile:build",
        str(profile_path),
        "-s",
        f"build_type={args.build_type}",
        "-o",
        f"&:with_grpc={'True' if args.with_grpc else 'False'}",
        "-o",
        "&:with_raft_protobuf=True",
        "-o",
        f"&:with_sqlite={'False' if args.without_sqlite else 'True'}",
        "--lockfile-out",
        str(lockfile_out),
    ]
    try:
        subprocess.run(cmd, cwd=ROOT, env=env, check=True)
    except subprocess.CalledProcessError as exc:
        if args.no_remote:
            raise SystemExit(
                "conan lock create failed in --no-remote mode. "
                "Pre-warm the local Conan cache or configure an internal remote before generating the lockfile."
            ) from exc
        raise SystemExit(
            "conan lock create failed with remotes enabled. "
            "Check runner network/DNS/socket policy, or switch to a pre-warmed cache/internal mirror."
        ) from exc
    print(f"generated lockfile: {lockfile_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
