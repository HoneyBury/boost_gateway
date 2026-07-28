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

REQUIRED_SERVICES = {
    "gateway",
    "login-backend",
    "room-backend",
    "battle-backend",
    "matchmaking-backend",
    "leaderboard-backend",
    "redis",
    "redis-exporter",
    "node-exporter",
    "cadvisor",
    "prometheus",
    "alertmanager",
    "grafana",
}
REQUIRED_PROMETHEUS_JOBS = {
    "gateway",
    "prometheus",
    "redis-exporter",
    "node-exporter",
    "cadvisor",
}
REQUIRED_ALERT_RULES = {
    "BoostGatewayBackendErrors",
    "BoostGatewayBackendTimeouts",
    "BoostGatewayCadvisorDown",
    "BoostGatewayContainerMemoryHigh",
    "BoostGatewayContainerRestartCollectorFailed",
    "BoostGatewayContainerRestarted",
    "BoostGatewayHighActiveSessions",
    "BoostGatewayHighFileDescriptors",
    "BoostGatewayHighRouteLatency",
    "BoostGatewayHighRSS",
    "BoostGatewayHostFilesystemLow",
    "BoostGatewayHostLoadHigh",
    "BoostGatewayHostMemoryHigh",
    "BoostGatewayHostTemperatureHigh",
    "BoostGatewayLeaderboardBackendErrors",
    "BoostGatewayNodeExporterDown",
    "BoostGatewayNoRecentAccepts",
    "BoostGatewayRedisExporterDown",
    "BoostGatewayRedisMemoryHigh",
    "BoostGatewayRedisRdbSaveFailed",
    "BoostGatewayRedisRdbSaveStale",
    "BoostGatewayRedisUnavailable",
    "BoostGatewayScrapeDown",
}
AOF_REQUIRED_ALERT_RULES = {
    "BoostGatewayRedisAofCounterMissing",
    "BoostGatewayRedisAofDelayedFsync",
    "BoostGatewayRedisAofDisabled",
    "BoostGatewayRedisAofRewriteFailed",
    "BoostGatewayRedisAofWriteFailed",
    "BoostGatewayRedisPersistenceCollectorFailed",
    "BoostGatewayRedisPersistenceConfigDrift",
}
REQUIRED_CONTAINER_NAMES = {
    "boost-gateway",
    "boost-login-backend",
    "boost-room-backend",
    "boost-battle-backend",
    "boost-matchmaking-backend",
    "boost-leaderboard-backend",
    "boost-redis",
    "boost-redis-exporter",
    "boost-node-exporter",
    "boost-cadvisor",
    "boost-prometheus",
    "boost-alertmanager",
    "boost-grafana",
}
REQUIRED_PROMETHEUS_METRICS = {
    "node_cpu_seconds_total",
    "node_load1",
    "node_memory_MemAvailable_bytes",
    "node_memory_MemTotal_bytes",
    "node_filesystem_avail_bytes",
    "node_filesystem_size_bytes",
    "node_disk_read_bytes_total",
    "node_disk_written_bytes_total",
    "node_network_receive_bytes_total",
    "node_network_transmit_bytes_total",
    "container_cpu_usage_seconds_total",
    "container_memory_working_set_bytes",
    "container_start_time_seconds",
    "boost_gateway_container_info",
    "boost_gateway_container_restart_count",
    "boost_gateway_container_restart_collection_success",
    "boost_gateway_container_restart_collection_timestamp_seconds",
    "redis_rdb_last_bgsave_status",
    "redis_rdb_changes_since_last_save",
    "redis_rdb_last_save_timestamp_seconds",
}
AOF_REQUIRED_PROMETHEUS_METRICS = {
    "boost_gateway_redis_aof_delayed_fsync_counter_present",
    "boost_gateway_redis_aof_delayed_fsync_total",
    "boost_gateway_redis_aof_enabled",
    "boost_gateway_redis_aof_last_bgrewrite_status",
    "boost_gateway_redis_aof_last_write_status",
    "boost_gateway_redis_persistence_collection_success",
    "boost_gateway_redis_persistence_collection_timestamp_seconds",
    "boost_gateway_redis_persistence_effective_config_valid",
}
GOVERNED_CONTAINER_QUERIES = {
    "cpu": (
        "sum by (container) ("
        "container_cpu_usage_seconds_total "
        "* on (id) group_left (container) boost_gateway_container_info)"
    ),
    "memory": (
        "sum by (container) ("
        "container_memory_working_set_bytes "
        "* on (id) group_left (container) boost_gateway_container_info)"
    ),
    "start-time": (
        "sum by (container) ("
        "container_start_time_seconds "
        "* on (id) group_left (container) boost_gateway_container_info)"
    ),
}
REQUIRED_PROMETHEUS_METRIC_PATTERNS = {
    "gateway-backend-requests": re.compile(r"gateway_backend_.*_requests_total\Z"),
    "gateway-backend-errors": re.compile(
        r"gateway_backend_.*_(?:errors|timeouts)_total\Z"
    ),
    "gateway-backend-latency": re.compile(
        r"gateway_backend_.*_(?:p99_latency_us|route_latency_us_bucket)\Z"
    ),
}
THERMAL_METRICS = {"node_hwmon_temp_celsius", "node_thermal_zone_temp"}
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE_ENV_BY_SERVICE = {
    "gateway": "GATEWAY_IMAGE_ID",
    "login-backend": "LOGIN_IMAGE_ID",
    "room-backend": "ROOM_IMAGE_ID",
    "battle-backend": "BATTLE_IMAGE_ID",
    "matchmaking-backend": "MATCHMAKING_IMAGE_ID",
    "leaderboard-backend": "LEADERBOARD_IMAGE_ID",
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def parse_compose_ps(output: str) -> list[dict[str, Any]]:
    try:
        document = json.loads(output)
        if isinstance(document, list):
            return [item for item in document if isinstance(item, dict)]
        if isinstance(document, dict):
            return [document]
    except json.JSONDecodeError:
        pass
    items: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"docker compose ps returned invalid JSON: {exc}"
            ) from exc
        if not isinstance(item, dict):
            raise RuntimeError("docker compose ps returned a non-object entry")
        items.append(item)
    return items


