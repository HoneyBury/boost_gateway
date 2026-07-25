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
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from check_release_compose import load_compose_document, validate_compose_document

REQUIRED_SERVICES = {
    "gateway",
    "login-backend",
    "room-backend",
    "battle-backend",
    "matchmaking-backend",
    "leaderboard-backend",
    "redis",
    "redis-exporter",
    "prometheus",
    "alertmanager",
    "grafana",
}
REQUIRED_PROMETHEUS_JOBS = {"gateway", "prometheus", "redis-exporter"}
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
            raise RuntimeError(f"docker compose ps returned invalid JSON: {exc}") from exc
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
        failures.append(f"Compose is missing required running services: {sorted(missing)}")
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
        service for service, image in expected.items() if IMAGE_ID_RE.fullmatch(image) is None
    ]
    if invalid:
        raise RuntimeError(f"image environment lacks immutable IDs for: {sorted(invalid)}")
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
    elif any(not isinstance(item, dict) or item.get("status") == "fail" for item in checks):
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


def add_check(
    checks: list[dict[str, Any]], name: str, passed: bool, detail: str, **extra: Any
) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail, **extra})


def verify(args: argparse.Namespace) -> dict[str, Any]:
    staging = args.staging_dir.resolve()
    compose = args.compose_file.resolve()
    checks: list[dict[str, Any]] = []
    document = load_compose_document(compose)
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
    redis = run([*compose_command, "exec", "-T", "redis", "redis-cli", "ping"])
    redis_passed = redis.returncode == 0 and redis.stdout.strip() == "PONG"
    add_check(
        checks,
        "redis-ping",
        redis_passed,
        (redis.stdout + redis.stderr).strip()[-1000:],
    )
    client = staging / "bin/sdk_full_flow_client"
    full_flow = run([str(client), args.host, str(args.port)], timeout=args.full_flow_timeout_seconds)
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
        "staging_manifest": str(staging / "manifest.json"),
        "compose_file": str(compose),
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
