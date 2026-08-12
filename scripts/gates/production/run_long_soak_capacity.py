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



from scripts.lib.long_soak_contract import *  # noqa: E402,F403


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=Path("build/release"))
    parser.add_argument("--configuration", default="Release")
    for flag in (
        "skip-build", "run-2h-soak", "run-8h-soak", "run-capacity",
        "run-business-capacity", "run-saturation", "run-business-operation-perf",
        "run-resource-stability-gate", "leaderboard-redis-comparison",
        "run-otel-comparison",
    ):
        parser.add_argument(f"--{flag}", action="store_true")
    parser.add_argument(
        "--capacity-case",
        action="append",
        default=[],
        help="Optional capacity preset case selection for focused diagnostics.",
    )
    parser.add_argument(
        "--saturation-case",
        action="append",
        default=[],
        help="Optional saturation manifest case selection for fixed 1/2/4 CPU or io_cores comparisons.",
    )
    numeric_options = {
        "perf-repetitions": (int, 3), "saturation-cpu-threshold-percent": (float, 85.0),
        "saturation-loadgen-headroom-percent": (float, 85.0), "business-flow-clients": (int, 3),
        "backend-pool-size": (int, 8), "battle-route-workers": (int, 8), "io-cores": (int, 4),
        "loadgen-io-threads": (int, 4), "business-operation-clients": (int, 16),
        "business-operation-iterations": (int, 10), "resource-stability-windows": (int, 8),
        "resource-stability-warmup-windows": (int, 2), "resource-stability-clients": (int, 16),
        "resource-stability-iterations": (int, 100), "leaderboard-redis-port": (int, 6379),
    }
    for option, (value_type, default) in numeric_options.items():
        parser.add_argument(f"--{option}", type=value_type, default=default)
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
    parser.add_argument("--leaderboard-redis-host", default="127.0.0.1")
    parser.add_argument("--leaderboard-redis-key", default="")
    parser.add_argument("--summary-path", type=Path, default=Path("runtime/validation/long-soak-capacity-summary.json"))
    args = parser.parse_args()
    validate_args(args, parser)
    return args


def main() -> int:
    args = parse_args()
    parent_pid_at_start = os.getppid()
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

    if not any((
        args.run_2h_soak,
        args.run_8h_soak,
        args.run_capacity,
        args.run_business_capacity,
        args.run_saturation,
    )):
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
    parent_death_signal_armed = False

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
        "run_saturation": args.run_saturation,
        "perf_repetitions": args.perf_repetitions,
        "capacity_cases": args.capacity_case,
        "saturation_cases": args.saturation_case,
        "saturation_cpu_threshold_percent": args.saturation_cpu_threshold_percent,
        "saturation_loadgen_headroom_percent": args.saturation_loadgen_headroom_percent,
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
        "run_resource_stability_gate": args.run_resource_stability_gate,
        "resource_stability_windows": args.resource_stability_windows,
        "resource_stability_warmup_windows": args.resource_stability_warmup_windows,
        "resource_stability_clients": args.resource_stability_clients,
        "resource_stability_iterations": args.resource_stability_iterations,
        "leaderboard_redis_comparison": args.leaderboard_redis_comparison,
        "leaderboard_redis_host": args.leaderboard_redis_host if args.leaderboard_redis_comparison else "",
        "leaderboard_redis_port": args.leaderboard_redis_port if args.leaderboard_redis_comparison else 0,
        "run_otel_comparison": args.run_otel_comparison,
        "environment": environment_snapshot(),
        "parent_pid_at_start": parent_pid_at_start,
        "parent_death_signal_armed": False,
        "parent_death_signal_policy": "linux-prctl-fail-closed",
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
            "saturation_summary_path": str(ROOT / "runtime/validation/saturation-baseline-summary.json") if args.run_saturation else "",
            "saturation_perf_summary_path": str(ROOT / "runtime/perf/fixed-runner-saturation/summary.json") if args.run_saturation else "",
        },
        "steps": steps,
    }

    with installed_signal_handlers(cancellation):
        try:
            parent_death_signal_armed = arm_parent_death_signal(
                expected_parent_pid=parent_pid_at_start
            )
            summary["parent_death_signal_armed"] = parent_death_signal_armed
            environment = summary["environment"]
            if isinstance(environment, dict):
                environment["parent_pid_at_start"] = parent_pid_at_start
                environment["parent_death_signal_armed"] = parent_death_signal_armed
            atomic_write_json(summary_path, summary)

            if args.run_2h_soak and not cancellation.cancelled:
                preset = LONG_SOAK_PRESETS["2h"]
                cmd = [
                    sys.executable,
                    str(ROOT / "scripts/gates/production/verify_production_resilience_gate.py"),
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
                    str(ROOT / "scripts/gates/production/verify_production_resilience_gate.py"),
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
                    sys.executable, str(ROOT / "scripts/producers/collect_release_baseline.py"),
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

            if args.run_saturation and not cancellation.cancelled:
                cmd = [
                    sys.executable, str(ROOT / "scripts/producers/collect_release_baseline.py"),
                    *common,
                    "--perf-preset", "saturation",
                    "--perf-repetitions", str(args.perf_repetitions),
                    "--perf-timeout-seconds", "10800",
                    "--backend-pool-size", str(args.backend_pool_size),
                    "--battle-route-workers", str(args.battle_route_workers),
                    "--io-cores", str(args.io_cores),
                    "--cpu-set", args.cpu_set,
                    "--loadgen-cpu-set", args.loadgen_cpu_set,
                    "--loadgen-io-threads", str(args.loadgen_io_threads),
                    "--saturation-cpu-threshold-percent",
                    str(args.saturation_cpu_threshold_percent),
                    "--saturation-loadgen-headroom-percent",
                    str(args.saturation_loadgen_headroom_percent),
                    "--summary-path", str(ROOT / "runtime/validation/saturation-baseline-summary.json"),
                    "--perf-output-root", str(ROOT / "runtime/perf/fixed-runner-saturation"),
                    "--skip-r4",
                ]
                for case_name in args.saturation_case:
                    cmd.extend(["--perf-case", case_name])
                execute_step(
                    "saturation curve evidence",
                    "saturation",
                    cmd,
                    10800,
                    ROOT / "runtime/validation/saturation-baseline-summary.json",
                )

            if args.run_business_capacity and not cancellation.cancelled:
                cmd = [
                    sys.executable, str(ROOT / "scripts/producers/collect_release_baseline.py"),
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
                if args.run_resource_stability_gate:
                    cmd.extend([
                        "--resource-stability-gate",
                        "--resource-stability-windows", str(args.resource_stability_windows),
                        "--resource-stability-warmup-windows", str(args.resource_stability_warmup_windows),
                        "--resource-stability-clients", str(args.resource_stability_clients),
                        "--resource-stability-iterations", str(args.resource_stability_iterations),
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
