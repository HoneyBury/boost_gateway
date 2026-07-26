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
PRODUCTION_COMPOSE = "deploy/operations/docker-compose.production.yml"

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
    "node-exporter:9100",
    "cadvisor:8080",
}

LEGACY_QUERY_TOKENS = {
    "backend_login_healthy_instances",
    "backend_room_healthy_instances",
    "backend_battle_healthy_instances",
    'job="login-backend"',
    'job="room-backend"',
    'job="battle-backend"',
    "gateway_sessions_accepted_total",
    "gateway_packets_received_total",
    "gateway_packets_sent_total",
    "gateway_packets_blocked_total",
    "gateway_login_success_total",
    "gateway_room_join_success_total",
    "gateway_battle_start_success_total",
    "gateway_bytes_received_total",
    "gateway_bytes_sent_total",
    "gateway_authenticated_sessions",
    "gateway_active_rooms",
    "gateway_active_battles",
}

REQUIRED_ALERTS = {
    "BoostGatewayScrapeDown",
    "BoostGatewayBackendErrors",
    "BoostGatewayBackendTimeouts",
    "BoostGatewayLeaderboardBackendErrors",
    "BoostGatewayRedisUnavailable",
    "BoostGatewayHighRouteLatency",
    "BoostGatewayRedisExporterDown",
    "BoostGatewayRedisMemoryHigh",
    "BoostGatewayHighActiveSessions",
    "BoostGatewayHighRSS",
    "BoostGatewayHighFileDescriptors",
    "BoostGatewayContainerMemoryHigh",
    "BoostGatewayNodeExporterDown",
    "BoostGatewayCadvisorDown",
    "BoostGatewayHostLoadHigh",
    "BoostGatewayHostMemoryHigh",
    "BoostGatewayHostFilesystemLow",
    "BoostGatewayHostTemperatureHigh",
    "BoostGatewayContainerRestarted",
    "BoostGatewayContainerRestartCollectorFailed",
    "BoostGatewayRedisRdbSaveFailed",
    "BoostGatewayRedisRdbSaveStale",
}

