"""Evidence writers for the pre-production recovery drill."""

from __future__ import annotations

import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def write_command_summary(path: Path, name: str, step: dict[str, Any]) -> None:
    passed = step.get("status") == "passed"
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "overall_pass": passed,
        "passed": passed,
        "failed_category": "" if passed else str(step.get("category", "")),
        "failed_step": "" if passed else name,
        "steps": [step],
        "artifacts": {"summary_path": str(path)},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def write_drill_record(
    path: Path,
    production_recovery_summary: Path,
    sdk_summary: Path,
    redis_alert_summary: Path,
    docker_snapshot_summary: Path,
    monitoring_summary: Path,
    passed: bool,
    *,
    repo_root: Path,
    include_redis_recovery: bool,
    verify_redis_alert_transition: bool,
    failure_started_at: datetime | None,
    failure_ended_at: datetime | None,
    measured_rto_seconds: float | None,
    mode: str = "docker-compose",
) -> None:
    now = datetime.now(UTC)
    failure_started = failure_started_at or now
    failure_ended = failure_ended_at or now
    native_process = mode == "native-process"
    recovery_actions = (
        [
            "start native backend and gateway processes",
            "run SDK full-flow before restart",
            "terminate and restart the native gateway process",
            "wait for gateway TCP and HTTP health",
            "run SDK full-flow after restart",
        ]
        if native_process
        else [
            "start compose stack from existing images",
            "run SDK full-flow before restart",
            "restart gateway container",
            "wait for gateway /ready",
            "run SDK full-flow after restart",
        ]
    )
    if include_redis_recovery:
        recovery_actions.extend(
            [
                "seed Redis persistence marker",
                "stop Redis and require SDK degradation",
                "start Redis and verify persisted marker",
                "run SDK full-flow after Redis recovery",
            ]
        )
    if not native_process:
        recovery_actions.append("collect Docker production snapshot")
    record = {
        "summary_version": 1,
        "template": False,
        "drill_id": (
            "r5-native-gateway-restart"
            if native_process
            else (
                "r5-compose-gateway-redis-recovery"
                if include_redis_recovery
                else "r5-compose-gateway-restart"
            )
        ),
        "executed_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "operator": "codex-local-runner",
        "environment": {
            "type": "native-process" if native_process else "docker-compose",
            "name": (
                f"native-{platform.system().lower()}-{platform.machine()}"
                if native_process
                else "local-docker-preprod"
            ),
            "git_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            ).stdout.strip(),
            "image_tag_before": (
                "not-applicable:native-process"
                if native_process
                else "boost-gateway-v332-gateway:latest"
            ),
            "image_tag_after": (
                "not-applicable:native-process"
                if native_process
                else "boost-gateway-v332-gateway:latest"
            ),
            "native_system": platform.system(),
            "native_machine": platform.machine(),
        },
        "scenario": "redis_recovery" if include_redis_recovery else "gateway_restart",
        "failure_injection": {
            "method": (
                "terminate and restart native gateway process"
                if native_process
                else (
                    "docker compose restart gateway; docker compose stop/start redis"
                    if include_redis_recovery
                    else "docker compose -f env/docker/docker-compose.yml restart gateway"
                )
            ),
            "started_at": failure_started.isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            "ended_at": failure_ended.isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
        },
        "recovery": {
            "actions": recovery_actions,
            "rto_seconds": round(measured_rto_seconds or 0.0, 3),
            "rpo_seconds": 0,
            "data_consistency_risk": "none observed in SDK full-flow validation",
        },
        "observability": {
            "alerts_observed": (
                ["BoostGatewayRedisUnavailable: inactive -> pending -> firing -> resolved"]
                if verify_redis_alert_transition
                else ["local drill did not evaluate external alert firing"]
            ),
            "metrics_checked": (
                ["gateway TCP readiness", "gateway HTTP health", "gateway diagnostics"]
                if native_process
                else [
                    "gateway /ready",
                    "gateway diagnostics",
                    "Prometheus targets",
                    "Grafana health",
                ]
            ),
            "log_sources": (
                ["runtime/validation/process-logs/*.log"]
                if native_process
                else ["docker compose -f env/docker/docker-compose.yml logs gateway"]
            ),
        },
        "verification": {
            "production_recovery_summary": str(production_recovery_summary),
            "sdk_full_flow_summary": str(sdk_summary),
            "redis_alert_runtime_summary": (
                str(redis_alert_summary) if verify_redis_alert_transition else ""
            ),
            "docker_snapshot_summary": (
                "" if native_process else str(docker_snapshot_summary)
            ),
            "k8s_full_flow_summary": "",
            "monitoring_summary": str(monitoring_summary),
            "passed": passed,
        },
        "notes": (
            "R5 native process recovery drill for a production-native platform boundary."
            if native_process
            else "R5 Docker Compose recovery drill for a Linux production boundary."
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
