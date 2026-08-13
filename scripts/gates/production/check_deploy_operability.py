#!/usr/bin/env python3
"""Validate deployment artifacts against the current runnable topology."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import argparse
import json
import platform
import re
import sys
from pathlib import Path
from typing import Any
from datetime import UTC, datetime



from scripts.lib.deploy_operability_contract import *  # noqa: E402,F403

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
