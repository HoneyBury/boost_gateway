#!/usr/bin/env python3
"""Validate deployment artifacts against the current runnable topology."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

BACKENDS = {
    "login-backend": ("v2_login_backend", "9202"),
    "room-backend": ("v2_room_backend", "9302"),
    "battle-backend": ("v2_battle_backend", "9303"),
    "matchmaking-backend": ("v2_match_backend", "9304"),
    "leaderboard-backend": ("v2_leaderboard_backend", "9305"),
}

SYSTEMD_UNITS = {
    "boost-gateway.service",
    "boost-login-backend.service",
    "boost-room-backend.service",
    "boost-battle-backend.service",
    "boost-match-backend.service",
    "boost-leaderboard-backend.service",
}

BINARIES = {
    "v2_gateway_demo",
    "v2_login_backend",
    "v2_room_backend",
    "v2_battle_backend",
    "v2_match_backend",
    "v2_leaderboard_backend",
    "v2_gateway_pressure",
}


def read_text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def validate_compose(path: Path, checks: list[dict[str, Any]]) -> None:
    text = path.read_text(encoding="utf-8")
    label = str(path.relative_to(REPO_ROOT))

    for service, (binary, port) in BACKENDS.items():
        add_check(
            checks,
            f"{label}:{service}:binary",
            f"SERVICE_BINARY: {binary}" in text,
            f"{service} uses {binary}",
        )
        add_check(
            checks,
            f"{label}:{service}:tcp-healthcheck",
            f'"nc", "-z", "127.0.0.1", "{port}"' in text,
            f"{service} probes TCP port {port}",
        )
        add_check(
            checks,
            f"{label}:{service}:no-http-healthcheck",
            f"http://localhost:{port}/health" not in text,
            f"{service} does not pretend to expose HTTP /health",
        )

    for host in ("login-backend", "room-backend", "battle-backend"):
        add_check(
            checks,
            f"{label}:gateway:{host}",
            host in text,
            f"gateway command routes to compose service {host}",
        )
    add_check(
        checks,
        f"{label}:leaderboard:redis-host",
        "REDIS_HOST: redis" in text,
        "leaderboard backend uses the compose Redis service by default",
    )
    add_check(
        checks,
        f"{label}:leaderboard:redis-health-dependency",
        "redis:\n        condition: service_healthy" in text,
        "leaderboard backend waits for Redis health in compose",
    )


def validate_systemd(checks: list[dict[str, Any]]) -> None:
    systemd_dir = REPO_ROOT / "deploy/systemd"
    cmake = read_text("CMakeLists.txt")

    for unit in sorted(SYSTEMD_UNITS):
        path = systemd_dir / unit
        add_check(checks, f"systemd:{unit}:exists", path.exists(), f"{unit} is present")
        add_check(
            checks,
            f"systemd:{unit}:installed",
            f"deploy/systemd/{unit}" in cmake,
            f"{unit} is installed by CMake",
        )
        if path.exists():
            text = path.read_text(encoding="utf-8")
            add_check(
                checks,
                f"systemd:{unit}:no-placeholder-docs",
                "github.com/example" not in text,
                f"{unit} documentation URL is not a placeholder",
            )


def validate_dockerfile(checks: list[dict[str, Any]]) -> None:
    text = read_text("env/docker/Dockerfile.backend")
    add_check(
        checks,
        "dockerfile-backend:nc-installed",
        "netcat-openbsd" in text,
        "backend runtime image contains a TCP probe utility",
    )
    add_check(
        checks,
        "dockerfile-backend:tcp-healthcheck",
        'nc -z 127.0.0.1 "${SERVICE_PORT}"' in text,
        "generic backend image uses TCP healthcheck",
    )


def validate_examples(checks: list[dict[str, Any]]) -> None:
    for relative in (
        "examples/v2_match_backend/main.cpp",
        "examples/v2_leaderboard_backend/main.cpp",
    ):
        text = read_text(relative)
        add_check(
            checks,
            f"{relative}:noninteractive-runtime",
            "Press Enter to stop" not in text and "std::cin.get" not in text,
            f"{relative} keeps running under systemd/docker until signalled",
        )
        add_check(
            checks,
            f"{relative}:service-port-env",
            'std::getenv("SERVICE_PORT")' in text,
            f"{relative} accepts generic container SERVICE_PORT",
        )


def validate_binaries(build_dir: Path | None, checks: list[dict[str, Any]]) -> None:
    if build_dir is None:
        return
    for binary in sorted(BINARIES):
        matches = list(build_dir.rglob(binary))
        add_check(
            checks,
            f"binary:{binary}",
            bool(matches),
            f"{binary} found under {build_dir}" if matches else f"{binary} missing under {build_dir}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, help="Optional build tree to validate binaries")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/deploy-operability-summary.json",
        help="Path for JSON summary output",
    )
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    validate_dockerfile(checks)
    validate_compose(REPO_ROOT / "docker-compose.yml", checks)
    validate_compose(REPO_ROOT / "env/docker/docker-compose.yml", checks)
    validate_systemd(checks)
    validate_examples(checks)
    validate_binaries(args.build_dir, checks)

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "passed": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }

    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(
        f"deploy operability: {'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    print(f"summary: {args.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
