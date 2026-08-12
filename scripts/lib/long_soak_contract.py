#!/usr/bin/env python3
"""Run long-soak and capacity evidence on a fixed production-validation host."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import json
import os
import platform
import signal
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.lib.cancellable_process import (
    CancellationState,
    arm_parent_death_signal,
    atomic_write_json,
    installed_signal_handlers,
    run_cancellable_process,
)
from scripts.lib.evidence_provenance import build_evidence_provenance



"""Shared implementation extracted from run_long_soak_capacity.py."""

ROOT = Path(__file__).resolve().parents[2]

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


def run_step(
    name: str,
    category: str,
    cmd: list[str],
    timeout_seconds: int,
    cancellation: CancellationState | None = None,
) -> dict[str, object]:
    print(f"==> {name}", flush=True)
    result = run_cancellable_process(
        cmd,
        ROOT,
        timeout_seconds,
        cancellation or CancellationState(),
        cancellation_grace_seconds=10.0,
        timeout_grace_seconds=0.5,
    )
    stdout = str(result.get("stdout", ""))
    stderr = str(result.get("stderr", ""))

    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    return {
        "name": name,
        "category": category,
        "command": cmd,
        "status": result["status"],
        "returncode": result.get("returncode"),
        "signal": result.get("signal", ""),
        "duration_seconds": result["duration_seconds"],
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
    }


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
    atomic_write_json(summary_path, summary)


def validate_child_summary(summary_path: Path) -> str:
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"child summary is unavailable or invalid: {summary_path}: {exc}"
    if not isinstance(summary, dict):
        return f"child summary must be a JSON object: {summary_path}"
    if summary.get("overall_pass") is not True:
        return f"child summary did not pass: {summary_path}"
    return ""



def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.run_business_operation_perf and not (args.run_capacity or args.run_business_capacity):
        parser.error("--run-business-operation-perf requires --run-capacity or --run-business-capacity")
    if args.run_resource_stability_gate and not args.run_business_capacity:
        parser.error("--run-resource-stability-gate requires --run-business-capacity")
    if args.run_resource_stability_gate and (
        args.resource_stability_windows < 5
        or args.resource_stability_warmup_windows < 1
        or args.resource_stability_windows - args.resource_stability_warmup_windows < 3
        or args.resource_stability_clients <= 0
        or args.resource_stability_clients % 2 != 0
        or args.resource_stability_iterations <= 0
    ):
        parser.error(
            "resource stability requires at least five windows, one warmup, three measurement windows, "
            "positive iterations, and a positive even client count"
        )
    if args.leaderboard_redis_comparison and not (
        args.run_business_operation_perf and args.run_business_capacity
    ):
        parser.error(
            "--leaderboard-redis-comparison requires --run-business-operation-perf "
            "and --run-business-capacity"
        )
    if args.leaderboard_redis_comparison and args.perf_repetitions < 3:
        parser.error("--leaderboard-redis-comparison requires --perf-repetitions >= 3")
    if args.run_otel_comparison and not args.run_business_capacity:
        parser.error("--run-otel-comparison requires --run-business-capacity")
    if args.run_otel_comparison and args.perf_repetitions < 3:
        parser.error("--run-otel-comparison requires --perf-repetitions >= 3")
    if args.loadgen_io_threads <= 0:
        parser.error("--loadgen-io-threads must be positive")
    if args.io_cores <= 0:
        parser.error("--io-cores must be positive")
    if not 0.0 < args.saturation_cpu_threshold_percent <= 100.0:
        parser.error("--saturation-cpu-threshold-percent must be in (0, 100]")
    if not 0.0 < args.saturation_loadgen_headroom_percent <= 100.0:
        parser.error("--saturation-loadgen-headroom-percent must be in (0, 100]")
    if (
        args.cpu_set
        and (args.run_capacity or args.run_business_capacity or args.run_saturation)
        and not args.loadgen_cpu_set
    ):
        parser.error(
            "capacity evidence with --cpu-set requires an explicit, reusable --loadgen-cpu-set"
        )
    if args.loadgen_cpu_set and not args.cpu_set:
        parser.error("--loadgen-cpu-set requires --cpu-set")
    if args.run_saturation and (not args.cpu_set or not args.loadgen_cpu_set):
        parser.error(
            "--run-saturation requires explicit disjoint --cpu-set and --loadgen-cpu-set"
        )
