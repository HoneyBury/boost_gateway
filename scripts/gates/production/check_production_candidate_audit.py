#!/usr/bin/env python3
"""Validate the production-candidate evidence map for P6."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]


REQUIRED_FILES = {
    "deployment-runbook": "docs/deployment/production-deployment-runbook.md",
    "operations-runbook": "docs/deployment/production-operations-runbook.md",
    "sdk-runbook": "sdk/docs/README.md",
    "sdk-compatibility-matrix": "sdk/docs/compatibility.md",
    "fixed-runner-playbook": "docs/fixed-runner-playbook.md",
    "release-governance": "docs/release-governance.md",
    "current-state": "docs/current-state.md",
    "mainline-execution-plan": "docs/mainline-execution-plan.md",
    "platform-production-boundaries": "docs/platform-production-boundaries.json",
    "production-evidence-manifest": "docs/production/production-candidate-evidence-manifest.json",
    "deploy-operability-gate": "scripts/gates/production/check_deploy_operability.py",
    "monitoring-operability-gate": "scripts/gates/production/check_monitoring_operability.py",
    "sdk-distribution-gate": "scripts/gates/sdk/check_sdk_distribution.py",
    "sdk-business-flow-gate": "scripts/gates/sdk/verify_sdk_business_flow.py",
    "sdk-full-flow-gate": "scripts/gates/sdk/verify_sdk_full_flow_client.py",
    "p5-resilience-gate": "scripts/gates/production/verify_production_resilience_gate.py",
    "p6-production-evidence-gate": "scripts/gates/production/verify_production_evidence_gate.py",
    "h0-h5-production-hardening-gate": "scripts/gates/production/check_production_hardening_gate.py",
    "production-gates-workflow": ".github/workflows/production-gates.yml",
}

REQUIRED_TEXT = {
    "docs/current-state.md": [
        "v3.6.2",
        "默认生产主链仍是 SDK + TCP gateway",
        "mainline-execution-plan.md",
    ],
    "docs/mainline-execution-plan.md": [
        "v3.6.2 release archive",
        "30 天不可变验证",
        "完成恢复演练",
    ],
    "sdk/docs/compatibility.md": [
        "v3.6.x",
        "v4.2.0",
        "BOOST_GATEWAY_SDK_LIBRARY",
        "disconnect callback",
    ],
    "docs/release-governance.md": [
        "verify_production_resilience_gate.py",
        "verify_production_evidence_gate.py",
        "verify_production_candidate_evidence.py",
        "verify_preprod_recovery_drill.py",
        "production_resilience_gate",
        "production_evidence_gate",
        "monitoring_operability_gate",
        "control_plane_operator_gate",
    ],
    "docs/fixed-runner-playbook.md": [
        "production-gates.yml",
        "p6-candidate-audit-summary.json",
    ],
    ".github/workflows/production-gates.yml": [
        "scripts/gates/production/verify_production_evidence_gate.py",
        "scripts/gates/production/verify_production_resilience_gate.py",
        "scripts/tools/render_validation_summary.py",
        "actions/upload-artifact@v4",
        "runtime/validation/production-evidence-summary.json",
        "runtime/validation/production-resilience-summary.json",
        "runtime/validation/p6-candidate-audit-summary.json",
    ],
    "docs/platform-production-boundaries.json": [
        "linux-x64",
        "linux-arm64",
        "macos-arm64",
        "v3.6.2",
    ],
}
def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def read_text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def validate_required_files(checks: list[dict[str, Any]]) -> None:
    for name, relative in sorted(REQUIRED_FILES.items()):
        path = REPO_ROOT / relative
        add_check(checks, f"file:{name}", path.exists(), relative)


def validate_required_text(checks: list[dict[str, Any]]) -> None:
    for relative, tokens in sorted(REQUIRED_TEXT.items()):
        path = REPO_ROOT / relative
        if not path.exists():
            add_check(checks, f"text:{relative}:exists", False, f"{relative} is missing")
            continue
        text = read_text(relative)
        for token in tokens:
            add_check(
                checks,
                f"text:{relative}:{token}",
                token in text,
                f"{relative} contains {token}",
            )


def validate_summary_contracts(checks: list[dict[str, Any]]) -> None:
    p6_gate = read_text("scripts/gates/production/verify_production_evidence_gate.py")
    p5_gate = read_text("scripts/gates/production/verify_production_resilience_gate.py")
    for key in ("passed", "failed_category", "failed_step", "steps"):
        add_check(
            checks,
            f"summary:p6:{key}",
            f'"{key}"' in p6_gate,
            f"P6 summary includes {key}",
        )
        add_check(
            checks,
            f"summary:p5:{key}",
            f'"{key}"' in p5_gate,
            f"P5 summary includes {key}",
        )
    for sub_summary in (
        "p6-stability-soak-summary.json",
        "p6-data-recovery-summary.json",
        "p6-specialized-e2e-summary.json",
        "p6-candidate-audit-summary.json",
    ):
        add_check(
            checks,
            f"summary:p6-child:{sub_summary}",
            sub_summary in p6_gate,
            f"P6 gate writes or references {sub_summary}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/production-candidate-audit-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path

    checks: list[dict[str, Any]] = []
    validate_required_files(checks)
    validate_required_text(checks)
    validate_summary_contracts(checks)

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "passed": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(
        f"production candidate audit: {'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
