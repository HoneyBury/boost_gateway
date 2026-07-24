#!/usr/bin/env python3
"""Validate maintained docs and CMake install packaging references."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[3]

REQUIRED_TOP_LEVEL_DOCS = [
    "docs/README.md",
    "docs/ONBOARDING.md",
    "docs/current-state.md",
    "docs/runner-inventory.md",
    "docs/runner-gate-standard.md",
    "docs/project-blueprint.md",
    "docs/mainline-execution-plan.md",
    "docs/single-node-enterprise-validation-plan.md",
    "docs/platform-production-boundaries.md",
    "docs/platform-production-boundaries.json",
    "docs/legacy/legacy-helper-inventory.md",
    "docs/architecture-overview.md",
    "docs/performance-baseline.md",
    "docs/legacy/v2-control-plane-preplan.md",
    "docs/deployment/production-deployment-runbook.md",
    "docs/deployment/production-operations-runbook.md",
    "docs/deployment/production-configuration-runbook.md",
    "docs/deployment/operations-host-admission-runbook.md",
    "docs/deployment/immutable-release-deployment-runbook.md",
    "docs/fixed-runner-playbook.md",
    "docs/release-governance.md",
    "docs/tls-mtls-runbook.md",
    "docs/decisions/v3.6-decision-manifest.json",
    "docs/production/production-candidate-evidence-manifest.json",
    "docs/production/production-recovery-drill-record-template.json",
]

ARCHIVED_RELEASE_DOCS = [
    "docs/archive/releases/v3.5.x-maintenance-plan.md",
    "docs/archive/releases/v3.5.2-freeze-todo.md",
    "docs/archive/releases/v3.6-implementation-status.md",
]


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def cmake_doc_references() -> list[str]:
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"docs/[A-Za-z0-9_./-]+", cmake)))


def local_markdown_links(path: Path) -> list[tuple[str, Path]]:
    links: list[tuple[str, Path]] = []
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", text):
        target = match.group(1).strip().strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        relative = unquote(target.split("#", 1)[0])
        if relative:
            links.append((target, (path.parent / relative).resolve()))
    return links


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/current-docs-install-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    checks: list[dict[str, Any]] = []
    for relative in REQUIRED_TOP_LEVEL_DOCS:
        add(checks, f"required:{relative}", (ROOT / relative).exists(), f"{relative} exists")
    for relative in ARCHIVED_RELEASE_DOCS:
        add(checks, f"archive:{relative}", (ROOT / relative).exists(), f"{relative} exists")

    maintained_markdown = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    for path in maintained_markdown:
        for target, resolved in local_markdown_links(path):
            relative = path.relative_to(ROOT).as_posix()
            add(
                checks,
                f"link:{relative}:{target}",
                resolved.exists(),
                f"{relative} local link {target!r} resolves to {resolved}",
            )

    for relative in cmake_doc_references():
        if relative == "docs/archive/":
            add(checks, "cmake:archive-directory", (ROOT / "docs/archive").is_dir(), "docs/archive directory exists")
            continue
        add(checks, f"cmake:{relative}", (ROOT / relative).exists(), f"{relative} exists")

    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    docs_index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    onboarding = (ROOT / "docs/ONBOARDING.md").read_text(encoding="utf-8")
    maintenance_plan = (ROOT / ARCHIVED_RELEASE_DOCS[0]).read_text(encoding="utf-8")
    freeze_todo = (ROOT / ARCHIVED_RELEASE_DOCS[1]).read_text(encoding="utf-8")
    archived_v36 = (ROOT / ARCHIVED_RELEASE_DOCS[2]).read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    platform_boundaries = json.loads(
        (ROOT / "docs/platform-production-boundaries.json").read_text(encoding="utf-8")
    )
    runner_matrix = json.loads((ROOT / ".github/runner-matrix.json").read_text(encoding="utf-8"))
    version_match = re.search(r"project\(boost_gateway\s+VERSION\s+(\d+\.\d+\.\d+)", cmake)
    version = version_match.group(1) if version_match else ""
    add(checks, "release:project-version", bool(version), f"project version={version!r}")
    add(checks, "release:readme-version", f"v{version}" in readme, f"README mentions v{version}")
    add(checks, "release:changelog-version", f"## v{version} " in changelog, f"CHANGELOG has v{version} section")
    add(
        checks,
        "docs:onboarding-indexed",
        "[开发者入门](ONBOARDING.md)" in docs_index,
        "the docs index identifies ONBOARDING.md as the new-contributor entrypoint",
    )
    add(
        checks,
        "docs:onboarding-development-contract",
        all(
            token in onboarding
            for token in (
                "python3.12 scripts/tools/ensure_conan_venv.py",
                "--conan-version 2.8.1",
                "linux-gcc-x64-debug-nogrpc-nosqlite.lock",
                '"&:with_raft_protobuf=True"',
                "build/contributor-debug",
                "scripts/run_tests.py unit",
                "v2_gateway_demo --script",
                "## CLion 配置",
                "--allow-dirty",
                "当前 `4.2.0`",
            )
        ),
        "onboarding preserves the pinned Conan, build, test, smoke, IDE, Docker and SDK contracts",
    )
    add(
        checks,
        "release:readme-current-release",
        f"releases/tag/v{version}" in readme
        and "SDK 4.2.0" in readme
        and "docs/current-state.md" in readme,
        "README points to the current release, SDK and maintained fact source",
    )
    add(
        checks,
        "release:platform-boundaries-current",
        platform_boundaries.get("platforms", {}).get("linux-arm64", {}).get("status")
        == f"supported-v{version}"
        and platform_boundaries.get("platforms", {}).get("macos-arm64", {}).get("status")
        == f"supported-v{version}",
        "ARM production platform statuses match the current project release",
    )
    macos_capabilities = runner_matrix.get("workflows", {}).get("macos-arm64", {}).get(
        "capabilities", {}
    )
    add(
        checks,
        "runner:macos-capacity-baseline-current",
        macos_capabilities.get("capacity_baseline") is True
        and "capacity_baseline_pending" not in macos_capabilities,
        "the macOS ARM64 runner matrix records the completed capacity baseline",
    )
    add(
        checks,
        "docs:readme-entrypoint-scope",
        len(readme.splitlines()) <= 140
        and "docs/ONBOARDING.md" in readme
        and "docs/archive/README.md" in readme
        and "29589708378" not in readme,
        "README stays concise and routes detailed setup/history to maintained docs",
    )
    add(
        checks,
        "release:history-archived-and-indexed",
        "archive/releases/v3.5.2-freeze-todo.md" in docs_index
        and "archive/releases/v3.6-implementation-status.md" in docs_index
        and "v3.5.2-freeze-todo.md" in maintenance_plan
        and "30063021104" in archived_v36,
        "closed v3.5/v3.6 release records are archived and indexed",
    )
    add(
        checks,
        "release:freeze-todo-runner-contract",
        all(
            token in freeze_todo
            for token in (
                "--no-remote --build=never",
                "第二台 Linux runner",
                "preprod-r5",
                "gh release download v3.5.2",
                "gh attestation verify",
                "不能跨 SHA 拼接",
            )
        ),
        "v3.5.2 freeze TODO preserves runner, offline, same-SHA and published-asset checks",
    )
    add(checks, "release:license-exists", (ROOT / "LICENSE").is_file(), "LICENSE exists")
    add(checks, "release:license-installed", "    LICENSE\n" in cmake, "LICENSE is included by CMake install")
    add(
        checks,
        "cmake:archives-installed-as-archive",
        'DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs/archive"' in cmake,
        "docs/archive installs under docs/archive",
    )
    add(
        checks,
        "cmake:decisions-installed",
        'install(DIRECTORY docs/decisions/' in cmake
        and 'DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs/decisions"' in cmake,
        "accepted next-minor decisions install under docs/decisions",
    )
    add(
        checks,
        "cmake:maintained-index-targets-installed",
        all(
            f"    {relative}\n" in cmake
            for relative in (
                "docs/platform-production-boundaries.md",
                "docs/platform-production-boundaries.json",
                "docs/runner-gate-standard.md",
                "docs/runner-inventory.md",
                "docs/script-inventory.json",
            )
        )
        and "install(DIRECTORY docs/todos/" in cmake,
        "CMake installs maintained top-level index targets and the TODO directory",
    )
    add(
        checks,
        "docs:next-minor-decisions-indexed",
        "decisions/v3.6-decision-manifest.json" in docs_index
        and "scripts/check_next_minor_decisions.py" in (ROOT / "docs/current-state.md").read_text(encoding="utf-8"),
        "docs index and current state point to the governed next-minor decisions",
    )
    operations_plan = (ROOT / "docs/single-node-enterprise-validation-plan.md").read_text(
        encoding="utf-8"
    )
    add(
        checks,
        "docs:single-node-enterprise-plan-indexed",
        "single-node-enterprise-validation-plan.md" in docs_index
        and "docs/single-node-enterprise-validation-plan.md" in cmake,
        "the maintained single-node enterprise plan is indexed and installed",
    )
    add(
        checks,
        "docs:single-node-enterprise-plan-contract",
        all(
            token in operations_plan
            for token in (
                "2,592,000s",
                "TODO-0007",
                "TODO-0018",
                "external SDK canary",
                "Day 0",
                "RTO",
                "RPO",
            )
        ),
        "the two-month plan preserves duration, TODO, canary, restart and recovery boundaries",
    )
    host_runbook = (ROOT / "docs/deployment/operations-host-admission-runbook.md").read_text(
        encoding="utf-8"
    )
    add(
        checks,
        "docs:operations-host-admission-contract",
        all(
            token in host_runbook
            for token in (
                "scripts/check_operations_host.py admit",
                "scripts/apply_operations_host_baseline.py plan",
                "prepare-reboot",
                "verify-reboot",
                "boot ID",
                "TODO-0008",
            )
        )
        and "operations-host-admission-runbook.md" in docs_index
        and "deploy/operations/" in cmake
        and "boost-gateway-compose.service" in cmake,
        "the operations host runbook, policy and Compose lifecycle unit are indexed and installed",
    )
    release_runbook = (
        ROOT / "docs/deployment/immutable-release-deployment-runbook.md"
    ).read_text(encoding="utf-8")
    add(
        checks,
        "docs:immutable-release-deployment-contract",
        all(
            token in release_runbook
            for token in (
                "scripts/prepare_release_runtime.py",
                "scripts/tools/build_release_images.py",
                "scripts/tools/verify_release_deployment.py",
                "--network=none",
                "sdk_full_flow_client",
                "TODO-0009",
                "TODO-0010",
            )
        )
        and "immutable-release-deployment-runbook.md" in docs_index
        and "deploy/runtime/" in cmake
        and "docker-compose.production.yml" in (
            ROOT / "deploy/systemd/boost-gateway-compose.service"
        ).read_text(encoding="utf-8"),
        "the immutable release consumer, runtime image, Compose and full-flow boundary is installed and indexed",
    )

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "docs_install" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(f"current docs install gate: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
