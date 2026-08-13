#!/usr/bin/env python3
"""Verify a source-build-free release Compose deployment and SDK full flow."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from check_release_compose import (  # noqa: E402
    load_compose_document,
    redis_persistence_mode,
    validate_compose_document,
)

from scripts.lib.release_deployment_verification import *  # noqa: E402,F401,F403

def run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )














def wait_valid_json(
    url: str,
    timeout_seconds: float,
    validator: Callable[[object], list[str]],
    retry_seconds: float = 1.0,
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            failures = validator(load_http_json(url))
            if not failures:
                return True, "validated"
            last_error = "; ".join(failures)
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(retry_seconds)
    return False, last_error


















def validate_redis_aof_runtime(compose_command: list[str]) -> tuple[bool, str]:
    expected = {
        "appendonly": "yes",
        "appendfsync": "everysec",
        "no-appendfsync-on-rewrite": "no",
        "aof-load-truncated": "no",
        "aof-use-rdb-preamble": "yes",
        "maxmemory-policy": "noeviction",
        "dir": "/data",
        "save": "300 100 60 10000",
        "stop-writes-on-bgsave-error": "yes",
    }
    config = run(
        [
            *compose_command,
            "exec",
            "-T",
            "redis",
            "redis-cli",
            "--raw",
            "CONFIG",
            "GET",
            *expected,
        ]
    )
    if config.returncode:
        return False, (config.stderr or config.stdout).strip()[-1000:]
    try:
        observed = parse_redis_config_get(config.stdout)
    except ValueError as exc:
        return False, str(exc)
    drift = {
        key: {"expected": value, "observed": observed.get(key)}
        for key, value in expected.items()
        if observed.get(key) != value
    }
    info = run(
        [
            *compose_command,
            "exec",
            "-T",
            "redis",
            "redis-cli",
            "--raw",
            "INFO",
            "persistence",
        ]
    )
    if info.returncode:
        return False, (info.stderr or info.stdout).strip()[-1000:]
    persistence: dict[str, str] = {}
    for raw in info.stdout.splitlines():
        if ":" in raw and not raw.startswith("#"):
            key, value = raw.split(":", 1)
            persistence[key] = value.strip()
    required_info = {
        "aof_enabled": "1",
        "aof_delayed_fsync": "0",
        "aof_last_write_status": "ok",
        "aof_last_bgrewrite_status": "ok",
        "rdb_last_bgsave_status": "ok",
    }
    info_drift = {
        key: {"expected": value, "observed": persistence.get(key)}
        for key, value in required_info.items()
        if persistence.get(key) != value
    }
    manifest = run(
        [
            *compose_command,
            "exec",
            "-T",
            "--user",
            "redis",
            "redis",
            "sh",
            "-eu",
            "-c",
            "test -s /data/appendonlydir/appendonly.aof.manifest",
        ]
    )
    detail = json.dumps(
        {
            "config_drift": drift,
            "info_drift": info_drift,
            "aof_manifest_present": manifest.returncode == 0,
            "aof_manifest_check": {
                "exit_code": manifest.returncode,
                "stdout_tail": manifest.stdout.strip()[-1000:],
                "stderr_tail": manifest.stderr.strip()[-1000:],
            },
        },
        sort_keys=True,
    )
    return not drift and not info_drift and manifest.returncode == 0, detail


def add_check(
    checks: list[dict[str, Any]], name: str, passed: bool, detail: str, **extra: Any
) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail, **extra})


def validate_legacy_redis_hardening_bridge(
    document: object, contract_failures: list[str]
) -> bool:
    if set(contract_failures) != LEGACY_REDIS_CONTRACT_FAILURES or len(
        contract_failures
    ) != len(LEGACY_REDIS_CONTRACT_FAILURES):
        return False
    services = document.get("services") if isinstance(document, dict) else None
    redis = services.get("redis") if isinstance(services, dict) else None
    if not isinstance(redis, dict):
        return False
    return (
        redis.get("image") == "redis:7-alpine"
        and redis.get("user") in {None, ""}
        and set(redis.get("cap_add", [])) == {"CHOWN", "SETGID", "SETUID"}
        and redis.get("cap_drop") == ["ALL"]
        and redis_persistence_mode(redis) == "rdb_only"
    )


def verify(args: argparse.Namespace) -> dict[str, Any]:
    staging = args.staging_dir.resolve()
    compose = args.compose_file.resolve()
    checks: list[dict[str, Any]] = []
    document = load_compose_document(compose)
    services = document.get("services") if isinstance(document, dict) else None
    redis_service = services.get("redis") if isinstance(services, dict) else None
    expected_redis_persistence = redis_persistence_mode(redis_service)
    aof_expected = expected_redis_persistence == "aof_everysec_rdb"
    contract_failures = validate_compose_document(document)
    legacy_redis_hardening_bridge = False
    if args.allow_legacy_redis_hardening_bridge:
        if not args.read_only:
            raise RuntimeError(
                "legacy Redis hardening bridge requires read-only verification"
            )
        legacy_redis_hardening_bridge = validate_legacy_redis_hardening_bridge(
            document, contract_failures
        )
        if legacy_redis_hardening_bridge:
            contract_failures = []
    add_check(
        checks,
        "resolved-production-compose-contract",
        not contract_failures,
        "; ".join(contract_failures),
    )
    if args.allow_legacy_redis_hardening_bridge:
        add_check(
            checks,
            "legacy-redis-hardening-bridge",
            legacy_redis_hardening_bridge,
            (
                "validated one-time RDB-only reconciliation bridge"
                if legacy_redis_hardening_bridge
                else "legacy Redis contract differs from the governed bridge"
            ),
        )
    compose_command = ["docker", "compose", "-f", str(compose)]
    ps = run([*compose_command, "ps", "--format", "json"])
    compose_items: list[dict[str, Any]] = []
    if ps.returncode:
        add_check(checks, "compose-service-state", False, ps.stderr.strip())
    else:
        compose_items = parse_compose_ps(ps.stdout)
        state_failures = verify_service_state(compose_items)
        add_check(
            checks,
            "compose-service-state",
            not state_failures,
            "; ".join(state_failures),
        )
    image_failures = verify_container_images(
        compose_items, load_expected_images(args.image_env_path.resolve())
    )
    add_check(
        checks,
        "container-image-identities",
        not image_failures,
        "; ".join(image_failures),
    )
    for name, url in (
        ("gateway-health", "http://127.0.0.1:9080/health"),
        ("prometheus-ready", "http://127.0.0.1:9090/-/ready"),
        ("alertmanager-ready", "http://127.0.0.1:9093/-/ready"),
        ("grafana-health", "http://127.0.0.1:3000/api/health"),
    ):
        passed, detail = wait_http(url, args.ready_timeout_seconds)
        add_check(checks, name, passed, detail[-1000:])
    readiness_passed, readiness_detail = wait_valid_json(
        "http://127.0.0.1:9080/ready",
        args.ready_timeout_seconds,
        validate_gateway_ready,
    )
    add_check(
        checks,
        "gateway-ready",
        readiness_passed,
        readiness_detail,
    )
    targets_passed, targets_detail = wait_valid_json(
        "http://127.0.0.1:9090/api/v1/targets?state=active",
        args.ready_timeout_seconds,
        validate_prometheus_targets,
    )
    add_check(
        checks,
        "prometheus-active-targets",
        targets_passed,
        targets_detail,
    )
    restart_collector = run(
        ["systemctl", "start", "boost-gateway-container-metrics.service"],
        timeout=30,
    )
    add_check(
        checks,
        "container-restart-metric-collector",
        restart_collector.returncode == 0,
        (restart_collector.stdout + restart_collector.stderr).strip()[-1000:],
    )
    metrics_passed, metrics_detail = wait_valid_json(
        "http://127.0.0.1:9090/api/v1/label/__name__/values",
        args.ready_timeout_seconds,
        lambda value: validate_prometheus_metric_inventory(
            value,
            REQUIRED_PROMETHEUS_METRICS
            | (AOF_REQUIRED_PROMETHEUS_METRICS if aof_expected else set()),
        ),
    )
    add_check(
        checks,
        "prometheus-required-metric-samples",
        metrics_passed,
        metrics_detail,
    )
    rules_passed, rules_detail = wait_valid_json(
        "http://127.0.0.1:9090/api/v1/rules?type=alert",
        args.ready_timeout_seconds,
        lambda value: validate_prometheus_rules(
            value,
            REQUIRED_ALERT_RULES
            | (AOF_REQUIRED_ALERT_RULES if aof_expected else set()),
        ),
    )
    add_check(
        checks,
        "prometheus-alert-rules-healthy",
        rules_passed,
        rules_detail,
    )
    for signal, expression in GOVERNED_CONTAINER_QUERIES.items():
        query = urllib.parse.urlencode({"query": expression})
        passed, detail = wait_valid_json(
            f"http://127.0.0.1:9090/api/v1/query?{query}",
            args.ready_timeout_seconds,
            validate_governed_container_query,
        )
        add_check(
            checks,
            f"governed-container-{signal}-samples",
            passed,
            detail,
        )
    retention_passed, retention_detail = wait_valid_json(
        "http://127.0.0.1:9090/api/v1/status/flags",
        args.ready_timeout_seconds,
        validate_prometheus_flags,
    )
    add_check(
        checks,
        "prometheus-retention-at-least-45-days",
        retention_passed,
        retention_detail,
    )
    query = urllib.parse.urlencode(
        {"query": "boost_gateway_container_restart_collection_success == 1"}
    )
    restart_samples_passed, restart_samples_detail = wait_valid_json(
        f"http://127.0.0.1:9090/api/v1/query?{query}",
        args.ready_timeout_seconds,
        validate_prometheus_nonempty_query,
    )
    add_check(
        checks,
        "container-restart-metric-complete",
        restart_samples_passed,
        restart_samples_detail,
    )
    redis = run([*compose_command, "exec", "-T", "redis", "redis-cli", "ping"])
    redis_passed = redis.returncode == 0 and redis.stdout.strip() == "PONG"
    add_check(
        checks,
        "redis-ping",
        redis_passed,
        (redis.stdout + redis.stderr).strip()[-1000:],
    )
    if aof_expected:
        aof_passed, aof_detail = validate_redis_aof_runtime(compose_command)
        add_check(checks, "redis-aof-effective-runtime", aof_passed, aof_detail)
        query = urllib.parse.urlencode(
            {
                "query": "boost_gateway_redis_persistence_collection_success == 1 "
                "and boost_gateway_redis_persistence_effective_config_valid == 1 "
                "and boost_gateway_redis_aof_delayed_fsync_counter_present == 1 "
                "and boost_gateway_redis_aof_delayed_fsync_total == 0"
            }
        )
        persistence_passed, persistence_detail = wait_valid_json(
            f"http://127.0.0.1:9090/api/v1/query?{query}",
            args.ready_timeout_seconds,
            validate_prometheus_nonempty_query,
        )
        add_check(
            checks,
            "redis-aof-prometheus-samples",
            persistence_passed,
            persistence_detail,
        )
    if not args.read_only:
        client = staging / "bin/sdk_full_flow_client"
        full_flow = run(
            [str(client), args.host, str(args.port)],
            timeout=args.full_flow_timeout_seconds,
        )
        add_check(
            checks,
            "release-sdk-full-flow",
            full_flow.returncode == 0,
            f"exit_code={full_flow.returncode}",
            stdout_tail=full_flow.stdout[-4000:],
            stderr_tail=full_flow.stderr[-4000:],
            source_build_performed=False,
        )
    failures = [check for check in checks if not check["passed"]]
    return {
        "summary_version": 2,
        "generated_at": now(),
        "overall_pass": not failures,
        "passed": not failures,
        "failed_step": failures[0]["name"] if failures else "",
        "source_build_performed": False,
        "public_conan_access_performed": False,
        "read_only_verification": args.read_only,
        "protected_state_mutated": False if args.read_only else True,
        "legacy_redis_hardening_bridge": legacy_redis_hardening_bridge,
        "staging_manifest": str(staging / "manifest.json"),
        "compose_file": str(compose),
        "expected_redis_persistence": expected_redis_persistence,
        "checks": checks,
        "failed": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, required=True)
    parser.add_argument(
        "--image-env-path",
        type=Path,
        default=Path("/etc/boost-gateway/compose-images.env"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9201)
    parser.add_argument("--ready-timeout-seconds", type=float, default=60)
    parser.add_argument("--full-flow-timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="skip the state-mutating SDK full flow for post-backup reconciliation",
    )
    parser.add_argument(
        "--allow-legacy-redis-hardening-bridge",
        action="store_true",
        help="accept only the exact pre-hardening RDB Redis contract during recovery reconciliation",
    )
    parser.add_argument("--summary-path", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = verify(args)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        summary = {
            "summary_version": 2,
            "generated_at": now(),
            "overall_pass": False,
            "passed": False,
            "failed_step": "release-deployment-verification",
            "failure": str(exc),
            "source_build_performed": False,
            "public_conan_access_performed": False,
        }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"release deployment verification: {'PASS' if summary['passed'] else 'FAIL'}")
    print(f"summary: {args.summary_path.resolve()}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
