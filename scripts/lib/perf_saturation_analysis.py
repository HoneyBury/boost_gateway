"""Performance baseline responsibility module: perf_saturation_analysis."""

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
def build_saturation_analysis(
    summary: dict[str, Any],
    *,
    cpu_threshold_percent: float = 85.0,
    loadgen_headroom_threshold_percent: float = 85.0,
) -> dict[str, Any]:
    service_constraint = summary.get("service_resource_constraint")
    loadgen_constraint = summary.get("loadgen_resource_constraint")
    service_constraint = service_constraint if isinstance(service_constraint, dict) else {}
    loadgen_constraint = loadgen_constraint if isinstance(loadgen_constraint, dict) else {}
    service_cpu_count = int(service_constraint.get("cpu_count", 0))
    loadgen_cpu_count = int(loadgen_constraint.get("cpu_count", 0))
    repetitions = int(summary.get("repetitions", 0))
    manifest = summary.get("case_manifest")
    manifest = manifest if isinstance(manifest, list) else []
    aggregates = summary.get("case_aggregates")
    aggregates = aggregates if isinstance(aggregates, list) else []
    resource_aggregates = summary.get("resource_analysis", {}).get("case_aggregates")
    resource_aggregates = resource_aggregates if isinstance(resource_aggregates, list) else []
    resource_runs = summary.get("resource_analysis", {}).get("per_run")
    resource_runs = resource_runs if isinstance(resource_runs, list) else []
    resource_by_case = {
        str(item.get("case_name", "")): item
        for item in resource_aggregates
        if isinstance(item, dict)
    }
    loadgen_cpu_by_case: dict[str, list[float]] = {}
    for run in resource_runs:
        if not isinstance(run, dict):
            continue
        value = run.get("loadgen", {}).get("cpu_percent_from_cpu_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            loadgen_cpu_by_case.setdefault(
                case_base_name(str(run.get("case_name", ""))), []
            ).append(float(value))
    isolation = evaluate_resource_isolation_evidence(summary)
    process_snapshots = summary.get("process_snapshots")
    process_snapshots = process_snapshots if isinstance(process_snapshots, dict) else {}
    load_end_boundaries: dict[str, list[bool]] = {}
    for run_name, snapshot in process_snapshots.items():
        if not isinstance(snapshot, dict) or run_name == "idle":
            continue
        load_end_boundaries.setdefault(case_base_name(str(run_name)), []).append(
            snapshot.get("measurement_boundary") == "loadgen_process_exit"
        )
    failures: list[str] = []
    if service_cpu_count <= 0 or service_constraint.get("applied") is not True:
        failures.append("service CPU affinity/quota is not explicit and verified")
    if loadgen_cpu_count <= 0 or loadgen_constraint.get("applied") is not True:
        failures.append("load-generator CPU affinity is not explicit and verified")
    if isolation.get("passed") is not True or isolation.get("required") is not True:
        failures.append("service/load-generator resource isolation evidence is incomplete")
    if repetitions <= 0:
        failures.append("repetition count must be positive")
    curve_complete = len(manifest) >= 3
    if len(aggregates) != len(manifest):
        failures.append("case manifest and aggregate counts differ")
    curve_load_models = sorted({
        str(model)
        for aggregate in aggregates
        if isinstance(aggregate, dict)
        for model in aggregate.get("load_models", [])
    })
    supported_load_models = {
        "closed_loop_one_in_flight_per_client",
        "open_loop_fixed_interval_per_client",
    }
    if len(curve_load_models) != 1 or curve_load_models[0] not in supported_load_models:
        failures.append("saturation curve must use one supported load model")
    analysis_load_model = curve_load_models[0] if len(curve_load_models) == 1 else "unknown"

    points: list[dict[str, Any]] = []
    for aggregate in aggregates:
        if not isinstance(aggregate, dict):
            continue
        case_name = str(aggregate.get("case_name", ""))
        resource = resource_by_case.get(case_name, {})
        gateway_cpu = (
            resource.get("services", {})
            .get("v2_gateway_demo", {})
            .get("cpu_percent_from_cpu_seconds", {})
            .get("median")
        )
        loadgen_values = loadgen_cpu_by_case.get(case_name, [])
        loadgen_cpu = statistics.median(loadgen_values) if loadgen_values else None
        target = int(aggregate.get("target_clients", {}).get("median", 0))
        terminal_client_errors = (
            int(aggregate.get("failed_clients", {}).get("max", 0))
            + int(aggregate.get("rejected_clients", {}).get("max", 0))
            + int(aggregate.get("cancelled_clients", {}).get("max", 0))
        )
        request_errors = int(aggregate.get("business_response_errors", {}).get("max", 0))
        total_errors = terminal_client_errors + request_errors
        message_count_consistent = aggregate.get("message_count_consistent") is True
        load_models = aggregate.get("load_models", [])
        boundary_evidence = load_end_boundaries.get(case_name, [])
        load_end_boundary_valid = (
            len(boundary_evidence) == repetitions and all(boundary_evidence)
        )
        ramp_up = float(aggregate.get("ramp_up_seconds", {}).get("max", 0.0))
        steady_elapsed = float(
            aggregate.get("steady_state_elapsed_seconds", {}).get("min", 0.0)
        )
        steady_target = float(
            aggregate.get("steady_state_target_seconds", {}).get("max", 0.0)
        )
        ramp_to_steady_ratio = ramp_up / steady_elapsed if steady_elapsed > 0.0 else 1.0
        configured_ceiling = float(
            aggregate.get("configured_request_rate_ceiling_ops_per_sec", {}).get("median", 0.0)
        )
        scheduled_offers = int(
            aggregate.get("open_loop_scheduled_offers", {}).get("median", 0)
        )
        attempted_offers = int(
            aggregate.get("business_send_attempts", {}).get("median", 0)
        )
        achieved_offer = attempted_offers / steady_elapsed if steady_elapsed > 0.0 else 0.0
        offer_to_configured_ratio = (
            achieved_offer / configured_ceiling if configured_ceiling > 0.0 else 0.0
        )
        open_loop_schedule_valid = (
            analysis_load_model != "open_loop_fixed_interval_per_client"
            or (
                scheduled_offers == attempted_offers
                and attempted_offers > 0
                and offer_to_configured_ratio >= 0.90
            )
        )
        timed_window_valid = (
            analysis_load_model != "open_loop_fixed_interval_per_client"
            or (steady_target > 0.0 and steady_elapsed >= steady_target * 0.99)
        )
        point_valid = (
            aggregate.get("runs") == repetitions
            and aggregate.get("measurement_started") is True
            and aggregate.get("steady_state_completed") is True
            and aggregate.get("configured_request_rate_is_bounded") is True
            and int(aggregate.get("bench_exit_code", 1)) == 0
            and target > 0
            and int(aggregate.get("started_clients", {}).get("min", 0)) == target
            and int(aggregate.get("tcp_connected_clients", {}).get("min", 0)) == target
            and int(aggregate.get("authenticated_clients", {}).get("min", 0)) == target
            and int(aggregate.get("peak_active_clients", {}).get("min", 0)) == target
            and int(aggregate.get("cancelled_clients", {}).get("max", 1)) == 0
            and message_count_consistent
            and load_models == [analysis_load_model]
            and open_loop_schedule_valid
            and timed_window_valid
            and isinstance(gateway_cpu, (int, float))
            and not isinstance(gateway_cpu, bool)
            and isinstance(loadgen_cpu, (int, float))
            and not isinstance(loadgen_cpu, bool)
            and ramp_to_steady_ratio <= 0.1
            and load_end_boundary_valid
        )
        achieved_send = float(
            aggregate.get("achieved_send_rate_ops_per_sec", {}).get("median", 0.0)
        )
        response_metric = (
            "achieved_successful_response_rate_ops_per_sec"
            if analysis_load_model == "open_loop_fixed_interval_per_client"
            else "achieved_response_rate_ops_per_sec"
        )
        achieved_response = float(aggregate.get(response_metric, {}).get("median", 0.0))
        p99 = float(aggregate.get("latency_p99_ms", {}).get("median", 0.0))
        gateway_cpu_percent = float(gateway_cpu) if isinstance(gateway_cpu, (int, float)) else 0.0
        quota_utilization = (
            gateway_cpu_percent / (service_cpu_count * 100.0) * 100.0
            if service_cpu_count > 0
            else 0.0
        )
        points.append({
            "case_id": case_name,
            "case_identity": aggregate.get("case_identity", {}),
            "evidence_valid": point_valid,
            "load_model": analysis_load_model,
            "configured_request_rate_ceiling_ops_per_sec": round(configured_ceiling, 3),
            "configured_offered_rate_ops_per_sec": round(configured_ceiling, 3),
            "achieved_offer_rate_ops_per_sec": round(achieved_offer, 3),
            "achieved_offer_to_configured_ratio": round(offer_to_configured_ratio, 6),
            "achieved_send_rate_ops_per_sec": round(achieved_send, 3),
            "achieved_response_rate_ops_per_sec": round(achieved_response, 3),
            "achieved_response_to_ceiling_ratio": round(
                achieved_response / configured_ceiling, 6
            ) if configured_ceiling > 0 else None,
            "throughput_msg_per_sec": float(
                aggregate.get("throughput_msg_per_sec", {}).get("median", 0.0)
            ),
            "latency_p99_ms": p99,
            "client_error_count": terminal_client_errors,
            "client_error_rate": round(terminal_client_errors / target, 6)
            if target > 0 else 1.0,
            "business_response_errors": request_errors,
            "request_error_rate": round(request_errors / attempted_offers, 6)
            if attempted_offers > 0 else 0.0,
            "total_error_count": total_errors,
            "message_count_consistent": message_count_consistent,
            "open_loop_scheduled_offers": scheduled_offers,
            "open_loop_schedule_valid": open_loop_schedule_valid,
            "timed_window_valid": timed_window_valid,
            "steady_state_target_seconds": round(steady_target, 3),
            "open_loop_average_schedule_lag_us": float(
                aggregate.get("open_loop_average_schedule_lag_us", {}).get("median", 0.0)
            ),
            "open_loop_max_schedule_lag_us": int(
                aggregate.get("open_loop_max_schedule_lag_us", {}).get("max", 0)
            ),
            "gateway_cpu_percent": round(gateway_cpu_percent, 3),
            "gateway_cpu_quota_percent": round(quota_utilization, 3),
            "loadgen_cpu_percent": round(float(loadgen_cpu), 3)
            if isinstance(loadgen_cpu, (int, float)) else None,
            "loadgen_cpu_quota_percent": round(
                float(loadgen_cpu) / (loadgen_cpu_count * 100.0) * 100.0, 3
            ) if isinstance(loadgen_cpu, (int, float)) and loadgen_cpu_count > 0 else None,
            "ramp_up_seconds": round(ramp_up, 3),
            "steady_state_elapsed_seconds": round(steady_elapsed, 3),
            "ramp_to_steady_ratio": round(ramp_to_steady_ratio, 6),
            "resource_window_accepted": ramp_to_steady_ratio <= 0.1,
            "load_end_boundary_valid": load_end_boundary_valid,
            "slo_met": p99 <= (
                battle_p99_limit_ms(case_name) if case_name.startswith("battle") else 50.0
            ) and total_errors == 0,
        })

    points.sort(key=lambda item: float(item["configured_request_rate_ceiling_ops_per_sec"]))
    ceilings = [float(point["configured_request_rate_ceiling_ops_per_sec"]) for point in points]
    if any(current <= previous for previous, current in zip(ceilings, ceilings[1:], strict=False)):
        failures.append("configured request-rate ceilings are not strictly increasing")
    gateway_cpu_threshold_point = next(
        (point for point in points
         if point.get("evidence_valid") is True
         and point["gateway_cpu_quota_percent"] >= cpu_threshold_percent),
        None,
    )
    cpu_point = next(
        (
            point for point in points
            if point.get("evidence_valid") is True
            and point["gateway_cpu_quota_percent"] >= cpu_threshold_percent
            and point["loadgen_cpu_quota_percent"] is not None
            and point["loadgen_cpu_quota_percent"] < loadgen_headroom_threshold_percent
        ),
        None,
    )
    def load_source_has_headroom(point: dict[str, Any]) -> bool:
        loadgen_quota = point.get("loadgen_cpu_quota_percent")
        return (
            point.get("evidence_valid") is True
            and isinstance(loadgen_quota, (int, float))
            and loadgen_quota < loadgen_headroom_threshold_percent
            and point.get("open_loop_schedule_valid") is True
        )

    slo_point = next(
        (point for point in points
         if point["latency_p99_ms"] > 50.0 and load_source_has_headroom(point)),
        None,
    )
    error_point = next(
        (point for point in points
         if point["total_error_count"] > 0 and load_source_has_headroom(point)),
        None,
    )
    throughput_point = None
    for previous, current in zip(points, points[1:], strict=False):
        previous_rate = float(previous["achieved_response_rate_ops_per_sec"])
        current_rate = float(current["achieved_response_rate_ops_per_sec"])
        ceiling_growth = (
            float(current["configured_request_rate_ceiling_ops_per_sec"])
            / max(float(previous["configured_request_rate_ceiling_ops_per_sec"]), 0.000001)
            - 1.0
        )
        response_growth = current_rate / max(previous_rate, 0.000001) - 1.0
        if (previous.get("evidence_valid") is True
                and ceiling_growth >= 0.2 and response_growth < 0.1
                and load_source_has_headroom(current)):
            throughput_point = current
            break

    knee_candidates = [
        point for point in (cpu_point, throughput_point, slo_point, error_point)
        if point is not None
    ]
    fixed_case_point = min(
        knee_candidates,
        key=lambda point: float(point["configured_request_rate_ceiling_ops_per_sec"]),
        default=None,
    )
    required_points = points
    post_saturation_invalid_points: list[str] = []
    if fixed_case_point is not None:
        fixed_index = points.index(fixed_case_point)
        required_points = points[:fixed_index + 1]
        post_saturation_invalid_points = [
            str(point["case_id"])
            for point in points[fixed_index + 1:]
            if point.get("evidence_valid") is not True
        ]
    if any(
        point.get("message_count_consistent") is not True
        for point in required_points
    ):
        failures.append(
            "one or more required saturation points have inconsistent total/response/push message counts"
        )
    if any(point.get("evidence_valid") is not True for point in required_points):
        failures.append(
            "one or more required saturation points have incomplete lifecycle or resource evidence"
        )

    evidence_valid = not failures
    saturation_found = evidence_valid and curve_complete and fixed_case_point is not None
    if evidence_valid and not curve_complete:
        inconclusive_reason = (
            "fewer than three cases were selected; evidence is a comparison point, not a saturation curve"
        )
    elif evidence_valid and gateway_cpu_threshold_point is not None and cpu_point is None:
        inconclusive_reason = (
            "Gateway reached its CPU threshold only while the load generator lacked required headroom"
        )
    elif failures:
        inconclusive_reason = "; ".join(failures)
    else:
        inconclusive_reason = (
            f"Gateway CPU did not reach {cpu_threshold_percent:.1f}% of its explicit quota"
        )
    return {
        "analysis_version": 1,
        "collection_pass": evidence_valid,
        "overall_pass": saturation_found,
        "evidence_valid": evidence_valid,
        "saturation_found": saturation_found,
        "analysis_mode": "curve" if curve_complete else "comparison_point",
        "curve_complete": curve_complete,
        "conclusion": "knee_found" if saturation_found else "inconclusive",
        "inconclusive_reason": "" if saturation_found else inconclusive_reason,
        "load_model": analysis_load_model,
        "load_model_note": (
            "Each client schedules requests at absolute fixed-interval deadlines independent of responses."
            if analysis_load_model == "open_loop_fixed_interval_per_client"
            else "Configured rate is a timer ceiling; one in-flight request per client means this is not open-loop offered load."
        ),
        "cpu_measurement_window": "case start through loadgen process exit (ramp plus steady state; quiescence excluded)",
        "cpu_measurement_window_policy": (
            "mixed-window evidence requires ramp_up_seconds / steady_state_elapsed_seconds <= 0.10"
        ),
        "cpu_threshold_percent_of_quota": cpu_threshold_percent,
        "loadgen_headroom_threshold_percent_of_quota": loadgen_headroom_threshold_percent,
        "service_cpu_count": service_cpu_count,
        "loadgen_cpu_count": loadgen_cpu_count,
        "points": points,
        "required_evidence_through_case": (
            fixed_case_point["case_id"] if fixed_case_point else None
        ),
        "post_saturation_invalid_points": post_saturation_invalid_points,
        "cpu_saturation_case": cpu_point["case_identity"] if cpu_point else None,
        "gateway_cpu_threshold_case": (
            gateway_cpu_threshold_point["case_identity"]
            if gateway_cpu_threshold_point else None
        ),
        "throughput_knee_case": throughput_point["case_identity"] if throughput_point else None,
        "slo_knee_case": slo_point["case_identity"] if slo_point else None,
        "error_knee_case": error_point["case_identity"] if error_point else None,
        "fixed_case_candidate": fixed_case_point["case_identity"] if fixed_case_point else None,
        "knee_reasons": [
            reason
            for reason, point in (
                ("gateway_cpu", cpu_point),
                ("throughput_plateau", throughput_point),
                ("latency_slo", slo_point),
                ("client_errors", error_point),
            )
            if point is not None
            and fixed_case_point is not None
            and point["case_identity"] == fixed_case_point["case_identity"]
        ],
        "comparison_axes": ["service_cpu_count", "io_cores"],
        "failures": failures,
    }
