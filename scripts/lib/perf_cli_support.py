"""Argument, layout, and initial evidence contracts for performance collection."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.lib.perf_release_contract import *  # noqa: F401,F403

PERF_BASELINE_ARGUMENTS = (
    ("--build-dir", {"default": str(Path("build/release").resolve())}),
    ("--run-preset", {"choices": ["smoke", "baseline", "capacity", "business-capacity", "saturation", "business-saturation", "business-open-saturation"], "default": "smoke"}),
    ("--repetitions", {"type": int, "default": 1}),
    ("--gateway-port", {"type": int, "default": 9201}),
    ("--login-port", {"type": int, "default": 9202}),
    ("--room-port", {"type": int, "default": 9302}),
    ("--battle-port", {"type": int, "default": 9303}),
    ("--matchmaking-port", {"type": int, "default": 9304}),
    ("--leaderboard-port", {"type": int, "default": 9305}),
    ("--http-port", {"type": int, "default": 9080}),
    ("--io-cores", {"type": int, "default": 4}),
    ("--cpu-set", {"default": ""}),
    ("--loadgen-cpu-set", {"default": ""}),
    ("--loadgen-io-threads", {"type": int, "default": 4}),
    ("--include-business-flow", {"action": "store_true"}),
    ("--business-flow-clients", {"type": int, "default": 1}),
    ("--backend-pool-size", {"type": int, "default": 0}),
    ("--battle-frame-push-every", {"type": int, "default": 0}),
    ("--battle-route-workers", {"type": int, "default": 0}),
    ("--business-operation-scenario", {"action": "append", "choices": sorted(BUSINESS_OPERATION_SEQUENCES), "default": []}),
    ("--business-operation-clients", {"type": int, "default": 16}),
    ("--business-operation-iterations", {"type": int, "default": 10}),
    ("--business-operation-timeout-seconds", {"type": float, "default": 5.0}),
    ("--resource-stability-gate", {"action": "store_true"}),
    ("--resource-stability-windows", {"type": int, "default": 8}),
    ("--resource-stability-warmup-windows", {"type": int, "default": 2}),
    ("--resource-stability-clients", {"type": int, "default": 16}),
    ("--resource-stability-iterations", {"type": int, "default": 100}),
    ("--leaderboard-redis-comparison", {"action": "store_true"}),
    ("--leaderboard-redis-host", {"default": "127.0.0.1"}),
    ("--leaderboard-redis-port", {"type": int, "default": 6379}),
    ("--leaderboard-redis-key", {"default": ""}),
    ("--otel-comparison", {"action": "store_true"}),
    ("--case", {"action": "append", "default": []}),
    ("--output-root", {"default": ""}),
    ("--saturation-cpu-threshold-percent", {"type": float, "default": 85.0}),
    ("--saturation-loadgen-headroom-percent", {"type": float, "default": 85.0}),
)

def prepare_perf_constraints(args: argparse.Namespace) -> dict[str, Any]:
    resolved = resolve_loadgen_cpu_set(args.cpu_set, args.loadgen_cpu_set)
    service = prepare_process_cpu_affinity(args.cpu_set, "--cpu-set")
    loadgen = prepare_process_cpu_affinity(resolved, "--loadgen-cpu-set")
    if resolved:
        collector = apply_cpu_affinity(resolved)
        loadgen["processes"].append({
            "workload": "python_collector_and_business_clients", "pid": os.getpid(),
            "requested_cpu_set": collector["requested"],
            "effective_cpu_set": collector["effective_cpu_set"],
            "verified": collector["applied"] is True,
        })
        loadgen["applied"] = collector["applied"] is True
        loadgen["effective_cpu_set"] = collector["effective_cpu_set"]
    if args.loadgen_io_threads <= 0:
        raise ValueError("--loadgen-io-threads must be positive")
    if args.run_preset in {"saturation", "business-saturation", "business-open-saturation"} and (not args.cpu_set or not resolved):
        raise ValueError("saturation presets require isolated --cpu-set and --loadgen-cpu-set capacity")
    if not 0.0 < args.saturation_cpu_threshold_percent <= 100.0:
        raise ValueError("--saturation-cpu-threshold-percent must be in (0, 100]")
    if not 0.0 < args.saturation_loadgen_headroom_percent <= 100.0:
        raise ValueError("--saturation-loadgen-headroom-percent must be in (0, 100]")
    if args.business_operation_scenario and min(args.business_operation_clients, args.business_operation_iterations, args.repetitions) <= 0:
        raise ValueError("business operation clients, iterations, and timeout must be positive")
    if args.business_operation_scenario and args.business_operation_timeout_seconds <= 0:
        raise ValueError("business operation clients, iterations, and timeout must be positive")
    if "matchmaking" in args.business_operation_scenario and args.business_operation_clients % 2:
        raise ValueError("--business-operation-clients must be even for the 1v1 matchmaking profile")
    measured_windows = args.resource_stability_windows - args.resource_stability_warmup_windows
    if args.resource_stability_gate and (
        args.resource_stability_windows < 5 or args.resource_stability_warmup_windows < 1
        or measured_windows < 3 or args.resource_stability_clients <= 0
        or args.resource_stability_clients % 2 or args.resource_stability_iterations <= 0
    ):
        raise ValueError("resource stability requires at least five windows, one warmup, three measurement windows, positive iterations, and a positive even client count")
    if args.leaderboard_redis_comparison:
        if "leaderboard" not in args.business_operation_scenario or args.repetitions < 3:
            raise ValueError("--leaderboard-redis-comparison requires leaderboard and --repetitions >= 3")
        if not 1 <= args.leaderboard_redis_port <= 65535:
            raise ValueError("--leaderboard-redis-port must be between 1 and 65535")
    if args.otel_comparison and args.repetitions < 3:
        raise ValueError("--otel-comparison requires --repetitions >= 3")
    if args.otel_comparison and not any(case["name"] == "battle-100-30s" for case in build_run_cases(args.run_preset)):
        raise ValueError("--otel-comparison requires a preset containing battle-100-30s")
    return {"resolved_loadgen_cpu_set": resolved, "service": service, "loadgen": loadgen}

def prepare_perf_layout(args: argparse.Namespace, root: Path, constraints: dict[str, Any]) -> dict[str, Any]:
    build_dir = Path(args.build_dir).resolve()
    output_root = Path(args.output_root).resolve() if args.output_root else root / "runtime" / "perf" / datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.output_root:
        for child in ("logs", "results"):
            shutil.rmtree(output_root / child, ignore_errors=True)
        for child in ("summary.json", "report.md"):
            with suppress(FileNotFoundError):
                (output_root / child).unlink()
    log_dir, result_dir = output_root / "logs", output_root / "results"
    log_dir.mkdir(parents=True, exist_ok=True); result_dir.mkdir(parents=True, exist_ok=True)
    executables = {name: resolve_executable(build_dir, binary) for name, binary in {
        "login": "v2_login_backend", "room": "v2_room_backend", "battle": "v2_battle_backend",
        "matchmaking": "v2_match_backend", "leaderboard": "v2_leaderboard_backend",
        "gateway": "v2_gateway_demo", "pressure": "v2_gateway_pressure",
    }.items()}
    run_cases = build_run_cases(args.run_preset)
    if args.case:
        selected = set(args.case); run_cases = [case for case in run_cases if case["name"] in selected]
        if not run_cases:
            raise ValueError("--case did not match any case in the selected --run-preset")
    service = constraints["service"]
    manifest = build_case_manifest(
        run_cases, service_cpu_set=str(service.get("requested", "")),
        service_cpu_count=int(service.get("cpu_count", 0)), io_cores=args.io_cores,
    )
    return {
        "build_dir": build_dir, "output_root": output_root, "log_dir": log_dir,
        "result_dir": result_dir, "executables": executables, "run_cases": run_cases,
        "case_manifest": manifest,
        "case_identity_by_name": {str(entry["case_name"]): entry for entry in manifest},
        "battle_max_frames": estimate_battle_max_frames(run_cases),
    }

def initial_perf_summary(args: argparse.Namespace, root: Path, layout: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    battle_max_frames = layout["battle_max_frames"]
    return {
        "collected_at": datetime.now().isoformat(timespec="seconds"), "host_platform": platform.platform(),
        "git_commit": git_commit(root), "preset": args.run_preset, "repetitions": args.repetitions,
        "build_dir": str(layout["build_dir"]), "output_dir": str(layout["output_root"]),
        "summary_version": 2, "case_manifest_version": 1, "case_manifest": layout["case_manifest"],
        "resource_constraint": constraints["service"], "service_resource_constraint": constraints["service"],
        "loadgen_resource_constraint": constraints["loadgen"],
        "topology": {
            "gateway_port": args.gateway_port, "login_port": args.login_port, "room_port": args.room_port,
            "battle_port": args.battle_port, "matchmaking_port": args.matchmaking_port,
            "leaderboard_port": args.leaderboard_port, "http_port": args.http_port,
            "io_cores": args.io_cores, "loadgen_io_threads": args.loadgen_io_threads,
            "battle_max_frames": battle_max_frames,
            "backend_connection_pool_size": args.backend_pool_size or int(os.environ.get("V2_BACKEND_CONNECTION_POOL_SIZE", "8")),
            "battle_frame_push_every": args.battle_frame_push_every or int(os.environ.get("V2_BATTLE_FRAME_PUSH_EVERY", "1")),
            "battle_route_workers": args.battle_route_workers or int(os.environ.get("V2_BATTLE_ROUTE_WORKERS", "8")),
        },
        "cases": [], "case_aggregates": [], "release_gates": {}, "process_snapshots": {},
        "business_flow": None, "business_operation_perf": None, "resource_stability_gate": None,
        "leaderboard_persistence_comparison": None, "otel_comparison": None,
        "saturation_analysis": None, "final_backend_metrics": {},
    }
