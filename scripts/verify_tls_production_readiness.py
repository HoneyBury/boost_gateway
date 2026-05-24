#!/usr/bin/env python3
"""Validate R1 TLS production-readiness evidence with rotation and failure diagnostics."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


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


def run_expected_failure(name: str, category: str, command: list[str], timeout_seconds: int) -> dict[str, Any]:
    step = run_step(name, category, command, timeout_seconds)
    returncode = int(step.get("returncode", 124 if step.get("status") == "timeout" else 0))
    step["expected_failure"] = True
    step["observed_status"] = step.get("status")
    step["status"] = "passed" if returncode != 0 else "failed"
    if step["status"] == "failed":
        step["stderr_tail"] = (str(step.get("stderr_tail", "")) + "\nexpected command to fail").strip()
    return step


def load_summary(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def full_flow_duration(summary: dict[str, Any]) -> float:
    checks = summary.get("checks", [])
    if not isinstance(checks, list):
        return 0.0
    for check in checks:
        if isinstance(check, dict) and check.get("name") == "run-sdk-full-flow-client":
            return float(check.get("duration_seconds", 0.0))
    return 0.0


def certificate_fingerprint(cert_path: Path) -> str:
    completed = subprocess.run(
        ["openssl", "x509", "-in", str(cert_path), "-noout", "-fingerprint", "-sha256", "-enddate"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        return completed.stderr.strip()
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/release")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--step-timeout-seconds", type=int, default=180)
    parser.add_argument("--max-full-flow-overhead-ratio", type=float, default=5.0)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=ROOT / "runtime/tls-readiness",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/r1-tls-production-readiness-summary.json",
    )
    args = parser.parse_args()

    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    validation_dir = summary_path.parent
    validation_dir.mkdir(parents=True, exist_ok=True)
    work_dir = args.work_dir if args.work_dir.is_absolute() else ROOT / args.work_dir
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    current_certs = work_dir / "current-certs"
    rotated_certs = work_dir / "rotated-certs"
    bad_certs = work_dir / "bad-certs"
    steps: list[dict[str, Any]] = []

    for name, cert_dir, days in [
        ("R1 generate current TLS cert set", current_certs, 45),
        ("R1 generate rotated TLS cert set", rotated_certs, 90),
        ("R1 generate mismatched TLS cert set", bad_certs, 30),
    ]:
        steps.append(
            run_step(
                name,
                "cert_generation",
                [
                    sys.executable,
                    str(ROOT / "scripts/gen_certs.py"),
                    "--output-dir",
                    str(cert_dir),
                    "--days",
                    str(days),
                ],
                60,
            )
        )

    transport_summary = validation_dir / "r1-transport-config-governance-summary.json"
    steps.append(
        run_step(
            "R1 transport config governance with default TLS full-flow",
            "transport_tls",
            [
                sys.executable,
                str(ROOT / "scripts/check_transport_config_governance.py"),
                "--generate-dev-certs",
                "--include-tls-full-flow",
                "--build-dir",
                str(args.build_dir),
                "--summary-path",
                str(transport_summary),
            ],
            args.step_timeout_seconds,
        )
    )

    plain_summary = validation_dir / "r1-sdk-full-flow-plain-summary.json"
    plain_cmd = [
        sys.executable,
        str(ROOT / "scripts/verify_sdk_full_flow_client.py"),
        "--build-dir",
        str(args.build_dir),
        "--summary-path",
        str(plain_summary),
    ]
    if args.skip_build:
        plain_cmd.append("--skip-build")
    steps.append(run_step("R1 plain SDK full-flow baseline", "perf_baseline", plain_cmd, args.step_timeout_seconds))

    tls_summary = validation_dir / "r1-sdk-full-flow-tls-summary.json"
    tls_cmd = [
        sys.executable,
        str(ROOT / "scripts/verify_sdk_full_flow_client.py"),
        "--build-dir",
        str(args.build_dir),
        "--backend-tls",
        "--tls-cert-dir",
        str(current_certs),
        "--gateway-tls-verify-mode",
        "server",
        "--gateway-tls-ca-cert-path",
        str(current_certs / "ca.crt"),
        "--summary-path",
        str(tls_summary),
    ]
    if args.skip_build:
        tls_cmd.append("--skip-build")
    steps.append(run_step("R1 TLS SDK full-flow with server verification", "perf_tls", tls_cmd, args.step_timeout_seconds))

    rotated_summary = validation_dir / "r1-sdk-full-flow-rotated-tls-summary.json"
    rotated_cmd = [
        sys.executable,
        str(ROOT / "scripts/verify_sdk_full_flow_client.py"),
        "--build-dir",
        str(args.build_dir),
        "--skip-build",
        "--backend-tls",
        "--tls-cert-dir",
        str(rotated_certs),
        "--gateway-tls-verify-mode",
        "server",
        "--gateway-tls-ca-cert-path",
        str(rotated_certs / "ca.crt"),
        "--summary-path",
        str(rotated_summary),
    ]
    steps.append(run_step("R1 rotated certificate SDK full-flow", "cert_rotation", rotated_cmd, args.step_timeout_seconds))

    failure_summary = validation_dir / "r1-sdk-full-flow-mismatched-ca-summary.json"
    mismatch_cmd = [
        sys.executable,
        str(ROOT / "scripts/verify_sdk_full_flow_client.py"),
        "--build-dir",
        str(args.build_dir),
        "--skip-build",
        "--backend-tls",
        "--tls-cert-dir",
        str(bad_certs),
        "--gateway-tls-verify-mode",
        "server",
        "--gateway-tls-ca-cert-path",
        str(current_certs / "ca.crt"),
        "--summary-path",
        str(failure_summary),
    ]
    steps.append(run_expected_failure("R1 mismatched CA failure diagnostic", "failure_diagnostics", mismatch_cmd, args.step_timeout_seconds))

    plain_doc = load_summary(plain_summary)
    tls_doc = load_summary(tls_summary)
    plain_duration = full_flow_duration(plain_doc)
    tls_duration = full_flow_duration(tls_doc)
    overhead_ratio = round(tls_duration / plain_duration, 3) if plain_duration > 0 else 0.0
    perf_passed = plain_duration > 0 and tls_duration > 0 and overhead_ratio <= args.max_full_flow_overhead_ratio
    steps.append(
        {
            "name": "R1 TLS/plain full-flow latency comparison",
            "category": "perf_comparison",
            "command": ["compare", str(plain_summary), str(tls_summary)],
            "status": "passed" if perf_passed else "failed",
            "duration_seconds": 0.0,
            "plain_full_flow_seconds": plain_duration,
            "tls_full_flow_seconds": tls_duration,
            "overhead_ratio": overhead_ratio,
            "max_overhead_ratio": args.max_full_flow_overhead_ratio,
            "stdout_tail": "",
            "stderr_tail": "" if perf_passed else "TLS/plain full-flow comparison exceeded threshold or lacked timing data",
        }
    )

    current_fp = certificate_fingerprint(current_certs / "server.crt")
    rotated_fp = certificate_fingerprint(rotated_certs / "server.crt")
    rotation_passed = current_fp != rotated_fp and "Fingerprint" in current_fp and "Fingerprint" in rotated_fp
    steps.append(
        {
            "name": "R1 certificate rotation fingerprint differs",
            "category": "cert_rotation",
            "command": ["openssl", "x509", "-fingerprint", str(current_certs / "server.crt"), str(rotated_certs / "server.crt")],
            "status": "passed" if rotation_passed else "failed",
            "duration_seconds": 0.0,
            "current_certificate": current_fp,
            "rotated_certificate": rotated_fp,
            "stdout_tail": "",
            "stderr_tail": "" if rotation_passed else "current and rotated certificate fingerprints did not differ",
        }
    )

    failed = next((step for step in steps if step.get("status") != "passed"), None)
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "passed": failed is None,
        "overall_pass": failed is None,
        "failed_category": str(failed.get("category", "")) if failed else "",
        "failed_step": str(failed.get("name", "")) if failed else "",
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "scope": {
            "tls_full_flow_with_server_verification": True,
            "certificate_rotation_full_flow": True,
            "mismatched_ca_expected_failure": True,
            "performance_comparison": "single business full-flow smoke comparison",
            "max_full_flow_overhead_ratio": args.max_full_flow_overhead_ratio,
        },
        "artifacts": {
            "summary_path": str(summary_path),
            "transport_config_governance_summary_path": str(transport_summary),
            "plain_full_flow_summary_path": str(plain_summary),
            "tls_full_flow_summary_path": str(tls_summary),
            "rotated_tls_full_flow_summary_path": str(rotated_summary),
            "mismatched_ca_summary_path": str(failure_summary),
            "work_dir": str(work_dir),
        },
        "performance_comparison": {
            "plain_full_flow_seconds": plain_duration,
            "tls_full_flow_seconds": tls_duration,
            "overhead_ratio": overhead_ratio,
        },
        "steps": steps,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"R1 TLS production readiness: {'PASS' if summary['overall_pass'] else 'FAIL'}")
    print(f"summary: {summary_path}")
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
