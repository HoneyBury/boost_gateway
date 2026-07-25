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


REPO_ROOT = Path(__file__).resolve().parents[3]
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
            '"SERVICE_PORT"' in read_text("src/app/config.cpp"),
            f"{relative} accepts generic container SERVICE_PORT via app::config overlay",
        )

    gateway_main = read_text("examples/v2_gateway_demo/main.cpp")
    gateway_server = read_text("src/v2/gateway/demo_server.cpp")
    for host, (host_flag, port_flag, _) in GATEWAY_ROUTED_BACKENDS.items():
        add_check(
            checks,
            f"examples/v2_gateway_demo/main.cpp:{host}:flag",
            host_flag in gateway_main and port_flag in gateway_main,
            f"gateway demo parses {host_flag}/{port_flag}",
        )
    add_check(
        checks,
        "src/v2/gateway/demo_server.cpp:container-listen-address",
        'listen("0.0.0.0"' in gateway_server,
        "gateway listens on all interfaces so Docker-published TCP ingress reaches the container",
    )


def validate_k8s(checks: list[dict[str, Any]]) -> None:
    k8s_dir = REPO_ROOT / "env/k8s"
    for file_name, expected_image in K8S_IMAGES.items():
        path = k8s_dir / file_name
        add_check(
            checks,
            f"k8s:{file_name}:manifest-exists",
            path.exists(),
            f"{path.relative_to(REPO_ROOT)} exists",
        )
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        add_check(
            checks,
            f"k8s:{file_name}:version-label",
            f'app.kubernetes.io/version: "{PROJECT_VERSION}"' in text,
            f"{file_name} uses app version {PROJECT_VERSION}",
        )
        add_check(
            checks,
            f"k8s:{file_name}:pinned-image",
            f"image: {expected_image}" in text,
            f"{file_name} uses pinned image {expected_image}",
        )
        add_check(
            checks,
            f"k8s:{file_name}:no-latest-image",
            ":latest" not in text,
            f"{file_name} does not use a floating latest tag",
        )

    for service, (_, port) in BACKENDS.items():
        path = k8s_dir / f"{service}-deployment.yaml"
        add_check(
            checks,
            f"k8s:{service}:manifest-exists",
            path.exists(),
            f"{path.relative_to(REPO_ROOT)} exists",
        )
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        add_check(
            checks,
            f"k8s:{service}:tcp-liveness",
            f"livenessProbe:\n            tcpSocket:\n              port: {port}" in text,
            f"{service} liveness probe uses TCP port {port}",
        )
        add_check(
            checks,
            f"k8s:{service}:tcp-readiness",
            f"readinessProbe:\n            tcpSocket:\n              port: {port}" in text,
            f"{service} readiness probe uses TCP port {port}",
        )
        add_check(
            checks,
            f"k8s:{service}:no-http-probe",
            f"path: /health\n              port: {port}" not in text,
            f"{service} does not use HTTP /health probe",
        )

    leaderboard = read_text("env/k8s/leaderboard-backend-deployment.yaml")
    add_check(
        checks,
        "k8s:leaderboard:redis-host",
        'name: REDIS_HOST\n              value: "redis"' in leaderboard,
        "leaderboard Kubernetes manifest points at Redis service",
    )

    gateway = read_text("env/k8s/gateway-deployment.yaml")
    add_check(
        checks,
        "k8s:gateway:http-health-probe-documented",
        "gateway `/health` is a liveness stub" in read_text(DEPLOYMENT_RUNBOOK),
        "gateway HTTP health probe limitation is documented in the production runbook",
    )
    add_check(
        checks,
        "k8s:gateway:routes-all-backends",
        all(host in gateway and host_flag in gateway and port_flag in gateway for host, (host_flag, port_flag, _) in GATEWAY_ROUTED_BACKENDS.items()),
        "gateway Kubernetes args route all five backend services",
    )


def validate_monitoring(checks: list[dict[str, Any]]) -> None:
    text = read_text("env/monitoring/prometheus.yml")
    add_check(
        checks,
        "prometheus:version",
        f'version: "{PROJECT_VERSION}"' in text,
        "Prometheus config version matches current release line",
    )
    add_check(
        checks,
        "prometheus:gateway-scrape",
        '"gateway:9080"' in text and "metrics_path: /metrics" in text,
        "Prometheus scrapes gateway HTTP metrics",
    )
    add_check(
        checks,
        "prometheus:alert-rules-loaded",
        "prometheus-alerts.yml" in text,
        "Prometheus loads production alert rules",
    )
    for service, (_, port) in BACKENDS.items():
        add_check(
            checks,
            f"prometheus:{service}:not-scraped",
            f"{service}:{port}" not in text,
            f"{service} is not scraped as HTTP metrics endpoint",
        )

    env_readme = read_text("env/README.md")
    add_check(
        checks,
        "docs:env-readme:gateway-only-scrape",
        "scrapes gateway `/metrics` only" in env_readme and "scrape /metrics from all 6 services" not in env_readme,
        "environment README describes the current gateway-only Prometheus scrape scope",
    )

    compose = read_text("env/docker/docker-compose.yml")
    add_check(
        checks,
        "compose:prometheus-alerts-mounted",
        "../monitoring/prometheus-alerts.yml:/etc/prometheus/prometheus-alerts.yml:ro" in compose,
        "Compose mounts Prometheus alert rules",
    )
    add_check(
        checks,
        "compose:grafana-datasource-mounted",
        "../monitoring/grafana-datasource.yml:/etc/grafana/provisioning/datasources/prometheus.yml:ro" in compose,
        "Compose mounts Grafana datasource provisioning",
    )
    add_check(
        checks,
        "compose:grafana-dashboard-provider-mounted",
        "../monitoring/grafana-dashboard-provider.yml:/etc/grafana/provisioning/dashboards/boost-gateway.yml:ro" in compose,
        "Compose mounts Grafana dashboard provider",
    )
    add_check(
        checks,
        "compose:grafana-dashboard-mounted",
        "../monitoring/grafana-dashboard.json:/var/lib/grafana/dashboards/boost-gateway.json:ro" in compose,
        "Compose mounts Grafana dashboard JSON",
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
    validate_docker_gateway_config(checks)
    validate_compose(REPO_ROOT / "env/docker/docker-compose.yml", checks)
    validate_systemd(checks)
    validate_examples(checks)
    validate_k8s(checks)
    validate_monitoring(checks)
    validate_binaries(args.build_dir, checks)

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "deploy_operability" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {
            "summary_path": str(args.summary_path),
            "compose_file": str(REPO_ROOT / "env/docker/docker-compose.yml"),
            "k8s_dir": str(REPO_ROOT / "env/k8s"),
            "systemd_dir": str(REPO_ROOT / "deploy/systemd"),
            "deployment_runbook": str(REPO_ROOT / DEPLOYMENT_RUNBOOK),
        },
    }

    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(
        f"deploy operability: {'PASS' if summary['overall_pass'] else 'FAIL'} "
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
