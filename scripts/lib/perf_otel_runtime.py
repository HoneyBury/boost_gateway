"""Performance baseline responsibility module: perf_otel_runtime."""

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
class LoopbackOtelCollector:
    """Small OTLP/HTTP JSON sink used only for fixed-runner comparison proof."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters = {
            "requests": 0,
            "spans": 0,
            "invalid_payloads": 0,
            "http_status_errors": 0,
            "span_status_errors": 0,
        }
        collector = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length)
                valid = False
                spans: list[object] = []
                try:
                    payload = json.loads(body)
                    raw_spans = payload.get("spans") if isinstance(payload, dict) else None
                    if self.path == "/v1/traces" and isinstance(raw_spans, list):
                        spans = raw_spans
                        valid = all(isinstance(span, dict) for span in spans)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
                with collector._lock:
                    collector._counters["requests"] += 1
                    if valid:
                        collector._counters["spans"] += len(spans)
                        collector._counters["span_status_errors"] += sum(
                            1 for span in spans if span.get("status") != "ok"
                        )
                    else:
                        collector._counters["invalid_payloads"] += 1
                        collector._counters["http_status_errors"] += 1
                self.send_response(200 if valid else 400)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1/traces"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

def counter_delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    return {key: int(after.get(key, 0)) - int(before.get(key, 0)) for key in after}

def total_backend_requests(diagnostics: dict[str, Any]) -> int:
    metrics = diagnostics.get("backend_metrics")
    if not isinstance(metrics, dict):
        return 0
    return sum(
        int(snapshot.get("total_requests", 0))
        for snapshot in metrics.values()
        if isinstance(snapshot, dict)
    )

def wait_for_otel_mode_quiescence(
    diagnostics_url: str,
    *,
    mode: str,
    initial_backend_requests: int,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    previous: tuple[int, int] | None = None
    stable_samples = 0
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = fetch_json(diagnostics_url)
        routed = total_backend_requests(latest) - initial_backend_requests
        enqueued = int(otel_exporter_metrics(latest).get("enqueued_spans", 0))
        current = (routed, enqueued)
        counters_agree = mode == "off" or routed == enqueued
        stable_samples = stable_samples + 1 if current == previous and counters_agree else 0
        if stable_samples >= 1:
            return latest
        previous = current
        time.sleep(0.1)
    return latest

def snapshot_processes(managed: list[ManagedProcess]) -> list[dict[str, Any]]:
    snapshots = []
    for item in managed:
        snap = process_snapshot(item.pid)
        snap["service_name"] = item.name
        snapshots.append(snap)
    return snapshots

def wait_for_service_quiescence(
    managed: list[ManagedProcess],
    diagnostics_url: str,
    *,
    timeout_seconds: float = 30.0,
    interval_seconds: float = 0.1,
    idle_cpu_fraction: float = 0.05,
) -> dict[str, Any]:
    """Wait until routing is stable and aggregate background CPU is below budget."""
    deadline = time.monotonic() + timeout_seconds
    previous: tuple[int, int, tuple[tuple[str, float], ...], float] | None = None
    latest: tuple[int, int, tuple[tuple[str, float], ...]] | None = None
    latest_cpu_delta = 0.0
    latest_cpu_budget = 0.0
    samples = 0
    while time.monotonic() < deadline:
        diagnostics = fetch_json(diagnostics_url)
        cpu_state = tuple(sorted(
            (
                process.name,
                round(float(process_snapshot(process.pid).get("cpu_seconds", 0.0)), 6),
            )
            for process in managed
        ))
        latest = (
            total_backend_requests(diagnostics),
            int(diagnostics.get("total_active_sessions", 0)),
            cpu_state,
        )
        sampled_at = time.monotonic()
        samples += 1
        if (
            previous is not None
            and latest[0] == previous[0]
            and latest[1] == 0
            and previous[1] == 0
        ):
            previous_cpu = dict(previous[2])
            current_cpu = dict(cpu_state)
            if current_cpu.keys() == previous_cpu.keys():
                elapsed = max(sampled_at - previous[3], interval_seconds, 0.001)
                process_count = max(1, len(current_cpu))
                latest_cpu_delta = sum(
                    max(0.0, current_cpu[name] - previous_cpu[name])
                    for name in current_cpu
                )
                latest_cpu_budget = max(
                    0.01 * process_count,
                    elapsed * idle_cpu_fraction * process_count,
                )
                if latest_cpu_delta <= latest_cpu_budget:
                    return {
                        "quiesced": True,
                        "samples": samples,
                        "backend_routed_requests": latest[0],
                        "active_sessions": latest[1],
                        "aggregate_cpu_delta_seconds": round(latest_cpu_delta, 6),
                        "aggregate_cpu_budget_seconds": round(latest_cpu_budget, 6),
                        "aggregate_cpu_percent": round(latest_cpu_delta / elapsed * 100.0, 3),
                        "idle_cpu_budget_percent": round(idle_cpu_fraction * 100.0, 3),
                        "aggregate_idle_cpu_budget_percent": round(
                            idle_cpu_fraction * process_count * 100.0, 3
                        ),
                        "managed_process_count": process_count,
                        "wait_seconds": round(timeout_seconds - max(0.0, deadline - time.monotonic()), 6),
                    }
        previous = (latest[0], latest[1], cpu_state, sampled_at)
        time.sleep(interval_seconds)
    raise RuntimeError(
        "managed service topology did not quiesce after load generation: "
        f"last_state={latest}, aggregate_cpu_delta_seconds={latest_cpu_delta:.6f}, "
        f"aggregate_cpu_budget_seconds={latest_cpu_budget:.6f}"
    )

def _darwin_ephemeral_port_range() -> tuple[int, int]:
    completed = subprocess.run(
        ["sysctl", "-n", "net.inet.ip.portrange.first", "net.inet.ip.portrange.last"],
        check=False,
        capture_output=True,
        text=True,
    )
    values = [int(value) for value in completed.stdout.split() if value.isdigit()]
    if completed.returncode != 0 or len(values) != 2 or values[0] > values[1]:
        raise RuntimeError("failed to resolve the Darwin ephemeral TCP port range")
    return values[0], values[1]

def _darwin_time_wait_count() -> int:
    completed = subprocess.run(
        ["netstat", "-an", "-p", "tcp"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("failed to inspect Darwin TCP socket state")
    return sum(1 for line in completed.stdout.splitlines() if "TIME_WAIT" in line.split())

def wait_for_local_connection_budget(
    target_connections: int,
    *,
    headroom: int = 1024,
    timeout_seconds: float = 60.0,
    interval_seconds: float = 1.0,
) -> dict[str, Any]:
    """Wait for Darwin's bounded ephemeral port pool before a local capacity run."""
    if platform.system() != "Darwin":
        return {
            "required": False,
            "platform": platform.system(),
            "target_connections": target_connections,
            "wait_seconds": 0.0,
        }

    first, last = _darwin_ephemeral_port_range()
    port_capacity = last - first + 1
    required_free = target_connections + headroom
    if required_free > port_capacity:
        raise RuntimeError(
            "Darwin ephemeral TCP port range cannot support the requested local capacity: "
            f"target={target_connections}, headroom={headroom}, capacity={port_capacity}"
        )

    maximum_time_wait = port_capacity - required_free
    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    initial_time_wait: int | None = None
    while True:
        current_time_wait = _darwin_time_wait_count()
        if initial_time_wait is None:
            initial_time_wait = current_time_wait
        if current_time_wait <= maximum_time_wait:
            return {
                "required": True,
                "platform": "Darwin",
                "target_connections": target_connections,
                "headroom": headroom,
                "ephemeral_port_first": first,
                "ephemeral_port_last": last,
                "ephemeral_port_capacity": port_capacity,
                "maximum_time_wait": maximum_time_wait,
                "initial_time_wait": initial_time_wait,
                "final_time_wait": current_time_wait,
                "wait_seconds": round(time.monotonic() - started_at, 6),
            }
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Darwin ephemeral TCP port budget did not recover before capacity load: "
                f"target={target_connections}, time_wait={current_time_wait}, "
                f"maximum_time_wait={maximum_time_wait}"
            )
        time.sleep(interval_seconds)

def bench_user_prefix(case_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", case_name).strip("_") or "run"
    prefix = f"bench_{normalized}"
    if len(prefix) > 48:
        checksum = zlib.crc32(prefix.encode("utf-8")) & 0xFFFFFFFF
        prefix = f"{prefix[:39]}_{checksum:08x}"
    return prefix


def otel_exporter_metrics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    raw = diagnostics.get("otel_exporter_metrics")
    if not isinstance(raw, dict):
        return {}
    return {
        "configured": raw.get("configured") is True,
        "enqueued_spans": int(raw.get("enqueued_spans", 0)),
        "exported_spans": int(raw.get("exported_spans", 0)),
        "successful_batches": int(raw.get("successful_batches", 0)),
        "failed_batches": int(raw.get("failed_batches", 0)),
        "buffered_spans": int(raw.get("buffered_spans", 0)),
    }
