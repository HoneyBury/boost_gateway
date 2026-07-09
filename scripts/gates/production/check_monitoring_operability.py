#!/usr/bin/env python3
"""Validate production monitoring artifacts against the current metrics surface."""

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
OPERATIONS_RUNBOOK = "docs/deployment/production-operations-runbook.md"
DEPLOYMENT_RUNBOOK = "docs/deployment/production-deployment-runbook.md"

BACKEND_TARGETS = {
    "login-backend:9202",
    "room-backend:9302",
    "battle-backend:9303",
    "matchmaking-backend:9304",
    "leaderboard-backend:9305",
}

REQUIRED_PROMETHEUS_TARGETS = {
    "gateway:9080",
    "localhost:9090",
    "redis-exporter:9121",
}

LEGACY_QUERY_TOKENS = {
    "backend_login_healthy_instances",
    "backend_room_healthy_instances",
    "backend_battle_healthy_instances",
    'job="login-backend"',
    'job="room-backend"',
    'job="battle-backend"',
}

REQUIRED_ALERTS = {
    "BoostGatewayScrapeDown",
    "BoostGatewayBackendErrors",
    "BoostGatewayBackendTimeouts",
    "BoostGatewayLeaderboardBackendErrors",
    "BoostGatewayHighRouteLatency",
    "BoostGatewayBusinessFlowFailure",
    "BoostGatewayRedisExporterDown",
    "BoostGatewayRedisMemoryHigh",
    "BoostGatewayRateLimitSpike",
    "BoostGatewayHighActiveSessions",
    "BoostGatewayHighRSS",
    "BoostGatewayHighFileDescriptors",
    "BoostGatewayContainerMemoryHigh",
}

REQUIRED_DASHBOARD_METRICS = {
    "gateway_active_sessions",
    "gateway_accepted_sessions_total",
    "gateway_outbound_dispatches_total",
    "gateway_backend_.*_requests_total",
    "gateway_backend_.*_errors_total",
    "gateway_backend_.*_timeouts_total",
    "gateway_backend_.*_p99_latency_us",
    "gateway_backend_route_latency_us_bucket",
    "redis_connected_clients",
    "redis_memory_used_bytes",
    "container_memory_working_set_bytes",
}


def read_text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def collect_dashboard_exprs(dashboard: dict[str, Any]) -> list[str]:
    exprs: list[str] = []
    for panel in dashboard.get("panels", []):
        if not isinstance(panel, dict):
            continue
        for target in panel.get("targets", []):
            if isinstance(target, dict) and isinstance(target.get("expr"), str):
                exprs.append(target["expr"])
    return exprs


def validate_prometheus(checks: list[dict[str, Any]]) -> None:
    prometheus = read_text("env/monitoring/prometheus.yml")
    compose = read_text("env/docker/docker-compose.yml")

    add_check(
        checks,
        "prometheus:rule-file",
        "prometheus-alerts.yml" in prometheus,
        "Prometheus loads the production alert rule file",
    )
    add_check(
        checks,
        "compose:alerts-mounted",
        "../monitoring/prometheus-alerts.yml:/etc/prometheus/prometheus-alerts.yml:ro" in compose,
        "Docker Compose mounts the alert rule file into Prometheus",
    )
    add_check(
        checks,
        "prometheus:alertmanager-target",
        "alertmanager:9093" in prometheus,
        "Prometheus routes alerts to Alertmanager",
    )
    add_check(
        checks,
        "prometheus:gateway-scrape",
        '"gateway:9080"' in prometheus and "metrics_path: /metrics" in prometheus,
        "Prometheus scrapes the gateway HTTP management endpoint",
    )
    for target in sorted(REQUIRED_PROMETHEUS_TARGETS):
        add_check(
            checks,
            f"prometheus:required-target:{target}",
            target in prometheus,
            f"Prometheus scrape config includes {target}",
        )
    for target in sorted(BACKEND_TARGETS):
        add_check(
            checks,
            f"prometheus:no-backend-http-scrape:{target}",
            target not in prometheus,
            f"{target} is not configured as an HTTP metrics target",
        )


