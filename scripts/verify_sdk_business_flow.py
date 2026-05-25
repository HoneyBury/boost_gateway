#!/usr/bin/env python3
"""Run SDK business-flow integration tests against the in-process gateway."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_step(name: str, command: list[str], checks: list[dict[str, Any]], timeout_seconds: int) -> bool:
    started = time.monotonic()
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
        status = "passed" if proc.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            time.sleep(0.5)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                stdout = exc.stdout
                stderr = exc.stderr
        else:
            stdout = exc.stdout
            stderr = exc.stderr
    stdout = stdout or ""
    stderr = stderr or ""
    passed = status == "passed"
    checks.append(
        {
            "name": name,
            "passed": passed,
            "status": status,
            "command": command,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": stdout[-6000:],
            "stderr": stderr[-6000:],
        }
    )
    return passed


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
        built = run_step(
            "build-sdk-business-flow-tests",
            ["cmake", "--build", str(build_dir), "--config", args.configuration, "--target", target],
            checks,
            args.test_timeout_seconds,
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
                "-C",
                args.configuration,
                "--output-on-failure",
            ],
            checks,
            args.test_timeout_seconds,
        )

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
