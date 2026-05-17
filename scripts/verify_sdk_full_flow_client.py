#!/usr/bin/env python3
"""Run the SDK full-flow example against a real gateway process."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
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


def wait_for_port(host: str, port: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/default")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9201)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/sdk-full-flow-client-summary.json",
    )
    args = parser.parse_args()

    gateway = args.build_dir / "examples/v2_gateway_demo/v2_gateway_demo"
    client = args.build_dir / "sdk/examples/sdk_full_flow_client"
    checks: list[dict[str, Any]] = []

    build_ok = run_command(
        "build-sdk-full-flow-targets",
        ["cmake", "--build", str(args.build_dir), "--target", "v2_gateway_demo", "sdk_full_flow_client"],
        checks,
    )
    if not build_ok:
        failed = [check for check in checks if not check["passed"]]
        return write_summary(args.summary_path, checks, failed)

    gateway_proc: subprocess.Popen[str] | None = None
    try:
        gateway_proc = subprocess.Popen(
            [str(gateway), "--http-port", "0"],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ready = wait_for_port(args.host, args.port, 10.0)
        checks.append(
            {
                "name": "gateway-ready",
                "passed": ready,
                "command": [str(gateway), "--http-port", "0"],
                "stdout": "",
                "stderr": "" if ready else "gateway did not open TCP port 9201 within 10s",
            }
        )
        if ready:
            run_command(
                "run-sdk-full-flow-client",
                [str(client), args.host, str(args.port)],
                checks,
            )
    finally:
        if gateway_proc is not None:
            gateway_proc.terminate()
            try:
                stdout, stderr = gateway_proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                gateway_proc.kill()
                stdout, stderr = gateway_proc.communicate(timeout=5)
            checks.append(
                {
                    "name": "gateway-shutdown",
                    "passed": True,
                    "command": ["terminate-gateway"],
                    "stdout": stdout[-8000:],
                    "stderr": stderr[-8000:],
                }
            )

    failed = [check for check in checks if not check["passed"]]
    return write_summary(args.summary_path, checks, failed)


def write_summary(path: Path, checks: list[dict[str, Any]], failed: list[dict[str, Any]]) -> int:
    summary = {
        "passed": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"sdk full-flow client: {'PASS' if summary['passed'] else 'FAIL'} "
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


if __name__ == "__main__":
    raise SystemExit(main())
