#!/usr/bin/env python3
"""Run long-soak and capacity evidence on a fixed production-validation host."""

from __future__ import annotations

import argparse
import json
import platform
import signal
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.lib.cancellable_process import (
    CancellationState,
    atomic_write_json,
    installed_signal_handlers,
    run_cancellable_process,
)
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
    parser.add_argument(
        "--capacity-case",
        action="append",
        default=[],
        help="Optional capacity preset case selection for focused diagnostics.",
    )
    parser.add_argument("--business-flow-clients", type=int, default=3)
    parser.add_argument("--backend-pool-size", type=int, default=8)
    parser.add_argument("--battle-route-workers", type=int, default=8)
    parser.add_argument("--io-cores", type=int, default=4)
    parser.add_argument(
        "--cpu-set",
        default="",
        help="Linux CPU affinity list for managed service processes in capacity collectors.",
    )
    parser.add_argument(
        "--loadgen-cpu-set",
        default="",
        help="Disjoint Linux CPU affinity list for capacity load generation.",
    )
    parser.add_argument("--loadgen-io-threads", type=int, default=4)
    parser.add_argument("--run-business-operation-perf", action="store_true")
    parser.add_argument("--business-operation-clients", type=int, default=16)
    parser.add_argument("--business-operation-iterations", type=int, default=10)
    parser.add_argument("--leaderboard-redis-comparison", action="store_true")
    parser.add_argument("--leaderboard-redis-host", default="127.0.0.1")
    parser.add_argument("--leaderboard-redis-port", type=int, default=6379)
    parser.add_argument("--leaderboard-redis-key", default="")
    parser.add_argument("--run-otel-comparison", action="store_true")
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/long-soak-capacity-summary.json"))
    args = parser.parse_args()
    if args.run_business_operation_perf and not (args.run_capacity or args.run_business_capacity):
        parser.error("--run-business-operation-perf requires --run-capacity or --run-business-capacity")
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
    if (
        args.cpu_set
        and (args.run_capacity or args.run_business_capacity)
        and not args.loadgen_cpu_set
    ):
        parser.error(
            "capacity evidence with --cpu-set requires an explicit, reusable --loadgen-cpu-set"
        )
    if args.loadgen_cpu_set and not args.cpu_set:
        parser.error("--loadgen-cpu-set requires --cpu-set")
    return args


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


