#!/usr/bin/env python3
"""Run SDK business-flow integration tests against the in-process gateway."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_step(name: str, command: list[str], checks: list[dict[str, Any]]) -> bool:
    result = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)
    passed = result.returncode == 0
    checks.append(
        {
            "name": name,
            "passed": passed,
            "command": command,
            "stdout": result.stdout[-6000:],
            "stderr": result.stderr[-6000:],
        }
    )
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/default")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/sdk-business-flow-summary.json",
    )
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    build_dir = args.build_dir
    target = "sdk_business_flow_tests"
    built = run_step(
        "build-sdk-business-flow-tests",
        ["cmake", "--build", str(build_dir), "--target", target],
        checks,
    )
    if built:
        run_step(
            "run-sdk-business-flow-tests",
            [
                "ctest",
                "--test-dir",
                str(build_dir),
                "-R",
                "GatewayFixture",
                "--output-on-failure",
            ],
            checks,
        )

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "passed": not failed,
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
