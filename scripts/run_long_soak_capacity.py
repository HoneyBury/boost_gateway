#!/usr/bin/env python3
"""Run long-soak and capacity evidence on a fixed production-validation host."""

from __future__ import annotations

import argparse
import json
import platform
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

LONG_SOAK_PRESETS = {
    "2h": {
        "soak_profile": "medium",
        "step_timeout_seconds": 10800,
        "summary_path": "runtime/validation/long-soak-2h-summary.json",
    },
    "8h": {
        "soak_profile": "medium",
        "step_timeout_seconds": 32400,
        "summary_path": "runtime/validation/long-soak-8h-summary.json",
    },
}


def tail(text: str | bytes | None, max_chars: int = 4000) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(name: str, category: str, cmd: list[str], timeout_seconds: int) -> dict[str, object]:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/release"))
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--run-2h-soak", action="store_true")
    parser.add_argument("--run-8h-soak", action="store_true")
    parser.add_argument("--run-capacity", action="store_true")
    parser.add_argument("--perf-repetitions", type=int, default=3)
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/long-soak-capacity-summary.json"))
    return parser.parse_args()


def environment_snapshot() -> dict[str, object]:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "host": socket.gethostname(),
        "cwd": str(ROOT),
    }


def main() -> int:
    args = parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    if not any((args.run_2h_soak, args.run_8h_soak, args.run_capacity)):
        print("no long-soak/capacity action selected", file=sys.stderr)
        return 2

    common = [
        "--build-dir",
        str(args.build_dir),
        "--configuration",
        args.configuration,
    ]
    if args.skip_build:
        common.append("--skip-build")

    steps: list[dict[str, object]] = []
    if args.run_2h_soak:
        preset = LONG_SOAK_PRESETS["2h"]
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "verify_production_resilience_gate.py"),
            *common,
            "--soak-profile",
            preset["soak_profile"],
            "--baseline-profile",
            "release",
            "--summary-path",
            str(ROOT / preset["summary_path"]),
            "--step-timeout-seconds",
            str(preset["step_timeout_seconds"]),
        ]
        steps.append(run_step("2h long-soak evidence", "long_soak", cmd, preset["step_timeout_seconds"] + 300))

    if args.run_8h_soak:
        preset = LONG_SOAK_PRESETS["8h"]
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "verify_production_resilience_gate.py"),
            *common,
            "--soak-profile",
            preset["soak_profile"],
            "--baseline-profile",
            "release",
            "--summary-path",
            str(ROOT / preset["summary_path"]),
            "--step-timeout-seconds",
            str(preset["step_timeout_seconds"]),
        ]
        steps.append(run_step("8h long-soak evidence", "long_soak", cmd, preset["step_timeout_seconds"] + 300))

    if args.run_capacity:
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "collect_release_baseline.py"),
            *common,
            "--perf-preset",
            "capacity",
            "--perf-repetitions",
            str(args.perf_repetitions),
            "--summary-path",
            str(ROOT / "runtime/validation/capacity-baseline-summary.json"),
            "--skip-r4",
        ]
        steps.append(run_step("capacity baseline evidence", "capacity", cmd, 10800))

    failed = next((step for step in steps if step.get("status") != "passed"), None)
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "build_dir": str(args.build_dir.resolve()),
        "configuration": args.configuration,
        "run_2h_soak": args.run_2h_soak,
        "run_8h_soak": args.run_8h_soak,
        "run_capacity": args.run_capacity,
        "perf_repetitions": args.perf_repetitions,
        "environment": environment_snapshot(),
        "overall_pass": failed is None,
        "passed": failed is None,
        "failed_category": "" if failed is None else str(failed.get("category")),
        "failed_step": "" if failed is None else str(failed.get("name")),
        "artifacts": {
            "summary_path": str(summary_path),
            "long_soak_2h_summary_path": str(ROOT / LONG_SOAK_PRESETS["2h"]["summary_path"]) if args.run_2h_soak else "",
            "long_soak_8h_summary_path": str(ROOT / LONG_SOAK_PRESETS["8h"]["summary_path"]) if args.run_8h_soak else "",
            "capacity_summary_path": str(ROOT / "runtime/validation/capacity-baseline-summary.json") if args.run_capacity else "",
        },
        "steps": steps,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
