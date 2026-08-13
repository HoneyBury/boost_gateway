"""Performance baseline responsibility module: perf_business_operations."""

from __future__ import annotations

import argparse
import concurrent.futures
import http.server
import json
import os
import platform
import re
import shutil
import statistics
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.request import urlopen

from scripts.lib.perf_statistics import (
    distribution,
    latency_percentile,
    linear_slope,
    metric_distribution,
)

import resource

from scripts.lib.perf_process_affinity import *  # noqa: F401,F403
from scripts.lib.perf_process_runtime import *  # noqa: F401,F403
from scripts.lib.perf_otel_runtime import *  # noqa: F401,F403
from scripts.lib.perf_bench_runtime import *  # noqa: F401,F403
from scripts.lib.perf_business_protocol import *  # noqa: F401,F403
def summarize_business_operations(
    operation_names: list[str],
    records: list[dict[str, Any]],
    duration_seconds: float,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for operation in operation_names:
        operation_records = [record for record in records if record["operation"] == operation]
        latencies = [float(record["latency_ms"]) for record in operation_records]
        succeeded = sum(1 for record in operation_records if record["ok"])
        errors: dict[str, int] = {}
        for record in operation_records:
            if not record["ok"]:
                error = str(record.get("error") or "unknown")
                errors[error] = errors.get(error, 0) + 1
        summaries.append({
            "operation": operation,
            "attempted": len(operation_records),
            "succeeded": succeeded,
            "failed": len(operation_records) - succeeded,
            "throughput_ops_per_sec": round(len(operation_records) / max(duration_seconds, 0.000001), 3),
            "latency_p50_ms": latency_percentile(latencies, 0.50),
            "latency_p99_ms": latency_percentile(latencies, 0.99),
            "errors": errors,
        })
    return summaries

def run_leaderboard_scenario(
    host: str,
    port: int,
    clients: int,
    iterations: int,
    timeout_seconds: float,
    run_id: str,
    persistence_mode: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as executor:
        futures = [
            executor.submit(
                run_business_operation_worker,
                host,
                port,
                "leaderboard",
                client_index,
                iterations,
                timeout_seconds,
                run_id,
            )
            for client_index in range(clients)
        ]
        workers = [future.result() for future in futures]
    duration_seconds = round(time.perf_counter() - started, 6)
    records = [record for worker in workers for record in worker["records"]]
    operation_names = [item[0] for item in BUSINESS_OPERATION_SEQUENCES["leaderboard"]]
    operations = summarize_business_operations(operation_names, records, duration_seconds)
    setup_failures = [
        {"client_index": worker["client_index"], "error": worker["setup_error"]}
        for worker in workers
        if worker["setup_error"]
    ]
    expected_per_operation = clients * iterations
    passed = not setup_failures and all(
        operation["attempted"] == expected_per_operation and operation["failed"] == 0
        for operation in operations
    )
    return {
        "scenario": "leaderboard",
        "passed": passed,
        "clients": clients,
        "iterations_per_client": iterations,
        "duration_seconds": duration_seconds,
        "expected_per_operation": expected_per_operation,
        "setup_failures": setup_failures,
        "persistence_mode": persistence_mode,
        "redis_comparison": False,
        "operations": operations,
    }

def aggregate_business_operation_runs(
    scenarios: list[str],
    runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    aggregates: list[dict[str, Any]] = []
    for scenario_name in scenarios:
        scenario_runs = [
            scenario
            for run in runs
            for scenario in run["scenarios"]
            if scenario["scenario"] == scenario_name
        ]
        operation_names = [item[0] for item in BUSINESS_OPERATION_SEQUENCES[scenario_name]]
        operation_aggregates: list[dict[str, Any]] = []
        for operation_name in operation_names:
            operation_runs = [
                operation
                for scenario in scenario_runs
                for operation in scenario["operations"]
                if operation["operation"] == operation_name
            ]
            operation_aggregates.append({
                "operation": operation_name,
                "attempted": sum(int(operation["attempted"]) for operation in operation_runs),
                "succeeded": sum(int(operation["succeeded"]) for operation in operation_runs),
                "failed": sum(int(operation["failed"]) for operation in operation_runs),
                "throughput_ops_per_sec": metric_distribution([
                    float(operation["throughput_ops_per_sec"]) for operation in operation_runs
                ]),
                "latency_p50_ms": metric_distribution([
                    float(operation["latency_p50_ms"])
                    for operation in operation_runs
                    if operation["latency_p50_ms"] is not None
                ]),
                "latency_p99_ms": metric_distribution([
                    float(operation["latency_p99_ms"])
                    for operation in operation_runs
                    if operation["latency_p99_ms"] is not None
                ]),
            })
        aggregate: dict[str, Any] = {
            "scenario": scenario_name,
            "runs": len(scenario_runs),
            "passed_runs": sum(1 for scenario in scenario_runs if scenario["passed"]),
            "passed": len(scenario_runs) == len(runs) and all(scenario["passed"] for scenario in scenario_runs),
            "operations": operation_aggregates,
        }
        if scenario_name == "matchmaking":
            time_to_match_samples = [
                float(value)
                for scenario in scenario_runs
                for value in scenario.get("time_to_match_samples_ms", [])
            ]
            aggregate.update({
                "setup_retry_count": sum(
                    int(scenario.get("setup_retry_count", 0)) for scenario in scenario_runs
                ),
                "time_to_match_samples": len(time_to_match_samples),
                "time_to_match_p50_ms": latency_percentile(time_to_match_samples, 0.50),
                "time_to_match_p99_ms": latency_percentile(time_to_match_samples, 0.99),
            })
        if scenario_name == "leaderboard":
            persistence_modes = sorted({str(scenario["persistence_mode"]) for scenario in scenario_runs})
            aggregate.update({
                "persistence_mode": persistence_modes[0] if len(persistence_modes) == 1 else "mixed",
                "redis_comparison": False,
            })
        aggregates.append(aggregate)
    return aggregates

def run_business_operation_perf(
    host: str,
    port: int,
    scenarios: list[str],
    clients: int,
    iterations: int,
    timeout_seconds: float,
    repetitions: int = 1,
    leaderboard_persistence_mode: str = "in_memory_only",
    resource_sample_callback: Callable[[int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected_scenarios = list(dict.fromkeys(scenarios))
    if clients <= 0 or iterations <= 0 or repetitions <= 0 or timeout_seconds <= 0:
        raise ValueError("business operation clients, iterations, repetitions, and timeout must be positive")
    if "matchmaking" in selected_scenarios and clients % 2 != 0:
        raise ValueError("1v1 matchmaking requires an even client count")
    runs: list[dict[str, Any]] = []
    for repetition in range(repetitions):
        scenario_summaries: list[dict[str, Any]] = []
        for scenario in selected_scenarios:
            run_id = f"{time.monotonic_ns()}_{repetition + 1}"
            if scenario == "matchmaking":
                scenario_summaries.append(run_matchmaking_scenario(
                    host, port, clients, iterations, timeout_seconds, run_id
                ))
            else:
                scenario_summaries.append(run_leaderboard_scenario(
                    host,
                    port,
                    clients,
                    iterations,
                    timeout_seconds,
                    run_id,
                    leaderboard_persistence_mode,
                ))
        run = {
            "run": repetition + 1,
            "passed": all(scenario["passed"] for scenario in scenario_summaries),
            "scenarios": scenario_summaries,
        }
        if resource_sample_callback is not None:
            run["resource_sample"] = resource_sample_callback(repetition + 1)
        runs.append(run)
    scenario_aggregates = aggregate_business_operation_runs(selected_scenarios, runs)
    overall_pass = len(runs) == repetitions and all(run["passed"] for run in runs)
    return {
        "summary_version": 2,
        "overall_pass": overall_pass,
        "passed": overall_pass,
        "gateway_host": host,
        "gateway_port": port,
        "clients": clients,
        "iterations_per_client": iterations,
        "requested_runs": repetitions,
        "completed_runs": len(runs),
        "runs": runs,
        "scenario_aggregates": scenario_aggregates,
        "leaderboard_persistence": {
            "mode": leaderboard_persistence_mode,
            "source": "explicit collector backend configuration",
            "redis_comparison": False,
        } if "leaderboard" in selected_scenarios else None,
    }


def run_matchmaking_scenario(
    host: str,
    port: int,
    clients: int,
    iterations: int,
    timeout_seconds: float,
    run_id: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    records: list[dict[str, Any]] = []
    setup_failures: list[dict[str, Any]] = []
    setup_retry_count = 0
    for iteration in range(iterations):
        entries, setup_retries = setup_matchmaking_cohort(
            host, port, clients, timeout_seconds, run_id, iteration
        )
        setup_retry_count += setup_retries
        setup_failures.extend(
            {"iteration": iteration, "client_index": entry["client_index"], "error": entry["error"]}
            for entry in entries
            if entry["error"]
        )
        active = [entry for entry in entries if entry["client"] is not None]
        try:
            match_started = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(active))) as executor:
                join_records = list(executor.map(
                    lambda entry: execute_match_request(entry, "match_join", 6001, 6002, timeout_seconds),
                    active,
                ))
            records.extend(join_records)

            match_deadline = time.monotonic() + timeout_seconds
            joined = [entry for entry, record in zip(active, join_records, strict=True) if record["ok"]]
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(joined))) as executor:
                status_records = list(executor.map(
                    lambda entry: poll_until_matched(entry, match_started, match_deadline),
                    joined,
                ))
            records.extend(status_records)

            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(active))) as executor:
                records.extend(executor.map(
                    lambda entry: execute_match_request(entry, "match_leave", 6004, 6005, timeout_seconds),
                    active,
                ))
        finally:
            for entry in active:
                entry["client"].close()

    duration_seconds = round(time.perf_counter() - started, 6)
    operations = summarize_business_operations(
        ["match_join", "match_status", "match_leave"], records, duration_seconds
    )
    expected_per_operation = clients * iterations
    matched_latencies = [
        float(record["latency_ms"])
        for record in records
        if record["operation"] == "match_status" and record["ok"]
    ]
    passed = not setup_failures and all(
        operation["attempted"] == expected_per_operation and operation["failed"] == 0
        for operation in operations
    )
    return {
        "scenario": "matchmaking",
        "passed": passed,
        "clients": clients,
        "iterations_per_client": iterations,
        "duration_seconds": duration_seconds,
        "expected_per_operation": expected_per_operation,
        "setup_failures": setup_failures,
        "setup_retry_count": setup_retry_count,
        "status_poll_attempts": sum(int(record.get("poll_attempts", 0)) for record in records),
        "time_to_match_samples_ms": matched_latencies,
        "time_to_match_p50_ms": latency_percentile(matched_latencies, 0.50),
        "time_to_match_p99_ms": latency_percentile(matched_latencies, 0.99),
        "operations": operations,
    }
