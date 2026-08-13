#!/usr/bin/env python3
"""Validate deployment artifacts against the current runnable topology."""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from pathlib import Path
from typing import Any
from datetime import UTC, datetime



"""Shared implementation extracted from check_deploy_operability.py."""

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_RUNBOOK = "docs/deployment/production-deployment-runbook.md"

def read_project_version() -> str:
    cmake = (REPO_ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    match = re.search(r"project\(boost_gateway\s+VERSION\s+(\d+\.\d+\.\d+)", cmake)
    if not match:
        raise RuntimeError("cannot resolve project version from CMakeLists.txt")
    return match.group(1)


PROJECT_VERSION = read_project_version()

BACKENDS = {
    "login-backend": ("v2_login_backend", "9202"),
    "room-backend": ("v2_room_backend", "9302"),
    "battle-backend": ("v2_battle_backend", "9303"),
    "matchmaking-backend": ("v2_match_backend", "9304"),
    "leaderboard-backend": ("v2_leaderboard_backend", "9305"),
}

GATEWAY_ROUTED_BACKENDS = {
    "login-backend": ("--login-host", "--login-port", "9202"),
    "room-backend": ("--room-host", "--room-port", "9302"),
    "battle-backend": ("--battle-host", "--battle-port", "9303"),
    "matchmaking-backend": ("--matchmaking-host", "--matchmaking-port", "9304"),
    "leaderboard-backend": ("--leaderboard-host", "--leaderboard-port", "9305"),
}

K8S_IMAGES = {
    "gateway-deployment.yaml": f"ghcr.io/boost-gateway/gateway:v{PROJECT_VERSION}",
    "login-backend-deployment.yaml": f"ghcr.io/boost-gateway/login-backend:v{PROJECT_VERSION}",
    "room-backend-deployment.yaml": f"ghcr.io/boost-gateway/room-backend:v{PROJECT_VERSION}",
    "battle-backend-deployment.yaml": f"ghcr.io/boost-gateway/battle-backend:v{PROJECT_VERSION}",
    "matchmaking-backend-deployment.yaml": f"ghcr.io/boost-gateway/matchmaking-backend:v{PROJECT_VERSION}",
    "leaderboard-backend-deployment.yaml": f"ghcr.io/boost-gateway/leaderboard-backend:v{PROJECT_VERSION}",
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
            f"</dev/tcp/127.0.0.1/{port}" in text,
            f"{service} probes TCP port {port}",
        )
        add_check(
            checks,
            f"{label}:{service}:no-http-healthcheck",
            f"http://localhost:{port}/health" not in text,
            f"{service} does not pretend to expose HTTP /health",
        )

    for host, (host_flag, port_flag, port) in GATEWAY_ROUTED_BACKENDS.items():
        add_check(
            checks,
            f"{label}:gateway:{host}",
            host in text and host_flag in text and port_flag in text and f'"{port}"' in text,
            f"gateway command routes to compose service {host}:{port}",
        )
        add_check(
            checks,
            f"{label}:gateway:{host}:healthy-dependency",
            f"{host}:\n        condition: service_healthy" in text,
            f"gateway waits for {host} to become healthy",
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
    add_check(
        checks,
        f"{label}:docker-gateway-config-mounted",
        "CONFIG_PATH: /app/config/environments/docker/gateway.json" in text,
        "gateway container selects Docker-specific backend routing config",
    )
    add_check(
        checks,
        f"{label}:grafana-nondefault-password",
        "GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-boost-gateway-change-me}" in text,
        "Grafana no longer hardcodes the admin/admin default password",
    )
    add_check(
        checks,
        f"{label}:management-localhost-bound",
        "${MANAGEMENT_HOST_BIND:-127.0.0.1}:9080:9080" in text,
        "gateway management port defaults to localhost binding",
    )
    add_check(
        checks,
        f"{label}:prometheus-localhost-bound",
        "${PROMETHEUS_HOST_BIND:-127.0.0.1}:9090:9090" in text,
        "Prometheus defaults to localhost binding",
    )
    add_check(
        checks,
        f"{label}:grafana-localhost-bound",
        "${GRAFANA_HOST_BIND:-127.0.0.1}:3000:3000" in text,
        "Grafana defaults to localhost binding",
    )
    add_check(
        checks,
        f"{label}:redis-localhost-bound",
        "${REDIS_HOST_BIND:-127.0.0.1}" in text,
        "Redis host publishing defaults to localhost binding",
    )
    add_check(
        checks,
        f"{label}:json-file-log-rotation",
        'max-size: "10m"' in text and 'max-file: "5"' in text,
        "Compose defines json-file log rotation",
    )
    add_check(
        checks,
        f"{label}:no-new-privileges",
        "no-new-privileges:true" in text,
        "Compose enables no-new-privileges on core services",
    )
    add_check(
        checks,
        f"{label}:redis-not-overhardened",
        "redis:\n" in text and "setpriv: setresuid failed" not in text,
        "Redis service is not statically validated by over-hardening rules in compose gate",
    )
    add_check(
        checks,
        f"{label}:alertmanager-service",
        "alertmanager:" in text and "prom/alertmanager" in text,
        "Compose includes Alertmanager",
    )
    add_check(
        checks,
        f"{label}:redis-exporter-service",
        "redis-exporter:" in text and "oliver006/redis_exporter" in text,
        "Compose includes Redis exporter",
    )


def validate_docker_gateway_config(checks: list[dict[str, Any]]) -> None:
    path = REPO_ROOT / "config/environments/docker/gateway.json"
    add_check(
        checks,
        "docker-gateway-config:exists",
        path.exists(),
        "Docker-specific gateway config is present in the governed environments tree",
    )
    if not path.exists():
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    backends = doc.get("backends", {})
    expected = {
        "login": ("login-backend", 9202),
        "room": ("room-backend", 9302),
        "battle": ("battle-backend", 9303),
        "match": ("matchmaking-backend", 9304),
        "leaderboard": ("leaderboard-backend", 9305),
    }
    for name, (host, port) in expected.items():
        entry = backends.get(name, {})
        add_check(
            checks,
            f"docker-gateway-config:{name}:host-port",
            entry.get("host") == host and entry.get("port") == port,
            f"{name} routes to Docker service {host}:{port}",
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

    gateway = (systemd_dir / "boost-gateway.service").read_text(encoding="utf-8")
    for unit in (
        "boost-login-backend.service",
        "boost-room-backend.service",
        "boost-battle-backend.service",
        "boost-match-backend.service",
        "boost-leaderboard-backend.service",
    ):
        add_check(
            checks,
            f"systemd:boost-gateway.service:requires:{unit}",
            unit in gateway,
            f"gateway unit depends on {unit}",
        )


def validate_dockerfile(checks: list[dict[str, Any]]) -> None:
    backend = read_text("env/docker/Dockerfile.backend")
    gateway = read_text("env/docker/Dockerfile.gateway")
    for label, text in (
        ("dockerfile-backend", backend),
        ("dockerfile-gateway", gateway),
    ):
        add_check(checks, f"{label}:runtime-only", text.count("FROM ") == 1, f"{label} has no dependency build stage")
        add_check(checks, f"{label}:no-package-install", "apt-get" not in text, f"{label} performs no network package installation")
        add_check(
            checks,
            f"{label}:staged-conan-binary",
            "COPY runtime/docker-rootfs/bin/" in text and "build-manifest.json" in text,
            f"{label} consumes the validated strict-Conan runtime context",
        )
    add_check(
        checks,
        "dockerfile-backend:no-probe-package",
        "netcat-openbsd" not in backend and "curl" not in backend,
        "backend healthcheck uses tools already present in the base image",
    )
    add_check(
        checks,
        "dockerfile-backend:tcp-healthcheck",
        "</dev/tcp/127.0.0.1/${SERVICE_PORT}" in backend,
        "generic backend image uses TCP healthcheck",
    )
    add_check(
        checks,
        "dockerfile-no-cmake-fetchcontent",
        "cmake" not in backend.lower() and "cmake" not in gateway.lower() and "FetchContent" not in backend + gateway,
        "Docker runtime images cannot configure or fetch CMake dependencies",
    )
