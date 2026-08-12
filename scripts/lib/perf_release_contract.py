"""Performance baseline responsibility module: perf_release_contract."""

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
from scripts.lib.perf_result_aggregation import *  # noqa: F401,F403
from scripts.lib.perf_resource_evidence import *  # noqa: F401,F403
from scripts.lib.perf_saturation_analysis import *  # noqa: F401,F403
from scripts.lib.perf_report import *  # noqa: F401,F403







def evaluate_release_gates(aggregates: list[dict[str, Any]]) -> dict[str, Any]:
    gates: dict[str, Any] = {"overall_pass": True, "checks": [], "warnings": []}
    for aggregate in aggregates:
        case_name = aggregate["case_name"]
        p99 = aggregate["latency_p99_ms"]["median"]
        throughput = aggregate["throughput_msg_per_sec"]["median"]
        rejected = aggregate["rejected_clients"]["max"]
        failed = aggregate["failed_clients"]["max"]
        forced_timeout = bool(aggregate.get("forced_timeout"))
        bench_exit_code = int(aggregate.get("bench_exit_code", 1))
        total_messages = aggregate["total_messages"]["median"]
        response_messages = aggregate.get("response_messages", {}).get("median", 0)
        successful_response_messages = aggregate.get(
            "successful_response_messages", {}
        ).get("median", response_messages)
        business_response_errors = aggregate.get("business_response_errors", {}).get("max", 0)
        push_messages = aggregate.get("push_messages", {}).get("median", 0)
        message_count_consistent = aggregate.get("message_count_consistent") is True
        target_min = int(aggregate.get("target_clients", {}).get("min", 0))
        target_max = int(aggregate.get("target_clients", {}).get("max", 0))
        started_min = int(aggregate.get("started_clients", {}).get("min", 0))
        tcp_connected_min = int(aggregate.get("tcp_connected_clients", {}).get("min", 0))
        authenticated_min = int(aggregate.get("authenticated_clients", {}).get("min", 0))
        peak_active_min = int(aggregate.get("peak_active_clients", {}).get("min", 0))
        cancelled = int(aggregate.get("cancelled_clients", {}).get("max", 1))
        cancelled_before_connect = int(
            aggregate.get("cancelled_before_connect", {}).get("max", 1)
        )
        ramp_completed = aggregate.get("ramp_completed") is True
        measurement_started = aggregate.get("measurement_started") is True
        steady_completed = aggregate.get("steady_state_completed") is True
        steady_target = float(
            aggregate.get("steady_state_target_seconds", {}).get("max", 0.0)
        )
        steady_elapsed = float(
            aggregate.get("steady_state_elapsed_seconds", {}).get("min", 0.0)
        )
        termination_reasons = set(aggregate.get("termination_reasons", []))
        lifecycle_valid = (
            target_min > 0
            and target_min == target_max
            and started_min == target_min
            and tcp_connected_min == target_min
            and authenticated_min == target_min
            and peak_active_min == target_min
            and cancelled == 0
            and cancelled_before_connect == 0
            and ramp_completed
            and measurement_started
            and bench_exit_code == 0
        )
        duration_window_valid = (
            steady_completed
            and steady_elapsed >= max(0.0, steady_target - 0.25)
        )
        natural_battle_window_valid = (
            case_name.startswith("battle")
            and steady_completed
            and steady_elapsed > 0.0
            and bool(termination_reasons)
            and termination_reasons <= {"natural_completion", "clients_completed"}
        )
        aggregate_window_valid = aggregate.get("steady_state_windows_valid")
        steady_window_valid = (
            aggregate_window_valid is True
            if aggregate_window_valid is not None
            else duration_window_valid or natural_battle_window_valid
        )

        evidence_observed = {
            "target_clients": target_min,
            "started_clients_min": started_min,
            "tcp_connected_clients_min": tcp_connected_min,
            "authenticated_clients_min": authenticated_min,
            "peak_active_clients_min": peak_active_min,
            "cancelled_clients": cancelled,
            "cancelled_before_connect": cancelled_before_connect,
            "ramp_completed": ramp_completed,
            "measurement_started": measurement_started,
            "steady_state_target_seconds": steady_target,
            "steady_state_elapsed_seconds_min": steady_elapsed,
            "steady_state_completed": steady_completed,
            "steady_state_windows_valid": steady_window_valid,
            "termination_reasons": sorted(termination_reasons),
            "bench_exit_code": bench_exit_code,
        }

        if case_name.startswith("echo"):
            passed = (
                lifecycle_valid and steady_window_valid and rejected == 0 and failed == 0
                and not forced_timeout and total_messages > 0 and p99 <= 50.0
                and message_count_consistent and total_messages == response_messages
            )
            if 45.0 <= p99 <= 50.0:
                gates["warnings"].append({
                    "case": case_name,
                    "warning": "echo p99 is within 10% of the 50ms gate",
                    "p99_ms": p99,
                })
            gates["checks"].append({
                "case": case_name,
                "passed": passed,
                "criteria": (
                    "echo: all target clients started/TCP-connected/authenticated/active before "
                    "measurement, cancelled=0, steady duration complete, total=response, "
                    "rejected=0, failed=0, p99<=50ms"
                ),
                "observed": {
                    **evidence_observed,
                    "p99_ms": p99,
                    "throughput_msg_per_sec": throughput,
                    "rejected_clients": rejected,
                    "failed_clients": failed,
                    "forced_timeout": forced_timeout,
                    "total_messages": total_messages,
                    "response_messages": response_messages,
                    "push_messages": push_messages,
                    "message_count_consistent": message_count_consistent,
                },
            })
        elif case_name.startswith("battle"):
            open_saturation_case = case_name.startswith("battle-open-")
            overload_criteria = (
                "request overload responses measured separately, "
                if open_saturation_case else ""
            )
            min_messages = minimum_battle_messages(case_name)
            p99_limit = battle_p99_limit_ms(case_name)
            min_observed_messages = aggregate["total_messages"]["min"]
            passed = (
                lifecycle_valid and steady_window_valid and
                rejected == 0 and failed == 0 and not forced_timeout and
                min_observed_messages >= min_messages and p99 <= p99_limit and
                message_count_consistent
            )
            if p99_limit * 0.9 <= p99 <= p99_limit:
                gates["warnings"].append({
                    "case": case_name,
                    "warning": f"battle p99 is within 10% of the {p99_limit:.0f}ms gate",
                    "p99_ms": p99,
                })
            gates["checks"].append({
                "case": case_name,
                "passed": passed,
                "criteria": (
                    "battle: all target clients started/TCP-connected/authenticated/active before "
                    "measurement, cancelled=0, bounded steady window complete, "
                    f"total=response+push, rejected=0, failed=0, forced_timeout=false, "
                    f"{overload_criteria}"
                    f"min_total_messages>={min_messages}, "
                    f"p99<={p99_limit:.0f}ms"
                ),
                "observed": {
                    **evidence_observed,
                    "p99_ms": p99,
                    "p99_limit_ms": p99_limit,
                    "throughput_msg_per_sec": throughput,
                    "rejected_clients": rejected,
                    "failed_clients": failed,
                    "forced_timeout": forced_timeout,
                    "total_messages": total_messages,
                    "response_messages": response_messages,
                    "successful_response_messages": successful_response_messages,
                    "business_response_errors": business_response_errors,
                    "push_messages": push_messages,
                    "message_count_consistent": message_count_consistent,
                    "min_total_messages": min_observed_messages,
                    "required_min_total_messages": min_messages,
                },
            })

    gates["overall_pass"] = all(check["passed"] for check in gates["checks"])
    return gates

