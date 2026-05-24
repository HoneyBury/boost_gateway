#!/usr/bin/env python3
"""Validate static BoostGateway Operator manifests used by the P5 gate."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_COMPONENTS = ["gateway", "login", "room", "battle", "match", "leaderboard"]
REQUIRED_CONDITIONS = ["Ready", "Progressing", "Degraded", "TLSReady"]
REQUIRED_STATUS_FIELDS = [
    "phase",
    "readyReplicas",
    "desiredReplicas",
    "components",
    "conditions",
]

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def has_sequence(text: str, *tokens: str) -> bool:
    position = 0
    for token in tokens:
        found = text.find(token, position)
        if found < 0:
            return False
        position = found + len(token)
    return True


def has_resource(text: str, resource: str) -> bool:
    return bool(re.search(rf'\b{re.escape(resource)}\b', text))


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str = "") -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def validate_crd(operator_dir: Path, checks: list[dict[str, Any]]) -> None:
    path = operator_dir / "config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml"
    text = read_text(path)
    add_check(checks, "crd-kind", "kind: CustomResourceDefinition" in text)
    add_check(checks, "crd-name", "name: boostgatewayclusters.gateway.boost.io" in text)
    add_check(checks, "crd-status-subresource", has_sequence(text, "subresources:", "status: {}"))
    add_check(checks, "crd-spec-components", all(re.search(rf"^\s*{name}:\s*$", text, re.MULTILINE) for name in REQUIRED_COMPONENTS))
    add_check(checks, "crd-status-fields", all(re.search(rf"^\s*{field}:\s*$", text, re.MULTILINE) for field in REQUIRED_STATUS_FIELDS))
    add_check(
        checks,
        "crd-component-status-shape",
        all(re.search(rf"^\s*{field}:\s*$", text, re.MULTILINE)
            for field in ["name", "kind", "desiredReplicas", "readyReplicas", "availableReplicas"]),
    )


def validate_rbac(operator_dir: Path, checks: list[dict[str, Any]]) -> None:
    text = read_text(operator_dir / "config/rbac/role.yaml")
    add_check(checks, "rbac-service-account", "name: boostgateway-operator-controller-manager" in text)
    add_check(checks, "rbac-role-binding", "kind: ClusterRoleBinding" in text and "name: boostgateway-operator-manager-role" in text)
    required_resources = {
        "boostgatewayclusters",
        "boostgatewayclusters/status",
        "deployments",
        "statefulsets",
        "services",
        "configmaps",
        "secrets",
        "certificates",
        "events",
    }
    missing = sorted(resource for resource in required_resources if not has_resource(text, resource))
    add_check(checks, "rbac-required-resources", not missing, f"missing={missing}")


def validate_manager(operator_dir: Path, checks: list[dict[str, Any]]) -> None:
    text = read_text(operator_dir / "config/manager/manager.yaml")
    add_check(checks, "manager-deployment", "kind: Deployment" in text)
    add_check(checks, "manager-service-account", "serviceAccountName: boostgateway-operator-controller-manager" in text)
    add_check(checks, "manager-probe-port", has_sequence(text, "containerPort: 8081", "name: probes"))
    add_check(checks, "manager-metrics-port", has_sequence(text, "containerPort: 8080", "name: metrics"))
    add_check(checks, "manager-health-probes", "readinessProbe:" in text and "livenessProbe:" in text)


def validate_sample(operator_dir: Path, checks: list[dict[str, Any]]) -> None:
    text = read_text(operator_dir / "config/samples/gateway_v1alpha1_boostgatewaycluster.yaml")
    add_check(checks, "sample-kind", "kind: BoostGatewayCluster" in text)
    add_check(checks, "sample-components", all(re.search(rf"^\s*{name}:\s*$", text, re.MULTILINE) for name in REQUIRED_COMPONENTS))
    ports_ok = all(re.search(rf"^\s*{name}:\s*(?:\n\s+.*)*?\n\s+port:\s*[1-9][0-9]*", text, re.MULTILINE) for name in REQUIRED_COMPONENTS)
    add_check(checks, "sample-component-ports", ports_ok)
    add_check(checks, "sample-gateway-management-port", bool(re.search(r"managementPort:\s*[1-9][0-9]*", text)))


def write_summary(path: Path, checks: list[dict[str, Any]]) -> int:
    failed = [check for check in checks if not check["passed"]]
    summary = {
        "passed": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"operator manifests: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check.get('detail', '')}")
        return 1
    print(f"summary: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-dir", type=Path, default=REPO_ROOT / "operator/boostgateway-operator")
    parser.add_argument("--summary-path", type=Path, default=REPO_ROOT / "runtime/validation/operator-manifests-summary.json")
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    operator_dir = args.operator_dir if args.operator_dir.is_absolute() else REPO_ROOT / args.operator_dir
    validate_crd(operator_dir, checks)
    validate_rbac(operator_dir, checks)
    validate_manager(operator_dir, checks)
    validate_sample(operator_dir, checks)
    return write_summary(args.summary_path, checks)


if __name__ == "__main__":
    raise SystemExit(main())
