#!/usr/bin/env python3
"""Preflight checks for fixed release, Redis, and kind runners."""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
from pathlib import Path


def check_command(name: str, required: bool, errors: list[str], warnings: list[str]) -> None:
    if shutil.which(name):
        return
    message = f"missing command: {name}"
    if required:
        errors.append(message)
    else:
        warnings.append(message)


def check_tcp(host: str, port: int, required: bool, errors: list[str], warnings: list[str]) -> None:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return
    except OSError as exc:
        message = f"cannot connect to {host}:{port}: {exc}"
        if required:
            errors.append(message)
        else:
            warnings.append(message)


def check_kind_cluster(required: bool, errors: list[str], warnings: list[str]) -> None:
    if not shutil.which("kind"):
        return
    try:
        subprocess.check_output(["kind", "get", "clusters"], text=True, stderr=subprocess.STDOUT, timeout=10)
    except Exception as exc:
        message = f"kind is installed but not usable: {exc}"
        if required:
            errors.append(message)
        else:
            warnings.append(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=["release-baseline", "specialized-e2e", "observability", "control-plane", "production-evidence"],
        required=True,
    )
    parser.add_argument("--build-dir", type=Path, default=Path("build/default"))
    parser.add_argument("--require-redis", action="store_true")
    parser.add_argument("--redis-host", default="127.0.0.1")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--require-kind", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    warnings: list[str] = []

    for command in ["python3", "cmake"]:
        check_command(command, True, errors, warnings)

    if args.profile == "release-baseline":
        check_command("ninja", False, errors, warnings)
    elif args.profile == "production-evidence":
        check_command("ninja", False, errors, warnings)
        if args.require_kind:
            for command in ["kind", "kubectl", "make", "docker"]:
                check_command(command, True, errors, warnings)
            check_kind_cluster(True, errors, warnings)
        if args.require_redis:
            check_tcp(args.redis_host, args.redis_port, True, errors, warnings)
    elif args.profile == "specialized-e2e":
        if args.require_kind:
            for command in ["kind", "kubectl", "make", "docker"]:
                check_command(command, True, errors, warnings)
            check_kind_cluster(True, errors, warnings)
        if args.require_redis:
            check_tcp(args.redis_host, args.redis_port, True, errors, warnings)
    elif args.profile == "observability":
        warnings.append("observability profile requires local 127.0.0.1 TCP bind permissions for backend and fake OTel collector tests")
    elif args.profile == "control-plane":
        check_command("go", True, errors, warnings)
        if args.require_kind:
            for command in ["kind", "kubectl", "make", "docker"]:
                check_command(command, True, errors, warnings)
            check_kind_cluster(True, errors, warnings)

    if args.build_dir.exists() and not args.build_dir.is_dir():
        errors.append(f"build path exists but is not a directory: {args.build_dir}")

    if warnings:
        print("fixed runner preflight warnings:")
        for warning in warnings:
            print(f"- {warning}")

    if errors:
        print("fixed runner preflight failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("fixed runner preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
