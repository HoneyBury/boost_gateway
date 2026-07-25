#!/usr/bin/env python3
"""Aggregate P5-P8 production business-closure gates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def tail(text: str | bytes | None, max_chars: int = 5000) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(name: str, category: str, cmd: list[str], timeout: int) -> dict[str, object]:
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
            timeout=timeout,
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
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(completed.stdout),
        "stderr_tail": tail(completed.stderr),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/default"))
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--include-otel-collector", action="store_true")
    parser.add_argument("--include-runtime-http", action="store_true")
    parser.add_argument("--include-k8s-full-flow", action="store_true")
    parser.add_argument("--include-operator-kind", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/p5-p8-business-closure-summary.json"))
    args = parser.parse_args()

    build_dir = args.build_dir
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    common_build_args = ["--build-dir", str(build_dir), "--configuration", args.configuration]
    if args.skip_build:
        common_build_args.append("--skip-build")

    steps: list[dict[str, object]] = []
    obs_cmd = [
        sys.executable,
        str(ROOT / "scripts/gates/production/verify_observability_gate.py"),
        *common_build_args,
        "--summary-path",
        str(ROOT / "runtime/validation/p5-observability-summary.json"),
    ]
    if args.include_otel_collector:
        obs_cmd.append("--include-otel-collector")
    if args.include_runtime_http:
        obs_cmd.append("--include-runtime-http")
    steps.append(run_step("P5 observability and OTel gate", "p5_observability", obs_cmd, args.timeout_seconds))

    tls_cmd = [
        sys.executable,
        str(ROOT / "scripts/gates/transport/check_tls_profile.py"),
        "--generate-dev-certs",
        "--summary-path",
        str(ROOT / "runtime/validation/p6-tls-profile-summary.json"),
    ]
    steps.append(run_step("P6 TLS profile boundary gate", "p6_tls", tls_cmd, 60))

    security_cmd = [sys.executable, str(ROOT / "scripts/gates/release/check_security_release_gate.py")]
    steps.append(run_step("P6 security release gate", "p6_security", security_cmd, 60))

    control_cmd = [
        sys.executable,
        str(ROOT / "scripts/gates/production/verify_control_plane_gate.py"),
        "--summary-path",
        str(ROOT / "runtime/validation/p7-control-plane-summary.json"),
    ]
    if args.include_operator_kind:
        control_cmd.append("--include-kind")
    steps.append(run_step("P7 control-plane gate", "p7_control_plane", control_cmd, args.timeout_seconds))

    if args.include_k8s_full_flow:
        k8s_cmd = [
            sys.executable,
            str(ROOT / "scripts/gates/k8s/verify_k8s_full_flow.py"),
            "--build-dir",
            str(build_dir),
            "--summary-path",
            str(ROOT / "runtime/validation/p7-k8s-full-flow-summary.json"),
        ]
        if args.skip_build:
            k8s_cmd.append("--skip-build")
        steps.append(run_step("P7 Kubernetes SDK full-flow gate", "p7_k8s_full_flow", k8s_cmd, args.timeout_seconds))

    steps.append(run_step(
        "P8 v3 proto schema gate",
        "p8_proto",
        [sys.executable, str(ROOT / "scripts/gates/governance/check_v3_proto_schema.py"), "--proto-dir", "proto/v3"],
        60,
    ))
    steps.append(run_step(
        "P8 v3 proto transport contract gate",
        "p8_proto",
        [
            sys.executable,
            str(ROOT / "scripts/gates/governance/check_v3_proto_schema.py"),
            "--proto-dir",
            "proto/v3",
            "--require-transport-contract",
        ],
        60,
    ))

    failed = next((step for step in steps if step.get("status") != "passed"), None)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(build_dir.resolve()),
        "configuration": args.configuration,
        "include_otel_collector": args.include_otel_collector,
        "include_runtime_http": args.include_runtime_http,
        "include_k8s_full_flow": args.include_k8s_full_flow,
        "include_operator_kind": args.include_operator_kind,
        "passed": failed is None,
        "failed_category": "" if failed is None else str(failed.get("category")),
        "failed_step": "" if failed is None else str(failed.get("name")),
        "steps": steps,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
