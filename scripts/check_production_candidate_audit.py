#!/usr/bin/env python3
"""Validate the production-candidate evidence map for P6."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


REQUIRED_FILES = {
    "deployment-runbook": "docs/production-deployment-runbook.md",
    "operations-runbook": "docs/production-operations-runbook.md",
    "sdk-runbook": "sdk/docs/README.md",
    "sdk-compatibility-matrix": "sdk/docs/compatibility.md",
    "release-checklist": "docs/v3-release-checklist.md",
    "reliability-matrix": "docs/reliability-matrix.md",
    "fixed-runner-playbook": "docs/fixed-runner-playbook.md",
    "production-evidence-runner": "docs/production-evidence-runner.md",
    "production-candidate-hardening-plan": "docs/archive/plans/production-candidate-hardening-plan.md",
    "p5-resilience-release": "docs/archive/releases/v3.3.2-p5-production-resilience.md",
    "p6-production-evidence-release": "docs/archive/releases/v3.3.2-p6-production-evidence.md",
    "deploy-operability-gate": "scripts/check_deploy_operability.py",
    "monitoring-operability-gate": "scripts/check_monitoring_operability.py",
    "sdk-distribution-gate": "scripts/check_sdk_distribution.py",
    "sdk-business-flow-gate": "scripts/verify_sdk_business_flow.py",
    "sdk-full-flow-gate": "scripts/verify_sdk_full_flow_client.py",
    "p5-resilience-gate": "scripts/verify_production_resilience_gate.py",
    "p6-production-evidence-gate": "scripts/verify_production_evidence_gate.py",
    "h0-h5-production-hardening-gate": "scripts/check_production_hardening_gate.py",
    "production-evidence-workflow": ".github/workflows/production-evidence.yml",
    "production-resilience-workflow": ".github/workflows/production-resilience.yml",
}

REQUIRED_TEXT = {
    "docs/archive/plans/production-stabilization-roadmap.md": [
        "当前阶段已经完成 P0-P6 收束",
        "docs/archive/plans/production-candidate-hardening-plan.md",
        "生产部署 runbook、运维 runbook、SDK 接入文档和 release checklist 与实际代码一致",
        "P6 生产证据在固定 runner 上可以稳定通过",
        "SDK 能以稳定线程模型和版本兼容策略被客户端项目接入",
    ],
    "docs/archive/plans/production-candidate-hardening-plan.md": [
        "H0：固定 Runner 常态化",
        "H1：长稳与资源曲线",
        "H2：容量边界与退化阈值",
        "H3：Kubernetes 发布演练",
        "H4：观测闭环增强",
        "H5：SDK 企业接入包",
    ],
    "docs/archive/releases/v3.3.2-h0-h5-production-hardening.md": [
        "H0 固定 Runner 常态化",
        "H1 长稳与资源曲线",
        "H2 容量边界与退化阈值",
        "H3 Kubernetes 发布演练",
        "H4 观测闭环增强",
        "H5 SDK 企业接入包",
    ],
    "sdk/docs/compatibility.md": [
        "v3.3.2",
        "v4.1.0",
        "BOOST_GATEWAY_SDK_LIBRARY",
        "disconnect callback",
    ],
    "docs/v3-release-checklist.md": [
        "P6 生产证据聚合入口",
        "P5 长稳故障回滚门禁",
        "P4 SDK 业务闭环",
        "P4 SDK 示例联调",
        "P6 生产证据聚合失败",
    ],
    "docs/reliability-matrix.md": [
        "production_resilience_gate",
        "production_evidence_gate",
        "monitoring_operability_gate",
        "sdk_enterprise_client_gate",
        "control_plane_operator_gate",
    ],
    "docs/fixed-runner-playbook.md": [
        "Production Resilience / P5",
        "P6 Production Evidence",
        "production-resilience.yml",
        "production-evidence.yml",
        "p6-candidate-audit-summary.json",
    ],
    "docs/production-evidence-runner.md": [
        "production-evidence.yml",
        "Redis + kind + observability runtime",
        "Full evidence",
        "p6-candidate-audit-summary.json",
    ],
    ".github/workflows/production-evidence.yml": [
        "scripts/verify_production_evidence_gate.py",
        "scripts/render_validation_summary.py",
        "actions/upload-artifact@v4",
        "runtime/validation/production-evidence-summary.json",
        "runtime/validation/p6-candidate-audit-summary.json",
    ],
    ".github/workflows/production-resilience.yml": [
        "scripts/verify_production_resilience_gate.py",
        "scripts/render_validation_summary.py",
        "actions/upload-artifact@v4",
        "runtime/validation/production-resilience-summary.json",
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
    p6_gate = read_text("scripts/verify_production_evidence_gate.py")
    p5_gate = read_text("scripts/verify_production_resilience_gate.py")
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
