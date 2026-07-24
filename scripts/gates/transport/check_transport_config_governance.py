#!/usr/bin/env python3
"""Validate N4 transport security and configuration governance evidence."""

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


ROOT = Path(__file__).resolve().parents[3]


def normalize_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def emit_text(text: str, *, stderr: bool = False) -> None:
    stream = sys.stderr if stderr else sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.buffer.write(text.encode(encoding, errors="replace"))


def tail(value: str | bytes | None, max_chars: int = 4000) -> str:
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
        emit_text(stdout)
    if stderr:
        emit_text(stderr, stderr=True)
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
    parser.add_argument("--generate-dev-certs", action="store_true")
    parser.add_argument(
        "--cert-dir",
        type=Path,
        default=ROOT / "certs",
        help="Directory containing generated development certificates.",
    )
    parser.add_argument("--include-tls-full-flow", action="store_true")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/release")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/n4-transport-config-governance-summary.json",
    )
    args = parser.parse_args()
    cert_dir = args.cert_dir if args.cert_dir.is_absolute() else ROOT / args.cert_dir

    tls_summary = ROOT / "runtime/validation/n4-tls-profile-summary.json"
    config_summary = ROOT / "runtime/validation/n4-config-governance-summary.json"
    tls_cmd = [
        sys.executable,
        str(ROOT / "scripts/gates/transport/check_tls_profile.py"),
        "--summary-path",
        str(tls_summary),
    ]
    if args.generate_dev_certs:
        tls_cmd.extend(["--generate-dev-certs", "--cert-dir", str(cert_dir)])

    steps = [
        run_step("N4 TLS/mTLS profile boundary", "tls_profile", tls_cmd, 60),
        run_step(
            "N4 configuration governance and drift check",
            "config_governance",
            [
                sys.executable,
                str(ROOT / "scripts/gates/governance/check_config_governance.py"),
                "--summary-path",
                str(config_summary),
            ],
            60,
        ),
    ]
    tls_full_flow_summary = ROOT / "runtime/validation/n4-tls-full-flow-summary.json"
    if args.include_tls_full_flow:
        steps.append(
            run_step(
                "N4 backend TLS SDK full-flow",
                "tls_full_flow",
                [
                    sys.executable,
                    str(ROOT / "scripts/gates/sdk/verify_sdk_full_flow_client.py"),
                    "--build-dir",
                    str(args.build_dir),
                    "--skip-build",
                    "--backend-tls",
                    "--tls-cert-dir",
                    str(cert_dir),
                    "--summary-path",
                    str(tls_full_flow_summary),
                ],
                120,
            )
        )

    failed = next((step for step in steps if step.get("status") != "passed"), None)
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": failed is None,
        "passed": failed is None,
        "failed_category": str(failed.get("category", "")) if failed else "",
        "failed_step": str(failed.get("name", "")) if failed else "",
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "artifacts": {
            "summary_path": str(summary_path),
            "tls_profile_summary_path": str(tls_summary),
            "config_governance_summary_path": str(config_summary),
            "tls_full_flow_summary_path": str(tls_full_flow_summary) if args.include_tls_full_flow else "",
        },
        "steps": steps,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"N4 transport/config governance gate: {'PASS' if summary['overall_pass'] else 'FAIL'}")
    print(f"summary: {summary_path}")
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
