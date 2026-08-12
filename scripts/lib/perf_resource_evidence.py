"""Performance baseline responsibility module: perf_resource_evidence."""

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
def case_base_name(snapshot_key: str) -> str:
    return re.sub(r"\.run\d+$", "", snapshot_key)

def snapshot_service_map(snapshots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("service_name") or item.get("process_name") or item.get("pid")): item for item in snapshots}

def numeric_snapshot_value(snapshot: dict[str, Any] | None, key: str) -> float | None:
    if snapshot is None:
        return None
    value = snapshot.get(key)
    if value is None:
        return None
    with suppress(TypeError, ValueError):
        return float(value)
    return None

def aggregate_numeric(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }

def service_resource_delta(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    elapsed_seconds: float,
) -> dict[str, Any]:
    fields = [
        "working_set_mb",
        "private_memory_mb",
        "virtual_memory_mb",
        "handles",
        "threads",
        "cpu_seconds",
        "cpu_percent",
    ]
    result: dict[str, Any] = {}
    for field in fields:
        after_value = numeric_snapshot_value(after, field)
        before_value = numeric_snapshot_value(before, field)
        result[field] = after_value
        result[f"{field}_delta"] = (
            round(after_value - before_value, 3)
            if after_value is not None and before_value is not None
            else None
        )
    cpu_delta = result.get("cpu_seconds_delta")
    result["cpu_percent_from_cpu_seconds"] = (
        round((float(cpu_delta) / elapsed_seconds) * 100.0, 3)
        if cpu_delta is not None and elapsed_seconds > 0
        else None
    )
    return result

def build_resource_window(
    service_before: list[dict[str, Any]],
    service_after: list[dict[str, Any]],
    loadgen_before: dict[str, Any],
    loadgen_after: dict[str, Any],
    elapsed_seconds: float,
    quiescence: dict[str, Any],
) -> dict[str, Any]:
    before_map = snapshot_service_map(service_before)
    after_map = snapshot_service_map(service_after)
    return {
        "elapsed_seconds": round(elapsed_seconds, 6),
        "quiescence": quiescence,
        "services": {
            name: service_resource_delta(before_map.get(name), after, elapsed_seconds)
            for name, after in after_map.items()
        },
        "loadgen": service_resource_delta(loadgen_before, loadgen_after, elapsed_seconds),
        "raw": {
            "service_before": service_before,
            "service_after": service_after,
            "loadgen_before": loadgen_before,
            "loadgen_after": loadgen_after,
        },
    }

def build_case_resource_evidence(
    *,
    service_before: list[dict[str, Any]],
    service_at_load_end: list[dict[str, Any]],
    loadgen: dict[str, Any],
    load_window_elapsed_seconds: float,
    quiescence: dict[str, Any],
    service_after_quiescence: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "before": service_before,
        "after": service_at_load_end,
        "loadgen": loadgen,
        "elapsed_seconds": round(load_window_elapsed_seconds, 6),
        "measurement_boundary": "loadgen_process_exit",
        "quiescence": quiescence,
        "post_quiescence": {
            "after": service_after_quiescence,
            "wait_seconds": float(quiescence.get("wait_seconds", 0.0)),
        },
    }

def analyze_resources(summary: dict[str, Any]) -> dict[str, Any]:
    snapshots = summary.get("process_snapshots", {})
    cases = summary.get("cases", [])

    per_run: list[dict[str, Any]] = []
    by_case: dict[str, dict[str, list[float]]] = {}
    for run in cases:
        case_name = str(run.get("case_name") or "")
        snapshot_key = case_name
        if snapshot_key not in snapshots:
            continue
        base_name = case_base_name(case_name)
        connected = max(0, int(run.get("connected_clients", 0)))
        run_snapshots = snapshots.get(snapshot_key)
        if not isinstance(run_snapshots, dict):
            continue
        elapsed = float(run_snapshots.get("elapsed_seconds", 0.0))
        before_map = snapshot_service_map(run_snapshots.get("before", []))
        after_map = snapshot_service_map(run_snapshots.get("after", []))
        services: dict[str, Any] = {}
        for service_name, after in after_map.items():
            delta = service_resource_delta(before_map.get(service_name), after, elapsed)
            if connected > 0:
                rss_delta = delta.get("working_set_mb_delta")
                handles_delta = delta.get("handles_delta")
                delta["rss_kb_per_connected_client"] = (
                    round((float(rss_delta) * 1024.0) / connected, 3)
                    if rss_delta is not None
                    else None
                )
                delta["handles_per_connected_client"] = (
                    round(float(handles_delta) / connected, 6)
                    if handles_delta is not None
                    else None
                )
            else:
                delta["rss_kb_per_connected_client"] = None
                delta["handles_per_connected_client"] = None
            services[service_name] = delta

            bucket = by_case.setdefault(base_name, {}).setdefault(service_name, {})
            for metric in (
                "working_set_mb",
                "working_set_mb_delta",
                "handles",
                "handles_delta",
                "threads",
                "threads_delta",
                "cpu_percent",
                "cpu_percent_from_cpu_seconds",
                "rss_kb_per_connected_client",
                "handles_per_connected_client",
            ):
                value = delta.get(metric)
                if value is not None:
                    bucket.setdefault(metric, []).append(float(value))

        per_run.append({
            "case_name": case_name,
            "connected_clients": connected,
            "elapsed_seconds": elapsed,
            "services": services,
            "loadgen": run_snapshots.get("loadgen", {}),
        })

    case_aggregates: list[dict[str, Any]] = []
    for case_name, service_metrics in sorted(by_case.items()):
        services = {}
        for service_name, metrics in sorted(service_metrics.items()):
            services[service_name] = {
                metric: aggregate_numeric(values)
                for metric, values in sorted(metrics.items())
            }
        case_aggregates.append({
            "case_name": case_name,
            "services": services,
        })

    return {
        "idle": snapshots.get("idle", []),
        "per_run": per_run,
        "case_aggregates": case_aggregates,
    }

