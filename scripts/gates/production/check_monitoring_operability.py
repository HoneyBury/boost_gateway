#!/usr/bin/env python3
"""Validate production monitoring artifacts against the current metrics surface."""

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



from scripts.lib.monitoring_operability_contract import *  # noqa: E402,F403

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


def validate_smtp_connect_relay(checks: list[dict[str, Any]]) -> None:
    socket = read_text("deploy/systemd/boost-gateway-smtp-proxy.socket")
    service = read_text("deploy/systemd/boost-gateway-smtp-proxy@.service")
    installer = read_text("deploy/operations/install_smtp_proxy_host_units.sh")
    activation = read_text("deploy/operations/switch_alertmanager_smtp_relay.sh")
    runbook = read_text("docs/deployment/long-run-observability-runbook.md")
    add_check(
        checks,
        "smtp-relay:safe-default-listener",
        "ListenStream=127.0.0.1:1587" in socket
        and "Accept=yes" in socket
        and "MaxConnections=32" in socket,
        "SMTP relay defaults to a bounded loopback socket",
    )
    add_check(
        checks,
        "smtp-relay:connect-proxy",
        "-X connect" in service
        and "StandardInput=socket" in service
        and "StandardOutput=socket" in service
        and "DynamicUser=yes" in service
        and "User=nobody" not in service,
        "each SMTP relay connection is unprivileged and has a fixed CONNECT proxy command",
    )
    add_check(
        checks,
        "smtp-relay:production-bridge",
        "docker inspect" in installer
        and "docker network inspect" in installer
        and "value.is_private" in installer
        and "value not in network" in installer
        and "printf 'ListenStream=%s:%s" in installer
        and 'ufw allow in on "${BRIDGE_NAME}"' in installer
        and 'from "${NETWORK_SUBNET}" to "${RELAY_HOST}"' in installer
        and "PROXY_ADDRESS=\"${BOOST_GATEWAY_CONNECT_PROXY:-127.0.0.1:7890}\""
        in installer
        and installer.count("openssl s_client") >= 2,
        "installer limits UFW to the private production bridge and verifies both proxy hops",
    )
    add_check(
        checks,
        "smtp-relay:secret-preserving-activation",
        "--no-deps --force-recreate" in activation
        and "alertmanager-secrets:/etc/alertmanager/secrets:ro" in activation
        and "CONFIG_REPLACED" in activation
        and "rollback" in activation
        and "gmail-app-password" not in activation,
        "activation preserves the existing secret, recreates only Alertmanager, and rolls back failures",
    )
    add_check(
        checks,
        "smtp-relay:documented-boundary",
        "HTTP CONNECT proxy" in runbook
        and "install_smtp_proxy_host_units.sh" in runbook
        and "switch_alertmanager_smtp_relay.sh" in runbook
        and "relay reachability alone is not delivery evidence" in runbook,
        "runbook distinguishes proxy reachability from real delivery evidence",
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
    validate_smtp_connect_relay(checks)
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
