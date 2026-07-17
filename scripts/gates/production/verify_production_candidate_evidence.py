#!/usr/bin/env python3
"""Aggregate R0 production-candidate evidence into one repeatable gate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lib.evidence_provenance import build_evidence_provenance


ROOT = Path(__file__).resolve().parents[3]


def tail(value: str | bytes | None, max_chars: int = 6000) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return text if len(text) <= max_chars else text[-max_chars:]


def emit_text(text: str, *, stderr: bool = False) -> None:
    stream = sys.stderr if stderr else sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.buffer.write(text.encode(encoding, errors="replace"))


def run_step(name: str, category: str, command: list[str], timeout_seconds: int) -> dict[str, Any]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
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
            "command": command,
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout),
            "stderr_tail": tail(exc.stderr),
        }

    if completed.stdout:
        emit_text(completed.stdout)
    if completed.stderr:
        emit_text(completed.stderr, stderr=True)
    return {
        "name": name,
        "category": category,
        "command": command,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(completed.stdout),
        "stderr_tail": tail(completed.stderr),
    }


def add_flag(command: list[str], enabled: bool, flag: str) -> None:
    if enabled:
        command.append(flag)


def baseline_profile_for(configuration: str) -> str:
    return "release" if configuration.strip().lower() == "release" else "debug"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/release")
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--include-redis-live", action="store_true")
    parser.add_argument("--include-kind", action="store_true")
    parser.add_argument("--include-runtime-http", action="store_true")
    parser.add_argument("--include-release-baseline", action="store_true")
    parser.add_argument("--include-capacity-baseline", action="store_true")
    parser.add_argument("--include-tls-full-flow", action="store_true")
    parser.add_argument("--include-n6-grpc-decision", action="store_true")
    parser.add_argument("--step-timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/r0-production-candidate-evidence-summary.json",
    )
    args = parser.parse_args()
    baseline_profile = baseline_profile_for(args.configuration)

    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    validation_dir = summary_path.parent
    validation_dir.mkdir(parents=True, exist_ok=True)

    preflight_cmd = [
        sys.executable,
        str(ROOT / "scripts/check_fixed_runner_environment.py"),
        "--profile",
        "production-evidence",
        "--build-dir",
        str(args.build_dir),
    ]
    add_flag(preflight_cmd, args.include_redis_live, "--require-redis")
    add_flag(preflight_cmd, args.include_kind, "--require-kind")

    resilience_summary = validation_dir / "r0-production-resilience-summary.json"
    resilience_cmd = [
        sys.executable,
        str(ROOT / "scripts/verify_production_resilience_gate.py"),
        "--build-dir",
        str(args.build_dir),
        "--configuration",
        args.configuration,
        "--summary-path",
        str(resilience_summary),
    ]
    add_flag(resilience_cmd, args.skip_build, "--skip-build")
    add_flag(resilience_cmd, args.include_redis_live, "--include-redis-live")
    add_flag(resilience_cmd, args.include_kind, "--include-operator-kind")
    add_flag(resilience_cmd, args.include_runtime_http, "--include-runtime-http")
    add_flag(resilience_cmd, args.include_release_baseline, "--include-release-baseline")
    add_flag(resilience_cmd, args.include_capacity_baseline, "--include-capacity-baseline")

    evidence_summary = validation_dir / "r0-production-evidence-gate-summary.json"
    evidence_cmd = [
        sys.executable,
        str(ROOT / "scripts/verify_production_evidence_gate.py"),
        "--build-dir",
        str(args.build_dir),
        "--configuration",
        args.configuration,
        "--soak-profile",
        "smoke",
        "--baseline-profile",
        baseline_profile,
        "--summary-path",
        str(evidence_summary),
    ]
    add_flag(evidence_cmd, args.skip_build, "--skip-build")
    add_flag(evidence_cmd, args.include_redis_live, "--include-redis-live")
    add_flag(evidence_cmd, args.include_kind, "--include-operator-kind")
    add_flag(evidence_cmd, args.include_release_baseline, "--include-release-baseline")
    add_flag(evidence_cmd, args.include_capacity_baseline, "--include-capacity-baseline")

    sdk_summary = validation_dir / "r0-sdk-enterprise-delivery-summary.json"
    sdk_cmd = [
        sys.executable,
        str(ROOT / "scripts/verify_sdk_enterprise_delivery.py"),
        "--build-dir",
        str(args.build_dir),
        "--configuration",
        args.configuration,
        "--summary-path",
        str(sdk_summary),
    ]
    add_flag(sdk_cmd, args.skip_build, "--skip-build")

    steps = [
        run_step("R0 fixed-runner environment preflight", "preflight", preflight_cmd, 90),
        run_step("R0 production resilience regression gate", "production_resilience", resilience_cmd, args.step_timeout_seconds),
        run_step("R0 production evidence gate", "production_evidence", evidence_cmd, args.step_timeout_seconds),
        run_step("R0 SDK enterprise delivery gate", "sdk_enterprise", sdk_cmd, args.step_timeout_seconds),
    ]

    tls_summary = validation_dir / "r0-transport-config-governance-summary.json"
    if args.include_tls_full_flow:
        steps.append(
            run_step(
                "R0 N4 transport/TLS full-flow regression",
                "transport_tls",
                [
                    sys.executable,
                    str(ROOT / "scripts/check_transport_config_governance.py"),
                    "--generate-dev-certs",
                    "--include-tls-full-flow",
                    "--build-dir",
                    str(args.build_dir),
                    "--summary-path",
                    str(tls_summary),
                ],
                args.step_timeout_seconds,
            )
        )

    n6_summary = validation_dir / "r0-n6-grpc-poc-decision-summary.json"
    if args.include_n6_grpc_decision:
        n6_cmd = [
            sys.executable,
            str(ROOT / "scripts/check_v3_grpc_poc_decision.py"),
            "--build-dir",
            str(args.build_dir),
            "--summary-path",
            str(n6_summary),
        ]
        add_flag(n6_cmd, args.skip_build, "--skip-build-targets")
        steps.append(run_step("R0 N6 gRPC PoC decision boundary", "grpc_decision", n6_cmd, args.step_timeout_seconds))

    failed = next((step for step in steps if step.get("status") != "passed"), None)
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "provenance": build_evidence_provenance(
            ROOT,
            build_configuration=args.configuration,
        ),
        "passed": failed is None,
        "overall_pass": failed is None,
        "failed_category": str(failed.get("category", "")) if failed else "",
        "failed_step": str(failed.get("name", "")) if failed else "",
        "scope": {
            "default_mode": "bounded_local_candidate_evidence",
            "include_redis_live": args.include_redis_live,
            "include_kind": args.include_kind,
            "include_runtime_http": args.include_runtime_http,
            "include_release_baseline": args.include_release_baseline,
            "include_capacity_baseline": args.include_capacity_baseline,
            "baseline_profile": baseline_profile,
            "include_tls_full_flow": args.include_tls_full_flow,
            "include_n6_grpc_decision": args.include_n6_grpc_decision,
        },
        "artifacts": {
            "summary_path": str(summary_path),
            "production_resilience_summary_path": str(resilience_summary),
            "production_evidence_summary_path": str(evidence_summary),
            "sdk_enterprise_summary_path": str(sdk_summary),
            "transport_tls_summary_path": str(tls_summary) if args.include_tls_full_flow else "",
            "n6_grpc_decision_summary_path": str(n6_summary) if args.include_n6_grpc_decision else "",
        },
        "steps": steps,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"R0 production-candidate evidence: {'PASS' if summary['passed'] else 'FAIL'}")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
