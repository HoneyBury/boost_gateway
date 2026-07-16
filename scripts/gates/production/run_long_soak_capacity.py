#!/usr/bin/env python3
"""Run long-soak and capacity evidence on a fixed production-validation host."""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts.lib.evidence_provenance import build_evidence_provenance


ROOT = Path(__file__).resolve().parents[3]

LONG_SOAK_PRESETS = {
    "2h": {
        "soak_profile": "long",
        "step_timeout_seconds": 16200,
        "summary_path": "runtime/validation/long-soak-2h-summary.json",
    },
    "8h": {
        "soak_profile": "overnight",
        "step_timeout_seconds": 37800,
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
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
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
        return {
            "name": name,
            "category": category,
            "command": cmd,
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(stdout),
            "stderr_tail": tail(stderr),
        }

    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "status": "passed" if proc is not None and proc.returncode == 0 else "failed",
        "returncode": proc.returncode if proc is not None else None,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/release"))
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--run-2h-soak", action="store_true")
    parser.add_argument("--run-8h-soak", action="store_true")
    parser.add_argument("--run-capacity", action="store_true")
    parser.add_argument("--run-business-capacity", action="store_true")
    parser.add_argument("--perf-repetitions", type=int, default=3)
    parser.add_argument("--business-flow-clients", type=int, default=3)
    parser.add_argument("--backend-pool-size", type=int, default=8)
    parser.add_argument("--battle-route-workers", type=int, default=8)
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


def attach_provenance(summary_path: Path, provenance: dict[str, object]) -> None:
    if not summary_path.exists():
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(summary, dict):
        return
    summary["provenance"] = provenance
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    if not any((args.run_2h_soak, args.run_8h_soak, args.run_capacity, args.run_business_capacity)):
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

    provenance = build_evidence_provenance(
        ROOT,
        build_configuration=args.configuration,
    )
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
        attach_provenance(ROOT / preset["summary_path"], provenance)

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
        attach_provenance(ROOT / preset["summary_path"], provenance)

    if args.run_capacity:
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "collect_release_baseline.py"),
            *common,
            "--perf-preset",
            "capacity",
            "--perf-repetitions",
            str(args.perf_repetitions),
            "--backend-pool-size",
            str(args.backend_pool_size),
            "--battle-route-workers",
            str(args.battle_route_workers),
            "--summary-path",
            str(ROOT / "runtime/validation/capacity-baseline-summary.json"),
            "--perf-output-root",
            str(ROOT / "runtime/perf/fixed-runner-capacity"),
            "--skip-r4",
        ]
        steps.append(run_step("capacity baseline evidence", "capacity", cmd, 10800))

    if args.run_business_capacity:
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "collect_release_baseline.py"),
            *common,
            "--perf-preset",
            "business-capacity",
            "--perf-repetitions",
            str(args.perf_repetitions),
            "--backend-pool-size",
            str(args.backend_pool_size),
            "--battle-route-workers",
            str(args.battle_route_workers),
            "--summary-path",
            str(ROOT / "runtime/validation/business-capacity-baseline-summary.json"),
            "--perf-output-root",
            str(ROOT / "runtime/perf/fixed-runner-business-capacity"),
            "--include-business-flow",
            "--business-flow-clients",
            str(args.business_flow_clients),
            "--skip-r4",
        ]
        steps.append(run_step("business-capacity baseline evidence", "business_capacity", cmd, 10800))

    failed = next((step for step in steps if step.get("status") != "passed"), None)
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "provenance": provenance,
        "build_dir": str(args.build_dir.resolve()),
        "configuration": args.configuration,
        "run_2h_soak": args.run_2h_soak,
        "run_8h_soak": args.run_8h_soak,
        "run_capacity": args.run_capacity,
        "run_business_capacity": args.run_business_capacity,
        "perf_repetitions": args.perf_repetitions,
        "business_flow_clients": args.business_flow_clients,
        "backend_pool_size": args.backend_pool_size,
        "battle_route_workers": args.battle_route_workers,
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
            "business_capacity_summary_path": str(ROOT / "runtime/validation/business-capacity-baseline-summary.json") if args.run_business_capacity else "",
            "capacity_perf_summary_path": str(ROOT / "runtime/perf/fixed-runner-capacity/summary.json") if args.run_capacity else "",
            "business_capacity_perf_summary_path": str(ROOT / "runtime/perf/fixed-runner-business-capacity/summary.json") if args.run_business_capacity else "",
        },
        "steps": steps,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary: {summary_path}")
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
