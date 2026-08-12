"""Performance baseline responsibility module: perf_process_affinity."""

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

def log_step(message: str) -> None:
    print(f"==> {message}", flush=True)

def is_windows() -> bool:
    return os.name == "nt"

def parse_cpu_set(value: str) -> set[int]:
    """Parse a Linux CPU list such as ``0-3,6`` into CPU identifiers."""
    cpus: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError("CPU set contains an empty segment")
        if "-" in part:
            bounds = part.split("-")
            if len(bounds) != 2 or not all(bound.isdigit() for bound in bounds):
                raise ValueError(f"invalid CPU range: {part}")
            first, last = (int(bound) for bound in bounds)
            if first > last:
                raise ValueError(f"CPU range is reversed: {part}")
            cpus.update(range(first, last + 1))
        elif part.isdigit():
            cpus.add(int(part))
        else:
            raise ValueError(f"invalid CPU identifier: {part}")
    if not cpus:
        raise ValueError("CPU set must select at least one CPU")
    return cpus

def format_cpu_set(cpus: set[int]) -> str:
    """Render CPU identifiers in the canonical Linux list form."""
    ordered = sorted(cpus)
    ranges: list[str] = []
    start = previous = ordered[0]
    for cpu in ordered[1:]:
        if cpu == previous + 1:
            previous = cpu
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = cpu
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)

def apply_cpu_affinity(cpu_set: str) -> dict[str, Any]:
    """Apply and verify affinity before children are spawned so they inherit it."""
    constraint: dict[str, Any] = {
        "type": "linux_cpu_affinity",
        "requested": cpu_set,
        "applied": False,
        "allowed_cpu_set_before": "",
        "effective_cpu_set": "",
        "cpu_count": 0,
    }
    if not cpu_set:
        constraint["type"] = "none"
        return constraint
    if platform.system() != "Linux" or not hasattr(os, "sched_setaffinity") or not hasattr(os, "sched_getaffinity"):
        raise RuntimeError("--cpu-set requires Linux sched affinity support")

    requested = parse_cpu_set(cpu_set)
    available = set(os.sched_getaffinity(0))
    unavailable = requested - available
    if unavailable:
        raise ValueError(
            f"requested CPUs are outside the collector's allowed set: {format_cpu_set(unavailable)} "
            f"(allowed: {format_cpu_set(available)})"
        )
    os.sched_setaffinity(0, requested)
    effective = set(os.sched_getaffinity(0))
    if effective != requested:
        raise RuntimeError(
            f"CPU affinity verification failed: requested {format_cpu_set(requested)}, "
            f"effective {format_cpu_set(effective)}"
        )
    constraint.update({
        "requested": format_cpu_set(requested),
        "applied": True,
        "allowed_cpu_set_before": format_cpu_set(available),
        "effective_cpu_set": format_cpu_set(effective),
        "cpu_count": len(effective),
    })
    return constraint

def prepare_process_cpu_affinity(cpu_set: str, option_name: str) -> dict[str, Any]:
    """Validate a child-process affinity without constraining the collector."""
    constraint: dict[str, Any] = {
        "type": "linux_cpu_affinity",
        "requested": cpu_set,
        "applied": False,
        "allowed_cpu_set_before": "",
        "effective_cpu_set": "",
        "cpu_count": 0,
        "processes": [],
    }
    if not cpu_set:
        constraint["type"] = "none"
        return constraint
    if platform.system() != "Linux" or not hasattr(os, "sched_getaffinity"):
        raise RuntimeError(f"{option_name} requires Linux sched affinity support")
    if shutil.which("taskset") is None:
        raise RuntimeError(f"{option_name} requires the Linux taskset command")

    requested = parse_cpu_set(cpu_set)
    available = set(os.sched_getaffinity(0))
    unavailable = requested - available
    if unavailable:
        raise ValueError(
            f"requested CPUs for {option_name} are outside the collector's allowed set: "
            f"{format_cpu_set(unavailable)} (allowed: {format_cpu_set(available)})"
        )
    canonical = format_cpu_set(requested)
    constraint.update({
        "requested": canonical,
        "allowed_cpu_set_before": format_cpu_set(available),
        "cpu_count": len(requested),
    })
    return constraint

