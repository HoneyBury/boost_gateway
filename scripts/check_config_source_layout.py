#!/usr/bin/env python3
"""Validate configuration source-of-truth and legacy config boundaries."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CURRENT = [
    "env/docker/docker-compose.yml",
    "env/docker/Dockerfile.gateway",
    "env/docker/Dockerfile.backend",
    "env/k8s/gateway-deployment.yaml",
    "env/k8s/login-backend-deployment.yaml",
    "env/k8s/room-backend-deployment.yaml",
    "env/k8s/battle-backend-deployment.yaml",
    "env/k8s/matchmaking-backend-deployment.yaml",
    "env/k8s/leaderboard-backend-deployment.yaml",
    "env/monitoring/prometheus.yml",
    "env/monitoring/prometheus-alerts.yml",
    "env/monitoring/grafana-dashboard.json",
    "env/redis/redis.conf",
]
REMOVED_LEGACY_PATHS = [
    "docker-compose.yml",
    "docker-compose.operator.yml",
    "prometheus/alerts.yml",
    "grafana/dashboard.json",
    "k8s/crds/gatewayservers.yaml",
    "k8s/helm/gateway-server/Chart.yaml",
    "k8s/helm/gateway-server/values.yaml",
]


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/config-source-layout-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    checks: list[dict[str, Any]] = []

    env_readme = (ROOT / "env/README.md").read_text(encoding="utf-8")
    add(checks, "env-readme-source-of-truth", "source of truth" in env_readme.lower(), "env README declares source of truth")
    add(checks, "env-readme-legacy-boundary", "legacy" in env_readme.lower(), "env README declares legacy boundary")

    for relative in REQUIRED_CURRENT:
        add(checks, f"current:{relative}", (ROOT / relative).exists(), "current config exists")

    for relative in REMOVED_LEGACY_PATHS:
        add(
            checks,
            f"legacy-removed:{relative}",
            not (ROOT / relative).exists(),
            "removed legacy path must stay absent",
        )

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "config_source_layout" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path), "env_readme_path": str(ROOT / "env/README.md")},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"config source layout: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
