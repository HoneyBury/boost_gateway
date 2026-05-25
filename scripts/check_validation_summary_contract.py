#!/usr/bin/env python3
"""Validate summary_version=2 contracts for maintained validation summaries."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUMMARIES = [
    "runtime/validation/r2-production-evidence-manifest-summary.json",
    "runtime/validation/r3-production-readiness-report-summary.json",
    "runtime/validation/fixed-runner-release-capacity-summary.json",
    "runtime/validation/n5-sdk-enterprise-delivery-summary.json",
    "runtime/validation/preprod-recovery-drill-summary.json",
    "runtime/validation/tls-preprod-multi-run-summary.json",
]
REQUIRED_KEYS = {
    "summary_version",
    "overall_pass",
    "passed",
    "artifacts",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="append", default=[])
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/validation-summary-contract-summary.json",
    )
    args = parser.parse_args()

    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    targets = args.summary or DEFAULT_SUMMARIES
    checks: list[dict[str, Any]] = []

    for target in targets:
        path = Path(target)
        if not path.is_absolute():
            path = ROOT / path
        name = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        if not path.exists():
            add(checks, f"exists:{name}", args.allow_missing, "summary exists")
            continue
        data = load_json(path)
        add(checks, f"json:{name}", bool(data), "summary is JSON object")
        if not data:
            continue
        missing = sorted(REQUIRED_KEYS - set(data))
        add(checks, f"required-keys:{name}", not missing, "missing=" + ",".join(missing))
        add(checks, f"summary-version:{name}", data.get("summary_version") == 2, "summary_version=2")
        add(checks, f"pass-fields:{name}", data.get("overall_pass") == data.get("passed"), "overall_pass matches passed")
        add(checks, f"artifacts-object:{name}", isinstance(data.get("artifacts"), dict), "artifacts is object")

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "summary_contract" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"validation summary contract: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