def evaluate_resource_isolation_evidence(summary: dict[str, Any]) -> dict[str, Any]:
    service = summary.get("service_resource_constraint")
    loadgen = summary.get("loadgen_resource_constraint")
    service = service if isinstance(service, dict) else {}
    loadgen = loadgen if isinstance(loadgen, dict) else {}
    required = service.get("type") == "linux_cpu_affinity"
    if not required:
        return {
            "case": "service-loadgen-resource-isolation",
            "passed": True,
            "required": False,
            "criteria": "required only for Linux CPU-constrained evidence",
            "observed": {"service_constraint_type": service.get("type", "none")},
        }

    service_set = parse_cpu_set(str(service.get("effective_cpu_set", "")))
    loadgen_set = parse_cpu_set(str(loadgen.get("effective_cpu_set", "")))
    service_limit = len(service_set) * 100.0 + 5.0
    loadgen_limit = len(loadgen_set) * 100.0 + 5.0
    failures: list[str] = []
    if (
        service.get("applied") is not True
        or loadgen.get("applied") is not True
        or not service_set.isdisjoint(loadgen_set)
    ):
        failures.append("service/loadgen affinity is missing, unverified, or overlapping")

    cases = summary.get("cases")
    per_run = summary.get("resource_analysis", {}).get("per_run")
    snapshots = summary.get("process_snapshots")
    cases = cases if isinstance(cases, list) else []
    per_run = per_run if isinstance(per_run, list) else []
    snapshots = snapshots if isinstance(snapshots, dict) else {}
    if len(per_run) != len(cases) or not cases:
        failures.append(f"resource run count mismatch: {len(per_run)}/{len(cases)}")
    for run in per_run:
        case_name = str(run.get("case_name", ""))
        raw = snapshots.get(case_name)
        services = run.get("services")
        loadgen_metrics = run.get("loadgen")
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("quiescence"), dict)
            or raw["quiescence"].get("quiesced") is not True
            or not isinstance(services, dict)
            or not services
            or not isinstance(loadgen_metrics, dict)
        ):
            failures.append(f"{case_name}: missing quiescence or resource evidence")
            continue
        service_cpu = [
            metrics.get("cpu_percent_from_cpu_seconds")
            for metrics in services.values()
            if isinstance(metrics, dict)
        ]
        loadgen_cpu = loadgen_metrics.get("cpu_percent_from_cpu_seconds")
        if (
            len(service_cpu) != len(services)
            or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
                for value in service_cpu
            )
            or sum(float(value) for value in service_cpu if isinstance(value, (int, float)))
            > service_limit
            or not isinstance(loadgen_cpu, (int, float))
            or isinstance(loadgen_cpu, bool)
            or not 0 <= loadgen_cpu <= loadgen_limit
        ):
            failures.append(f"{case_name}: CPU delta exceeds its physical affinity limit")

    return {
        "case": "service-loadgen-resource-isolation",
        "passed": not failures,
        "required": True,
        "criteria": (
            "disjoint verified affinity, per-run quiescence, adjacent snapshots, and physical CPU totals"
        ),
        "observed": {
            "service_cpu_set": format_cpu_set(service_set),
            "loadgen_cpu_set": format_cpu_set(loadgen_set),
            "resource_runs": len(per_run),
            "failures": failures,
        },
    }
