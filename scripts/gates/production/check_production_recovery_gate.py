#!/usr/bin/env python3
"""Validate N3 deployment recovery, rollback, and disaster-recovery evidence."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_RUNBOOK = "docs/deployment/production-deployment-runbook.md"
OPERATIONS_RUNBOOK = "docs/deployment/production-operations-runbook.md"
DRILL_RECORD_TEMPLATE = "docs/production/production-recovery-drill-record-template.json"
DEPLOY_K8S_TOOL = "scripts/tools/deploy_k8s.py"
K8S_FULL_FLOW_GATE = "scripts/gates/k8s/verify_k8s_full_flow.py"
DRILL_RECORD_VALIDATOR = "scripts/gates/production/check_recovery_drill_record.py"

APP_MANIFESTS = [
    "gateway-deployment.yaml",
    "login-backend-deployment.yaml",
    "room-backend-deployment.yaml",
    "battle-backend-deployment.yaml",
    "matchmaking-backend-deployment.yaml",
    "leaderboard-backend-deployment.yaml",
]

BACKEND_SERVICES = [
    "login-backend",
    "room-backend",
    "battle-backend",
    "matchmaking-backend",
    "leaderboard-backend",
]


def read_text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def exists(relative: str) -> bool:
    return (REPO_ROOT / relative).exists()


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def contains(relative: str, token: str) -> bool:
    return exists(relative) and token in read_text(relative)


def validate_compose_recovery(checks: list[dict[str, Any]]) -> None:
    compose = read_text("env/docker/docker-compose.yml")
    deployment = read_text(DEPLOYMENT_RUNBOOK)
    operations = read_text(OPERATIONS_RUNBOOK)

    services = ["gateway", *BACKEND_SERVICES, "redis", "prometheus", "grafana", "alertmanager"]
    for service in services:
        add(
            checks,
            f"compose:{service}:declared",
            f"{service}:" in compose,
            f"{service} is declared in Docker Compose",
        )

    add(checks, "compose:restart-policy", compose.count("restart: unless-stopped") >= 6, "core services restart unless stopped")
    add(checks, "compose:redis-volume", "redis-data:" in compose and "redis-data:" in deployment, "Redis persistent volume is declared and documented")
    add(checks, "compose:logs-volume", "gateway-logs:" in compose and "login-logs:" in compose, "service log volumes are declared")
    add(checks, "compose:log-rotation", 'max-size: "10m"' in compose and 'max-file: "5"' in compose, "json-file log rotation is enabled")
    add(checks, "compose:rollback-command", "docker compose -f env/docker/docker-compose.yml up -d --no-build" in operations, "Docker rollback command is documented")
    add(checks, "compose:post-rollback-full-flow", "verify_sdk_full_flow_client.py" in operations, "post-rollback SDK full-flow validation is documented")
    add(checks, "compose:redis-recovery-doc", "redis-cli ping" in operations and "PVC" in operations, "Redis restore/recovery workflow is documented")


def validate_k8s_recovery(checks: list[dict[str, Any]]) -> None:
    k8s_dir = REPO_ROOT / "env/k8s"
    operations = read_text(OPERATIONS_RUNBOOK)
    deployment = read_text(DEPLOYMENT_RUNBOOK)

    for manifest_name in APP_MANIFESTS:
        relative = f"env/k8s/{manifest_name}"
        path = k8s_dir / manifest_name
        add(checks, f"k8s:{manifest_name}:exists", path.exists(), f"{relative} exists")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        add(checks, f"k8s:{manifest_name}:rolling-update", "type: RollingUpdate" in text and "maxUnavailable: 0" in text and "maxSurge: 1" in text, "manifest uses zero-downtime rolling update")
        add(checks, f"k8s:{manifest_name}:resources", "resources:" in text and "requests:" in text and "limits:" in text, "manifest has resource requests/limits")
        add(checks, f"k8s:{manifest_name}:probes", "livenessProbe:" in text and "readinessProbe:" in text, "manifest has liveness/readiness probes")
        add(checks, f"k8s:{manifest_name}:hpa", "kind: HorizontalPodAutoscaler" in text, "manifest has HPA")
        add(checks, f"k8s:{manifest_name}:pdb", "kind: PodDisruptionBudget" in text, "manifest has PDB")

    redis = read_text("env/k8s/redis-deployment.yaml")
    add(checks, "k8s:redis:pvc", "persistentVolumeClaim:" in redis and "redis-data" in redis, "Redis uses a PVC")
    add(checks, "k8s:redis:probes", "livenessProbe:" in redis and "readinessProbe:" in redis, "Redis has liveness/readiness probes")
    add(checks, "k8s:redis:resources", "resources:" in redis and "requests:" in redis and "limits:" in redis, "Redis has resource requests/limits")
    add(checks, "k8s:rollback-command", "kubectl -n boost-gateway rollout undo deploy/gateway" in operations, "Kubernetes rollback command is documented")
    add(checks, "k8s:rollout-status-doc", "kubectl -n boost-gateway rollout status deploy/gateway" in deployment and "kubectl -n boost-gateway rollout status deploy/gateway" in operations, "rollout status verification is documented")
    add(checks, "k8s:events-and-logs-doc", "kubectl -n boost-gateway get events --sort-by=.lastTimestamp" in operations and "kubectl -n boost-gateway logs deploy/gateway" in operations, "Kubernetes events/logs triage is documented")


def validate_scripts(checks: list[dict[str, Any]]) -> None:
    deploy = read_text(DEPLOY_K8S_TOOL)
    k8s_flow = read_text(K8S_FULL_FLOW_GATE)
    resilience = read_text("scripts/gates/production/verify_production_resilience_gate.py")
    hardening = read_text("scripts/gates/production/check_production_hardening_gate.py")
    drill_record = read_text(DRILL_RECORD_VALIDATOR)

    add(checks, "script:deploy-k8s:dry-run", "--dry-run" in deploy and "--validate=false" in deploy, "deploy_k8s supports client-side dry-run")
    add(checks, "script:deploy-k8s:delete", "--delete" in deploy and "--ignore-not-found=true" in deploy, "deploy_k8s supports idempotent delete")
    add(checks, "script:k8s-full-flow:rollout", "rollout" in k8s_flow and "status" in k8s_flow, "K8s full-flow gate waits for rollout")
    add(checks, "script:k8s-full-flow:port-forward", "port-forward" in k8s_flow and "sdk_full_flow_client" in k8s_flow, "K8s full-flow gate validates real SDK traffic")
    add(checks, "script:resilience:n3-recovery", "check_production_recovery_gate.py" in resilience, "production resilience gate includes N3 recovery evidence")
    add(checks, "script:drill-record:exists", exists(DRILL_RECORD_VALIDATOR), "recovery drill record validator exists")
    add(checks, "script:drill-record:scenario", "ALLOWED_SCENARIOS" in drill_record and "gateway_restart" in drill_record, "drill record validator constrains scenario names")
    add(checks, "script:drill-record:rto-rpo", "rto_seconds" in drill_record and "rpo_seconds" in drill_record, "drill record validator checks RTO/RPO")
    add(checks, "script:drill-record:summaries", "SUMMARY_FIELDS" in drill_record and "sdk_full_flow_summary" in drill_record, "drill record validator checks validation summary paths")
    add(checks, "script:hardening:h3-k8s", "validate_h3" in hardening and "HorizontalPodAutoscaler" in hardening, "production hardening gate covers K8s resource/HPA/PDB evidence")


def validate_runbooks(checks: list[dict[str, Any]]) -> None:
    deployment = read_text(DEPLOYMENT_RUNBOOK)
    operations = read_text(OPERATIONS_RUNBOOK)
    roadmap = read_text("docs/archive/plans/production-stabilization-roadmap.md")

    add(checks, "runbook:n3-section", "check_production_recovery_gate.py" in deployment and "check_production_recovery_gate.py" in operations, "N3 recovery section exists in runbooks")
    add(checks, "runbook:rto-rpo", "RTO" in deployment and "RPO" in deployment, "RTO/RPO targets are documented")
    add(checks, "runbook:failure-matrix", "gateway 重启" in deployment and "Redis 恢复" in deployment and "镜像回滚" in deployment, "deployment runbook has recovery drill matrix")
    add(checks, "runbook:recovery-record", "check_recovery_drill_record.py" in operations and "production-recovery-drill-record-template.json" in operations, "operations runbook defines recovery record requirements")
    add(checks, "runbook:drill-template", "production-recovery-drill-record-template.json" in deployment and "production-recovery-drill-record-template.json" in operations, "recovery drill record template is documented")
    add(checks, "runbook:drill-validator", "check_recovery_drill_record.py" in deployment and "check_recovery_drill_record.py" in operations, "recovery drill record validator is documented")
    add(checks, "roadmap:n3-status", "check_production_recovery_gate.py" in roadmap and "verify_production_resilience_gate.py" in roadmap, "roadmap references the N3 recovery gate")


def validate_drill_record_assets(checks: list[dict[str, Any]]) -> None:
    template_path = REPO_ROOT / DRILL_RECORD_TEMPLATE
    add(checks, "drill-template:exists", template_path.exists(), "recovery drill record template exists")
    if not template_path.exists():
        return

    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        add(checks, "drill-template:json", False, f"template JSON parses: {exc}")
        return

    add(checks, "drill-template:json", isinstance(template, dict), "template JSON is an object")
    add(checks, "drill-template:template-flag", template.get("template") is True, "template is explicitly marked")
    add(checks, "drill-template:scenario", template.get("scenario") in {"gateway_restart", "backend_restart", "redis_recovery", "compose_image_rollback", "k8s_rollout_rollback", "network_jitter", "config_reload"}, "template uses a supported scenario")
    add(checks, "drill-template:rto-rpo", "rto_seconds" in template.get("recovery", {}) and "rpo_seconds" in template.get("recovery", {}), "template captures RTO/RPO")
    add(checks, "drill-template:observability", "alerts_observed" in template.get("observability", {}) and "metrics_checked" in template.get("observability", {}), "template captures alerts and metrics")
    add(checks, "drill-template:verification", "passed" in template.get("verification", {}) and "sdk_full_flow_summary" in template.get("verification", {}), "template captures final verification summaries")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-path", type=Path, default=REPO_ROOT / "runtime/validation/production-recovery-summary.json")
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path

    checks: list[dict[str, Any]] = []
    validate_compose_recovery(checks)
    validate_k8s_recovery(checks)
    validate_scripts(checks)
    validate_runbooks(checks)
    validate_drill_record_assets(checks)

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
        "failed_category": "recovery_gate" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {
            "summary_path": str(summary_path),
            "deployment_runbook": str(REPO_ROOT / DEPLOYMENT_RUNBOOK),
            "operations_runbook": str(REPO_ROOT / OPERATIONS_RUNBOOK),
            "drill_record_template": str(REPO_ROOT / DRILL_RECORD_TEMPLATE),
        },
    }

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"production recovery gate: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks)-len(failed)}/{len(checks)} checks)")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
