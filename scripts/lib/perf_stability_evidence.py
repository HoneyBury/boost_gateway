"""Performance baseline responsibility module: perf_stability_evidence."""

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
def evaluate_resource_stability_gate(
    samples: list[dict[str, Any]],
    *,
    warmup_windows: int,
    required_services: list[str] | None = None,
    require_full_flow: bool = False,
    max_rss_tail_growth_mb: float = 4.0,
    max_rss_slope_mb_per_window: float = 0.5,
    max_handle_growth: int = 4,
    max_thread_growth: int = 2,
) -> dict[str, Any]:
    measured = samples[warmup_windows:]
    observed_service_names = {
        str(snapshot.get("service_name", ""))
        for sample in measured
        for snapshot in sample.get("services", [])
        if snapshot.get("service_name")
    }
    expected_service_names = set(required_services or observed_service_names)
    service_names = sorted(expected_service_names | observed_service_names)
    checks: list[dict[str, Any]] = []
    minimum_samples = 3
    checks.append({
        "name": "measurement-window-count",
        "passed": len(measured) >= minimum_samples,
        "observed": len(measured),
        "limit": minimum_samples,
    })
    checks.append({
        "name": "service-set-present",
        "passed": bool(observed_service_names)
        and expected_service_names <= observed_service_names,
        "observed": sorted(observed_service_names),
        "limit": sorted(expected_service_names),
    })
    complete_lifecycle_windows = sum(
        1
        for sample in measured
        if isinstance(sample.get("full_flow"), dict)
        and sample["full_flow"].get("passed") is True
    )
    checks.append({
        "name": "complete-business-lifecycle-windows",
        "passed": not require_full_flow
        or complete_lifecycle_windows == len(measured),
        "observed": complete_lifecycle_windows,
        "limit": len(measured) if require_full_flow else "not required",
    })

    service_results: dict[str, Any] = {}
    for service_name in service_names:
        snapshots = []
        for sample in measured:
            snapshot = next(
                (
                    item
                    for item in sample.get("services", [])
                    if item.get("service_name") == service_name
                ),
                None,
            )
            if isinstance(snapshot, dict):
                snapshots.append(snapshot)

        complete = len(snapshots) == len(measured)
        rss_values = [float(item["working_set_mb"]) for item in snapshots]
        handle_values = [
            int(item["handles"])
            for item in snapshots
            if item.get("handles") is not None
        ]
        thread_values = [
            int(item["threads"])
            for item in snapshots
            if item.get("threads") is not None
        ]
        rss_growth = max(0.0, rss_values[-1] - rss_values[0]) if rss_values else 0.0
        rss_slope = max(0.0, linear_slope(rss_values))
        handle_growth = (
            max(0, max(handle_values) - handle_values[0]) if handle_values else None
        )
        thread_growth = (
            max(0, max(thread_values) - thread_values[0]) if thread_values else None
        )
        service_passed = (
            complete
            and len(rss_values) == len(measured)
            and len(handle_values) == len(measured)
            and len(thread_values) == len(measured)
            and rss_growth <= max_rss_tail_growth_mb
            and rss_slope <= max_rss_slope_mb_per_window
            and handle_growth is not None
            and handle_growth <= max_handle_growth
            and thread_growth is not None
            and thread_growth <= max_thread_growth
        )
        service_results[service_name] = {
            "passed": service_passed,
            "samples": len(snapshots),
            "rss_mb": rss_values,
            "rss_tail_growth_mb": round(rss_growth, 3),
            "rss_slope_mb_per_window": round(rss_slope, 6),
            "handle_growth": handle_growth,
            "thread_growth": thread_growth,
        }
        checks.append({
            "name": f"service-resource-stability:{service_name}",
            "passed": service_passed,
            "observed": service_results[service_name],
            "limit": {
                "rss_tail_growth_mb": max_rss_tail_growth_mb,
                "rss_slope_mb_per_window": max_rss_slope_mb_per_window,
                "handle_growth": max_handle_growth,
                "thread_growth": max_thread_growth,
            },
        })

    return {
        "summary_version": 1,
        "passed": all(check["passed"] for check in checks),
        "warmup_windows": warmup_windows,
        "measurement_windows": len(measured),
        "thresholds": {
            "max_rss_tail_growth_mb": max_rss_tail_growth_mb,
            "max_rss_slope_mb_per_window": max_rss_slope_mb_per_window,
            "max_handle_growth": max_handle_growth,
            "max_thread_growth": max_thread_growth,
        },
        "services": service_results,
        "checks": checks,
        "samples": samples,
    }

def leaderboard_aggregate(summary: dict[str, Any]) -> dict[str, Any]:
    return next(
        (item for item in summary.get("scenario_aggregates", []) if item.get("scenario") == "leaderboard"),
        {},
    )

