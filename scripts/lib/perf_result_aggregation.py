"""Performance baseline responsibility module: perf_result_aggregation."""

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

try:
    import resource
except ImportError:  # pragma: no cover - unavailable on Windows
    resource = None  # type: ignore[assignment]

from scripts.lib.perf_process_affinity import *  # noqa: F401,F403
from scripts.lib.perf_process_runtime import *  # noqa: F401,F403
from scripts.lib.perf_otel_runtime import *  # noqa: F401,F403
from scripts.lib.perf_bench_runtime import *  # noqa: F401,F403
from scripts.lib.perf_business_protocol import *  # noqa: F401,F403
from scripts.lib.perf_business_operations import *  # noqa: F401,F403
from scripts.lib.perf_stability_evidence import *  # noqa: F401,F403
def aggregate_case_runs(case_name: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    def steady_window_valid(run: dict[str, Any]) -> bool:
        if run.get("steady_state_completed") is not True:
            return False
        target = float(run.get("steady_state_target_seconds", 0.0))
        elapsed = float(run.get("steady_state_elapsed_seconds", 0.0))
        if elapsed >= max(0.0, target - 0.25):
            return True
        return (
            case_name.startswith("battle")
            and elapsed > 0.0
            and str(run.get("termination_reason", ""))
            in {"natural_completion", "clients_completed"}
        )

    def numeric_series(key: str) -> list[float]:
        return [float(run.get(key, 0.0)) for run in runs]

    throughput = numeric_series("throughput_msg_per_sec")
    p50 = numeric_series("latency_p50_ms")
    p90 = numeric_series("latency_p90_ms")
    p99 = numeric_series("latency_p99_ms")
    totals = [int(run.get("total_messages", 0)) for run in runs]
    responses = [int(run.get("response_messages", 0)) for run in runs]
    successful_responses = [
        int(run.get("successful_response_messages", run.get("response_messages", 0)))
        for run in runs
    ]
    pushes = [int(run.get("push_messages", 0)) for run in runs]
    connected = [int(run.get("connected_clients", 0)) for run in runs]
    target = [int(run.get("target_clients", 0)) for run in runs]
    started = [int(run.get("started_clients", 0)) for run in runs]
    tcp_connected = [int(run.get("tcp_connected_clients", 0)) for run in runs]
    authenticated = [int(run.get("authenticated_clients", 0)) for run in runs]
    active = [int(run.get("active_clients", 0)) for run in runs]
    peak_active = [int(run.get("peak_active_clients", 0)) for run in runs]
    cancelled = [int(run.get("cancelled_clients", 0)) for run in runs]
    cancelled_before_connect = [int(run.get("cancelled_before_connect", 0)) for run in runs]
    ramp_up = numeric_series("ramp_up_seconds")
    ramp_timeout = numeric_series("ramp_timeout_seconds")
    steady_target = numeric_series("steady_state_target_seconds")
    steady_elapsed = numeric_series("steady_state_elapsed_seconds")
    configured_rate_ceiling = numeric_series("configured_request_rate_ceiling_ops_per_sec")
    achieved_send_rate = numeric_series("achieved_send_rate_ops_per_sec")
    achieved_response_rate = numeric_series("achieved_response_rate_ops_per_sec")
    achieved_successful_response_rate = [
        float(run.get(
            "achieved_successful_response_rate_ops_per_sec",
            run.get("achieved_response_rate_ops_per_sec", 0.0),
        ))
        for run in runs
    ]
    send_attempts = [int(run.get("business_send_attempts", 0)) for run in runs]
    send_successes = [int(run.get("business_send_successes", 0)) for run in runs]
    response_errors = [int(run.get("business_response_errors", 0)) for run in runs]
    scheduled_offers = [int(run.get("open_loop_scheduled_offers", 0)) for run in runs]
    schedule_lag_average = numeric_series("open_loop_average_schedule_lag_us")
    schedule_lag_max = [int(run.get("open_loop_max_schedule_lag_us", 0)) for run in runs]
    rejected = [int(run.get("rejected_clients", 0)) for run in runs]
    failed = [int(run.get("failed_clients", 0)) for run in runs]
    forced_timeouts = [bool(run.get("forced_timeout") or run.get("collector_forced_timeout")) for run in runs]
    bench_exit_codes = [int(run.get("bench_exit_code", 0)) for run in runs]
    if case_name.startswith("echo"):
        message_count_consistent = all(
            int(run.get("total_messages", 0))
            == int(run.get("response_messages", 0))
            for run in runs
        )
    else:
        message_count_consistent = all(
            int(run.get("total_messages", 0))
            == int(run.get("response_messages", 0)) + int(run.get("push_messages", 0))
            for run in runs
        )

    def integer_distribution(values: list[int]) -> dict[str, int]:
        return {
            "min": min(values),
            "median": int(statistics.median(values)),
            "max": max(values),
        }

    def numeric_distribution(values: list[float]) -> dict[str, float]:
        return {
            "min": min(values),
            "median": statistics.median(values),
            "max": max(values),
        }

    runtime_metric_names = (
        "gateway_queue_processed_items",
        "gateway_queue_batches",
        "gateway_queue_average_batch_size",
        "gateway_queue_average_wait_us",
        "gateway_queue_lifetime_max_wait_us",
        "gateway_queue_average_handle_us",
        "gateway_queue_lifetime_max_handle_us",
        "gateway_queue_lifetime_peak_depth",
        "battle_route_completed_tasks",
        "battle_route_average_queue_wait_us",
        "battle_route_average_task_execution_us",
        "backend_requests",
        "backend_average_latency_us",
    )
    runtime_metrics = {
        metric: numeric_distribution([
            float(run["gateway_runtime_metrics"][metric])
            for run in runs
            if isinstance(run.get("gateway_runtime_metrics"), dict)
            and run["gateway_runtime_metrics"].get(metric) is not None
        ])
        for metric in runtime_metric_names
        if any(
            isinstance(run.get("gateway_runtime_metrics"), dict)
            and run["gateway_runtime_metrics"].get(metric) is not None
            for run in runs
        )
    }

    return {
        "case_name": case_name,
        "runs": len(runs),
        "throughput_msg_per_sec": {
            "min": min(throughput),
            "median": statistics.median(throughput),
            "max": max(throughput),
        },
        "latency_p50_ms": {
            "min": min(p50),
            "median": statistics.median(p50),
            "max": max(p50),
        },
        "latency_p90_ms": {
            "min": min(p90),
            "median": statistics.median(p90),
            "max": max(p90),
        },
        "latency_p99_ms": {
            "min": min(p99),
            "median": statistics.median(p99),
            "max": max(p99),
        },
        "total_messages": {
            "min": min(totals),
            "median": int(statistics.median(totals)),
            "max": max(totals),
        },
        "response_messages": integer_distribution(responses),
        "successful_response_messages": integer_distribution(successful_responses),
        "push_messages": integer_distribution(pushes),
        "message_count_consistent": message_count_consistent,
        "target_clients": integer_distribution(target),
        "started_clients": integer_distribution(started),
        "tcp_connected_clients": integer_distribution(tcp_connected),
        "authenticated_clients": integer_distribution(authenticated),
        "active_clients": integer_distribution(active),
        "peak_active_clients": integer_distribution(peak_active),
        "cancelled_clients": integer_distribution(cancelled),
        "cancelled_before_connect": integer_distribution(cancelled_before_connect),
        "connected_clients": integer_distribution(connected),
        "ramp_up_seconds": numeric_distribution(ramp_up),
        "ramp_timeout_seconds": numeric_distribution(ramp_timeout),
        "ramp_completed": all(run.get("ramp_completed") is True for run in runs),
        "measurement_started": all(run.get("measurement_started") is True for run in runs),
        "steady_state_target_seconds": numeric_distribution(steady_target),
        "steady_state_elapsed_seconds": numeric_distribution(steady_elapsed),
        "steady_state_completed": all(run.get("steady_state_completed") is True for run in runs),
        "steady_state_windows_valid": all(steady_window_valid(run) for run in runs),
        "termination_reasons": sorted({str(run.get("termination_reason", "")) for run in runs}),
        "load_models": sorted({str(run.get("load_model", "")) for run in runs}),
        "configured_request_rate_is_bounded": all(
            run.get("configured_request_rate_is_bounded") is True for run in runs
        ),
        "configured_request_rate_ceiling_ops_per_sec": numeric_distribution(
            configured_rate_ceiling
        ),
        "business_send_attempts": integer_distribution(send_attempts),
        "business_send_successes": integer_distribution(send_successes),
        "business_response_errors": integer_distribution(response_errors),
        "open_loop_scheduled_offers": integer_distribution(scheduled_offers),
        "open_loop_average_schedule_lag_us": numeric_distribution(schedule_lag_average),
        "open_loop_max_schedule_lag_us": integer_distribution(schedule_lag_max),
        "achieved_send_rate_ops_per_sec": numeric_distribution(achieved_send_rate),
        "achieved_response_rate_ops_per_sec": numeric_distribution(achieved_response_rate),
        "achieved_successful_response_rate_ops_per_sec": numeric_distribution(
            achieved_successful_response_rate
        ),
        "rejected_clients": {
            "min": min(rejected),
            "median": int(statistics.median(rejected)),
            "max": max(rejected),
        },
        "failed_clients": {
            "min": min(failed),
            "median": int(statistics.median(failed)),
            "max": max(failed),
        },
        "forced_timeout": any(forced_timeouts),
        "bench_exit_code": max(bench_exit_codes),
        "gateway_runtime_metrics": runtime_metrics,
    }

def gateway_runtime_metric_delta(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, float | int | None]:
    def section(value: dict[str, Any], name: str) -> dict[str, Any]:
        raw = value.get(name)
        return raw if isinstance(raw, dict) else {}

    def counter_delta(lhs: dict[str, Any], rhs: dict[str, Any], name: str) -> int:
        return max(0, int(rhs.get(name, 0)) - int(lhs.get(name, 0)))

    queue_before = section(before, "gateway_queue")
    queue_after = section(after, "gateway_queue")
    queue_processed = counter_delta(queue_before, queue_after, "processed_items")
    queue_batches = counter_delta(queue_before, queue_after, "processed_batches")
    wait_total_ns = counter_delta(queue_before, queue_after, "total_queue_wait_ns")
    handle_total_ns = counter_delta(queue_before, queue_after, "total_handle_ns")

    route_before = section(before, "battle_route")
    route_after = section(after, "battle_route")
    route_completed = counter_delta(route_before, route_after, "completed_tasks")
    route_wait_us = counter_delta(route_before, route_after, "total_queue_wait_us")
    route_execution_us = counter_delta(
        route_before, route_after, "total_task_execution_us"
    )

    backend_before = before.get("backend_metrics")
    backend_after = after.get("backend_metrics")
    if not isinstance(backend_before, dict):
        backend_before = {}
    if not isinstance(backend_after, dict):
        backend_after = {}
    backend_requests = 0
    backend_latency_us = 0
    backend_samples = 0
    for service, current in backend_after.items():
        if not isinstance(current, dict):
            continue
        previous = backend_before.get(service)
        if not isinstance(previous, dict):
            previous = {}
        backend_requests += counter_delta(previous, current, "total_requests")
        backend_latency_us += counter_delta(previous, current, "total_latency_us")
        backend_samples += counter_delta(previous, current, "latency_sample_count")

    return {
        "gateway_queue_processed_items": queue_processed,
        "gateway_queue_batches": queue_batches,
        "gateway_queue_average_batch_size": (
            round(queue_processed / queue_batches, 3) if queue_batches else 0.0
        ),
        "gateway_queue_average_wait_us": (
            round(wait_total_ns / queue_processed / 1000.0, 3)
            if queue_processed else 0.0
        ),
        "gateway_queue_lifetime_max_wait_us": int(
            queue_after.get("max_queue_wait_us", 0)
        ),
        "gateway_queue_average_handle_us": (
            round(handle_total_ns / queue_processed / 1000.0, 3)
            if queue_processed else 0.0
        ),
        "gateway_queue_lifetime_max_handle_us": int(
            queue_after.get("max_handle_us", 0)
        ),
        "gateway_queue_lifetime_peak_depth": int(
            queue_after.get("peak_queued_items", 0)
        ),
        "battle_route_completed_tasks": route_completed,
        "battle_route_average_queue_wait_us": (
            round(route_wait_us / route_completed, 3) if route_completed else 0.0
        ),
        "battle_route_average_task_execution_us": (
            round(route_execution_us / route_completed, 3) if route_completed else 0.0
        ),
        "backend_requests": backend_requests,
        "backend_average_latency_us": (
            round(backend_latency_us / backend_samples, 3) if backend_samples else 0.0
        ),
    }


def aggregate_otel_mode(
    mode: str,
    runs: list[dict[str, Any]],
    mode_backend_routed_requests: int,
    battle_backend_pid: int,
) -> dict[str, Any]:
    performance = aggregate_case_runs("battle-100-30s", runs)
    return {
        "mode": mode,
        "runs": len(runs),
        "performance": performance,
        "gateway_cpu_seconds": distribution([
            float(run["gateway_resources"]["cpu_seconds_delta"])
            for run in runs
            if run["gateway_resources"].get("cpu_seconds_delta") is not None
        ]),
        "gateway_rss_mb": distribution([
            float(run["gateway_resources"]["rss_mb_after"])
            for run in runs
        ]),
        "backend_routed_requests": mode_backend_routed_requests,
        "per_run_backend_routed_requests": sum(
            int(run["backend_routed_requests"]) for run in runs
        ),
        "gateway_cpu_affinities": sorted({
            str(run["gateway_resources"].get("cpu_affinity", "")) for run in runs
        }),
        "gateway_pid": runs[0]["gateway_resources"].get("pid") if runs else None,
        "battle_backend_pid": battle_backend_pid,
        "runs_detail": runs,
    }

def median_delta(off_value: float | None, on_value: float | None) -> dict[str, float | None]:
    if off_value is None or on_value is None:
        return {"off": off_value, "on": on_value, "on_minus_off": None, "delta_percent": None}
    difference = float(on_value) - float(off_value)
    return {
        "off": off_value,
        "on": on_value,
        "on_minus_off": round(difference, 3),
        "delta_percent": round(difference / float(off_value) * 100.0, 3)
        if float(off_value) != 0.0 else None,
    }