REQUIRED_DASHBOARD_METRICS = {
    "gateway_active_sessions",
    "gateway_accepted_sessions_total",
    "gateway_outbound_dispatches_total",
    "gateway_backend_login_requests_total",
    "gateway_backend_room_requests_total",
    "gateway_backend_battle_requests_total",
    "gateway_backend_matchmaking_requests_total",
    "gateway_backend_leaderboard_requests_total",
    "gateway_backend_login_errors_total",
    "gateway_backend_login_timeouts_total",
    "gateway_backend_.*_p99_latency_us",
    "gateway_backend_route_latency_us_bucket",
    "redis_connected_clients",
    "redis_memory_used_bytes",
    "container_memory_working_set_bytes",
    "boost_gateway_container_info",
    "node_cpu_seconds_total",
    "node_load1",
    "node_memory_MemAvailable_bytes",
    "node_filesystem_avail_bytes",
    "node_disk_read_bytes_total",
    "node_disk_written_bytes_total",
    "node_network_receive_bytes_total",
    "node_network_transmit_bytes_total",
    "node_hwmon_temp_celsius",
    "container_start_time_seconds",
    "boost_gateway_container_restart_count",
    "redis_rdb_last_bgsave_status",
    "redis_rdb_changes_since_last_save",
    "redis_rdb_last_save_timestamp_seconds",
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
    compose = read_text(PRODUCTION_COMPOSE)

    add_check(
        checks,
        "prometheus:rule-file",
        "prometheus-alerts.yml" in prometheus,
        "Prometheus loads the production alert rule file",
    )
    add_check(
        checks,
        "compose:alerts-mounted",
        "../../env/monitoring/prometheus-alerts.yml:/etc/prometheus/prometheus-alerts.yml:ro" in compose,
        "Production Compose mounts the alert rule file into Prometheus",
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
    compose = read_text(PRODUCTION_COMPOSE)
    datasource = read_text("env/monitoring/grafana-datasource.yml")
    provider = read_text("env/monitoring/grafana-dashboard-provider.yml")

    add_check(
        checks,
        "grafana:datasource-provisioned",
        "../../env/monitoring/grafana-datasource.yml:/etc/grafana/provisioning/datasources/prometheus.yml:ro" in compose
        and "url: http://prometheus:9090" in datasource
        and "isDefault: true" in datasource,
        "Docker Compose provisions the Prometheus datasource for Grafana",
    )
    add_check(
        checks,
        "grafana:dashboard-provider-provisioned",
        "../../env/monitoring/grafana-dashboard-provider.yml:/etc/grafana/provisioning/dashboards/boost-gateway.yml:ro" in compose
        and "path: /var/lib/grafana/dashboards" in provider,
        "Docker Compose provisions the dashboard provider",
    )
    add_check(
        checks,
        "grafana:dashboard-json-mounted",
        "../../env/monitoring/grafana-dashboard.json:/var/lib/grafana/dashboards/boost-gateway.json:ro" in compose,
        "Docker Compose mounts the Boost Gateway dashboard JSON",
    )
    add_check(
        checks,
        "grafana:admin-credentials-required",
        "GF_SECURITY_ADMIN_USER: ${GRAFANA_ADMIN_USER:?" in compose
        and "GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?" in compose
        and "GRAFANA_ADMIN_USER:-admin" not in compose,
        "Production Compose requires non-default Grafana credential inputs",
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
        "alerts:explicit-backend-red",
        all(
            f"gateway_backend_{service}_{outcome}_total" in alerts
            for service in ("login", "room", "battle", "matchmaking", "leaderboard")
            for outcome in ("errors", "timeouts")
        )
        and "sum(rate({__name__=~" not in alerts,
        "backend RED alerts enumerate the real metric families without colliding label sets",
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
        "alerts:governed-host-container-exporters",
        "up{job=\"node-exporter\"}" in alerts
        and "up{job=\"cadvisor\"}" in alerts
        and "container_memory_working_set_bytes" in alerts
        and "boost_gateway_container_info" in alerts
        and "on (id)" in alerts,
        "host and container runtime alerts use the governed production exporters",
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
    add_check(
        checks,
        "grafana:backend-histogram-service-label",
        "sum by (exported_service, le)" in joined
        and "sum by (service, le)" not in joined,
        "dashboard preserves the gateway backend label renamed by Prometheus",
    )
    add_check(
        checks,
        "grafana:governed-container-identity",
        "boost_gateway_container_info" in joined
        and "on (id)" in joined
        and "name=~\"boost-" not in joined,
        "dashboard joins cAdvisor samples to the governed container identity map",
    )


def validate_evidence_scheduler(checks: list[dict[str, Any]]) -> None:
    scheduler = read_text("scripts/tools/schedule_observability_evidence.py")
    service = read_text(
        "deploy/systemd/boost-gateway-observability-evidence@.service"
    )
    daily = read_text(
        "deploy/systemd/boost-gateway-observability-evidence-daily.timer"
    )
    weekly = read_text(
        "deploy/systemd/boost-gateway-observability-evidence-weekly.timer"
    )
    installer = read_text("deploy/operations/install_observability_host_units.sh")
    add_check(
        checks,
        "evidence-scheduler:closed-utc-periods",
        "previous full UTC day" in scheduler
        and "previous full ISO week" in scheduler
        and "daily-" in scheduler
        and "weekly-" in scheduler,
        "scheduler records uniquely named previous closed UTC periods",
    )
    for signal in (
        "node_load1",
        "node_disk_read_bytes_total",
        "node_network_receive_bytes_total",
        "boost_gateway_container_info",
        "gateway_backend_login_errors_total",
        "prometheus_rule_evaluation_failures_total",
        "redis_rdb_last_bgsave_status",
    ):
        add_check(
            checks,
            f"evidence-scheduler:signal:{signal}",
            signal in scheduler,
            f"scheduled evidence includes real signal {signal}",
        )
    add_check(
        checks,
        "evidence-scheduler:no-canary-or-docker",
        "sdk_full_flow" not in scheduler
        and "subprocess" not in scheduler
        and '"docker"' not in scheduler,
        "scheduler does not run the SDK canary or Docker commands",
    )
    add_check(
        checks,
        "evidence-scheduler:hardened-loopback-service",
        "IPAddressDeny=any" in service
        and "IPAddressAllow=localhost" in service
        and "InaccessiblePaths=/etc/boost-gateway /run/docker.sock" in service
        and "ReadWritePaths=/var/lib/boost-gateway-evidence/observability" in service
        and "EnvironmentFile=" not in service,
        "scheduled evidence service is loopback-only and cannot read secrets or Docker",
    )
    add_check(
        checks,
        "evidence-scheduler:persistent-timers",
        "00:15:00 UTC" in daily
        and "Mon *-*-* 00:45:00 UTC" in weekly
        and "Persistent=true" in daily
        and "Persistent=true" in weekly,
        "daily and weekly evidence timers are persistent and UTC-governed",
    )
    add_check(
        checks,
        "evidence-scheduler:installed",
        "schedule_observability_evidence.py" in installer
        and "boost-gateway-observability-evidence-daily.timer" in installer
        and "boost-gateway-observability-evidence-weekly.timer" in installer,
        "host installer deploys and enables the evidence scheduler",
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
    validate_evidence_scheduler(checks)
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