def verify_service_state(items: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    inventory: dict[str, dict[str, Any]] = {}
    for item in items:
        service = str(item.get("Service", item.get("service", "")))
        if service:
            inventory[service] = item
    missing = REQUIRED_SERVICES - set(inventory)
    if missing:
        failures.append(
            f"Compose is missing required running services: {sorted(missing)}"
        )
    for service in sorted(REQUIRED_SERVICES & set(inventory)):
        item = inventory[service]
        state = str(item.get("State", item.get("state", ""))).lower()
        health = str(item.get("Health", item.get("health", ""))).lower()
        if state != "running":
            failures.append(f"{service} is not running: {state or 'unknown'}")
        if health != "healthy":
            failures.append(f"{service} is not healthy: {health or 'unknown'}")
    return failures


def load_expected_images(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    expected = {
        service: values.get(variable, "")
        for service, variable in IMAGE_ENV_BY_SERVICE.items()
    }
    invalid = [
        service
        for service, image in expected.items()
        if IMAGE_ID_RE.fullmatch(image) is None
    ]
    if invalid:
        raise RuntimeError(
            f"image environment lacks immutable IDs for: {sorted(invalid)}"
        )
    return expected


def verify_container_images(
    items: list[dict[str, Any]], expected: dict[str, str]
) -> list[str]:
    container_ids = {
        str(item.get("Service", item.get("service", ""))): str(
            item.get("ID", item.get("id", ""))
        )
        for item in items
    }
    failures: list[str] = []
    for service, expected_id in sorted(expected.items()):
        container_id = container_ids.get(service, "")
        if not container_id:
            failures.append(f"no container ID for service: {service}")
            continue
        inspected = run(["docker", "inspect", "--format", "{{.Image}}", container_id])
        actual_id = inspected.stdout.strip()
        if inspected.returncode or actual_id != expected_id:
            failures.append(
                f"container image ID mismatch for {service}: {actual_id or inspected.stderr.strip()}"
            )
    return failures


def wait_http(url: str, timeout_seconds: float) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                body = response.read(4096).decode("utf-8", errors="replace")
                if 200 <= response.status < 300:
                    return True, body
                last_error = f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(1)
    return False, last_error


def load_http_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=3) as response:
        document = json.loads(response.read().decode("utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError(f"JSON endpoint did not return an object: {url}")
    return document


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


def validate_gateway_ready(document: object) -> list[str]:
    if not isinstance(document, dict):
        return ["gateway readiness response is not an object"]
    failures: list[str] = []
    if document.get("ready") is not True or document.get("status") != "pass":
        failures.append("gateway did not report ready=true and status=pass")
    checks = document.get("checks")
    if not isinstance(checks, list) or not checks:
        failures.append("gateway readiness response has no checks")
    elif any(
        not isinstance(item, dict) or item.get("status") == "fail" for item in checks
    ):
        failures.append("gateway readiness contains a failed check")
    return failures


def validate_prometheus_targets(document: object) -> list[str]:
    if not isinstance(document, dict) or document.get("status") != "success":
        return ["Prometheus targets response is not successful"]
    data = document.get("data")
    targets = data.get("activeTargets") if isinstance(data, dict) else None
    if not isinstance(targets, list):
        return ["Prometheus targets response has no activeTargets array"]
    jobs: set[str] = set()
    failures: list[str] = []
    for target in targets:
        if not isinstance(target, dict):
            failures.append("Prometheus returned a non-object target")
            continue
        labels = target.get("labels")
        job = str(labels.get("job", "")) if isinstance(labels, dict) else ""
        if job:
            jobs.add(job)
        if target.get("health") != "up" or target.get("lastError"):
            failures.append(f"Prometheus target is not up: {job or 'unknown'}")
    missing = REQUIRED_PROMETHEUS_JOBS - jobs
    if missing:
        failures.append(f"Prometheus is missing required jobs: {sorted(missing)}")
    return failures


def validate_prometheus_metric_inventory(
    document: object, required_metrics: set[str] | None = None
) -> list[str]:
    if not isinstance(document, dict) or document.get("status") != "success":
        return ["Prometheus metric-name response is not successful"]
    data = document.get("data")
    if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
        return ["Prometheus metric-name response has no string array"]
    metrics = set(data)
    failures: list[str] = []
    missing = (required_metrics or REQUIRED_PROMETHEUS_METRICS) - metrics
    if missing:
        failures.append(
            f"Prometheus has no samples for required metrics: {sorted(missing)}"
        )
    if not (THERMAL_METRICS & metrics):
        failures.append("Prometheus has no host thermal samples")
    for label, pattern in REQUIRED_PROMETHEUS_METRIC_PATTERNS.items():
        if not any(pattern.fullmatch(metric) for metric in metrics):
            failures.append(f"Prometheus has no samples for metric group: {label}")
    return failures


def validate_prometheus_rules(
    document: object, required_rules: set[str] | None = None
) -> list[str]:
    if not isinstance(document, dict) or document.get("status") != "success":
        return ["Prometheus rules response is not successful"]
    data = document.get("data")
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return ["Prometheus rules response has no groups array"]
    observed: set[str] = set()
    failures: list[str] = []
    for group in groups:
        rules = group.get("rules") if isinstance(group, dict) else None
        if not isinstance(rules, list):
            failures.append("Prometheus returned an invalid rule group")
            continue
        for rule in rules:
            if not isinstance(rule, dict):
                failures.append("Prometheus returned a non-object rule")
                continue
            name = str(rule.get("name", ""))
            if name:
                observed.add(name)
            if rule.get("health") != "ok" or rule.get("lastError"):
                failures.append(
                    f"Prometheus rule is unhealthy: {name or 'unknown'}: "
                    f"{rule.get('lastError') or rule.get('health') or 'unknown'}"
                )
    missing = (required_rules or REQUIRED_ALERT_RULES) - observed
    if missing:
        failures.append(
            f"Prometheus is missing required alert rules: {sorted(missing)}"
        )
    return failures


def validate_governed_container_query(document: object) -> list[str]:
    if not isinstance(document, dict) or document.get("status") != "success":
        return ["Prometheus governed-container query is not successful"]
    data = document.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list):
        return ["Prometheus governed-container query has no result array"]
    observed: set[str] = set()
    for sample in result:
        metric = sample.get("metric") if isinstance(sample, dict) else None
        container = str(metric.get("container", "")) if isinstance(metric, dict) else ""
        if container:
            observed.add(container)
    missing = REQUIRED_CONTAINER_NAMES - observed
    unexpected = observed - REQUIRED_CONTAINER_NAMES
    failures: list[str] = []
    if missing:
        failures.append(
            f"Prometheus has no sample for governed containers: {sorted(missing)}"
        )
    if unexpected:
        failures.append(
            f"Prometheus returned unmanaged containers: {sorted(unexpected)}"
        )
    return failures


def validate_prometheus_flags(document: object) -> list[str]:
    if not isinstance(document, dict) or document.get("status") != "success":
        return ["Prometheus flags response is not successful"]
    data = document.get("data")
    value = data.get("storage.tsdb.retention.time") if isinstance(data, dict) else None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([dwy])", str(value or ""))
    if match is None:
        return ["Prometheus retention flag is missing or unsupported"]
    days = float(match.group(1)) * {"d": 1.0, "w": 7.0, "y": 365.0}[match.group(2)]
    return (
        [] if days >= 45 else [f"Prometheus retention is shorter than 45 days: {value}"]
    )


def validate_prometheus_nonempty_query(document: object) -> list[str]:
    if not isinstance(document, dict) or document.get("status") != "success":
        return ["Prometheus query response is not successful"]
    data = document.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    return (
        []
        if isinstance(result, list) and result
        else ["Prometheus query returned no samples"]
    )


def parse_redis_config_get(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if len(lines) % 2:
        raise ValueError("Redis CONFIG GET returned an odd number of lines")
    return {lines[index]: lines[index + 1] for index in range(0, len(lines), 2)}


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
    add_check(
        checks,
        "resolved-production-compose-contract",
        not contract_failures,
        "; ".join(contract_failures),
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
