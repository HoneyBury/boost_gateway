#!/usr/bin/env python3
"""Collect the bounded Release baseline entrypoint for production-candidate checks."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def normalize_output(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    return text


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    text = normalize_output(text)
    return text if len(text) <= max_chars else text[-max_chars:]


def emit_text(text: str, *, stderr: bool = False) -> None:
    stream = sys.stderr if stderr else sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.buffer.write(text.encode(encoding, errors="replace"))


def run_step(name: str, cmd: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
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
        "command": cmd,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/windows-ninja-release"))
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--baseline-timeout-seconds", type=int, default=60)
    parser.add_argument("--perf-preset", choices=["smoke", "baseline", "capacity", "business-capacity"], default="baseline")
    parser.add_argument("--include-business-flow", action="store_true")
    parser.add_argument("--business-flow-clients", type=int, default=1)
    parser.add_argument("--perf-repetitions", type=int, default=3)
    parser.add_argument("--perf-timeout-seconds", type=int, default=600)
    parser.add_argument("--skip-r4", action="store_true")
    parser.add_argument("--skip-perf", action="store_true")
    parser.add_argument("--perf-output-root", type=Path, default=None)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/release-baseline-summary.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[2]
    summary_path = args.summary_path if args.summary_path.is_absolute() else root / args.summary_path
    perf_output = args.perf_output_root if args.perf_output_root is not None else root / "runtime" / "perf" / "release-baseline"
    if not perf_output.is_absolute():
        perf_output = root / perf_output
    summary: dict[str, object] = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(args.build_dir.resolve()),
        "configuration": args.configuration,
        "baseline_profile": "release",
        "perf_preset": args.perf_preset,
        "perf_repetitions": args.perf_repetitions,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "overall_pass": False,
        "passed": False,
        "failed_category": "",
        "failed_step": "",
        "steps": [],
        "artifacts": {
            "summary_path": str(summary_path),
            "r4_contract_summary_path": str(root / "runtime" / "validation" / "release-r4-contract-summary.json"),
            "performance_summary_path": str(perf_output / "summary.json"),
            "performance_report_path": str(perf_output / "report.md"),
        },
    }

    if args.skip_r4 and args.skip_perf:
        summary["failed_category"] = "configuration"
        summary["failed_step"] = "no release baseline steps selected"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print("release baseline failed: --skip-r4 and --skip-perf disable all checks", file=sys.stderr)
        print(f"summary: {summary_path}", file=sys.stderr)
        return 2

    steps: list[dict[str, object]] = []

    if not args.skip_build and not args.skip_perf:
        build_cmd = [
            "cmake",
            "--build",
            str(args.build_dir),
        ]
        if args.configuration:
            build_cmd.extend(["--config", args.configuration])
        build_cmd.extend([
            "--target",
            "v2_login_backend",
            "v2_room_backend",
            "v2_battle_backend",
            "v2_gateway_demo",
            "v2_gateway_pressure",
            "v2_arch_benchmark",
            "project_v2_unit_tests",
            "project_v2_integration_tests",
        ])
        step = run_step(
            "build release baseline targets",
            build_cmd,
            root,
            max(120, args.baseline_timeout_seconds + 60),
        )
        steps.append(step)
        if step["status"] != "passed":
            summary["steps"] = steps
            summary["failed_category"] = "build"
            summary["failed_step"] = str(step["name"])
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(f"summary: {summary_path}")
            return 1

    if not args.skip_r4:
        cmd = [
            sys.executable,
            str(root / "scripts" / "verify_r4_contract.py"),
            "--build-dir",
            str(args.build_dir),
            "--configuration",
            args.configuration,
            "--baseline-profile",
            "release",
            "--baseline-timeout-seconds",
            str(args.baseline_timeout_seconds),
            "--summary-path",
            str(root / "runtime" / "validation" / "release-r4-contract-summary.json"),
        ]
        if args.skip_build or not args.skip_perf:
            cmd.append("--skip-build")

        steps.append(run_step(
            "release R4 contract baseline",
            cmd,
            root,
            args.baseline_timeout_seconds + 90,
        ))

    if not args.skip_perf:
        perf_cmd = [
            sys.executable,
            str(root / "scripts" / "collect_v2_perf_baseline.py"),
            "--build-dir",
            str(args.build_dir),
            "--run-preset",
            args.perf_preset,
            "--repetitions",
            str(args.perf_repetitions),
            "--output-root",
            str(perf_output),
        ]
        if args.include_business_flow:
            perf_cmd.extend(["--include-business-flow", "--business-flow-clients", str(args.business_flow_clients)])
        steps.append(run_step(
            "release multi-process performance baseline",
            perf_cmd,
            root,
            args.perf_timeout_seconds,
        ))

    summary["steps"] = steps
    summary["duration_seconds"] = round(sum(float(step.get("duration_seconds", 0.0)) for step in steps), 3)
    failed = next((step for step in steps if step["status"] != "passed"), None)
    if failed is None:
        summary["overall_pass"] = True
        summary["passed"] = True
    else:
        summary["failed_category"] = str(failed.get("category", "release_baseline"))
        summary["failed_step"] = str(failed["name"])

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