def build_leaderboard_persistence_comparison(
    in_memory_summary: dict[str, Any],
    redis_summary: dict[str, Any],
    *,
    repetitions: int,
    redis_host: str,
    redis_port: int,
    redis_key: str,
    in_memory_log_verified: bool,
    redis_log_verified: bool,
    ping_before: bool,
    ping_after: bool,
    redis_zcard: int,
    expected_min_zcard: int,
) -> dict[str, Any]:
    in_memory = leaderboard_aggregate(in_memory_summary)
    redis = leaderboard_aggregate(redis_summary)
    deltas: list[dict[str, Any]] = []
    redis_operations = {item["operation"]: item for item in redis.get("operations", [])}
    for operation in in_memory.get("operations", []):
        peer = redis_operations.get(operation.get("operation"))
        if not peer:
            continue
        metrics: dict[str, Any] = {}
        for metric in ("throughput_ops_per_sec", "latency_p50_ms", "latency_p99_ms"):
            baseline = operation.get(metric, {}).get("median")
            observed = peer.get(metric, {}).get("median")
            if baseline is None or observed is None:
                continue
            metrics[metric] = {
                "in_memory_median": baseline,
                "redis_median": observed,
                "redis_minus_in_memory": round(float(observed) - float(baseline), 3),
                "delta_percent": round((float(observed) - float(baseline)) / float(baseline) * 100.0, 3)
                if float(baseline) != 0.0 else None,
            }
        deltas.append({"operation": operation.get("operation"), "metrics": metrics})

    modes_passed = all(
        aggregate.get("passed") is True
        and int(aggregate.get("runs", 0)) == repetitions
        and int(aggregate.get("passed_runs", 0)) == repetitions
        and all(int(operation.get("failed", -1)) == 0 for operation in aggregate.get("operations", []))
        for aggregate in (in_memory, redis)
    )
    redis_proof = {
        "host": redis_host,
        "port": redis_port,
        "key": redis_key,
        "ping_before": ping_before,
        "ping_after": ping_after,
        "zcard": redis_zcard,
        "expected_min_zcard": expected_min_zcard,
        "verified": ping_before and ping_after and redis_zcard >= expected_min_zcard,
    }
    verified = modes_passed and in_memory_log_verified and redis_log_verified and redis_proof["verified"]
    return {
        "requested": True,
        "verified": verified,
        "passed": verified,
        "repetitions_per_mode": repetitions,
        "execution_order": ["in_memory_only", "redis_primary_with_memory_shadow"],
        "modes": {
            "in_memory_only": {
                "log_verified": in_memory_log_verified,
                "summary": in_memory_summary,
            },
            "redis_primary_with_memory_shadow": {
                "log_verified": redis_log_verified,
                "summary": redis_summary,
            },
        },
        "redis_proof": redis_proof,
        "deltas": deltas,
    }

def estimate_battle_max_frames(cases: list[dict[str, Any]]) -> int:
    max_frames = 3
    for case in cases:
        if case.get("scenario") != "battle":
            continue
        duration_ms = (
            int(case.get("duration_seconds", 0))
            + int(case.get("ramp_timeout_seconds", 0))
        ) * 1000
        interval_ms = int(case.get("interval_ms") or 100)
        if duration_ms <= 0 or interval_ms <= 0:
            continue
        room_group_size = max(2, int(case.get("room_group_size", 2)))
        offers_per_client = (duration_ms + interval_ms - 1) // interval_ms + 1
        max_frames = max(
            max_frames,
            max(3, offers_per_client * room_group_size),
        )
    return max_frames

def run_business_flow_case(
    root: Path,
    build_dir: Path,
    output_root: Path,
    gateway_host: str,
    gateway_port: int,
    concurrent_clients: int = 1,
) -> dict[str, Any]:
    summary_path = output_root / "business-flow-summary.json"
    started = time.monotonic()
    client_path = resolve_executable(build_dir, "sdk_full_flow_client")
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    all_passed = True
    failure_reason = ""
    for index in range(max(1, concurrent_clients)):
        cmd = [str(client_path), gateway_host, str(gateway_port)]
        try:
            proc = subprocess.run(
                cmd,
                cwd=client_path.parent,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_parts.append(normalize_process_output(exc.stdout)[-4000:])
            stderr_parts.append(normalize_process_output(exc.stderr)[-4000:])
            all_passed = False
            failure_reason = f"SDK full-flow client timed out after {exc.timeout}s"
            break

        stdout_parts.append(normalize_process_output(proc.stdout)[-4000:])
        stderr_parts.append(normalize_process_output(proc.stderr)[-4000:])
        if proc.returncode != 0:
            all_passed = False
            failure_reason = f"SDK full-flow client exited with {proc.returncode}"
            break
    duration = round(time.monotonic() - started, 3)
    summary = {
        "passed": all_passed,
        "total_checks": max(1, concurrent_clients),
        "failed_checks": 0 if all_passed else 1,
        "gateway_host": gateway_host,
        "gateway_port": gateway_port,
        "concurrent_clients": max(1, concurrent_clients),
        "failure_reason": failure_reason,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    result: dict[str, Any] = {
        "name": "sdk-full-flow-business-path",
        "passed": all_passed,
        "duration_seconds": duration,
        "concurrent_clients": max(1, concurrent_clients),
        "command": [str(client_path), gateway_host, str(gateway_port)],
        "summary_path": str(summary_path),
        "stdout_tail": "\n".join(stdout_parts)[-8000:],
        "stderr_tail": "\n".join(stderr_parts)[-8000:],
    }
    if summary_path.exists():
        try:
            result["summary"] = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            result["passed"] = False
            result["stderr_tail"] = f"{result['stderr_tail']}\ninvalid business flow summary: {exc}"
    return result
