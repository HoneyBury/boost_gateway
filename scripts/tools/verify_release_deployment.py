#!/usr/bin/env python3
"""Verify a source-build-free release Compose deployment and SDK full flow."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
    if ps.returncode:
        add_check(checks, "compose-service-state", False, ps.stderr.strip())
    else:
        state_failures = verify_service_state(parse_compose_ps(ps.stdout))
        add_check(
            checks,
            "compose-service-state",
            not state_failures,
            "; ".join(state_failures),
        )
    for name, url in (
        ("gateway-health", "http://127.0.0.1:9080/health"),
        ("prometheus-ready", "http://127.0.0.1:9090/-/ready"),
        ("alertmanager-ready", "http://127.0.0.1:9093/-/ready"),
        ("grafana-health", "http://127.0.0.1:3000/api/health"),
    ):
        passed, detail = wait_http(url, args.ready_timeout_seconds)
        add_check(checks, name, passed, detail[-1000:])
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
