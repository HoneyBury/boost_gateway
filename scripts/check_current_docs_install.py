#!/usr/bin/env python3
"""Validate maintained docs and CMake install packaging references."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TOP_LEVEL_DOCS = [
    "docs/README.md",
    "docs/current-state.md",
    "docs/architecture-overview.md",
    "docs/reliability-matrix.md",
    "docs/performance-baseline.md",
    "docs/production-deployment-runbook.md",
    "docs/production-operations-runbook.md",
    "docs/production-configuration-runbook.md",
    "docs/production-evidence-runner.md",
    "docs/fixed-runner-playbook.md",
    "docs/tls-mtls-runbook.md",
    "docs/v3-release-checklist.md",
    "docs/production-candidate-evidence-manifest.json",
    "docs/production-recovery-drill-record-template.json",
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
    add(
        checks,
        "cmake:archives-installed-as-archive",
        'DESTINATION "${CMAKE_INSTALL_DATADIR}/${PROJECT_NAME}/docs/archive"' in cmake,
        "docs/archive installs under docs/archive",
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
