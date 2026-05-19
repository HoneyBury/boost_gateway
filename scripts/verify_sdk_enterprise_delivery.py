#!/usr/bin/env python3
"""Run the N5 SDK enterprise delivery and client compatibility gate."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def normalize_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def tail(value: str | bytes | None, max_chars: int = 5000) -> str:
    text = normalize_output(value)
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(name: str, category: str, cmd: list[str], timeout_seconds: int) -> dict[str, Any]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "category": category,
            "command": cmd,
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout),
            "stderr_tail": tail(exc.stderr),
        }

    stdout = normalize_output(completed.stdout)
    stderr = normalize_output(completed.stderr)
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/default")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-runtime-full-flow", action="store_true")
    parser.add_argument("--step-timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/n5-sdk-enterprise-delivery-summary.json",
    )
    args = parser.parse_args()

    build_dir = args.build_dir
    artifacts = {
        "summary_path": str(args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path),
        "distribution_summary_path": str(ROOT / "runtime/validation/n5-sdk-distribution-summary.json"),
        "package_consumer_summary_path": str(ROOT / "runtime/validation/n5-sdk-package-consumer-summary.json"),
        "business_flow_summary_path": str(ROOT / "runtime/validation/n5-sdk-business-flow-summary.json"),
        "full_flow_client_summary_path": str(ROOT / "runtime/validation/n5-sdk-full-flow-client-summary.json"),
        "tls_full_flow_client_summary_path": str(ROOT / "runtime/validation/n5-sdk-tls-full-flow-client-summary.json"),
    }
    steps: list[dict[str, Any]] = []
    steps.append(
        run_step(
            "N5 SDK distribution and wrapper diagnostics",
            "distribution",
            [
                sys.executable,
                str(ROOT / "scripts/check_sdk_distribution.py"),
                "--build-dir",
                str(build_dir),
                "--summary-path",
                artifacts["distribution_summary_path"],
            ],
            120,
        )
    )
    steps.append(
        run_step(
            "N5 SDK package consumer matrix",
            "package_consumer",
            [
                sys.executable,
                str(ROOT / "scripts/verify_sdk_package_consumer.py"),
                "--build-dir",
                str(build_dir),
                "--summary-path",
                artifacts["package_consumer_summary_path"],
            ],
            args.step_timeout_seconds,
        )
    )
    steps.append(
        run_step(
            "N5 SDK in-process business flow",
            "business_flow",
            [
                sys.executable,
                str(ROOT / "scripts/verify_sdk_business_flow.py"),
                "--build-dir",
                str(build_dir),
                "--summary-path",
                artifacts["business_flow_summary_path"],
            ],
            args.step_timeout_seconds,
        )
    )
    if not args.skip_runtime_full_flow:
        full_flow_cmd = [
            sys.executable,
            str(ROOT / "scripts/verify_sdk_full_flow_client.py"),
            "--build-dir",
            str(build_dir),
            "--summary-path",
            artifacts["full_flow_client_summary_path"],
        ]
        if args.skip_build:
            full_flow_cmd.append("--skip-build")
        steps.append(
            run_step(
                "N5 real gateway SDK full-flow example",
                "runtime_full_flow",
                full_flow_cmd,
                args.step_timeout_seconds,
            )
        )
        tls_full_flow_cmd = [
            sys.executable,
            str(ROOT / "scripts/verify_sdk_full_flow_client.py"),
            "--build-dir",
            str(build_dir),
            "--backend-tls",
            "--summary-path",
            artifacts["tls_full_flow_client_summary_path"],
        ]
        if args.skip_build:
            tls_full_flow_cmd.append("--skip-build")
        steps.append(
            run_step(
                "N5 real gateway SDK TLS full-flow example",
                "runtime_tls_full_flow",
                tls_full_flow_cmd,
                args.step_timeout_seconds,
            )
        )

    failed = next((step for step in steps if step.get("status") != "passed"), None)
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(build_dir.resolve()),
        "overall_pass": failed is None,
        "passed": failed is None,
        "failed_category": str(failed.get("category", "")) if failed else "",
        "failed_step": str(failed.get("name", "")) if failed else "",
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "artifacts": artifacts,
        "steps": steps,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"N5 SDK enterprise delivery gate: {'PASS' if summary['passed'] else 'FAIL'}")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