def validate_grafana_provisioning(checks: list[dict[str, Any]]) -> None:
    compose = read_text("env/docker/docker-compose.yml")
    datasource = read_text("env/monitoring/grafana-datasource.yml")
    provider = read_text("env/monitoring/grafana-dashboard-provider.yml")

    add_check(
        checks,
        "grafana:datasource-provisioned",
        "../monitoring/grafana-datasource.yml:/etc/grafana/provisioning/datasources/prometheus.yml:ro" in compose
        and "url: http://prometheus:9090" in datasource
        and "isDefault: true" in datasource,
        "Docker Compose provisions the Prometheus datasource for Grafana",
    )
    add_check(
        checks,
        "grafana:dashboard-provider-provisioned",
        "../monitoring/grafana-dashboard-provider.yml:/etc/grafana/provisioning/dashboards/boost-gateway.yml:ro" in compose
        and "path: /var/lib/grafana/dashboards" in provider,
        "Docker Compose provisions the dashboard provider",
    )
    add_check(
        checks,
        "grafana:dashboard-json-mounted",
        "../monitoring/grafana-dashboard.json:/var/lib/grafana/dashboards/boost-gateway.json:ro" in compose,
        "Docker Compose mounts the Boost Gateway dashboard JSON",
    )
    add_check(
        checks,
        "grafana:admin-password-not-default",
        "GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-boost-gateway-change-me}" in compose,
        "Grafana compose defaults no longer hardcode admin/admin",
    )


def validate_alerts(checks: list[dict[str, Any]]) -> None:
    alerts = read_text("env/monitoring/prometheus-alerts.yml")
    for alert in sorted(REQUIRED_ALERTS):
        add_check(
            checks,
            f"alerts:required:{alert}",
            f"alert: {alert}" in alerts,
            f"{alert} rule exists",
        )
    for token in LEGACY_QUERY_TOKENS:
        add_check(
            checks,
            f"alerts:no-legacy-token:{token}",
            token not in alerts,
            f"alert rules do not reference legacy or nonexistent metric token {token}",
        )
    add_check(
        checks,
        "alerts:no-legacy-token:backend_route",
        "backend_route_" not in alerts.replace("gateway_backend_route_latency_us", ""),
        "alert rules do not reference legacy backend_route metrics outside the current gateway latency histogram",
    )
    add_check(
        checks,
        "alerts:leaderboard-redis-proxy",
        "gateway_backend_leaderboard_errors_total" in alerts
        and "Redis" in alerts,
        "Redis degradation is inferred from current leaderboard backend gateway counters",
    )
    add_check(
        checks,
        "alerts:route-latency-slo",
        "gateway_backend_.*_p99_latency_us" in alerts or "gateway_backend_route_latency_us_bucket" in alerts,
        "alert rules include a backend route latency P99 SLO signal based on current gateway metrics",
    )
    add_check(
        checks,
        "alerts:business-flow-slo",
        "gateway_login_success_total" in alerts and "gateway_room_join_success_total" in alerts and "gateway_battle_start_success_total" in alerts,
        "alert rules include business-flow success counters for login/room/battle",
    )
    add_check(
        checks,
        "alerts:optional-process-exporter-labeled",
        "optional-process" in alerts
        and "process_resident_memory_bytes" in alerts
        and "process_open_fds" in alerts,
        "RSS/fd alerts are clearly marked as optional process exporter rules",
    )
    add_check(
        checks,
        "alerts:optional-cadvisor-labeled",
        "optional-cadvisor" in alerts
        and "container_memory_working_set_bytes" in alerts,
        "container runtime alerts are clearly marked as optional cAdvisor rules",
    )


