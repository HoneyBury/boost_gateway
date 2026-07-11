#!/usr/bin/env python3
"""Validate production configuration governance conventions.

This gate intentionally avoids third-party JSON schema dependencies so it can
run in a fresh CI or server shell. It checks the parts that have caused real
operability drift in this project: expected files, service names, ports, and
documented schema ownership.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
ENVIRONMENTS = ("local", "docker", "production")
SERVICES = {
    "login": 9202,
    "room": 9302,
    "battle": 9303,
    "matchmaking": 9304,
    "leaderboard": 9305,
}


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str, category: str = "config") -> None:
    checks.append({"name": name, "category": category, "passed": passed, "detail": detail})


def load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:  # pragma: no cover - diagnostics path
        return {"__load_error__": str(exc)}


def require_file(checks: list[dict[str, Any]], path: Path, category: str = "config") -> bool:
    exists = path.is_file()
    add(checks, f"{path.relative_to(ROOT)} exists", exists, str(path.relative_to(ROOT)), category)
    return exists


def check_json_loaded(checks: list[dict[str, Any]], path: Path, doc: dict, category: str = "config") -> bool:
    loaded = "__load_error__" not in doc
    add(
        checks,
        f"{path.relative_to(ROOT)} parses as JSON",
        loaded,
        doc.get("__load_error__", "valid JSON object"),
        category,
    )
    return loaded


def check_gateway(checks: list[dict[str, Any]], env: str) -> None:
    path = ROOT / "config" / "environments" / env / "gateway.json"
    if not require_file(checks, path):
        return
    doc = load_json(path)
    if not check_json_loaded(checks, path, doc):
        return
    gateway = doc.get("gateway", {})
    backends = doc.get("backends", {})
    port = gateway.get("port")
    add(
        checks,
        f"{env} gateway.port valid",
        isinstance(port, int) and 1 <= port <= 65535,
        f"{path.relative_to(ROOT)} gateway.port={port}",
    )
    for service, expected_port in SERVICES.items():
        backend_key = "match" if service == "matchmaking" else service
        backend = backends.get(backend_key, {})
        add(
            checks,
            f"{env} gateway backend {backend_key} port",
            backend.get("port") == expected_port,
            f"{path.relative_to(ROOT)} backends.{backend_key}.port={backend.get('port')} expected={expected_port}",
        )


def check_backend(checks: list[dict[str, Any]], env: str, service: str, expected_port: int) -> None:
    path = ROOT / "config" / "environments" / env / f"{service}.json"
    if not require_file(checks, path):
        return
    doc = load_json(path)
    if not check_json_loaded(checks, path, doc):
        return
    service_doc = doc.get("service", {})
    add(
        checks,
        f"{env} {service} service.name",
        service_doc.get("name") == service,
        f"{path.relative_to(ROOT)} service.name={service_doc.get('name')} expected={service}",
    )
    add(
        checks,
        f"{env} {service} service.port",
        service_doc.get("port") == expected_port,
        f"{path.relative_to(ROOT)} service.port={service_doc.get('port')} expected={expected_port}",
    )
    version = service_doc.get("config_version")
    add(
        checks,
        f"{env} {service} config_version",
        isinstance(version, str) and bool(version),
        f"{path.relative_to(ROOT)} service.config_version={version}",
    )

    if service == "room":
        max_frames = doc.get("battle", {}).get("max_frames")
        add(
            checks,
            f"{env} room battle.max_frames",
            isinstance(max_frames, int) and max_frames > 0,
            f"{path.relative_to(ROOT)} battle.max_frames={max_frames}",
        )
    if service == "leaderboard":
        redis = doc.get("redis", {})
        redis_port = redis.get("port")
        add(
            checks,
            f"{env} leaderboard redis.port",
            isinstance(redis_port, int) and 1 <= redis_port <= 65535,
            f"{path.relative_to(ROOT)} redis.port={redis_port}",
        )


def check_schema_files(checks: list[dict[str, Any]]) -> None:
    require_file(checks, ROOT / "config" / "schemas" / "gateway.schema.json", "schema")
    require_file(checks, ROOT / "config" / "schemas" / "backend-service.schema.json", "schema")
    for service in SERVICES:
        require_file(checks, ROOT / "config" / "schemas" / f"{service}.schema.json", "schema")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def check_compose_drift(checks: list[dict[str, Any]]) -> None:
    compose = text(ROOT / "env/docker/docker-compose.yml")
    add(checks, "compose mounts governed config tree", "../../config:/app/config:ro" in compose, "gateway/backends use config tree", "drift")
    add(checks, "compose mounts backend tls certs profile", "../../certs:/app/certs:ro" in compose and "BACKEND_TLS_ENABLED" in compose, "backend TLS profile remains opt-in", "drift")
    for service in ("gateway", "login", "room", "battle", "matchmaking", "leaderboard"):
        env_name = "matchmaking" if service == "matchmaking" else service
        binary_service = "matchmaking" if service == "matchmaking" else service
        expected = f"CONFIG_PATH: /app/config/environments/docker/{env_name}.json"
        add(checks, f"compose {binary_service} CONFIG_PATH", expected in compose, expected, "drift")
    for service, port in SERVICES.items():
        backend_key = "matchmaking" if service == "matchmaking" else service
        add(checks, f"compose {backend_key} SERVICE_PORT", f'SERVICE_PORT: "{port}"' in compose, f"SERVICE_PORT={port}", "drift")


def check_k8s_drift(checks: list[dict[str, Any]]) -> None:
    gateway_manifest = text(ROOT / "env/k8s/gateway-deployment.yaml")
    production_gateway = load_json(ROOT / "config/environments/production/gateway.json")
    if not check_json_loaded(checks, ROOT / "config/environments/production/gateway.json", production_gateway, "drift"):
        return
    gateway = production_gateway.get("gateway", {})
    add(
        checks,
        "k8s gateway game port matches production config",
        f"containerPort: {gateway.get('port')}" in gateway_manifest and f"port: {gateway.get('port')}" in gateway_manifest,
        f"gateway.port={gateway.get('port')}",
        "drift",
    )
    add(
        checks,
        "k8s gateway management port matches production config",
        f"containerPort: {gateway.get('http_management_port')}" in gateway_manifest
        and f"port: {gateway.get('http_management_port')}" in gateway_manifest,
        f"gateway.http_management_port={gateway.get('http_management_port')}",
        "drift",
    )
    for backend, config_backend in production_gateway.get("backends", {}).items():
        manifest_name = "matchmaking" if backend == "match" else backend
        expected_host = f"{manifest_name}-backend.boost-gateway.svc.cluster.local"
        passed = expected_host in gateway_manifest and f'"port": {config_backend.get("port")}' in gateway_manifest
        add(checks, f"k8s gateway backend {backend} route", passed, f"{expected_host}:{config_backend.get('port')}", "drift")
    add(
        checks,
        "k8s gateway config map declares runtime governance keys",
        '"feature_flags"' in gateway_manifest and '"security_policy"' in gateway_manifest and '"tls"' in gateway_manifest,
        "feature_flags/security_policy/tls must not drift out of k8s profile",
        "drift",
    )
    login_manifest = text(ROOT / "env/k8s/login-backend-deployment.yaml")
    add(
        checks,
        "k8s login backend tls secret profile",
        "secretName: backend-tls" in login_manifest and "BACKEND_TLS_ENABLED" in login_manifest,
        "login backend TLS Secret mount is explicit and default-off",
        "drift",
    )


def check_helm_drift(checks: list[dict[str, Any]]) -> None:
    values = text(ROOT / "env/k8s/helm/boost-gateway/values.yaml")
    add(checks, "helm tls defaults disabled", "tls:" in values and "enabled: false" in values, "tls.enabled=false", "drift")
    add(checks, "helm backend tls defaults disabled", "backend:" in values and "secretName: backend-tls" in values, "backend tls profile", "drift")
    add(checks, "helm gateway port matches production config", "port: 9201" in values and "mgmtPort: 9080" in values, "gateway ports", "drift")
    for service, port in SERVICES.items():
        key = "match" if service == "matchmaking" else service
        add(checks, f"helm {key} port matches backend", f"port: {port}" in values, f"{key}.port={port}", "drift")


def check_docs(checks: list[dict[str, Any]]) -> None:
    runbook = text(ROOT / "docs/deployment/production-configuration-runbook.md")
    runbook_lower = runbook.lower()
    for token in (
        "python3 scripts/check_config_governance.py",
        "Config Drift",
    ):
        add(checks, f"configuration runbook documents {token}", token in runbook, token, "docs")
    add(checks, "configuration runbook documents drift", "drift" in runbook_lower, "drift", "docs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-path", type=Path, default=ROOT / "runtime/validation/config-governance-summary.json")
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    check_schema_files(checks)
    for env in ENVIRONMENTS:
        check_gateway(checks, env)
        for service, port in SERVICES.items():
            check_backend(checks, env, service, port)
    require_file(checks, ROOT / "config" / "secrets" / ".env.example", "secret")
    check_compose_drift(checks)
    check_k8s_drift(checks)
    check_helm_drift(checks)
    check_docs(checks)

    failed = [check for check in checks if not check["passed"]]
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "failed_category": failed[0]["category"] if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "checks": checks,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"configuration governance gate: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks)-len(failed)}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    if failed:
        for check in failed[:20]:
            print(f"  - {check['category']} / {check['name']}: {check['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
