#!/usr/bin/env python3
"""Validate the current TLS/mTLS production profile boundary."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout)


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def file_contains(path: Path, token: str) -> bool:
    return path.exists() and token in path.read_text(encoding="utf-8")


def validate_gateway_config(checks: list[dict[str, Any]], config_path: Path) -> None:
    doc = load_json(config_path)
    feature_flags = doc.get("feature_flags", {})
    tls_flag = feature_flags.get("v3_tls_enabled", {})
    security = doc.get("security_policy", {})
    tls = doc.get("tls", {})

    add(
        checks,
        "tls-feature-flag-default-off",
        tls_flag.get("enabled") is False and int(tls_flag.get("rollout_percentage", -1)) == 0,
        "default production config keeps v3_tls_enabled disabled",
    )
    add(
        checks,
        "tls-global-require-default-off",
        security.get("require_tls") is False,
        "default production config keeps security_policy.require_tls=false",
    )
    add(
        checks,
        "tls-leaderboard-mtls-policy",
        isinstance(security.get("leaderboard"), dict)
        and security["leaderboard"].get("tls_required") is True
        and security["leaderboard"].get("mtls_required") is True,
        "leaderboard policy remains the sensitive mTLS target",
    )
    add(
        checks,
        "tls-cert-paths-declared",
        all(tls.get(key) for key in ("cert_chain_path", "private_key_path", "ca_cert_path")),
        "gateway config declares certificate, key and CA paths",
    )


def validate_source_boundary(checks: list[dict[str, Any]]) -> None:
    add(
        checks,
        "backend-connection-has-tls-handshake",
        file_contains(ROOT / "src/v2/service/backend_connection.cpp", "tls_handshake")
        and file_contains(ROOT / "include/v2/service/backend_connection.h", "tls_config"),
        "backend client connection supports TLS handshake when options include tls_config",
    )
    add(
        checks,
        "backend-server-has-tls-listener",
        file_contains(ROOT / "src/v2/service/backend_server.cpp", "handle_tls_session")
        and file_contains(ROOT / "include/v2/service/backend_server.h", "BackendServerOptions")
        and file_contains(ROOT / "src/v2/login/login_backend_service.cpp", "tls_config_"),
        "backend server supports opt-in TLS listener and login service can consume backend TLS config",
    )
    add(
        checks,
        "all-backend-examples-wire-tls-config",
        all(
            file_contains(ROOT / path, "set_tls_config(config.tls_config)")
            for path in (
                "examples/v2_room_backend/main.cpp",
                "examples/v2_battle_backend/main.cpp",
                "examples/v2_match_backend/main.cpp",
                "examples/v2_leaderboard_backend/main.cpp",
            )
        )
        and file_contains(ROOT / "examples/v2_login_backend/main.cpp", "options.tls_config = config.tls_config"),
        "all backend entrypoints consume governed backend TLS config",
    )
    add(
        checks,
        "backend-tls-full-flow-test",
        file_contains(ROOT / "tests/v2/integration/backend_routing_test.cpp", "BackendTlsListenerCompletesLoginRequest"),
        "integration test covers backend TLS listener plus BackendConnection request/response flow",
    )
    add(
        checks,
        "gateway-security-policy-gates-tls",
        file_contains(ROOT / "src/v2/gateway/gateway_service_bridge.cpp", "v3_tls_enabled")
        and file_contains(ROOT / "src/v2/gateway/gateway_service_bridge.cpp", "security_policy_->require_tls"),
        "gateway bridge gates TLS by security policy and feature flag",
    )
    add(
        checks,
        "client-ingress-plain-boundary-documented",
        file_contains(ROOT / "docs/tls-mtls-runbook.md", "plain TCP")
        or file_contains(ROOT / "docs/current-state.md", "plain TCP")
        or file_contains(ROOT / "docs/tls-mtls-runbook.md", "默认生产链路仍是 plain TCP")
        or file_contains(ROOT / "docs/current-state.md", "默认生产结论仍是 plain TCP"),
        "docs explicitly avoid claiming client ingress TLS is default production behavior",
    )
    add(
        checks,
        "certificate-generator-exists",
        (ROOT / "scripts/tools/gen_certs.py").exists() and (ROOT / "scripts/tools/gen_certs.sh").exists(),
        "dev certificate generator exists",
    )
    add(
        checks,
        "docker-backend-tls-profile",
        file_contains(ROOT / "env/docker/docker-compose.yml", "BACKEND_TLS_ENABLED")
        and file_contains(ROOT / "env/docker/docker-compose.yml", "../../certs:/app/certs:ro"),
        "Docker Compose exposes an opt-in backend TLS profile and cert mount",
    )
    add(
        checks,
        "k8s-backend-tls-secret-profile",
        file_contains(ROOT / "env/k8s/login-backend-deployment.yaml", "secretName: backend-tls")
        and file_contains(ROOT / "env/k8s/login-backend-deployment.yaml", "BACKEND_TLS_ENABLED"),
        "Kubernetes login backend has an opt-in TLS Secret mount profile",
    )


def validate_certs(checks: list[dict[str, Any]], generate: bool, cert_dir: Path) -> None:
    certs = [cert_dir / "ca.crt", cert_dir / "server.crt", cert_dir / "server.key"]
    if generate and not all(path.exists() for path in certs):
        result = run([
            sys.executable,
            str(ROOT / "scripts/tools/gen_certs.py"),
            "--output-dir",
            str(cert_dir),
        ])
        add(
            checks,
            "generate-dev-certs",
            result.returncode == 0,
            (result.stdout + result.stderr)[-2000:],
        )
    add(
        checks,
        "dev-certs-present",
        all(path.exists() for path in certs),
        f"{cert_dir}/ca.crt, server.crt and server.key are present after optional generation",
    )
    openssl = run(["openssl", "x509", "-in", str(cert_dir / "server.crt"), "-noout", "-subject", "-issuer"])
    add(
        checks,
        "server-cert-readable",
        openssl.returncode == 0 and "subject=" in openssl.stdout and "issuer=" in openssl.stdout,
        (openssl.stdout + openssl.stderr)[-2000:],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/environments/production/gateway.json")
    parser.add_argument("--generate-dev-certs", action="store_true")
    parser.add_argument(
        "--cert-dir",
        type=Path,
        default=ROOT / "certs",
        help="Directory containing generated development certificates.",
    )
    parser.add_argument("--summary-path", type=Path, default=ROOT / "runtime/validation/tls-profile-summary.json")
    args = parser.parse_args()

    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    cert_dir = args.cert_dir if args.cert_dir.is_absolute() else ROOT / args.cert_dir
    checks: list[dict[str, Any]] = []
    validate_gateway_config(checks, args.config)
    validate_source_boundary(checks)
    validate_certs(checks, args.generate_dev_certs, cert_dir)

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "tls_profile" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "config": str(args.config),
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {
            "summary_path": str(summary_path),
            "config_path": str(args.config),
            "cert_dir": str(cert_dir),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"tls profile gate: {'PASS' if summary['overall_pass'] else 'FAIL'} ({len(checks)-len(failed)}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
