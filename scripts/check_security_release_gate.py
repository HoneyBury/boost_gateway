#!/usr/bin/env python3
"""Validate production security and audit release assumptions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def check_login_backend_config(root: Path, errors: list[str], warnings: list[str]) -> None:
    config_path = root / "config" / "login_backend.json"
    if not config_path.exists():
        errors.append("missing config/login_backend.json")
        return

    config = load_json(config_path)
    auth = config.get("login_backend", {}).get("auth", {})
    provider = str(auth.get("provider", "dev"))
    if provider == "dev":
        warnings.append(
            "config/login_backend.json uses dev auth; production must set "
            "V2_LOGIN_AUTH_MODE=production and V2_LOGIN_JWT_SECRET or V2_LOGIN_JWT_PUBLIC_KEY"
        )
    elif provider not in {"jwt", "dev"}:
        errors.append(f"unsupported login_backend.auth.provider: {provider}")


def check_source_contracts(root: Path, errors: list[str]) -> None:
    login_header = root / "include" / "v2" / "login" / "login_backend_service.h"
    login_source = root / "src" / "v2" / "login" / "login_backend_service.cpp"
    login_main = root / "examples" / "v2_login_backend" / "main.cpp"
    admin_doc = root / "docs" / "v1-admin-audit-rules.md"
    admin_source = root / "src" / "game" / "gateway" / "admin_service.cpp"

    required_snippets = {
        login_header: ["production_auth_required"],
        login_source: ["jwt_required", "production auth requires"],
        login_main: ["V2_LOGIN_AUTH_MODE", "V2_LOGIN_JWT_SECRET", "V2_LOGIN_JWT_PUBLIC_KEY"],
    admin_doc: ["admin_invoke", "admin_denied", "默认启用最小 ACL"],
    admin_source: ["AUDIT_LOG(\"admin_invoke\"", "admin_denied", "is_authorized", "payload_excerpt"],
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
    root = Path(__file__).resolve().parent.parent
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
