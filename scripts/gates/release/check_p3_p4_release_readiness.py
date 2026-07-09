#!/usr/bin/env python3
"""Check that P3/P4 gates are wired into the current release path."""

from __future__ import annotations

import argparse
import ast
import json
from datetime import UTC, datetime
from pathlib import Path


def read_text(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def has_call_with_script(source: str, script_name: str) -> bool:
    return script_name in source


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/p3-p4-release-readiness-summary.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[3]
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    checks: list[dict[str, object]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    rc = read_text(root, "scripts/gates/release/verify_release_candidate.py")
    data = read_text(root, "scripts/verify_data_recovery_gate.py")
    obs = read_text(root, "scripts/verify_observability_gate.py")
    governance = read_text(root, "docs/release-governance.md")
    current = read_text(root, "docs/current-state.md")

    try:
        ast.parse(data)
        add("p3:script-syntax", True, "verify_data_recovery_gate.py parses")
    except SyntaxError as exc:
        add("p3:script-syntax", False, f"syntax error: {exc}")

    try:
        ast.parse(obs)
        add("p4:script-syntax", True, "verify_observability_gate.py parses")
    except SyntaxError as exc:
        add("p4:script-syntax", False, f"syntax error: {exc}")

    add(
        "rc:p3-wired",
        has_call_with_script(rc, "verify_data_recovery_gate.py") and "rc-data-recovery-summary.json" in rc,
        "release candidate gate runs P3 data recovery and writes an RC summary",
    )
    add(
        "rc:p4-wired",
        has_call_with_script(rc, "verify_observability_gate.py") and "rc-observability-gate-summary.json" in rc,
        "release candidate gate runs P4 observability and writes an RC summary",
    )
    add(
        "p3:summary-versioned",
        '"summary_version": 2' in data and '"overall_pass": False' in data and '"artifacts"' in data,
        "P3 summary follows the versioned evidence contract",
    )
    add(
        "p3:environment-captured",
        "platform.platform()" in data and "platform.node()" in data,
        "P3 summary captures host/platform metadata",
    )
    add(
        "p3:bounded-default",
        "--include-redis-live" in data and "--include-settlement-replay" in data,
        "P3 keeps Redis live and settlement replay behind explicit flags",
    )
    add(
        "p4:summary-versioned",
        '"summary_version": 2' in obs and '"overall_pass": False' in obs and '"artifacts"' in obs,
        "P4 summary follows the versioned evidence contract",
    )
    add(
        "p4:bounded-default",
        "--include-otel-collector" in obs and "--include-runtime-http" in obs,
        "P4 keeps collector/runtime HTTP checks behind explicit flags",
    )
    add(
        "docs:release-checklist-p3",
        "verify_data_recovery_gate.py" in governance,
        "release governance docs reference the P3 gate",
    )
    add(
        "docs:release-checklist-p4",
        "verify_observability_gate.py" in governance,
        "release governance docs reference the P4 gate",
    )
    add(
        "docs:reliability-p3-p4",
        "verify_data_recovery_gate.py" in governance and "verify_observability_gate.py" in governance,
        "release governance docs reference both P3 and P4",
    )
    add(
        "docs:current-state-p3-p4",
        "verify_data_recovery_gate.py" in current and "verify_observability_gate.py" in current,
        "current-state keeps P3/P4 as current capabilities",
    )

    passed = all(bool(check["passed"]) for check in checks)
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": passed,
        "passed": passed,
        "failed_checks": [check for check in checks if not check["passed"]],
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"p3/p4 release readiness: {'PASS' if passed else 'FAIL'} ({sum(1 for c in checks if c['passed'])}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

