#!/usr/bin/env python3
"""Collect v2 multi-process performance baseline data across platforms."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from scripts.lib.perf_process_affinity import *  # noqa: E402,F401,F403
from scripts.lib.perf_process_runtime import *  # noqa: E402,F401,F403
from scripts.lib.perf_otel_runtime import *  # noqa: E402,F401,F403
from scripts.lib.perf_bench_runtime import *  # noqa: E402,F401,F403
from scripts.lib.perf_business_protocol import *  # noqa: E402,F401,F403
from scripts.lib.perf_business_operations import *  # noqa: E402,F401,F403
from scripts.lib.perf_stability_evidence import *  # noqa: E402,F401,F403
from scripts.lib.perf_result_aggregation import *  # noqa: E402,F401,F403
from scripts.lib.perf_resource_evidence import *  # noqa: E402,F401,F403
from scripts.lib.perf_saturation_analysis import *  # noqa: E402,F401,F403
from scripts.lib.perf_report import *  # noqa: E402,F401,F403
from scripts.lib.perf_release_contract import *  # noqa: E402,F401,F403
from scripts.lib.perf_cli_support import *  # noqa: E402,F401,F403

def main() -> int:
    parser = argparse.ArgumentParser(description="Collect v2 performance baseline data.")
    for flag, options in PERF_BASELINE_ARGUMENTS:
        parser.add_argument(flag, **options)
    args = parser.parse_args()

    try:
        constraints = prepare_perf_constraints(args)
    except (RuntimeError, ValueError, OSError) as exc:
        parser.error(str(exc))
    resolved_loadgen_cpu_set = constraints["resolved_loadgen_cpu_set"]
    service_resource_constraint = constraints["service"]
    loadgen_resource_constraint = constraints["loadgen"]

    root = Path(__file__).resolve().parents[2]
    try:
        layout = prepare_perf_layout(args, root, constraints)
    except ValueError as exc:
        parser.error(str(exc))
    build_dir = layout["build_dir"]
    output_root = layout["output_root"]
    log_dir = layout["log_dir"]
    result_dir = layout["result_dir"]
    executables = layout["executables"]
    run_cases = layout["run_cases"]
    case_manifest = layout["case_manifest"]
    case_identity_by_name = layout["case_identity_by_name"]
    battle_max_frames = layout["battle_max_frames"]
    managed: list[ManagedProcess] = []
    try:
        topology = start_perf_topology(args, root, layout, service_resource_constraint)
        managed = topology["managed"]
        battle_process = topology["battle_process"]
        leaderboard_process = topology["leaderboard_process"]
        gateway_process = topology["gateway_process"]
        gateway_args = topology["gateway_args"]
        gateway_env = topology["gateway_env"]
        in_memory_log_verified = topology["in_memory_log_verified"]

        summary = initial_perf_summary(args, root, layout, constraints)
        summary["process_snapshots"]["idle"] = snapshot_processes(managed)

        for case in run_cases:
            case_runs: list[dict[str, Any]] = []
            for repetition in range(args.repetitions):
                run_key = f"{case['name']}.run{repetition + 1}"
                connection_budget = wait_for_local_connection_budget(int(case["clients"]))
                diagnostics_before = fetch_json(
                    f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
                )
                service_before = snapshot_processes(managed)
                resource_started_at = time.monotonic()
                load_end: dict[str, Any] = {}

                def capture_load_end() -> None:
                    load_end["service_after"] = snapshot_processes(managed)
                    load_end["monotonic"] = time.monotonic()

                run_result = invoke_bench_case(
                    executables["pressure"],
                    args.gateway_port,
                    {**case, "name": run_key},
                    result_dir,
                    loadgen_cpu_set=resolved_loadgen_cpu_set,
                    loadgen_io_threads=args.loadgen_io_threads,
                    on_load_end=capture_load_end,
                )
                service_at_load_end = load_end.get("service_after")
                load_finished_at = load_end.get("monotonic")
                if not isinstance(service_at_load_end, list) or not isinstance(
                    load_finished_at, (int, float)
                ):
                    raise RuntimeError(f"missing load-end resource boundary: {run_key}")
                load_window_elapsed_seconds = max(
                    0.0, float(load_finished_at) - resource_started_at
                )
                quiescence = wait_for_service_quiescence(
                    managed,
                    f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json",
                )
                service_after_quiescence = snapshot_processes(managed)
                total_resource_elapsed_seconds = max(
                    0.0, time.monotonic() - resource_started_at
                )
                run_result["case_name"] = run_key
                run_result["base_case_name"] = case["name"]
                run_result["case_identity"] = case_identity_by_name[str(case["name"])]
                run_result["local_connection_budget"] = connection_budget
                run_result["resource_elapsed_seconds"] = round(
                    load_window_elapsed_seconds, 6
                )
                run_result["resource_total_elapsed_seconds"] = round(
                    total_resource_elapsed_seconds, 6
                )
                case_runs.append(run_result)

                loadgen_affinity = run_result["loadgen_resources"]["startup_affinity"]
                loadgen_resource_constraint["processes"].append({
                    "case_name": run_key,
                    **loadgen_affinity,
                })
                if resolved_loadgen_cpu_set:
                    loadgen_resource_constraint["applied"] = all(
                        evidence["verified"]
                        for evidence in loadgen_resource_constraint["processes"]
                    )
                    if loadgen_resource_constraint["applied"]:
                        loadgen_resource_constraint["effective_cpu_set"] = (
                            loadgen_resource_constraint["requested"]
                        )

                diagnostics = fetch_json(f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json")
                run_result["gateway_runtime_metrics"] = gateway_runtime_metric_delta(
                    diagnostics_before, diagnostics
                )
                diagnostics_path = result_dir / f"{run_key}.gateway.diagnostics.json"
                diagnostics_path.write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False), encoding="utf-8")
                summary["process_snapshots"][run_key] = build_case_resource_evidence(
                    service_before=service_before,
                    service_at_load_end=service_at_load_end,
                    loadgen=run_result["loadgen_resources"],
                    load_window_elapsed_seconds=load_window_elapsed_seconds,
                    quiescence=quiescence,
                    service_after_quiescence=service_after_quiescence,
                )

            aggregate = aggregate_case_runs(case["name"], case_runs)
            aggregate["case_identity"] = case_identity_by_name[str(case["name"])]
            summary["cases"].extend(case_runs)
            summary["case_aggregates"].append(aggregate)

        summary["release_gates"] = evaluate_release_gates(summary["case_aggregates"])
        if args.otel_comparison:
            log_step("Running fresh-Gateway OTel off/on performance comparison")
            comparison_case = next(case for case in run_cases if case["name"] == "battle-100-30s")
            gateway_process.stop()
            managed.remove(gateway_process)
            otel_collector = LoopbackOtelCollector()
            otel_collector.start()

            def run_otel_mode(mode: str, endpoint: str) -> tuple[dict[str, Any], bool, dict[str, int], dict[str, Any]]:
                nonlocal battle_process
                battle_process.stop()
                managed.remove(battle_process)
                battle_process = ManagedProcess(
                    f"v2_battle_backend.otel-{mode}",
                    executables["battle"],
                    [str(args.battle_port)],
                    log_dir,
                    cpu_set=args.cpu_set,
                )
                managed.append(battle_process)
                record_process_affinity(
                    service_resource_constraint,
                    battle_process,
                    workload=f"otel_{mode}",
                )
                wait_tcp_port("127.0.0.1", args.battle_port)
                mode_env = {**gateway_env, "OTEL_EXPORT_ENDPOINT": endpoint}
                process = ManagedProcess(
                    f"v2_gateway_demo.otel-{mode}",
                    executables["gateway"],
                    gateway_args,
                    log_dir,
                    mode_env,
                    cpu_set=args.cpu_set,
                )
                managed.append(process)
                record_process_affinity(
                    service_resource_constraint,
                    process,
                    workload=f"otel_{mode}",
                )
                try:
                    wait_tcp_port("127.0.0.1", args.gateway_port)
                    wait_tcp_port("127.0.0.1", args.http_port)
                    time.sleep(2.0)
                    mode_initial_diagnostics = fetch_json(
                        f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
                    )
                    collector_before_mode = otel_collector.snapshot()
                    mode_runs: list[dict[str, Any]] = []
                    for repetition in range(args.repetitions):
                        diagnostics_before = fetch_json(
                            f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
                        )
                        process_before = process_snapshot(process.pid)
                        collector_before = otel_collector.snapshot()
                        run = invoke_bench_case(
                            executables["pressure"],
                            args.gateway_port,
                            {
                                **comparison_case,
                                "name": f"otel-{mode}.battle-100-30s.run{repetition + 1}",
                            },
                            result_dir,
                            loadgen_cpu_set=resolved_loadgen_cpu_set,
                            loadgen_io_threads=args.loadgen_io_threads,
                        )
                        otel_loadgen_affinity = run["loadgen_resources"]["startup_affinity"]
                        loadgen_resource_constraint["processes"].append({
                            "workload": f"otel_{mode}",
                            "case_name": f"otel-{mode}.battle-100-30s.run{repetition + 1}",
                            **otel_loadgen_affinity,
                        })
                        loadgen_resource_constraint["applied"] = all(
                            evidence.get("verified") is True
                            for evidence in loadgen_resource_constraint["processes"]
                        )
                        diagnostics_after = fetch_json(
                            f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
                        )
                        process_after = process_snapshot(process.pid)
                        collector_after = otel_collector.snapshot()
                        cpu_before = process_before.get("cpu_seconds")
                        cpu_after = process_after.get("cpu_seconds")
                        run.update({
                            "case_name": f"otel-{mode}.battle-100-30s.run{repetition + 1}",
                            "base_case_name": "battle-100-30s",
                            "otel_mode": mode,
                            "gateway_resources": {
                                "cpu_seconds_before": cpu_before,
                                "cpu_seconds_after": cpu_after,
                                "cpu_seconds_delta": round(float(cpu_after) - float(cpu_before), 3)
                                if cpu_before is not None and cpu_after is not None else None,
                                "rss_mb_after": process_after.get("working_set_mb", 0.0),
                                "cpu_affinity": process_after.get("cpu_affinity", ""),
                                "pid": process.pid,
                            },
                            "backend_routed_requests": (
                                total_backend_requests(diagnostics_after)
                                - total_backend_requests(diagnostics_before)
                            ),
                            "collector_delta": counter_delta(collector_after, collector_before),
                            "exporter_metrics_after": otel_exporter_metrics(diagnostics_after),
                        })
                        mode_runs.append(run)
                    final_diagnostics = wait_for_otel_mode_quiescence(
                        f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json",
                        mode=mode,
                        initial_backend_requests=total_backend_requests(mode_initial_diagnostics),
                    )
                    log_text = process.log_text()
                    marker_present = "OTLP export enabled" in log_text
                    log_verified = marker_present if mode == "on" else not marker_present
                    collector_delta_mode = counter_delta(
                        otel_collector.snapshot(), collector_before_mode
                    )
                    mode_backend_routed_requests = (
                        total_backend_requests(final_diagnostics)
                        - total_backend_requests(mode_initial_diagnostics)
                    )
                    return (
                        aggregate_otel_mode(
                            mode,
                            mode_runs,
                            mode_backend_routed_requests,
                            battle_process.pid,
                        ),
                        log_verified,
                        collector_delta_mode,
                        otel_exporter_metrics(final_diagnostics),
                    )
                finally:
                    process.stop()
                    managed.remove(process)

            try:
                off_mode, off_log, off_collector, off_exporter = run_otel_mode("off", "")
                on_mode, on_log, on_collector, on_exporter = run_otel_mode(
                    "on", otel_collector.endpoint
                )
                summary["otel_comparison"] = build_otel_comparison(
                    off_mode,
                    on_mode,
                    repetitions=args.repetitions,
                    off_log_verified=off_log,
                    on_log_verified=on_log,
                    collector_off=off_collector,
                    collector_on=on_collector,
                    off_exporter=off_exporter,
                    on_exporter=on_exporter,
                )
            finally:
                otel_collector.stop()

            summary["release_gates"].setdefault("checks", []).append({
                "case": "otel-off-on-comparison",
                "passed": summary["otel_comparison"]["verified"] is True,
                "criteria": (
                    "fresh Gateway and Battle Backend per OTel mode, battle-100 at least three runs per process; absolute gate, "
                    "runtime exporter counters, backend route and loopback collector proof agree"
                ),
                "observed": {
                    "repetitions_per_mode": args.repetitions,
                    "performance_regression_policy": "observed_not_thresholded",
                    "proof": summary["otel_comparison"]["proof"],
                },
            })
            if summary["otel_comparison"]["verified"] is not True:
                summary["release_gates"]["overall_pass"] = False

            gateway_process = ManagedProcess(
                "v2_gateway_demo.post-otel",
                executables["gateway"],
                gateway_args,
                log_dir,
                gateway_env,
                cpu_set=args.cpu_set,
            )
            managed.append(gateway_process)
            record_process_affinity(
                service_resource_constraint,
                gateway_process,
                workload="post_otel",
            )
            wait_tcp_port("127.0.0.1", args.gateway_port)
            wait_tcp_port("127.0.0.1", args.http_port)
            time.sleep(2.0)
        if args.business_operation_scenario:
            selected_scenarios = list(dict.fromkeys(args.business_operation_scenario))
            log_step(f"Running concurrent business operation performance: {', '.join(selected_scenarios)}")
            business_service_before = snapshot_processes(managed)
            business_loadgen_before = process_snapshot(os.getpid())
            business_resource_started_at = time.monotonic()
            business_diagnostics_before = fetch_json(
                f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
            )
            summary["business_operation_perf"] = run_business_operation_perf(
                "127.0.0.1",
                args.gateway_port,
                selected_scenarios,
                args.business_operation_clients,
                args.business_operation_iterations,
                args.business_operation_timeout_seconds,
                args.repetitions,
                "in_memory_only",
            )
            business_diagnostics_after = fetch_json(
                f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
            )
            summary["business_operation_perf"]["gateway_runtime_metrics"] = (
                gateway_runtime_metric_delta(
                    business_diagnostics_before, business_diagnostics_after
                )
            )
            business_quiescence = wait_for_service_quiescence(
                managed,
                f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json",
            )
            summary["business_operation_perf"]["resource_evidence"] = build_resource_window(
                business_service_before,
                snapshot_processes(managed),
                business_loadgen_before,
                process_snapshot(os.getpid()),
                max(0.0, time.monotonic() - business_resource_started_at),
                business_quiescence,
            )
            if args.leaderboard_redis_comparison:
                redis_key = args.leaderboard_redis_key.strip() or (
                    f"lb:perf:{summary['git_commit'][:12]}:{os.getpid()}:{time.monotonic_ns()}"
                )
                ping_before = redis_command(
                    args.leaderboard_redis_host,
                    args.leaderboard_redis_port,
                    "PING",
                ) == "PONG"
                if not ping_before:
                    raise RuntimeError("Redis comparison endpoint did not respond to PING")
                redis_command(args.leaderboard_redis_host, args.leaderboard_redis_port, "DEL", redis_key)
                redis_command(args.leaderboard_redis_host, args.leaderboard_redis_port, "DEL", f"{redis_key}:names")

                log_step("Restarting leaderboard topology for Redis persistence comparison")
                gateway_process.stop()
                managed.remove(gateway_process)
                leaderboard_process.stop()
                managed.remove(leaderboard_process)

                leaderboard_process = ManagedProcess(
                    "v2_leaderboard_backend.redis",
                    executables["leaderboard"],
                    [str(args.leaderboard_port)],
                    log_dir,
                    {
                        "SERVICE_PORT": str(args.leaderboard_port),
                        "LEADERBOARD_PORT": str(args.leaderboard_port),
                        "LEADERBOARD_CONFIG_PATH": str(
                            root / "config/environments/local/leaderboard.json"
                        ),
                        "REDIS_HOST": args.leaderboard_redis_host,
                        "REDIS_PORT": str(args.leaderboard_redis_port),
                        "REDIS_LEADERBOARD_KEY": redis_key,
                        "BOOST_DISABLE_REDIS_AUTO_CONNECT": "0",
                        "BOOST_LOG_LEVEL": "info",
                    },
                    cpu_set=args.cpu_set,
                )
                managed.append(leaderboard_process)
                record_process_affinity(
                    service_resource_constraint,
                    leaderboard_process,
                    workload="leaderboard_redis",
                )
                wait_tcp_port("127.0.0.1", args.leaderboard_port)
                redis_log_marker = (
                    "Redis leaderboard and event store enabled "
                    f"({args.leaderboard_redis_host}:{args.leaderboard_redis_port})"
                )
                redis_log_verified = wait_process_log(leaderboard_process, redis_log_marker)
                if not redis_log_verified:
                    raise RuntimeError("Redis leaderboard startup did not emit the required enabled marker")

                gateway_process = ManagedProcess(
                    "v2_gateway_demo.redis",
                    executables["gateway"],
                    gateway_args,
                    log_dir,
                    gateway_env,
                    cpu_set=args.cpu_set,
                )
                managed.append(gateway_process)
                record_process_affinity(
                    service_resource_constraint,
                    gateway_process,
                    workload="leaderboard_redis",
                )
                wait_tcp_port("127.0.0.1", args.gateway_port)
                wait_tcp_port("127.0.0.1", args.http_port)
                time.sleep(2.0)
                redis_service_before = snapshot_processes(managed)
                redis_loadgen_before = process_snapshot(os.getpid())
                redis_resource_started_at = time.monotonic()
                redis_diagnostics_before = fetch_json(
                    f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
                )
                redis_perf = run_business_operation_perf(
                    "127.0.0.1",
                    args.gateway_port,
                    ["leaderboard"],
                    args.business_operation_clients,
                    args.business_operation_iterations,
                    args.business_operation_timeout_seconds,
                    args.repetitions,
                    "redis_primary_with_memory_shadow",
                )
                redis_diagnostics_after = fetch_json(
                    f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
                )
                redis_perf["gateway_runtime_metrics"] = gateway_runtime_metric_delta(
                    redis_diagnostics_before, redis_diagnostics_after
                )
                redis_quiescence = wait_for_service_quiescence(
                    managed,
                    f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json",
                )
                redis_perf["resource_evidence"] = build_resource_window(
                    redis_service_before,
                    snapshot_processes(managed),
                    redis_loadgen_before,
                    process_snapshot(os.getpid()),
                    max(0.0, time.monotonic() - redis_resource_started_at),
                    redis_quiescence,
                )
                ping_after = redis_command(
                    args.leaderboard_redis_host,
                    args.leaderboard_redis_port,
                    "PING",
                ) == "PONG"
                redis_zcard_raw = redis_command(
                    args.leaderboard_redis_host,
                    args.leaderboard_redis_port,
                    "ZCARD",
                    redis_key,
                )
                redis_zcard = redis_zcard_raw if isinstance(redis_zcard_raw, int) else -1
                comparison = build_leaderboard_persistence_comparison(
                    summary["business_operation_perf"],
                    redis_perf,
                    repetitions=args.repetitions,
                    redis_host=args.leaderboard_redis_host,
                    redis_port=args.leaderboard_redis_port,
                    redis_key=redis_key,
                    in_memory_log_verified=in_memory_log_verified,
                    redis_log_verified=redis_log_verified,
                    ping_before=ping_before,
                    ping_after=ping_after,
                    redis_zcard=redis_zcard,
                    expected_min_zcard=args.business_operation_clients * args.repetitions,
                )
                summary["leaderboard_persistence_comparison"] = comparison
                summary["business_operation_perf"]["leaderboard_persistence"]["redis_comparison"] = True
                summary["business_operation_perf"]["leaderboard_persistence"]["comparison_verified"] = comparison["verified"]
                summary["business_operation_perf"]["passed"] = (
                    summary["business_operation_perf"]["passed"] and comparison["verified"]
                )
                summary["business_operation_perf"]["overall_pass"] = summary["business_operation_perf"]["passed"]
            business_operation_path = result_dir / "business-operation-perf.json"
            business_operation_path.write_text(
                json.dumps(summary["business_operation_perf"], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            completed_business_runs = int(summary["business_operation_perf"]["completed_runs"])
            aggregate_run_counts = [
                int(item["runs"])
                for item in summary["business_operation_perf"]["scenario_aggregates"]
            ]
            business_operation_passed = (
                bool(summary["business_operation_perf"]["passed"])
                and completed_business_runs == args.repetitions
                and all(count == args.repetitions for count in aggregate_run_counts)
                and (
                    not args.leaderboard_redis_comparison
                    or summary["leaderboard_persistence_comparison"]["verified"] is True
                )
            )
            summary["release_gates"].setdefault("checks", []).append({
                "case": "concurrent-business-operations",
                "passed": business_operation_passed,
                "criteria": "all requested runs and matchmaking/leaderboard operations complete without failure",
                "observed": {
                    "scenarios": selected_scenarios,
                    "clients": args.business_operation_clients,
                    "iterations_per_client": args.business_operation_iterations,
                    "requested_runs": args.repetitions,
                    "completed_runs": completed_business_runs,
                    "aggregate_run_counts": aggregate_run_counts,
                    "leaderboard_redis_comparison": args.leaderboard_redis_comparison,
                },
            })
            if not business_operation_passed:
                summary["release_gates"]["overall_pass"] = False
            summary["process_snapshots"]["business-operation-perf"] = snapshot_processes(managed)
        if args.resource_stability_gate:
            log_step("Running accelerated resource stability gate")
            diagnostics_url = (
                f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
            )

            def capture_resource_window(window: int) -> dict[str, Any]:
                full_flow = run_business_flow_case(
                    root,
                    build_dir,
                    output_root,
                    gateway_host="127.0.0.1",
                    gateway_port=args.gateway_port,
                    concurrent_clients=1,
                )
                quiescence = wait_for_service_quiescence(managed, diagnostics_url)
                return {
                    "window": window,
                    "full_flow": full_flow,
                    "quiescence": quiescence,
                    "services": snapshot_processes(managed),
                }

            resource_workload = run_business_operation_perf(
                "127.0.0.1",
                args.gateway_port,
                ["matchmaking", "leaderboard"],
                args.resource_stability_clients,
                args.resource_stability_iterations,
                args.business_operation_timeout_seconds,
                args.resource_stability_windows,
                (
                    "redis_primary_with_memory_shadow"
                    if args.leaderboard_redis_comparison
                    else "in_memory_only"
                ),
                resource_sample_callback=capture_resource_window,
            )
            resource_samples = [
                run["resource_sample"]
                for run in resource_workload["runs"]
                if isinstance(run.get("resource_sample"), dict)
            ]
            resource_gate = evaluate_resource_stability_gate(
                resource_samples,
                warmup_windows=args.resource_stability_warmup_windows,
                required_services=[process.name for process in managed],
                require_full_flow=True,
            )
            full_flow_windows_passed = all(
                isinstance(sample.get("full_flow"), dict)
                and sample["full_flow"].get("passed") is True
                for sample in resource_samples
            )
            resource_gate["workload"] = {
                "passed": resource_workload["passed"],
                "clients": args.resource_stability_clients,
                "iterations_per_client": args.resource_stability_iterations,
                "windows": args.resource_stability_windows,
                "scenarios": ["matchmaking", "leaderboard"],
                "full_flow_windows_passed": full_flow_windows_passed,
                "summary": resource_workload,
            }
            resource_gate["passed"] = (
                bool(resource_gate["passed"])
                and bool(resource_workload["passed"])
                and full_flow_windows_passed
            )
            summary["resource_stability_gate"] = resource_gate
            resource_gate_path = result_dir / "resource-stability-gate.json"
            resource_gate_path.write_text(
                json.dumps(resource_gate, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            summary["release_gates"].setdefault("checks", []).append({
                "case": "accelerated-resource-stability",
                "passed": resource_gate["passed"],
                "criteria": (
                    "all matchmaking/leaderboard windows pass and post-warmup Gateway/backend "
                    "RSS slope, RSS tail growth, file descriptors, and threads remain bounded"
                ),
                "observed": {
                    "windows": args.resource_stability_windows,
                    "warmup_windows": args.resource_stability_warmup_windows,
                    "clients": args.resource_stability_clients,
                    "iterations_per_client": args.resource_stability_iterations,
                    "thresholds": resource_gate["thresholds"],
                    "services": resource_gate["services"],
                },
            })
            if not resource_gate["passed"]:
                summary["release_gates"]["overall_pass"] = False
        summary["resource_analysis"] = analyze_resources(summary)
        resource_isolation_check = evaluate_resource_isolation_evidence(summary)
        summary["release_gates"].setdefault("checks", []).append(resource_isolation_check)
        if not resource_isolation_check["passed"]:
            summary["release_gates"]["overall_pass"] = False
        if args.run_preset in {"saturation", "business-saturation", "business-open-saturation"}:
            summary["saturation_analysis"] = build_saturation_analysis(
                summary,
                cpu_threshold_percent=args.saturation_cpu_threshold_percent,
                loadgen_headroom_threshold_percent=args.saturation_loadgen_headroom_percent,
            )
        final_diagnostics = fetch_json(f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json")
        summary["final_backend_metrics"] = final_diagnostics.get("backend_metrics", {})
        (result_dir / "final.gateway.diagnostics.json").write_text(
            json.dumps(final_diagnostics, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if args.include_business_flow:
            log_step("Running SDK full-flow business coverage")
            summary["business_flow"] = run_business_flow_case(
                root,
                build_dir,
                output_root,
                gateway_host="127.0.0.1",
                gateway_port=args.gateway_port,
                concurrent_clients=max(1, args.business_flow_clients),
            )
            if not summary["business_flow"].get("passed"):
                summary["release_gates"].setdefault("checks", []).append({
                    "case": "sdk-full-flow-business-path",
                    "passed": False,
                    "criteria": "SDK full-flow covers login/room/battle/matchmaking/leaderboard/settlement",
                    "observed": {"duration_seconds": summary["business_flow"].get("duration_seconds")},
                })
                summary["release_gates"]["overall_pass"] = False
        summary["n1_profiles"] = {
            "run_preset": args.run_preset,
            "business_flow_clients": max(1, args.business_flow_clients),
            "supports_long_soak_followup": True,
            "supports_capacity_followup": args.run_preset in {"capacity", "business-capacity"},
            "supports_saturation_comparison": args.run_preset in {"saturation", "business-saturation", "business-open-saturation"},
        }

        summary_path = output_root / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        report_path = output_root / "report.md"
        report_path.write_text(render_markdown_report(summary), encoding="utf-8")
        log_step(f"Baseline collection completed: {output_root}")
        log_step(f"Markdown report written: {report_path}")
        if args.run_preset in {"saturation", "business-saturation", "business-open-saturation"}:
            analysis = summary.get("saturation_analysis")
            if isinstance(analysis, dict) and analysis.get("collection_pass") is True:
                return 0
            log_step("Saturation evidence collection is invalid")
            return 2
        if (
            (
                args.run_preset == "smoke"
                and not args.business_operation_scenario
                and not args.resource_stability_gate
            )
            or summary["release_gates"].get("overall_pass")
        ):
            return 0
        log_step("Release performance gates failed")
        return 2
    finally:
        for proc in reversed(managed):
            proc.stop()


if __name__ == "__main__":
    sys.exit(main())
