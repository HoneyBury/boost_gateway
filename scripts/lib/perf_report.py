"""Performance baseline responsibility module: perf_report."""

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
from scripts.lib.perf_business_operations import *  # noqa: F401,F403
from scripts.lib.perf_stability_evidence import *  # noqa: F401,F403
from scripts.lib.perf_result_aggregation import *  # noqa: F401,F403
from scripts.lib.perf_resource_evidence import *  # noqa: F401,F403
from scripts.lib.perf_saturation_analysis import *  # noqa: F401,F403
def minimum_battle_messages(case_name: str) -> int:
    if case_name.startswith("battle-open-"):
        return 1_000
    if case_name.startswith("battle-500"):
        return 20_000
    if case_name.startswith("battle-100"):
        return 5_000
    if case_name.startswith("battle-20"):
        return 1_000
    if case_name.startswith("battle-2"):
        return 50
    return 1

def battle_p99_limit_ms(case_name: str) -> float:
    if case_name.startswith("battle-open-"):
        return 5_000.0
    if case_name.startswith("battle-100"):
        return 250.0
    if case_name.startswith("battle-500"):
        return 500.0
    return 100.0

def fmt_number(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "true" if value else "false"
    with suppress(TypeError, ValueError):
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)

def aggregate_metric(resource_case: dict[str, Any], service: str, metric: str, stat: str = "median") -> Any:
    service_metrics = resource_case.get("services", {}).get(service, {})
    metric_value = service_metrics.get(metric)
    if isinstance(metric_value, dict):
        return metric_value.get(stat)
    return None

