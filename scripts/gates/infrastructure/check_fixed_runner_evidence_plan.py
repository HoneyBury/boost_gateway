#!/usr/bin/env python3
"""Validate fixed-runner workflow evidence wiring, runner policy, and required summary paths."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
LINUX_LOCKFILE = "conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock"
LINUX_PROFILE = "conan/profiles/linux-gcc-x64"
SELF_HOSTED_LINUX_RUNNER = '["self-hosted","Linux","X64"]'
GITHUB_HOSTED_UBUNTU_RUNNER = '"ubuntu-latest"'

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
    "release": {
        "path": ".github/workflows/release.yml",
        "tokens": (
            LINUX_LOCKFILE,
            LINUX_PROFILE,
            "conan install",
            "--lockfile",
            "enable_conan_validation",
            "conan-preflight",
        ),
        "summaries": ("runtime/validation/release-baseline-summary.json",),
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

README_TOKENS = (
    "GitHub-hosted `ubuntu-latest`",
    "fixed-runner 证据",
    "runner-matrix.json",
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

    runner_matrix_path = ".github/runner-matrix.json"
    add(checks, "runner-matrix:exists", exists(runner_matrix_path), f"{runner_matrix_path} exists")
    runner_matrix = json.loads(read(runner_matrix_path)) if exists(runner_matrix_path) else {}
    matrix_workflows = runner_matrix.get("workflows", {})
    add(
        checks,
        "runner-matrix:default-runner:self-hosted-linux",
        runner_matrix.get("default_runner") == SELF_HOSTED_LINUX_RUNNER,
        f"{runner_matrix_path} default_runner stays on {SELF_HOSTED_LINUX_RUNNER} for fixed-runner evidence",
    )
    add(
        checks,
        "runner-matrix:ci:github-hosted-default",
        matrix_workflows.get("ci", {}).get("runner") == GITHUB_HOSTED_UBUNTU_RUNNER,
        f"{runner_matrix_path} pins ci runner fallback to {GITHUB_HOSTED_UBUNTU_RUNNER}",
    )
    add(
        checks,
        "runner-matrix:ci:vars-hint",
        matrix_workflows.get("ci", {}).get("vars_hint") == "CI_RUNNER",
        f"{runner_matrix_path} exposes CI_RUNNER override for ci",
    )
    add(
        checks,
        "runner-matrix:ci:github-hosted-capability",
        matrix_workflows.get("ci", {}).get("capabilities", {}).get("github_hosted_fallback") is True,
        f"{runner_matrix_path} records ci github-hosted fallback capability",
    )
    for workflow_name, expected_var in (
        ("release", "RELEASE_RUNNER"),
        ("conan-validate", "CONAN_VALIDATE_RUNNER"),
        ("specialized-e2e", "SPECIALIZED_E2E_RUNNER"),
    ):
        entry = matrix_workflows.get(workflow_name, {})
        add(
            checks,
            f"runner-matrix:{workflow_name}:self-hosted-default",
            entry.get("runner") == SELF_HOSTED_LINUX_RUNNER,
            f"{runner_matrix_path} keeps {workflow_name} on {SELF_HOSTED_LINUX_RUNNER}",
        )
        add(
            checks,
            f"runner-matrix:{workflow_name}:vars-hint",
            entry.get("vars_hint") == expected_var,
            f"{runner_matrix_path} exposes {expected_var} override for {workflow_name}",
        )

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

    ci_workflow = read(".github/workflows/ci.yml")
    add(
        checks,
        "workflow:ci:github-hosted-default",
        "default: '\"ubuntu-latest\"'" in ci_workflow and "vars.CI_RUNNER" in ci_workflow,
        ".github/workflows/ci.yml defaults to ubuntu-latest and keeps CI_RUNNER override",
    )
    add(
        checks,
        "workflow:ci:cache-key-includes-conan-inputs",
        all(token in ci_workflow for token in ("conanfile.py", "conan/profiles/**", "conan/remotes*.json", "conan/locks/*.lock")),
        ".github/workflows/ci.yml cache key is bound to Conan graph inputs",
    )

    release_workflow = read(".github/workflows/release.yml")
    add(
        checks,
        "workflow:release:runner-input-declared",
        "workflow_dispatch:" in release_workflow and "inputs:" in release_workflow and "\n      runner:\n" in release_workflow,
        ".github/workflows/release.yml declares workflow_dispatch runner input",
    )
    add(
        checks,
        "workflow:release:release-runner-override",
        "vars.RELEASE_RUNNER" in release_workflow,
        ".github/workflows/release.yml keeps RELEASE_RUNNER override",
    )

    conan_validate_workflow = read(".github/workflows/conan-validate.yml")
    add(
        checks,
        "workflow:conan-validate:runner-override",
        "vars.CONAN_VALIDATE_RUNNER" in conan_validate_workflow,
        ".github/workflows/conan-validate.yml keeps CONAN_VALIDATE_RUNNER override",
    )

    specialized_e2e_workflow = read(".github/workflows/specialized-e2e.yml")
    add(
        checks,
        "workflow:specialized-e2e:runner-override",
        "vars.SPECIALIZED_E2E_RUNNER" in specialized_e2e_workflow,
        ".github/workflows/specialized-e2e.yml keeps SPECIALIZED_E2E_RUNNER override",
    )

    fixed_runner_doc = read("docs/fixed-runner-playbook.md")
    for token in DOC_TOKENS:
        add(checks, f"docs:fixed-runner:{token}", token in fixed_runner_doc, f"fixed-runner playbook mentions {token}")

    github_readme = read(".github/README.md")
    for token in README_TOKENS:
        add(checks, f"docs:github-readme:{token}", token in github_readme, f".github/README.md mentions {token}")

    manifest_path = "docs/production/production-candidate-evidence-manifest.json"
    if not exists(manifest_path):
        manifest_path = "docs/production-candidate-evidence-manifest.json"
    if exists(manifest_path):
        manifest = json.loads(read(manifest_path))
        manifest_text = json.dumps(manifest, ensure_ascii=False)
        for summary in (
            "runtime/validation/long-soak-capacity-summary.json",
            "runtime/validation/fixed-runner-release-capacity-summary.json",
            "runtime/validation/preprod-recovery-drill-summary.json",
            "runtime/validation/tls-preprod-multi-run-summary.json",
        ):
            add(checks, f"manifest:requires:{summary}", summary in manifest_text, f"manifest references {summary}")
    else:
        for summary in (
            "runtime/validation/long-soak-capacity-summary.json",
            "runtime/validation/fixed-runner-release-capacity-summary.json",
            "runtime/validation/preprod-recovery-drill-summary.json",
            "runtime/validation/tls-preprod-multi-run-summary.json",
        ):
            add(checks, f"manifest:requires:{summary}", False, f"manifest missing, cannot check {summary}")

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
