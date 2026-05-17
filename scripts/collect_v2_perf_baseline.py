#!/usr/bin/env python3
"""Collect v2 multi-process performance baseline data across platforms."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen


def log_step(message: str) -> None:
    print(f"==> {message}", flush=True)


def is_windows() -> bool:
    return os.name == "nt"


def exe_name(base: str) -> str:
    return f"{base}.exe" if is_windows() else base


def resolve_executable(build_dir: Path, base_name: str) -> Path:
    target_names = {exe_name(base_name), base_name}
    matches = sorted(
        p for p in build_dir.rglob("*")
        if p.is_file() and p.name in target_names
    )
    if is_windows():
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


def wait_tcp_port(host: str, port: int, timeout_seconds: float = 30.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with suppress(OSError):
            with socket.create_connection((host, port), timeout=0.5):
                return
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for TCP {host}:{port}")


def parse_cpu_time_to_seconds(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    days = 0
    if "-" in value:
        day_part, value = value.split("-", 1)
        with suppress(ValueError):
            days = int(day_part)
    parts = value.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours = "0"
            minutes, seconds = parts
        else:
            return None
        return days * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None


def count_open_files(pid: int) -> int | None:
    proc_fd = Path(f"/proc/{pid}/fd")
    if proc_fd.is_dir():
        with suppress(OSError):
            return sum(1 for _ in proc_fd.iterdir())

    try:
        output = subprocess.check_output(
            ["lsof", "-p", str(pid), "-Fn"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    # lsof -Fn emits one "n..." line per named open file plus process header lines.
    return sum(1 for line in output.splitlines() if line.startswith("n"))


def thread_count(pid: int) -> int | None:
    for cmd in (
        ["ps", "-o", "nlwp=", "-p", str(pid)],
        ["ps", "-o", "thcount=", "-p", str(pid)],
    ):
        try:
            output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        if output:
            with suppress(ValueError):
                return int(output.splitlines()[-1].strip())
    return None


def process_cpu_seconds(pid: int) -> float | None:
    for field in ("cputime", "time"):
        try:
            output = subprocess.check_output(
                ["ps", "-o", f"{field}=", "-p", str(pid)],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        if not output:
            continue
        parsed = parse_cpu_time_to_seconds(output.splitlines()[-1])
        if parsed is not None:
            return parsed
    return None


def process_snapshot(pid: int) -> dict[str, Any]:
    if is_windows():
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                f"$p = Get-Process -Id {pid} -ErrorAction Stop; "
                "[pscustomobject]@{"
                "pid=$p.Id;"
                "process_name=$p.ProcessName;"
                "working_set_mb=[math]::Round($p.WorkingSet64 / 1MB, 2);"
                "private_memory_mb=[math]::Round($p.PrivateMemorySize64 / 1MB, 2);"
                "virtual_memory_mb=[math]::Round($p.VirtualMemorySize64 / 1MB, 2);"
                "handles=$p.Handles;"
                "threads=$p.Threads.Count;"
                "cpu_seconds=[math]::Round($p.CPU, 2)"
                "} | ConvertTo-Json -Compress"
            ),
        ]
        output = subprocess.check_output(cmd, text=True).strip()
        return json.loads(output)

    status_path = Path(f"/proc/{pid}/status")
    if not status_path.exists():
        cmd = ["ps", "-o", "pid=,comm=,rss=,vsz=,%cpu=", "-p", str(pid)]
        try:
            output = subprocess.check_output(cmd, text=True).strip()
        except subprocess.CalledProcessError:
            return {
                "pid": pid,
                "process_name": "",
                "working_set_mb": 0.0,
                "private_memory_mb": None,
                "virtual_memory_mb": 0.0,
                "handles": count_open_files(pid),
                "threads": thread_count(pid),
                "cpu_seconds": process_cpu_seconds(pid),
            }
        parts = output.split()
        if len(parts) < 5:
            return {
                "pid": pid,
                "process_name": "",
                "working_set_mb": 0.0,
                "private_memory_mb": None,
                "virtual_memory_mb": 0.0,
                "handles": count_open_files(pid),
                "threads": thread_count(pid),
                "cpu_seconds": process_cpu_seconds(pid),
            }
        return {
            "pid": int(parts[0]),
            "process_name": parts[1],
            "working_set_mb": round(float(parts[2]) / 1024.0, 2),
            "private_memory_mb": None,
            "virtual_memory_mb": round(float(parts[3]) / 1024.0, 2),
            "handles": count_open_files(pid),
            "threads": thread_count(pid),
            "cpu_percent": float(parts[4]),
            "cpu_seconds": process_cpu_seconds(pid),
        }

    with open(status_path, "r", encoding="utf-8") as fh:
        status_lines = fh.readlines()

    info: dict[str, str] = {}
    for line in status_lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        info[key.strip()] = value.strip()

    def parse_kb(value: str | None) -> float:
        if not value:
            return 0.0
        parts = value.split()
        if not parts:
            return 0.0
        return round(float(parts[0]) / 1024.0, 2)

    return {
        "pid": pid,
        "process_name": info.get("Name", ""),
        "working_set_mb": parse_kb(info.get("VmRSS")),
        "private_memory_mb": parse_kb(info.get("RssAnon")),
        "virtual_memory_mb": parse_kb(info.get("VmSize")),
        "handles": count_open_files(pid),
        "threads": int(info.get("Threads", "0")),
        "cpu_seconds": process_cpu_seconds(pid),
    }


class ManagedProcess:
    def __init__(self, name: str, executable: Path, args: list[str], log_dir: Path, env: dict[str, str] | None = None) -> None:
        self.name = name
        self.stdout_path = log_dir / f"{name}.stdout.log"
        self.stderr_path = log_dir / f"{name}.stderr.log"
        self.stdout_handle = open(self.stdout_path, "w", encoding="utf-8")
        self.stderr_handle = open(self.stderr_path, "w", encoding="utf-8")
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        self.proc = subprocess.Popen(
            [str(executable), *args],
            cwd=executable.parent,
            stdout=self.stdout_handle,
            stderr=self.stderr_handle,
            stdin=subprocess.DEVNULL,
            env=merged_env,
        )

    @property
    def pid(self) -> int:
        return self.proc.pid

    def stop(self) -> None:
        if self.proc.poll() is None:
            with suppress(Exception):
                if is_windows():
                    self.proc.kill()
                else:
                    self.proc.send_signal(signal.SIGTERM)
                    self.proc.wait(timeout=5)
            if self.proc.poll() is None:
                with suppress(Exception):
                    self.proc.kill()
        self.stdout_handle.close()
        self.stderr_handle.close()


def snapshot_processes(managed: list[ManagedProcess]) -> list[dict[str, Any]]:
    snapshots = []
    for item in managed:
        snap = process_snapshot(item.pid)
        snap["service_name"] = item.name
        snapshots.append(snap)
    return snapshots


def invoke_bench_case(pressure_exe: Path, gateway_port: int, case: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    args = [
        "--host", "127.0.0.1",
        "--port", str(gateway_port),
        "--scenario", case["scenario"],
        "--clients", str(case["clients"]),
        "--duration", str(case["duration_seconds"]),
    ]
    if case.get("messages", 0) > 0:
        args.extend(["--messages", str(case["messages"])])
    if case.get("interval_ms") is not None:
        args.extend(["--interval", str(case["interval_ms"])])
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
    args.extend(["--output", str(json_path)])

    log_step(f"Running bench case: {case_name}")
    proc = subprocess.Popen(
        [str(pressure_exe), *args],
        cwd=pressure_exe.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    timeout_seconds = int(case["duration_seconds"]) + 10
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
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
    if proc.returncode != 0 and not result.get("forced_timeout"):
        raise RuntimeError(f"Bench case failed: {case_name} (exit {proc.returncode})")
    if not result:
        raise RuntimeError(f"Bench case did not emit JSON result: {case_name}")
    return result


def estimate_battle_max_frames(cases: list[dict[str, Any]]) -> int:
    max_frames = 3
    for case in cases:
        if case.get("scenario") != "battle":
            continue
        duration_ms = int(case.get("duration_seconds", 0)) * 1000
        interval_ms = int(case.get("interval_ms") or 100)
        if duration_ms <= 0 or interval_ms <= 0:
            continue
        max_frames = max(max_frames, max(3, duration_ms // interval_ms))
    return max_frames


def fetch_json(url: str) -> Any:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def git_commit(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
    except Exception:
        return "unknown"


def aggregate_case_runs(case_name: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    def numeric_series(key: str) -> list[float]:
        return [float(run.get(key, 0.0)) for run in runs]

    throughput = numeric_series("throughput_msg_per_sec")
    p50 = numeric_series("latency_p50_ms")
    p90 = numeric_series("latency_p90_ms")
    p99 = numeric_series("latency_p99_ms")
    totals = [int(run.get("total_messages", 0)) for run in runs]
    connected = [int(run.get("connected_clients", 0)) for run in runs]
    rejected = [int(run.get("rejected_clients", 0)) for run in runs]
    failed = [int(run.get("failed_clients", 0)) for run in runs]
    forced_timeouts = [bool(run.get("forced_timeout") or run.get("collector_forced_timeout")) for run in runs]

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
        "connected_clients": {
            "min": min(connected),
            "median": int(statistics.median(connected)),
            "max": max(connected),
        },
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
    }


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
    idle: dict[str, Any] | None,
    loaded: dict[str, Any] | None,
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
        loaded_value = numeric_snapshot_value(loaded, field)
        idle_value = numeric_snapshot_value(idle, field)
        result[field] = loaded_value
        result[f"{field}_delta"] = (
            round(loaded_value - idle_value, 3)
            if loaded_value is not None and idle_value is not None
            else None
        )
    cpu_delta = result.get("cpu_seconds_delta")
    result["cpu_percent_from_cpu_seconds"] = (
        round((float(cpu_delta) / elapsed_seconds) * 100.0, 3)
        if cpu_delta is not None and elapsed_seconds > 0
        else None
    )
    return result


def analyze_resources(summary: dict[str, Any]) -> dict[str, Any]:
    snapshots = summary.get("process_snapshots", {})
    idle_map = snapshot_service_map(snapshots.get("idle", []))
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
        elapsed = float(run.get("elapsed_seconds", 0.0))
        loaded_map = snapshot_service_map(snapshots.get(snapshot_key, []))
        services: dict[str, Any] = {}
        for service_name, loaded in loaded_map.items():
            delta = service_resource_delta(idle_map.get(service_name), loaded, elapsed)
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


def minimum_battle_messages(case_name: str) -> int:
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
    if case_name.startswith("battle-100") or case_name.startswith("battle-500"):
        return 250.0
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


def evaluate_release_gates(aggregates: list[dict[str, Any]]) -> dict[str, Any]:
    gates: dict[str, Any] = {"overall_pass": True, "checks": [], "warnings": []}
    for aggregate in aggregates:
        case_name = aggregate["case_name"]
        p99 = aggregate["latency_p99_ms"]["median"]
        throughput = aggregate["throughput_msg_per_sec"]["median"]
        rejected = aggregate["rejected_clients"]["max"]
        failed = aggregate["failed_clients"]["max"]
        forced_timeout = bool(aggregate.get("forced_timeout"))
        total_messages = aggregate["total_messages"]["median"]

        if case_name.startswith("echo"):
            passed = rejected == 0 and failed == 0 and not forced_timeout and total_messages > 0 and p99 <= 50.0
            if p99 >= 45.0:
                gates["warnings"].append({
                    "case": case_name,
                    "warning": "echo p99 is within 10% of the 50ms gate",
                    "p99_ms": p99,
                })
            gates["checks"].append({
                "case": case_name,
                "passed": passed,
                "criteria": "echo: rejected=0, failed=0, p99<=50ms",
                "observed": {
                    "p99_ms": p99,
                    "throughput_msg_per_sec": throughput,
                    "rejected_clients": rejected,
                    "failed_clients": failed,
                    "forced_timeout": forced_timeout,
                    "total_messages": total_messages,
                },
            })
        elif case_name.startswith("battle"):
            min_messages = minimum_battle_messages(case_name)
            p99_limit = battle_p99_limit_ms(case_name)
            min_observed_messages = aggregate["total_messages"]["min"]
            passed = (
                rejected == 0 and failed == 0 and not forced_timeout and
                min_observed_messages >= min_messages and p99 <= p99_limit
            )
            if p99 >= p99_limit * 0.9:
                gates["warnings"].append({
                    "case": case_name,
                    "warning": f"battle p99 is within 10% of the {p99_limit:.0f}ms gate",
                    "p99_ms": p99,
                })
            gates["checks"].append({
                "case": case_name,
                "passed": passed,
                "criteria": f"battle: rejected=0, failed=0, forced_timeout=false, min_total_messages>={min_messages}, p99<={p99_limit:.0f}ms",
                "observed": {
                    "p99_ms": p99,
                    "p99_limit_ms": p99_limit,
                    "throughput_msg_per_sec": throughput,
                    "rejected_clients": rejected,
                    "failed_clients": failed,
                    "forced_timeout": forced_timeout,
                    "total_messages": total_messages,
                    "min_total_messages": min_observed_messages,
                    "required_min_total_messages": min_messages,
                },
            })

    gates["overall_pass"] = all(check["passed"] for check in gates["checks"])
    return gates


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect v2 performance baseline data.")
    parser.add_argument("--build-dir", default=str(Path("build/release").resolve()))
    parser.add_argument("--run-preset", choices=["smoke", "baseline", "capacity"], default="smoke")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--gateway-port", type=int, default=9201)
    parser.add_argument("--login-port", type=int, default=9202)
    parser.add_argument("--room-port", type=int, default=9302)
    parser.add_argument("--battle-port", type=int, default=9303)
    parser.add_argument("--http-port", type=int, default=9080)
    parser.add_argument("--io-cores", type=int, default=4)
    parser.add_argument(
        "--backend-pool-size",
        type=int,
        default=0,
        help="Override V2_BACKEND_CONNECTION_POOL_SIZE for explicit backend pool experiments.",
    )
    parser.add_argument("--output-root", default="")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    build_dir = Path(args.build_dir).resolve()
    output_root = Path(args.output_root).resolve() if args.output_root else root / "runtime" / "perf" / datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = output_root / "logs"
    result_dir = output_root / "results"
    log_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)

    executables = {
        "login": resolve_executable(build_dir, "v2_login_backend"),
        "room": resolve_executable(build_dir, "v2_room_backend"),
        "battle": resolve_executable(build_dir, "v2_battle_backend"),
        "gateway": resolve_executable(build_dir, "v2_gateway_demo"),
        "pressure": resolve_executable(build_dir, "v2_gateway_pressure"),
    }

    if args.run_preset == "capacity":
        run_cases = [
            {"name": "echo-1000-30s", "scenario": "echo", "clients": 1000, "duration_seconds": 30, "interval_ms": 50},
            {"name": "echo-5000-30s", "scenario": "echo", "clients": 5000, "duration_seconds": 30, "interval_ms": 50},
            {"name": "echo-10000-30s", "scenario": "echo", "clients": 10000, "duration_seconds": 30, "interval_ms": 50},
            {"name": "battle-100-30s", "scenario": "battle", "clients": 100, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_100", "room_group_size": 2},
            {"name": "battle-500-30s", "scenario": "battle", "clients": 500, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_500", "room_group_size": 2},
        ]
    elif args.run_preset == "baseline":
        run_cases = [
            {"name": "echo-100-30s", "scenario": "echo", "clients": 100, "duration_seconds": 30, "interval_ms": 50},
            {"name": "echo-1000-30s", "scenario": "echo", "clients": 1000, "duration_seconds": 30, "interval_ms": 50},
            {"name": "battle-20-30s", "scenario": "battle", "clients": 20, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_20", "room_group_size": 2},
            {"name": "battle-100-30s", "scenario": "battle", "clients": 100, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_100", "room_group_size": 2},
        ]
    else:
        run_cases = [
            {"name": "echo-20-10s", "scenario": "echo", "clients": 20, "duration_seconds": 10, "interval_ms": 50},
            {"name": "battle-2-10s", "scenario": "battle", "clients": 2, "duration_seconds": 10, "interval_ms": 100, "room": "perf_smoke_battle"},
        ]

    battle_max_frames = estimate_battle_max_frames(run_cases)
    managed: list[ManagedProcess] = []
    try:
        log_step("Starting v2 backend topology")
        managed.append(ManagedProcess("v2_login_backend", executables["login"], [str(args.login_port)], log_dir))
        wait_tcp_port("127.0.0.1", args.login_port)

        battle_env = {"V2_BATTLE_MAX_FRAMES": str(battle_max_frames)}

        managed.append(ManagedProcess("v2_room_backend", executables["room"], [str(args.room_port)], log_dir, battle_env))
        wait_tcp_port("127.0.0.1", args.room_port)

        managed.append(ManagedProcess("v2_battle_backend", executables["battle"], [str(args.battle_port)], log_dir))
        wait_tcp_port("127.0.0.1", args.battle_port)

        gateway_args = [
            "--io-cores", str(args.io_cores),
            "--http-port", str(args.http_port),
            "--login-port", str(args.login_port),
            "--room-port", str(args.room_port),
            "--battle-port", str(args.battle_port),
        ]
        gateway_env = {
            "V2_RATE_LIMIT_CONNECTION": "100000",
            "V2_RATE_LIMIT_MESSAGE_TYPE": "200000",
            "V2_RATE_LIMIT_IP": "200000",
            "V2_RATE_LIMIT_USER": "100000",
            "V2_RATE_LIMIT_LOGIN": "50000",
            "V2_BATTLE_MAX_FRAMES": str(battle_max_frames),
        }
        if args.backend_pool_size > 0:
            gateway_env["V2_BACKEND_CONNECTION_POOL_SIZE"] = str(args.backend_pool_size)
        managed.append(ManagedProcess("v2_gateway_demo", executables["gateway"], gateway_args, log_dir, gateway_env))
        wait_tcp_port("127.0.0.1", args.gateway_port)
        wait_tcp_port("127.0.0.1", args.http_port)

        time.sleep(2.0)

        summary: dict[str, Any] = {
            "collected_at": datetime.now().isoformat(timespec="seconds"),
            "host_platform": platform.platform(),
            "git_commit": git_commit(root),
            "preset": args.run_preset,
            "repetitions": args.repetitions,
            "build_dir": str(build_dir),
            "output_dir": str(output_root),
            "topology": {
                "gateway_port": args.gateway_port,
                "login_port": args.login_port,
                "room_port": args.room_port,
                "battle_port": args.battle_port,
                "http_port": args.http_port,
                "io_cores": args.io_cores,
                "battle_max_frames": battle_max_frames,
                "backend_connection_pool_size": args.backend_pool_size or int(os.environ.get("V2_BACKEND_CONNECTION_POOL_SIZE", "1")),
            },
            "cases": [],
            "case_aggregates": [],
            "release_gates": {},
            "process_snapshots": {},
        }
        summary["process_snapshots"]["idle"] = snapshot_processes(managed)

        for case in run_cases:
            case_runs: list[dict[str, Any]] = []
            for repetition in range(args.repetitions):
                run_result = invoke_bench_case(
                    executables["pressure"],
                    args.gateway_port,
                    {**case, "name": f"{case['name']}.run{repetition + 1}"},
                    result_dir,
                )
                run_result["case_name"] = f"{case['name']}.run{repetition + 1}"
                run_result["base_case_name"] = case["name"]
                case_runs.append(run_result)

                diagnostics = fetch_json(f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json")
                diagnostics_path = result_dir / f"{case['name']}.run{repetition + 1}.gateway.diagnostics.json"
                diagnostics_path.write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False), encoding="utf-8")
                summary["process_snapshots"][f"{case['name']}.run{repetition + 1}"] = snapshot_processes(managed)

            aggregate = aggregate_case_runs(case["name"], case_runs)
            summary["cases"].extend(case_runs)
            summary["case_aggregates"].append(aggregate)

        summary["release_gates"] = evaluate_release_gates(summary["case_aggregates"])
        summary["resource_analysis"] = analyze_resources(summary)

        summary_path = output_root / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        report_path = output_root / "report.md"
        report_path.write_text(render_markdown_report(summary), encoding="utf-8")
        log_step(f"Baseline collection completed: {output_root}")
        log_step(f"Markdown report written: {report_path}")
        if args.run_preset == "smoke" or summary["release_gates"].get("overall_pass"):
            return 0
        log_step("Release performance gates failed")
        return 2
    finally:
        for proc in reversed(managed):
            proc.stop()


if __name__ == "__main__":
    sys.exit(main())
