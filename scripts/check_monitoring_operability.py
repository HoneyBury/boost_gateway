#!/usr/bin/env python3
"""Validate production monitoring artifacts against the current metrics surface."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

BACKEND_TARGETS = {
    "login-backend:9202",
    "room-backend:9302",
    "battle-backend:9303",
    "matchmaking-backend:9304",
    "leaderboard-backend:9305",
}

LEGACY_QUERY_TOKENS = {
    "backend_route_",
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
    "BoostGatewayRateLimitSpike",
    "BoostGatewayHighActiveSessions",
    "BoostGatewayHighRSS",
    "BoostGatewayHighFileDescriptors",
}

REQUIRED_DASHBOARD_METRICS = {
    "gateway_active_sessions",
    "gateway_accepted_sessions_total",
    "gateway_outbound_dispatches_total",
    "gateway_backend_.*_requests_total",
    "gateway_backend_.*_errors_total",
    "gateway_backend_.*_timeouts_total",
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
        "prometheus:gateway-scrape",
        '"gateway:9080"' in prometheus and "metrics_path: /metrics" in prometheus,
        "Prometheus scrapes the gateway HTTP management endpoint",
    )
    for target in sorted(BACKEND_TARGETS):
        add_check(
            checks,
            f"prometheus:no-backend-http-scrape:{target}",
            target not in prometheus,
            f"{target} is not configured as an HTTP metrics target",
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
        "alerts:leaderboard-redis-proxy",
        "gateway_backend_leaderboard_errors_total" in alerts
        and "Redis" in alerts,
        "Redis degradation is inferred from current leaderboard backend gateway counters",
    )
    add_check(
        checks,
        "alerts:optional-process-exporter-labeled",
        "optional-process" in alerts
        and "process_resident_memory_bytes" in alerts
        and "process_open_fds" in alerts,
        "RSS/fd alerts are clearly marked as optional process exporter rules",
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


def validate_docs(checks: list[dict[str, Any]]) -> None:
    env_readme = read_text("env/README.md")
    runbook = read_text("docs/production-operations-runbook.md")
    deployment = read_text("docs/production-deployment-runbook.md")
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
    validate_alerts(checks)
    validate_dashboard(checks)
    validate_docs(checks)

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
        f"monitoring operability: {'PASS' if summary['passed'] else 'FAIL'} "
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
