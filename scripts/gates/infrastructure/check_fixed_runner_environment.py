#!/usr/bin/env python3
"""Preflight checks for fixed release, Redis, and kind runners."""

from __future__ import annotations

import argparse
import os
import json
import platform
import shutil
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def check_command(name: str, required: bool, errors: list[str], warnings: list[str]) -> dict[str, object]:
    path = shutil.which(name)
    if path:
        return {"name": f"command:{name}", "required": required, "status": "passed", "path": path}
    message = f"missing command: {name}"
    if required:
        errors.append(message)
    else:
        warnings.append(message)
    return {"name": f"command:{name}", "required": required, "status": "failed" if required else "warning", "message": message}


def check_tcp(host: str, port: int, required: bool, errors: list[str], warnings: list[str]) -> dict[str, object]:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return {"name": f"tcp:{host}:{port}", "required": required, "status": "passed"}
    except OSError as exc:
        message = f"cannot connect to {host}:{port}: {exc}"
        if required:
            errors.append(message)
        else:
            warnings.append(message)
        return {"name": f"tcp:{host}:{port}", "required": required, "status": "failed" if required else "warning", "message": message}


def check_kind_cluster(required: bool, errors: list[str], warnings: list[str]) -> dict[str, object]:
    if not shutil.which("kind"):
        return {"name": "kind:clusters", "required": required, "status": "skipped", "message": "kind command is not installed"}
    try:
        output = subprocess.check_output(["kind", "get", "clusters"], text=True, stderr=subprocess.STDOUT, timeout=10)
        clusters = [line.strip() for line in output.splitlines() if line.strip()]
        return {"name": "kind:clusters", "required": required, "status": "passed", "clusters": clusters}
    except Exception as exc:
        message = f"kind is installed but not usable: {exc}"
        if required:
            errors.append(message)
        else:
            warnings.append(message)
        return {"name": "kind:clusters", "required": required, "status": "failed" if required else "warning", "message": message}


def environment_snapshot() -> dict[str, object]:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "host": socket.gethostname(),
        "cwd": os.getcwd(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=[
            "release-baseline",
            "specialized-e2e",
            "observability",
            "control-plane",
            "production-resilience",
            "production-evidence",
            "cloud-production",
        ],
        required=True,
    )
    parser.add_argument("--build-dir", type=Path, default=Path("build/default"))
    parser.add_argument("--require-redis", action="store_true")
    parser.add_argument("--redis-host", default="127.0.0.1")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--require-kind", action="store_true")
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/fixed-runner-preflight-summary.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, object]] = []

    for command in ["python3", "cmake"]:
        checks.append(check_command(command, True, errors, warnings))

    if args.profile == "release-baseline":
        checks.append(check_command("ninja", False, errors, warnings))
    elif args.profile in {"production-resilience", "production-evidence"}:
        checks.append(check_command("ninja", False, errors, warnings))
        if args.profile == "production-resilience":
            warnings.append("production-resilience profile uses bounded default soak; 2h/8h soak must run on a fixed runner with an expanded timeout")
            checks.append({
                "name": "resilience:long-soak-runner",
                "required": False,
                "status": "warning",
                "message": warnings[-1],
            })
        if args.require_kind:
            for command in ["kind", "kubectl", "make", "docker"]:
                checks.append(check_command(command, True, errors, warnings))
            checks.append(check_kind_cluster(True, errors, warnings))
        if args.require_redis:
            checks.append(check_tcp(args.redis_host, args.redis_port, True, errors, warnings))
    elif args.profile == "specialized-e2e":
        if args.require_kind:
            for command in ["kind", "kubectl", "make", "docker"]:
                checks.append(check_command(command, True, errors, warnings))
            checks.append(check_kind_cluster(True, errors, warnings))
        if args.require_redis:
            checks.append(check_tcp(args.redis_host, args.redis_port, True, errors, warnings))
    elif args.profile == "observability":
        warnings.append("observability profile requires local 127.0.0.1 TCP bind permissions for backend, fake OTel collector, and gateway HTTP runtime tests")
        checks.append({
            "name": "observability:loopback-bind",
            "required": False,
            "status": "warning",
            "message": warnings[-1],
        })
    elif args.profile == "control-plane":
        checks.append(check_command("go", True, errors, warnings))
        if args.require_kind:
            for command in ["kind", "kubectl", "make", "docker"]:
                checks.append(check_command(command, True, errors, warnings))
            checks.append(check_kind_cluster(True, errors, warnings))
    elif args.profile == "cloud-production":
        for command in ["python3", "cmake", "ninja", "docker", "kubectl", "kind", "go", "systemctl"]:
            checks.append(check_command(command, True, errors, warnings))
        checks.append(check_kind_cluster(True, errors, warnings))
        checks.append(check_tcp(args.redis_host, args.redis_port, False, errors, warnings))
        warnings.append(
            "cloud-production profile assumes this host is the fixed production-validation runner; "
            "2h/8h soak and capacity evidence should run here or on an equivalently isolated machine"
        )
        checks.append({
            "name": "cloud-production:fixed-host-contract",
            "required": False,
            "status": "warning",
            "message": warnings[-1],
        })

    if args.build_dir.exists() and not args.build_dir.is_dir():
        message = f"build path exists but is not a directory: {args.build_dir}"
        errors.append(message)
        checks.append({"name": "build-dir:shape", "required": True, "status": "failed", "message": message})
    else:
        checks.append({
            "name": "build-dir:shape",
            "required": True,
            "status": "passed",
            "path": str(args.build_dir),
            "exists": args.build_dir.exists(),
        })

    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "profile": args.profile,
        "build_dir": str(args.build_dir),
        "require_redis": args.require_redis,
        "redis_endpoint": f"{args.redis_host}:{args.redis_port}",
        "require_kind": args.require_kind,
        "environment": environment_snapshot(),
        "overall_pass": not errors,
        "passed": not errors,
        "warnings": warnings,
        "errors": errors,
        "checks": checks,
        "artifacts": {
            "summary_path": str(summary_path),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if warnings:
        print("fixed runner preflight warnings:")
        for warning in warnings:
            print(f"- {warning}")

    if errors:
        print("fixed runner preflight failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 1

    print("fixed runner preflight passed")
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
