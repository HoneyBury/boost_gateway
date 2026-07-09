#!/usr/bin/env python3
"""Run a real gateway process and verify its HTTP observability endpoints."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_command(name: str, command: list[str], checks: list[dict[str, Any]]) -> bool:
    result = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)
    passed = result.returncode == 0
    checks.append(
        {
            "name": name,
            "passed": passed,
            "command": command,
            "stdout": result.stdout[-8000:],
            "stderr": result.stderr[-8000:],
        }
    )
    return passed


def write_summary(path: Path, checks: list[dict[str, Any]]) -> int:
    failed = [check for check in checks if not check["passed"]]
    summary = {
        "passed": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"gateway observability runtime: {'PASS' if summary['passed'] else 'FAIL'} "
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
    print(f"summary: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/default")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/gateway-observability-runtime-summary.json",
    )
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []

    if not args.skip_build:
        build_ok = run_command(
            "build-runtime-observability-targets",
            [
                "cmake",
                "--build",
                str(args.build_dir),
                "--target",
                "v2_gateway_demo",
                "v2_login_backend",
                "v2_room_backend",
                "v2_battle_backend",
                "v2_match_backend",
                "v2_leaderboard_backend",
                "sdk_full_flow_client",
            ],
            checks,
        )
        if not build_ok:
            return write_summary(args.summary_path, checks)

    full_flow_summary = args.summary_path.parent / "gateway-observability-runtime-full-flow-summary.json"
    cmd = [
        str(REPO_ROOT / "scripts" / "verify_sdk_full_flow_client.py"),
        "--build-dir",
        str(args.build_dir),
        "--summary-path",
        str(full_flow_summary),
    ]
    if args.skip_build:
        cmd.append("--skip-build")
    ok = run_command("sdk-full-flow-with-runtime-http-diagnostics", [sys.executable, *cmd], checks)
    if ok and full_flow_summary.exists():
        doc = json.loads(full_flow_summary.read_text(encoding="utf-8"))
        backend_check = next(
            (check for check in doc.get("checks", []) if check.get("name") == "backend-metrics-cover-six-service-flow"),
            None,
        )
        checks.append(
            {
                "name": "runtime-http-diagnostics-business-metrics",
                "passed": bool(backend_check and backend_check.get("passed")),
                "command": ["read", str(full_flow_summary)],
                "stdout": json.dumps(backend_check, indent=2, sort_keys=True)[-8000:] if backend_check else "",
                "stderr": "" if backend_check else "full-flow summary did not include backend metrics check",
            }
        )

    return write_summary(args.summary_path, checks)


if __name__ == "__main__":
    raise SystemExit(main())