def build_run_cases(run_preset: str) -> list[dict[str, Any]]:
    capacity_ramp = {"ramp_clients_per_second": 2000, "ramp_timeout_seconds": 90}
    baseline_ramp = {"ramp_clients_per_second": 1000, "ramp_timeout_seconds": 45}
    smoke_ramp = {"ramp_clients_per_second": 200, "ramp_timeout_seconds": 15}
    if run_preset == "saturation":
        saturation_ramp = {"ramp_clients_per_second": 2000, "ramp_timeout_seconds": 60}
        return [
            {"name": "echo-sat-c500-i100-60s", "scenario": "echo", "clients": 500, "duration_seconds": 60, "interval_ms": 100, **saturation_ramp},
            {"name": "echo-sat-c1000-i100-60s", "scenario": "echo", "clients": 1000, "duration_seconds": 60, "interval_ms": 100, **saturation_ramp},
            {"name": "echo-sat-c1000-i50-60s", "scenario": "echo", "clients": 1000, "duration_seconds": 60, "interval_ms": 50, **saturation_ramp},
            {"name": "echo-sat-c1000-i20-60s", "scenario": "echo", "clients": 1000, "duration_seconds": 60, "interval_ms": 20, **saturation_ramp},
            {"name": "echo-sat-c1000-i10-60s", "scenario": "echo", "clients": 1000, "duration_seconds": 60, "interval_ms": 10, **saturation_ramp},
            {"name": "echo-sat-c2000-i10-60s", "scenario": "echo", "clients": 2000, "duration_seconds": 60, "interval_ms": 10, **saturation_ramp},
        ]
    if run_preset == "business-saturation":
        saturation_ramp = {"ramp_clients_per_second": 500, "ramp_timeout_seconds": 90}
        return [
            {"name": "battle-20-sat-60s", "scenario": "battle", "clients": 20, "duration_seconds": 60, "interval_ms": 100, "room": "perf_battle_sat_20", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-100-sat-60s", "scenario": "battle", "clients": 100, "duration_seconds": 60, "interval_ms": 100, "room": "perf_battle_sat_100", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-250-sat-60s", "scenario": "battle", "clients": 250, "duration_seconds": 60, "interval_ms": 100, "room": "perf_battle_sat_250", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-500-sat-60s", "scenario": "battle", "clients": 500, "duration_seconds": 60, "interval_ms": 100, "room": "perf_battle_sat_500", "room_group_size": 2, **saturation_ramp},
        ]
    if run_preset == "business-open-saturation":
        saturation_ramp = {"ramp_clients_per_second": 750, "ramp_timeout_seconds": 120}
        return [
            {"name": "battle-open-c100-i100-60s", "scenario": "battle", "clients": 100, "duration_seconds": 60, "interval_ms": 100, "load_model": "open-loop", "room": "perf_battle_open_100", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-open-c200-i100-60s", "scenario": "battle", "clients": 200, "duration_seconds": 60, "interval_ms": 100, "load_model": "open-loop", "room": "perf_battle_open_200", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-open-c250-i100-60s", "scenario": "battle", "clients": 250, "duration_seconds": 60, "interval_ms": 100, "load_model": "open-loop", "room": "perf_battle_open_250", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-open-c300-i100-60s", "scenario": "battle", "clients": 300, "duration_seconds": 60, "interval_ms": 100, "load_model": "open-loop", "room": "perf_battle_open_300", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-open-c350-i100-60s", "scenario": "battle", "clients": 350, "duration_seconds": 60, "interval_ms": 100, "load_model": "open-loop", "room": "perf_battle_open_350", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-open-c400-i100-60s", "scenario": "battle", "clients": 400, "duration_seconds": 60, "interval_ms": 100, "load_model": "open-loop", "room": "perf_battle_open_400", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-open-c450-i100-60s", "scenario": "battle", "clients": 450, "duration_seconds": 60, "interval_ms": 100, "load_model": "open-loop", "room": "perf_battle_open_450", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-open-c500-i100-60s", "scenario": "battle", "clients": 500, "duration_seconds": 60, "interval_ms": 100, "load_model": "open-loop", "room": "perf_battle_open_500", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-open-c750-i100-60s", "scenario": "battle", "clients": 750, "duration_seconds": 60, "interval_ms": 100, "load_model": "open-loop", "room": "perf_battle_open_750", "room_group_size": 2, **saturation_ramp},
            {"name": "battle-open-c1000-i100-60s", "scenario": "battle", "clients": 1000, "duration_seconds": 60, "interval_ms": 100, "load_model": "open-loop", "room": "perf_battle_open_1000", "room_group_size": 2, **saturation_ramp},
        ]
    if run_preset == "capacity":
        return [
            {"name": "echo-1000-30s", "scenario": "echo", "clients": 1000, "duration_seconds": 30, "interval_ms": 50, **capacity_ramp},
            {"name": "echo-5000-30s", "scenario": "echo", "clients": 5000, "duration_seconds": 30, "interval_ms": 50, **capacity_ramp},
            {"name": "echo-10000-30s", "scenario": "echo", "clients": 10000, "duration_seconds": 30, "interval_ms": 50, **capacity_ramp},
            {"name": "battle-100-30s", "scenario": "battle", "clients": 100, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_100", "room_group_size": 2, **capacity_ramp},
            {"name": "battle-500-30s", "scenario": "battle", "clients": 500, "duration_seconds": 30, "interval_ms": 200, "room": "perf_battle_500", "room_group_size": 2, **capacity_ramp},
        ]
    if run_preset == "business-capacity":
        return [
            {"name": "echo-1000-30s", "scenario": "echo", "clients": 1000, "duration_seconds": 30, "interval_ms": 50, **capacity_ramp},
            {"name": "battle-100-30s", "scenario": "battle", "clients": 100, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_100", "room_group_size": 2, **capacity_ramp},
            {"name": "battle-500-30s", "scenario": "battle", "clients": 500, "duration_seconds": 30, "interval_ms": 200, "room": "perf_battle_500", "room_group_size": 2, **capacity_ramp},
        ]
    if run_preset == "baseline":
        return [
            {"name": "echo-100-30s", "scenario": "echo", "clients": 100, "duration_seconds": 30, "interval_ms": 50, **baseline_ramp},
            {"name": "echo-1000-30s", "scenario": "echo", "clients": 1000, "duration_seconds": 30, "interval_ms": 50, **baseline_ramp},
            {"name": "battle-20-30s", "scenario": "battle", "clients": 20, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_20", "room_group_size": 2, **baseline_ramp},
            {"name": "battle-100-30s", "scenario": "battle", "clients": 100, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_100", "room_group_size": 2, **baseline_ramp},
        ]
    return [
        {"name": "echo-20-10s", "scenario": "echo", "clients": 20, "duration_seconds": 10, "interval_ms": 50, **smoke_ramp},
        {"name": "battle-2-10s", "scenario": "battle", "clients": 2, "duration_seconds": 10, "interval_ms": 100, "room": "perf_smoke_battle", **smoke_ramp},
    ]

def build_case_manifest(
    cases: list[dict[str, Any]],
    *,
    service_cpu_set: str,
    service_cpu_count: int,
    io_cores: int,
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for case in cases:
        interval_ms = int(case.get("interval_ms", 0))
        clients = int(case["clients"])
        configured_ceiling = (
            round(clients * 1000.0 / interval_ms, 3) if interval_ms > 0 else None
        )
        case_id = str(case["name"])
        manifest.append({
            "case_id": case_id,
            "case_name": case_id,
            "scenario": str(case["scenario"]),
            "clients": clients,
            "interval_ms": interval_ms,
            "duration_seconds": int(case["duration_seconds"]),
            "ramp_clients_per_second": int(case["ramp_clients_per_second"]),
            "ramp_timeout_seconds": int(case["ramp_timeout_seconds"]),
            "load_model": (
                "open_loop_fixed_interval_per_client"
                if case.get("load_model") == "open-loop"
                else "closed_loop_one_in_flight_per_client"
            ),
            "configured_request_rate_ceiling_ops_per_sec": configured_ceiling,
            "service_cpu_set": service_cpu_set,
            "service_cpu_count": service_cpu_count,
            "io_cores": io_cores,
            "comparison_identity": (
                f"{case_id}|service_cpu_count={service_cpu_count}|io_cores={io_cores}"
            ),
            "comparison_axes": ["service_cpu_count", "io_cores"],
        })
    return manifest


def build_otel_comparison(
    off: dict[str, Any],
    on: dict[str, Any],
    *,
    repetitions: int,
    off_log_verified: bool,
    on_log_verified: bool,
    collector_off: dict[str, int],
    collector_on: dict[str, int],
    off_exporter: dict[str, Any],
    on_exporter: dict[str, Any],
) -> dict[str, Any]:
    off_absolute_gate = evaluate_release_gates([off["performance"]])
    on_absolute_gate = evaluate_release_gates([on["performance"]])
    empty_collector = {
        "requests": 0,
        "spans": 0,
        "invalid_payloads": 0,
        "http_status_errors": 0,
        "span_status_errors": 0,
    }
    counter_fields = (
        "enqueued_spans", "exported_spans", "successful_batches", "failed_batches", "buffered_spans"
    )
    off_proof = (
        off_log_verified
        and collector_off == empty_collector
        and off_exporter.get("configured") is False
        and all(int(off_exporter.get(field, 0)) == 0 for field in counter_fields)
    )
    routed = int(on.get("backend_routed_requests", 0))
    enqueued = int(on_exporter.get("enqueued_spans", -1))
    exported = int(on_exporter.get("exported_spans", -1))
    buffered = int(on_exporter.get("buffered_spans", -1))
    on_proof = (
        on_log_verified
        and on_exporter.get("configured") is True
        and routed > 0
        and enqueued == routed
        and exported == int(collector_on.get("spans", -1))
        and int(on_exporter.get("successful_batches", -1)) == int(collector_on.get("requests", -1))
        and int(on_exporter.get("failed_batches", -1)) == 0
        and buffered == enqueued - exported
        and int(collector_on.get("requests", 0)) > 0
        and int(collector_on.get("spans", 0)) > 0
        and int(collector_on.get("invalid_payloads", -1)) == 0
        and int(collector_on.get("http_status_errors", -1)) == 0
        and int(collector_on.get("span_status_errors", -1)) == 0
    )
    complete = int(off.get("runs", 0)) == repetitions and int(on.get("runs", 0)) == repetitions and repetitions >= 3
    absolute_gate_passed = off_absolute_gate.get("overall_pass") is True and on_absolute_gate.get("overall_pass") is True
    affinity_verified = (
        off.get("gateway_cpu_affinities") == on.get("gateway_cpu_affinities")
        and bool(off.get("gateway_cpu_affinities"))
        and all(bool(value) for value in off.get("gateway_cpu_affinities", []))
    )
    fresh_gateway_per_mode = (
        off.get("gateway_pid") is not None
        and on.get("gateway_pid") is not None
        and off.get("gateway_pid") != on.get("gateway_pid")
    )
    fresh_battle_backend_per_mode = (
        off.get("battle_backend_pid") is not None
        and on.get("battle_backend_pid") is not None
        and off.get("battle_backend_pid") != on.get("battle_backend_pid")
    )
    verified = (
        complete
        and fresh_gateway_per_mode
        and fresh_battle_backend_per_mode
        and affinity_verified
        and off_proof
        and on_proof
        and absolute_gate_passed
    )
    return {
        "requested": True,
        "verified": verified,
        "passed": verified,
        "repetitions_per_mode": repetitions,
        "case": "battle-100-30s",
        "performance_regression_policy": "observed_not_thresholded",
        "execution_model": "fresh_gateway_and_battle_backend_per_mode_three_or_more_runs_per_process",
        "fresh_gateway_per_mode": fresh_gateway_per_mode,
        "fresh_battle_backend_per_mode": fresh_battle_backend_per_mode,
        "absolute_gate_passed": absolute_gate_passed,
        "affinity_verified": affinity_verified,
        "modes": {"off": off, "on": on},
        "proof": {
            "off": {"verified": off_proof, "log_verified": off_log_verified, "collector": collector_off, "exporter": off_exporter},
            "on": {"verified": on_proof, "log_verified": on_log_verified, "collector": collector_on, "exporter": on_exporter},
        },
        "absolute_gates": {"off": off_absolute_gate, "on": on_absolute_gate},
        "deltas": {
            "throughput_msg_per_sec": median_delta(off["performance"]["throughput_msg_per_sec"]["median"], on["performance"]["throughput_msg_per_sec"]["median"]),
            "latency_p99_ms": median_delta(off["performance"]["latency_p99_ms"]["median"], on["performance"]["latency_p99_ms"]["median"]),
            "gateway_cpu_seconds": median_delta(off["gateway_cpu_seconds"]["median"], on["gateway_cpu_seconds"]["median"]),
            "gateway_rss_mb": median_delta(off["gateway_rss_mb"]["median"], on["gateway_rss_mb"]["median"]),
        },
    }