def main() -> int:
    args = parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    atomic_write_json(
        summary_path,
        {
            "summary_version": 2,
            "generated_at": datetime.now(UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "overall_pass": False,
            "passed": False,
            "interrupted": False,
            "interruption_signal": "",
            "current_step": "initializing",
            "completed_steps": [],
            "failed_category": "orchestrator",
            "failed_step": "initializing",
            "steps": [],
        },
    )

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
    completed_steps: list[str] = []
    cancellation = CancellationState()
    current_step = ""
    interrupted = False
    interruption_signal = ""
    unexpected_error = ""

    def execute_step(
        name: str,
        category: str,
        command: list[str],
        timeout_seconds: int,
        artifact_path: Path | None = None,
    ) -> dict[str, object]:
        nonlocal current_step, interrupted, interruption_signal
        current_step = name
        if artifact_path is not None:
            artifact_path.unlink(missing_ok=True)
        result = run_step(name, category, command, timeout_seconds, cancellation)
        steps.append(result)
        if artifact_path is not None:
            attach_provenance(artifact_path, provenance)
            if result.get("status") == "passed":
                validation_error = validate_child_summary(artifact_path)
                if validation_error:
                    result["status"] = "failed"
                    result["artifact_validation_error"] = validation_error
                    result["stderr_tail"] = tail(
                        "\n".join(
                            part
                            for part in (
                                str(result.get("stderr_tail", "")),
                                validation_error,
                            )
                            if part
                        )
                    )
        if result.get("status") == "cancelled":
            interrupted = True
            interruption_signal = str(result.get("signal", ""))
            if not cancellation.cancelled:
                cancellation.request(int(getattr(signal, interruption_signal, signal.SIGTERM)))
        else:
            completed_steps.append(name)
            current_step = ""
        return result

    summary: dict[str, object] = {
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
        "capacity_cases": args.capacity_case,
        "business_flow_clients": args.business_flow_clients,
        "backend_pool_size": args.backend_pool_size,
        "battle_route_workers": args.battle_route_workers,
        "io_cores": args.io_cores,
        "cpu_set": args.cpu_set,
        "loadgen_cpu_set": args.loadgen_cpu_set,
        "loadgen_io_threads": args.loadgen_io_threads,
        "run_business_operation_perf": args.run_business_operation_perf,
        "business_operation_clients": args.business_operation_clients,
        "business_operation_iterations": args.business_operation_iterations,
        "leaderboard_redis_comparison": args.leaderboard_redis_comparison,
        "leaderboard_redis_host": args.leaderboard_redis_host if args.leaderboard_redis_comparison else "",
        "leaderboard_redis_port": args.leaderboard_redis_port if args.leaderboard_redis_comparison else 0,
        "run_otel_comparison": args.run_otel_comparison,
        "environment": environment_snapshot(),
        "overall_pass": False,
        "passed": False,
        "interrupted": False,
        "interruption_signal": "",
        "current_step": "",
        "completed_steps": [],
        "failed_category": "",
        "failed_step": "",
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

    with installed_signal_handlers(cancellation):
        atomic_write_json(summary_path, summary)
        try:
            if args.run_2h_soak and not cancellation.cancelled:
                preset = LONG_SOAK_PRESETS["2h"]
                cmd = [
                    sys.executable,
                    str(ROOT / "scripts" / "verify_production_resilience_gate.py"),
                    *common,
                    "--soak-profile", preset["soak_profile"],
                    "--baseline-profile", "release",
                    "--summary-path", str(ROOT / preset["summary_path"]),
                    "--step-timeout-seconds", str(preset["step_timeout_seconds"]),
                ]
                execute_step(
                    "2h long-soak evidence", "long_soak", cmd,
                    int(preset["step_timeout_seconds"]) + 300,
                    ROOT / str(preset["summary_path"]),
                )

            if args.run_8h_soak and not cancellation.cancelled:
                preset = LONG_SOAK_PRESETS["8h"]
                cmd = [
                    sys.executable,
                    str(ROOT / "scripts" / "verify_production_resilience_gate.py"),
                    *common,
                    "--soak-profile", preset["soak_profile"],
                    "--baseline-profile", "release",
                    "--summary-path", str(ROOT / preset["summary_path"]),
                    "--step-timeout-seconds", str(preset["step_timeout_seconds"]),
                ]
                execute_step(
                    "8h long-soak evidence", "long_soak", cmd,
                    int(preset["step_timeout_seconds"]) + 300,
                    ROOT / str(preset["summary_path"]),
                )

            if args.run_capacity and not cancellation.cancelled:
                cmd = [
                    sys.executable, str(ROOT / "scripts" / "collect_release_baseline.py"),
                    *common,
                    "--perf-preset", "capacity",
                    "--perf-repetitions", str(args.perf_repetitions),
                    "--backend-pool-size", str(args.backend_pool_size),
                    "--battle-route-workers", str(args.battle_route_workers),
                    "--io-cores", str(args.io_cores),
                    "--summary-path", str(ROOT / "runtime/validation/capacity-baseline-summary.json"),
                    "--perf-output-root", str(ROOT / "runtime/perf/fixed-runner-capacity"),
                    "--skip-r4",
                ]
                if args.cpu_set:
                    cmd.extend(["--cpu-set", args.cpu_set])
                if args.loadgen_cpu_set:
                    cmd.extend(["--loadgen-cpu-set", args.loadgen_cpu_set])
                cmd.extend(["--loadgen-io-threads", str(args.loadgen_io_threads)])
                for case_name in args.capacity_case:
                    cmd.extend(["--perf-case", case_name])
                if args.run_business_operation_perf and not args.run_business_capacity:
                    cmd.extend([
                        "--business-operation-scenario", "matchmaking",
                        "--business-operation-scenario", "leaderboard",
                        "--business-operation-clients", str(args.business_operation_clients),
                        "--business-operation-iterations", str(args.business_operation_iterations),
                    ])
                execute_step(
                    "capacity baseline evidence",
                    "capacity",
                    cmd,
                    10800,
                    ROOT / "runtime/validation/capacity-baseline-summary.json",
                )

            if args.run_business_capacity and not cancellation.cancelled:
                cmd = [
                    sys.executable, str(ROOT / "scripts" / "collect_release_baseline.py"),
                    *common,
                    "--perf-preset", "business-capacity",
                    "--perf-repetitions", str(args.perf_repetitions),
                    "--backend-pool-size", str(args.backend_pool_size),
                    "--battle-route-workers", str(args.battle_route_workers),
                    "--io-cores", str(args.io_cores),
                    "--summary-path", str(ROOT / "runtime/validation/business-capacity-baseline-summary.json"),
                    "--perf-output-root", str(ROOT / "runtime/perf/fixed-runner-business-capacity"),
                    "--include-business-flow",
                    "--business-flow-clients", str(args.business_flow_clients),
                    "--skip-r4",
                ]
                if args.cpu_set:
                    cmd.extend(["--cpu-set", args.cpu_set])
                if args.loadgen_cpu_set:
                    cmd.extend(["--loadgen-cpu-set", args.loadgen_cpu_set])
                cmd.extend(["--loadgen-io-threads", str(args.loadgen_io_threads)])
                if args.run_business_operation_perf:
                    cmd.extend([
                        "--business-operation-scenario", "matchmaking",
                        "--business-operation-scenario", "leaderboard",
                        "--business-operation-clients", str(args.business_operation_clients),
                        "--business-operation-iterations", str(args.business_operation_iterations),
                    ])
                if args.leaderboard_redis_comparison:
                    cmd.extend([
                        "--leaderboard-redis-comparison",
                        "--leaderboard-redis-host", args.leaderboard_redis_host,
                        "--leaderboard-redis-port", str(args.leaderboard_redis_port),
                    ])
                    if args.leaderboard_redis_key:
                        cmd.extend(["--leaderboard-redis-key", args.leaderboard_redis_key])
                if args.run_otel_comparison:
                    cmd.append("--otel-comparison")
                execute_step(
                    "business-capacity baseline evidence",
                    "business_capacity",
                    cmd,
                    10800,
                    ROOT / "runtime/validation/business-capacity-baseline-summary.json",
                )
        except Exception as exc:
            unexpected_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            def finalize_summary() -> None:
                nonlocal interrupted, interruption_signal, current_step
                if cancellation.cancelled and not interrupted:
                    interrupted = True
                    interruption_signal = cancellation.signal_name
                    if not current_step:
                        current_step = "between_steps"
                failed = next(
                    (step for step in steps if step.get("status") != "passed"), None
                )
                passed = not interrupted and not unexpected_error and failed is None
                summary.update({
                    "generated_at": datetime.now(UTC)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                    "interrupted": interrupted,
                    "interruption_signal": interruption_signal,
                    "current_step": current_step,
                    "completed_steps": completed_steps,
                    "overall_pass": passed,
                    "passed": passed,
                    "failed_category": (
                        "interrupted" if interrupted else "orchestrator"
                        if unexpected_error else "" if failed is None
                        else str(failed.get("category"))
                    ),
                    "failed_step": (
                        current_step if interrupted else unexpected_error
                        if unexpected_error else "" if failed is None
                        else str(failed.get("name"))
                    ),
                    "steps": steps,
                })
                atomic_write_json(summary_path, summary)

            finalize_summary()
            if cancellation.cancelled and summary.get("interrupted") is not True:
                finalize_summary()
            print(f"summary: {summary_path}")

    if interrupted:
        signal_number = cancellation.signal_number or getattr(signal, interruption_signal, 1)
        return 128 + int(signal_number)
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
