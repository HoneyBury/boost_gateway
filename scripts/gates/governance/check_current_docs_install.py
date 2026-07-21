#!/usr/bin/env python3
"""Validate maintained docs and CMake install packaging references."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]

REQUIRED_TOP_LEVEL_DOCS = [
    "docs/README.md",
    "docs/current-state.md",
    "docs/runner-inventory.md",
    "docs/runner-gate-standard.md",
    "docs/project-blueprint.md",
    "docs/v3.5.x-maintenance-plan.md",
    "docs/v3.5.2-freeze-todo.md",
    "docs/legacy/legacy-helper-inventory.md",
    "docs/architecture-overview.md",
    "docs/performance-baseline.md",
    "docs/legacy/v2-control-plane-preplan.md",
    "docs/deployment/production-deployment-runbook.md",
    "docs/deployment/production-operations-runbook.md",
    "docs/deployment/production-configuration-runbook.md",
    "docs/fixed-runner-playbook.md",
    "docs/tls-mtls-runbook.md",
    "docs/decisions/v3.6-decision-manifest.json",
    "docs/production/production-candidate-evidence-manifest.json",
    "docs/production/production-recovery-drill-record-template.json",
]


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def cmake_doc_references() -> list[str]:
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"docs/[A-Za-z0-9_./-]+", cmake)))


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

    for relative in cmake_doc_references():
        if relative == "docs/archive/":
            add(checks, "cmake:archive-directory", (ROOT / "docs/archive").is_dir(), "docs/archive directory exists")
            continue
        add(checks, f"cmake:{relative}", (ROOT / relative).exists(), f"{relative} exists")

    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    docs_index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    maintenance_plan = (ROOT / "docs/v3.5.x-maintenance-plan.md").read_text(encoding="utf-8")
    freeze_todo = (ROOT / "docs/v3.5.2-freeze-todo.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version_match = re.search(r"project\(boost_gateway\s+VERSION\s+(\d+\.\d+\.\d+)", cmake)
    version = version_match.group(1) if version_match else ""
    add(checks, "release:project-version", bool(version), f"project version={version!r}")
    add(checks, "release:readme-version", f"v{version}" in readme, f"README mentions v{version}")
    add(checks, "release:changelog-version", f"## v{version} " in changelog, f"CHANGELOG has v{version} section")
    add(
        checks,
        "release:readme-published-facts",
        "releases/tag/v3.5.2" in readme
        and "29589708378" in readme
        and "3142ffe7578e457e7d6fba63a6a00c3366874252b9f56894e9e8f9c7a31e047b" in readme,
        "README records the current stable tag release, workflow and independently verified digest",
    )
    add(
        checks,
        "release:readme-closed-boundary",
        "29587996645" in readme
        and "29588720453" in readme
        and "29591469812" in readme
        and "myserver" in readme
        and "overall_pass=true" in readme,
        "README records same-SHA myserver evidence and AOI published-asset verification",
    )
    add(
        checks,
        "release:freeze-todo-indexed",
        "docs/v3.5.2-freeze-todo.md" in readme
        and "v3.5.2-freeze-todo.md" in docs_index
        and "docs/v3.5.2-freeze-todo.md" in maintenance_plan,
        "v3.5.2 freeze TODO is linked from README, docs index and maintenance plan",
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
        "docs:next-minor-decisions-indexed",
        "decisions/v3.6-decision-manifest.json" in docs_index
        and "scripts/check_next_minor_decisions.py" in (ROOT / "docs/current-state.md").read_text(encoding="utf-8"),
        "docs index and current state point to the governed next-minor decisions",
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
