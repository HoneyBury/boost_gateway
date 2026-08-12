"""Performance baseline responsibility module: perf_bench_runtime."""

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
def invoke_bench_case(
    pressure_exe: Path,
    gateway_port: int,
    case: dict[str, Any],
    run_dir: Path,
    *,
    loadgen_cpu_set: str = "",
    loadgen_io_threads: int = 4,
    on_load_end: Callable[[], None] | None = None,
) -> dict[str, Any]:
    args = [
        "--host", "127.0.0.1",
        "--port", str(gateway_port),
        "--scenario", case["scenario"],
        "--clients", str(case["clients"]),
        "--duration", str(case["duration_seconds"]),
        "--io-threads", str(loadgen_io_threads),
        "--ramp-clients-per-second", str(case.get("ramp_clients_per_second", 200)),
        "--ramp-timeout", str(case.get("ramp_timeout_seconds", 60)),
        "--user-prefix", bench_user_prefix(str(case["name"])),
    ]
    if case.get("messages", 0) > 0:
        args.extend(["--messages", str(case["messages"])])
    if case.get("interval_ms") is not None:
        args.extend(["--interval", str(case["interval_ms"])])
    if case.get("load_model"):
        args.extend(["--load-model", str(case["load_model"])])
    if case.get("room"):
        room_name = str(case["room"])
        if case.get("scenario") == "battle":
            safe_case_name = str(case["name"]).replace(".", "_").replace("-", "_")
            room_name = f"{room_name}_{safe_case_name}"
        args.extend(["--room", room_name])
    if case.get("room_group_size"):
        args.extend(["--room-group-size", str(case["room_group_size"])])

    case_name = case["name"]
    stdout_path = run_dir / f"{case_name}.stdout.log"
    stderr_path = run_dir / f"{case_name}.stderr.log"
    json_path = run_dir / f"{case_name}.result.json"
    for path in (stdout_path, stderr_path, json_path):
        with suppress(FileNotFoundError):
            path.unlink()
    args.extend(["--output", str(json_path)])

    log_step(f"Running bench case: {case_name}")
    children_cpu_before = completed_children_cpu_seconds()
    proc = subprocess.Popen(
        affinity_command(pressure_exe, args, loadgen_cpu_set),
        cwd=pressure_exe.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    startup_affinity = (
        verify_process_cpu_affinity(proc.pid, loadgen_cpu_set)
        if loadgen_cpu_set
        else {
            "pid": proc.pid,
            "requested_cpu_set": "",
            "effective_cpu_set": process_snapshot(proc.pid).get("cpu_affinity", ""),
            "verified": True,
        }
    )
    sample_started_at = time.monotonic()
    resource_samples: list[dict[str, Any]] = [process_snapshot(proc.pid)]
    sampler_stop = threading.Event()

    def sample_loadgen() -> None:
        while not sampler_stop.wait(0.25):
            if proc.poll() is not None:
                return
            snapshot = process_snapshot(proc.pid)
            if snapshot.get("process_name"):
                resource_samples.append(snapshot)

    sampler = threading.Thread(target=sample_loadgen, name=f"{case_name}-resource-sampler", daemon=True)
    sampler.start()
    timeout_seconds = (
        int(case.get("ramp_timeout_seconds", 60))
        + int(case["duration_seconds"])
        + 15
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
    finally:
        if on_load_end is not None:
            on_load_end()
        sampler_stop.set()
        sampler.join(timeout=2)
    sample_elapsed_seconds = max(0.0, time.monotonic() - sample_started_at)
    children_cpu_after = completed_children_cpu_seconds()
    stdout_path.write_text(stdout or "", encoding="utf-8")
    stderr_path.write_text(stderr or "", encoding="utf-8")
    if proc.returncode != 0 and not json_path.exists():
        raise RuntimeError(f"Bench case failed: {case_name} (exit {proc.returncode})")

    if json_path.exists():
        result = json.loads(json_path.read_text(encoding="utf-8"))
    else:
        json_line = None
        for line in reversed((stdout or "").splitlines()):
            stripped = line.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                json_line = stripped
                break
        if json_line is None:
            raise RuntimeError(f"Bench case did not emit JSON result: {case_name}")
        result = json.loads(json_line)
        json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    if timed_out:
        result["collector_forced_timeout"] = True
        json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    if not result:
        raise RuntimeError(f"Bench case did not emit JSON result: {case_name}")
    result["bench_exit_code"] = int(proc.returncode or 0)
    first_sample = resource_samples[0]
    last_sample = resource_samples[-1]
    loadgen_resources = service_resource_delta(
        first_sample,
        last_sample,
        sample_elapsed_seconds,
    )
    if children_cpu_before is not None and children_cpu_after is not None:
        children_cpu_delta = max(0.0, children_cpu_after - children_cpu_before)
        loadgen_resources.update({
            "cpu_seconds_before": round(children_cpu_before, 6),
            "cpu_seconds_after": round(children_cpu_after, 6),
            "cpu_seconds_delta": round(children_cpu_delta, 6),
            "cpu_percent_from_cpu_seconds": round(
                children_cpu_delta / sample_elapsed_seconds * 100.0, 3
            ) if sample_elapsed_seconds > 0 else None,
        })
    loadgen_resources.update({
        "startup_affinity": startup_affinity,
        "before": first_sample,
        "after": last_sample,
        "sample_count": len(resource_samples),
        "sample_elapsed_seconds": round(sample_elapsed_seconds, 6),
        "working_set_mb_peak": max(
            float(sample.get("working_set_mb", 0.0)) for sample in resource_samples
        ),
    })
    result["loadgen_resources"] = loadgen_resources
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