def resolve_loadgen_cpu_set(service_cpu_set: str, loadgen_cpu_set: str) -> str:
    """Resolve a load-generator set that is disjoint from constrained services."""
    if not service_cpu_set:
        if loadgen_cpu_set:
            raise ValueError("--loadgen-cpu-set requires --cpu-set so services can be isolated explicitly")
        return ""
    service = parse_cpu_set(service_cpu_set)
    if loadgen_cpu_set:
        loadgen = parse_cpu_set(loadgen_cpu_set)
    else:
        if platform.system() != "Linux" or not hasattr(os, "sched_getaffinity"):
            raise RuntimeError("automatic loadgen CPU isolation requires Linux sched affinity support")
        loadgen = set(os.sched_getaffinity(0)) - service
        if not loadgen:
            raise ValueError(
                "--cpu-set consumes every allowed CPU; provide a runner with at least one disjoint loadgen CPU"
            )
    overlap = service & loadgen
    if overlap:
        raise ValueError(
            "--cpu-set and --loadgen-cpu-set must be disjoint; overlapping CPUs: "
            f"{format_cpu_set(overlap)}"
        )
    return format_cpu_set(loadgen)

def affinity_command(executable: Path, args: list[str], cpu_set: str) -> list[str]:
    command = [str(executable), *args]
    if not cpu_set:
        return command
    taskset = shutil.which("taskset")
    if taskset is None:
        raise RuntimeError("CPU-affined child process requires the Linux taskset command")
    return [taskset, "--cpu-list", format_cpu_set(parse_cpu_set(cpu_set)), *command]

def verify_process_cpu_affinity(
    pid: int,
    cpu_set: str,
    timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    requested = parse_cpu_set(cpu_set)
    deadline = time.monotonic() + timeout_seconds
    last_effective: set[int] = set()
    while time.monotonic() < deadline:
        try:
            last_effective = set(os.sched_getaffinity(pid))
        except (OSError, ProcessLookupError, PermissionError):
            last_effective = set()
        if last_effective == requested:
            canonical = format_cpu_set(requested)
            return {
                "pid": pid,
                "requested_cpu_set": canonical,
                "effective_cpu_set": canonical,
                "verified": True,
            }
        time.sleep(0.01)
    raise RuntimeError(
        f"child process {pid} CPU affinity verification failed: requested "
        f"{format_cpu_set(requested)}, effective "
        f"{format_cpu_set(last_effective) if last_effective else 'unavailable'}"
    )

def record_process_affinity(
    constraint: dict[str, Any],
    process: ManagedProcess,
    *,
    workload: str,
) -> None:
    if not constraint.get("requested"):
        return
    processes = constraint.setdefault("processes", [])
    processes.append({"workload": workload, "service_name": process.name, **process.startup_affinity})
    constraint["applied"] = all(item.get("verified") is True for item in processes)
    if constraint["applied"]:
        constraint["effective_cpu_set"] = constraint["requested"]

def exe_name(base: str) -> str:
    return f"{base}.exe" if is_windows() else base

def resolve_executable(build_dir: Path, base_name: str) -> Path:
    target_names = {exe_name(base_name), base_name}
    matches = sorted(
        p for p in build_dir.rglob("*")
        if p.is_file() and p.name in target_names
    )
    direct_matches = [
        p for p in matches
        if "build" not in p.relative_to(build_dir).parts[:-1]
    ]
    if direct_matches:
        matches = sorted(direct_matches, key=lambda p: (len(p.relative_to(build_dir).parts), str(p)))
    elif is_windows():
        preferred = [
            p for p in matches
            if any(part.lower() in {"debug", "release", "relwithdebinfo", "minsizerel"} for part in p.parts)
        ]
        if preferred:
            matches = preferred
    if not matches:
        target_name = exe_name(base_name)
        raise FileNotFoundError(f"Executable not found: {target_name} under {build_dir}")
    return matches[0]
