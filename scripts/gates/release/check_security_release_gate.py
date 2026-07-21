#!/usr/bin/env python3
"""Validate production security and audit release assumptions."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import urlparse
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def check_login_backend_config(root: Path, errors: list[str], warnings: list[str]) -> None:
    config_path = root / "config" / "environments" / "production" / "login.json"
    if not config_path.exists():
        errors.append("missing config/environments/production/login.json")
        return

    config = load_json(config_path)
    auth = config.get("auth", {})
    mode = str(auth.get("mode", "dev"))
    if mode != "external-jwt":
        errors.append(
            "config/environments/production/login.json must use auth.mode=external-jwt; "
            "the production login backend validates externally issued RS256 JWTs only"
        )
    public_sources = [
        bool(str(auth.get("jwt_public_key_pem", ""))),
        isinstance(auth.get("jwt_key_ring"), dict) and bool(auth.get("jwt_key_ring")),
        bool(str(auth.get("jwks_uri", ""))),
    ]
    if sum(public_sources) != 1:
        errors.append("production login config must declare exactly one RS256 public key source")
    if public_sources[2]:
        uri = urlparse(str(auth.get("jwks_uri", "")))
        allowed_hosts = {
            host.strip().lower()
            for host in str(auth.get("jwks_allowed_hosts", "")).split(",")
            if host.strip()
        }
        if uri.scheme != "https" or not uri.hostname or uri.hostname.lower() not in allowed_hosts:
            errors.append("production JWKS URI must use HTTPS and an explicitly allowlisted host")
        if auth.get("jwks_allow_loopback_http") is True:
            errors.append("production login config must not allow loopback HTTP JWKS")
    if str(auth.get("jwt_secret", "")) or str(auth.get("jwt_private_key_pem", "")):
        errors.append("production login config must not declare local JWT signing material")
    if not str(auth.get("jwt_issuer", "")) or not str(auth.get("jwt_audience", "")):
        errors.append("production login config must declare JWT issuer and audience")
    if mode not in {"external-jwt", "jwt", "prod", "production"}:
        errors.append(f"unsupported login auth.mode: {mode}")


def check_source_contracts(root: Path, errors: list[str]) -> None:
    login_header = root / "include" / "v2" / "login" / "login_backend_service.h"
    login_source = root / "src" / "v2" / "login" / "login_backend_service.cpp"
    login_main = root / "examples" / "v2_login_backend" / "main.cpp"
    config_source = root / "src" / "app" / "config.cpp"
    admin_doc = root / "docs" / "archive" / "history-v1" / "v1-admin-audit-rules.md"
    current_state_doc = root / "docs" / "current-state.md"
    release_governance_doc = root / "docs" / "release-governance.md"

    required_snippets = {
        login_header: ["production_auth_required", "RS256", "jwt_key_ring", "jwks_http"],
        login_source: ["external_identity_provider_required", "require_expiration", "production auth requires", "StaticJwtKeyResolver", "JwksKeyResolver"],
        login_main: ["resolve_backend_config_path", "load_backend_service_config"],
        config_source: ["V2_LOGIN_AUTH_MODE", "V2_LOGIN_JWT_PUBLIC_KEY", "V2_LOGIN_JWT_KEY_RING", "V2_LOGIN_JWKS_URI"],
        admin_doc: ["admin_invoke", "admin_denied", "ACL"],
        release_governance_doc: ["legacy demo admin surface", "不代表当前 v2 主线提供正式 admin 控制面"],
        current_state_doc: ["admin_service", "legacy-v1 / demo-only", "不进入默认 gate"],
    }
    for path, snippets in required_snippets.items():
        if not path.exists():
            errors.append(f"missing security evidence path: {path.relative_to(root)}")
            continue
        text = path.read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                errors.append(f"{path.relative_to(root)} missing required security marker: {snippet}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict-config", action="store_true", help="Treat dev config defaults as release failures")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]
    errors: list[str] = []
    warnings: list[str] = []

    check_login_backend_config(root, errors, warnings)
    check_source_contracts(root, errors)

    if args.strict_config:
        errors.extend(warnings)
        warnings = []

    if warnings:
        print("security release gate warnings:")
        for warning in warnings:
            print(f"- {warning}")

    if errors:
        print("security release gate failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("security release gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
