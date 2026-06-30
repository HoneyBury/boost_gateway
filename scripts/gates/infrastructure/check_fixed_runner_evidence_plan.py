#!/usr/bin/env python3
"""Validate fixed-runner workflow evidence wiring and required summary paths."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LINUX_LOCKFILE = "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock"
LINUX_PROFILE = "conan/profiles/linux-gcc-x64"

WORKFLOW_REQUIREMENTS = {
    "conan_validate": {
        "path": ".github/workflows/conan-validate.yml",
        "tokens": (
            LINUX_LOCKFILE,
            LINUX_PROFILE,
            "conan install",
            "--lockfile",
            "BOOST_USE_CONAN_DEPS=ON",
            "--target \"$target\"",
            "actions/upload-artifact@v4",
        ),
        "summaries": (),
    },
    "release_baseline": {
        "path": ".github/workflows/release-baseline.yml",
        "tokens": (
            LINUX_LOCKFILE,
            LINUX_PROFILE,
            "enable_conan_validation",
            "build/conan-release-baseline-cmake",
            "runtime/validation/release-baseline-summary.json",
            "runtime/perf/release-baseline/summary.json",
            "actions/upload-artifact@v4",
        ),
        "summaries": (
            "runtime/validation/release-baseline-summary.json",
            "runtime/perf/release-baseline/summary.json",
        ),
    },
    "long_soak_capacity": {
        "path": ".github/workflows/long-soak-capacity.yml",
        "tokens": (
            LINUX_LOCKFILE,
            LINUX_PROFILE,
            "build/conan-long-soak-capacity-cmake",
            "runtime/validation/long-soak-capacity-summary.json",
            "runtime/validation/fixed-runner-release-capacity-summary.json",
            "runtime/perf/fixed-runner-capacity/**",
            "runtime/perf/fixed-runner-business-capacity/**",
            "actions/upload-artifact@v4",
        ),
        "summaries": (
            "runtime/validation/long-soak-capacity-summary.json",
            "runtime/validation/fixed-runner-release-capacity-summary.json",
        ),
    },
    "production_evidence": {
        "path": ".github/workflows/production-evidence.yml",
        "tokens": (
            LINUX_LOCKFILE,
            LINUX_PROFILE,
            "build/conan-production-evidence-cmake",
            "runtime/validation/production-evidence-summary.json",
            "runtime/validation/r2-production-evidence-manifest-fixed-runner-summary.json",
            "runtime/validation/r3-production-readiness-report-summary.json",
            "actions/upload-artifact@v4",
        ),
        "summaries": (
            "runtime/validation/production-evidence-summary.json",
            "runtime/validation/r2-production-evidence-manifest-fixed-runner-summary.json",
        ),
    },
}

DOC_TOKENS = (
    "Ubuntu Fixed-Runner 第一批执行矩阵",
    "不能用本机 smoke 或 `--allow-missing` 结果替代",
    "python3 scripts/check_fixed_runner_evidence_plan.py",
    "Linux `nosqlite` lockfile",
    "overall_pass=true",
)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def exists(relative: str) -> bool:
    return (ROOT / relative).exists()


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/fixed-runner-evidence-plan-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    checks: list[dict[str, Any]] = []
    add(checks, "lockfile:linux-nosqlite-exists", exists(LINUX_LOCKFILE), f"{LINUX_LOCKFILE} exists")
    add(checks, "profile:linux-gcc-x64-exists", exists(LINUX_PROFILE), f"{LINUX_PROFILE} exists")

    expected_summaries: list[str] = []
    for name, requirement in WORKFLOW_REQUIREMENTS.items():
        path = str(requirement["path"])
        add(checks, f"workflow:{name}:exists", exists(path), f"{path} exists")
        content = read(path) if exists(path) else ""
        for token in requirement["tokens"]:
            add(checks, f"workflow:{name}:token:{token}", token in content, f"{path} includes {token}")
        for summary in requirement["summaries"]:
            expected_summaries.append(summary)
            add(checks, f"workflow:{name}:uploads:{summary}", summary in content, f"{path} uploads {summary}")

    fixed_runner_doc = read("docs/fixed-runner-playbook.md")
    for token in DOC_TOKENS:
        add(checks, f"docs:fixed-runner:{token}", token in fixed_runner_doc, f"fixed-runner playbook mentions {token}")

    manifest = json.loads(read("docs/production-candidate-evidence-manifest.json"))
    manifest_text = json.dumps(manifest, ensure_ascii=False)
    for summary in (
        "runtime/validation/long-soak-capacity-summary.json",
        "runtime/validation/fixed-runner-release-capacity-summary.json",
        "runtime/validation/preprod-recovery-drill-summary.json",
        "runtime/validation/tls-preprod-multi-run-summary.json",
    ):
        add(checks, f"manifest:requires:{summary}", summary in manifest_text, f"manifest references {summary}")

    validation_contract = read("scripts/gates/governance/check_validation_summary_contract.py")
    for summary in expected_summaries:
        add(
            checks,
            f"summary-contract:knows:{summary}",
            summary in validation_contract or summary.endswith("/summary.json"),
            f"validation summary contract can audit {summary}",
        )

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "fixed_runner_evidence_plan" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "expected_fixed_runner_summaries": sorted(set(expected_summaries)),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(f"fixed-runner evidence plan: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
