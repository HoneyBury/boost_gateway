#!/usr/bin/env python3
"""Run SDK business-flow integration tests against the in-process gateway."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.lib.subprocess_utils import run_step

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/default")
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--test-timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/sdk-business-flow-summary.json",
    )
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    build_dir = args.build_dir
    target = "sdk_business_flow_tests"
    built = True
    if not args.skip_build:
        result = run_step(
            name="build-sdk-business-flow-tests",
            command=["cmake", "--build", str(build_dir), "--config", args.configuration, "--target", target],
            cwd=REPO_ROOT,
            timeout_seconds=args.test_timeout_seconds,
        )
        checks.append(result)
        built = bool(result["passed"])
    if built:
        checks.append(run_step(
            name="run-sdk-business-flow-tests",
            command=[
                "ctest",
                "--test-dir",
                str(build_dir),
                "-R",
                "GatewayFixture",
                "-C",
                args.configuration,
                "--output-on-failure",
            ],
            cwd=REPO_ROOT,
            timeout_seconds=args.test_timeout_seconds,
        ))

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "passed": not failed,
        "overall_pass": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"sdk business flow: {'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    if failed:
        for check in failed:
            print(f"  - {check['name']}")
            if check.get("stdout"):
                print(check["stdout"])
            if check.get("stderr"):
                print(check["stderr"])
        return 1
    print(f"summary: {args.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