def validate_dashboard(checks: list[dict[str, Any]]) -> None:
    dashboard = json.loads(read_text("env/monitoring/grafana-dashboard.json"))
    exprs = collect_dashboard_exprs(dashboard)
    joined = "\n".join(exprs)
    add_check(
        checks,
        "grafana:json",
        isinstance(dashboard.get("panels"), list) and bool(exprs),
        "Grafana dashboard is valid JSON and has query targets",
    )
    for metric in sorted(REQUIRED_DASHBOARD_METRICS):
        add_check(
            checks,
            f"grafana:metric:{metric}",
            re.search(metric, joined) is not None,
            f"dashboard references current metric pattern {metric}",
        )
    for token in LEGACY_QUERY_TOKENS:
        add_check(
            checks,
            f"grafana:no-legacy-token:{token}",
            token not in joined,
            f"dashboard does not reference legacy or nonexistent metric token {token}",
        )
    add_check(
        checks,
        "grafana:no-legacy-token:backend_route",
        "backend_route_" not in joined.replace("gateway_backend_route_latency_us", ""),
        "dashboard does not reference legacy backend_route metrics outside the current gateway latency histogram",
    )


def validate_docs(checks: list[dict[str, Any]]) -> None:
    env_readme = read_text("env/README.md")
    runbook = read_text(OPERATIONS_RUNBOOK)
    deployment = read_text(DEPLOYMENT_RUNBOOK)
    add_check(
        checks,
        "docs:env-alert-path",
        "env/monitoring/prometheus-alerts.yml" in env_readme,
        "environment README points to the real alert rules path",
    )
    add_check(
        checks,
        "docs:gateway-only-scrape",
        "scrapes gateway `/metrics` only" in env_readme
        and "后端服务没有 HTTP `/metrics`" in deployment,
        "docs preserve the gateway-only scrape boundary",
    )
    add_check(
        checks,
        "docs:redis-exporter",
        "redis_exporter" in deployment or "redis exporter" in runbook.lower() or "redis-exporter" in env_readme,
        "docs explain Redis exporter runtime metrics",
    )
    add_check(
        checks,
        "docs:alertmanager",
        "Alertmanager" in deployment or "Alertmanager" in runbook or "alertmanager" in env_readme,
        "docs explain Alertmanager in the monitoring topology",
    )
    add_check(
        checks,
        "docs:host-observability-profile",
        "host-observability" in env_readme or "cAdvisor" in deployment,
        "docs explain the optional host-observability profile",
    )
    add_check(
        checks,
        "docs:host-observability-prometheus",
        "prometheus.host-observability.yml" in env_readme or "9091" in deployment,
        "docs explain the isolated Prometheus scrape path for optional host observability",
    )
    add_check(
        checks,
        "docs:slo",
        "SLI" in runbook and "SLO" in runbook,
        "operations runbook documents SLI/SLO expectations",
    )
    for phrase in (
        "backend down",
        "Redis down",
        "gateway error rate",
        "connection spike",
        "rollback",
        "logs",
    ):
        add_check(
            checks,
            f"docs:operations-runbook:{phrase}",
            phrase in runbook,
            f"operations runbook covers {phrase}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/monitoring-operability-summary.json",
    )
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    validate_prometheus(checks)
    validate_grafana_provisioning(checks)
    validate_alerts(checks)
    validate_dashboard(checks)
    validate_docs(checks)

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
        "failed_category": "monitoring_operability" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {
            "summary_path": str(args.summary_path),
            "prometheus_config": str(REPO_ROOT / "env/monitoring/prometheus.yml"),
            "prometheus_alerts": str(REPO_ROOT / "env/monitoring/prometheus-alerts.yml"),
            "grafana_dashboard": str(REPO_ROOT / "env/monitoring/grafana-dashboard.json"),
            "operations_runbook": str(REPO_ROOT / OPERATIONS_RUNBOOK),
            "deployment_runbook": str(REPO_ROOT / DEPLOYMENT_RUNBOOK),
        },
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(
        f"monitoring operability: {'PASS' if summary['overall_pass'] else 'FAIL'} "
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