def render_markdown_report(summary: dict[str, Any]) -> str:
    resource_constraint = summary.get("resource_constraint", {})
    lines = [
        "# v2 Performance Baseline Report",
        "",
        f"- Collected at: `{summary.get('collected_at', 'unknown')}`",
        f"- Git commit: `{summary.get('git_commit', 'unknown')}`",
        f"- Platform: `{summary.get('host_platform', 'unknown')}`",
        f"- Build dir: `{summary.get('build_dir', 'unknown')}`",
        f"- Preset: `{summary.get('preset', 'unknown')}`",
        f"- Repetitions: `{summary.get('repetitions', 'unknown')}`",
        f"- Backend pool size: `{summary.get('topology', {}).get('backend_connection_pool_size', 'unknown')}`",
        f"- CPU affinity: `{resource_constraint.get('effective_cpu_set') or 'unconstrained'}`",
        f"- Output dir: `{summary.get('output_dir', 'unknown')}`",
        "",
        "## Release Gates",
        "",
    ]
    gates = summary.get("release_gates", {})
    lines.append(f"- Overall pass: **{fmt_number(gates.get('overall_pass'))}**")
    warnings = gates.get("warnings", [])
    if warnings:
        lines.append(f"- Warnings: {len(warnings)}")
    else:
        lines.append("- Warnings: 0")
    lines.extend([
        "",
        "| Case | Pass | Criteria | Observed |",
        "| --- | --- | --- | --- |",
    ])
    for check in gates.get("checks", []):
        observed = check.get("observed", {})
        observed_text = (
            f"p99={fmt_number(observed.get('p99_ms'))}ms, "
            f"throughput={fmt_number(observed.get('throughput_msg_per_sec'))}/s, "
            f"rejected={fmt_number(observed.get('rejected_clients'))}, "
            f"failed={fmt_number(observed.get('failed_clients'))}, "
            f"forced_timeout={fmt_number(observed.get('forced_timeout'))}"
        )
        lines.append(
            f"| `{check.get('case')}` | {fmt_number(check.get('passed'))} | "
            f"{check.get('criteria')} | {observed_text} |"
        )

    lines.extend([
        "",
        "## Case Aggregates",
        "",
        "| Case | Runs | Connected | Messages | Throughput msg/s | P50 ms | P90 ms | P99 ms | Rejected | Failed | Forced timeout |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    for aggregate in summary.get("case_aggregates", []):
        lines.append(
            f"| `{aggregate.get('case_name')}` | {aggregate.get('runs')} | "
            f"{fmt_number(aggregate.get('connected_clients', {}).get('median'))} | "
            f"{fmt_number(aggregate.get('total_messages', {}).get('median'))} | "
            f"{fmt_number(aggregate.get('throughput_msg_per_sec', {}).get('median'))} | "
            f"{fmt_number(aggregate.get('latency_p50_ms', {}).get('median'))} | "
            f"{fmt_number(aggregate.get('latency_p90_ms', {}).get('median'))} | "
            f"{fmt_number(aggregate.get('latency_p99_ms', {}).get('median'))} | "
            f"{fmt_number(aggregate.get('rejected_clients', {}).get('max'))} | "
            f"{fmt_number(aggregate.get('failed_clients', {}).get('max'))} | "
            f"{fmt_number(aggregate.get('forced_timeout'))} |"
        )

    saturation = summary.get("saturation_analysis")
    if isinstance(saturation, dict):
        lines.extend([
            "",
            "## Saturation Analysis",
            "",
            f"- Collection pass: **{fmt_number(saturation.get('collection_pass'))}**",
            f"- Conclusion: `{saturation.get('conclusion', 'inconclusive')}`",
            f"- Load model: `{saturation.get('load_model')}`",
            f"- CPU threshold: {fmt_number(saturation.get('cpu_threshold_percent_of_quota'))}% of quota",
            f"- Loadgen headroom threshold: {fmt_number(saturation.get('loadgen_headroom_threshold_percent_of_quota'))}% of quota",
            f"- Required evidence through: `{saturation.get('required_evidence_through_case') or 'all selected cases'}`",
            "- Invalid points beyond confirmed saturation: "
            + (
                ", ".join(
                    f"`{case_id}`"
                    for case_id in saturation.get("post_saturation_invalid_points", [])
                )
                or "none"
            ),
            f"- Inconclusive reason: {saturation.get('inconclusive_reason') or 'n/a'}",
            "",
            "| Case | Ceiling ops/s | Send ops/s | Response ops/s | Gateway CPU quota % | Loadgen CPU quota % | P99 ms | Errors |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
        for point in saturation.get("points", []):
            lines.append(
                f"| `{point.get('case_id')}` | "
                f"{fmt_number(point.get('configured_request_rate_ceiling_ops_per_sec'))} | "
                f"{fmt_number(point.get('achieved_send_rate_ops_per_sec'))} | "
                f"{fmt_number(point.get('achieved_response_rate_ops_per_sec'))} | "
                f"{fmt_number(point.get('gateway_cpu_quota_percent'))} | "
                f"{fmt_number(point.get('loadgen_cpu_quota_percent'))} | "
                f"{fmt_number(point.get('latency_p99_ms'))} | "
                f"{fmt_number(point.get('client_error_count'))} |"
            )

    otel_comparison = summary.get("otel_comparison")
    if isinstance(otel_comparison, dict):
        deltas = otel_comparison.get("deltas", {})
        on_proof = otel_comparison.get("proof", {}).get("on", {})
        lines.extend([
            "",
            "## OTel Off/On Comparison",
            "",
            f"- Verified: **{fmt_number(otel_comparison.get('verified'))}**",
            f"- Runs per mode: {fmt_number(otel_comparison.get('repetitions_per_mode'))}",
            f"- Regression policy: `{otel_comparison.get('performance_regression_policy')}`",
            f"- Collector spans: {fmt_number(on_proof.get('collector', {}).get('spans'))}",
            f"- Exporter failed batches: {fmt_number(on_proof.get('exporter', {}).get('failed_batches'))}",
            "",
            "| Metric | Off median | On median | On - off | Delta % |",
            "| --- | ---: | ---: | ---: | ---: |",
        ])
        for metric in (
            "throughput_msg_per_sec",
            "latency_p99_ms",
            "gateway_cpu_seconds",
            "gateway_rss_mb",
        ):
            delta = deltas.get(metric, {})
            lines.append(
                f"| `{metric}` | {fmt_number(delta.get('off'), 3)} | "
                f"{fmt_number(delta.get('on'), 3)} | "
                f"{fmt_number(delta.get('on_minus_off'), 3)} | "
                f"{fmt_number(delta.get('delta_percent'), 3)} |"
            )

    business_operation_perf = summary.get("business_operation_perf")
    if isinstance(business_operation_perf, dict):
        lines.extend([
            "",
            "## Business Operation Performance",
            "",
            f"- Runs: {business_operation_perf.get('completed_runs', 0)}/{business_operation_perf.get('requested_runs', 0)}",
            "",
            "| Scenario | Operation | Attempted | Succeeded | Failed | Throughput ops/s | P50 ms | P99 ms |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
        for scenario in business_operation_perf.get("scenario_aggregates", []):
            for operation in scenario.get("operations", []):
                lines.append(
                    f"| `{scenario.get('scenario')}` | `{operation.get('operation')}` | "
                    f"{fmt_number(operation.get('attempted'))} | "
                    f"{fmt_number(operation.get('succeeded'))} | "
                    f"{fmt_number(operation.get('failed'))} | "
                    f"{fmt_number(operation.get('throughput_ops_per_sec', {}).get('median'), 3)} | "
                    f"{fmt_number(operation.get('latency_p50_ms', {}).get('median'), 3)} | "
                    f"{fmt_number(operation.get('latency_p99_ms', {}).get('median'), 3)} |"
                )
            if scenario.get("scenario") == "matchmaking":
                lines.append(
                    f"| `matchmaking` | `time_to_match` | {fmt_number(scenario.get('time_to_match_samples'))} | "
                    f"{fmt_number(scenario.get('time_to_match_samples'))} | 0 | n/a | "
                    f"{fmt_number(scenario.get('time_to_match_p50_ms'), 3)} | "
                    f"{fmt_number(scenario.get('time_to_match_p99_ms'), 3)} |"
                )

    business_flow = summary.get("business_flow")
    if isinstance(business_flow, dict):
        flow_summary = business_flow.get("summary") if isinstance(business_flow.get("summary"), dict) else {}
        lines.extend([
            "",
            "## Business Flow Coverage",
            "",
            "| Check | Value |",
            "| --- | --- |",
            f"| pass | {fmt_number(business_flow.get('passed'))} |",
            f"| duration seconds | {fmt_number(business_flow.get('duration_seconds'), 3)} |",
            f"| concurrent clients | {fmt_number(business_flow.get('concurrent_clients'))} |",
            f"| total checks | {fmt_number(flow_summary.get('total_checks'))} |",
            f"| failed checks | {fmt_number(flow_summary.get('failed_checks'))} |",
            f"| summary | `{business_flow.get('summary_path')}` |",
        ])

    backend_metrics = summary.get("final_backend_metrics")
    if isinstance(backend_metrics, dict) and backend_metrics:
        lines.extend([
            "",
            "## Backend Metrics Snapshot",
            "",
            "| Service | Requests | Successes | Errors | Timeouts | Avg latency us | P99 latency us | Samples |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
        for service in ("login", "room", "battle", "matchmaking", "leaderboard"):
            metric = backend_metrics.get(service)
            if not isinstance(metric, dict):
                continue
            lines.append(
                f"| `{service}` | "
                f"{fmt_number(metric.get('total_requests'))} | "
                f"{fmt_number(metric.get('total_successes'))} | "
                f"{fmt_number(metric.get('total_errors'))} | "
                f"{fmt_number(metric.get('total_timeouts'))} | "
                f"{fmt_number(metric.get('avg_latency_us'))} | "
                f"{fmt_number(metric.get('p99_latency_us'))} | "
                f"{fmt_number(metric.get('latency_sample_count'))} |"
            )

    resources = summary.get("resource_analysis", {})
    lines.extend([
        "",
        "## Gateway Resource Aggregates",
        "",
        "| Case | RSS MB | RSS delta MB | RSS KB/client | fd | fd delta | fd/client | Threads | CPU % snapshot | CPU % from delta |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for resource_case in resources.get("case_aggregates", []):
        lines.append(
            f"| `{resource_case.get('case_name')}` | "
            f"{fmt_number(aggregate_metric(resource_case, 'v2_gateway_demo', 'working_set_mb'))} | "
            f"{fmt_number(aggregate_metric(resource_case, 'v2_gateway_demo', 'working_set_mb_delta'))} | "
            f"{fmt_number(aggregate_metric(resource_case, 'v2_gateway_demo', 'rss_kb_per_connected_client'), 3)} | "
            f"{fmt_number(aggregate_metric(resource_case, 'v2_gateway_demo', 'handles'))} | "
            f"{fmt_number(aggregate_metric(resource_case, 'v2_gateway_demo', 'handles_delta'))} | "
            f"{fmt_number(aggregate_metric(resource_case, 'v2_gateway_demo', 'handles_per_connected_client'), 6)} | "
            f"{fmt_number(aggregate_metric(resource_case, 'v2_gateway_demo', 'threads'))} | "
            f"{fmt_number(aggregate_metric(resource_case, 'v2_gateway_demo', 'cpu_percent'))} | "
            f"{fmt_number(aggregate_metric(resource_case, 'v2_gateway_demo', 'cpu_percent_from_cpu_seconds'))} |"
        )

    lines.extend([
        "",
        "## Gateway Runtime Stage Metrics",
        "",
        "| Case | Queue batch | Queue wait us | Handle us | Route queue us | Route execution us | Backend latency us |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for case in summary.get("case_aggregates", []):
        metrics = case.get("gateway_runtime_metrics", {})

        def runtime_median(name: str) -> Any:
            value = metrics.get(name)
            return value.get("median") if isinstance(value, dict) else None

        lines.append(
            f"| `{case.get('case_name')}` | "
            f"{fmt_number(runtime_median('gateway_queue_average_batch_size'), 3)} | "
            f"{fmt_number(runtime_median('gateway_queue_average_wait_us'), 3)} | "
            f"{fmt_number(runtime_median('gateway_queue_average_handle_us'), 3)} | "
            f"{fmt_number(runtime_median('battle_route_average_queue_wait_us'), 3)} | "
            f"{fmt_number(runtime_median('battle_route_average_task_execution_us'), 3)} | "
            f"{fmt_number(runtime_median('backend_average_latency_us'), 3)} |"
        )

    lines.extend([
        "",
        "## Artifacts",
        "",
        "- Raw summary: `summary.json`",
        "- Markdown report: `report.md`",
        "- Case result JSON files: `results/*.result.json`",
        "- Gateway diagnostics snapshots: `results/*.gateway.diagnostics.json`",
        "- Process logs: `logs/*.log`",
        "",
    ])
    return "\n".join(lines)
