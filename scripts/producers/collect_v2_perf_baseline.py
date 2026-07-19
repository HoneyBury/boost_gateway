#!/usr/bin/env python3
"""Collect v2 multi-process performance baseline data across platforms."""

from __future__ import annotations

import argparse
import concurrent.futures
import http.server
import json
import math
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
from typing import Any
from urllib.request import urlopen


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

    snapshot = {
        "pid": pid,
        "process_name": info.get("Name", ""),
        "working_set_mb": parse_kb(info.get("VmRSS")),
        "private_memory_mb": parse_kb(info.get("RssAnon")),
        "virtual_memory_mb": parse_kb(info.get("VmSize")),
        "handles": count_open_files(pid),
        "threads": int(info.get("Threads", "0")),
        "cpu_seconds": process_cpu_seconds(pid),
    }
    if hasattr(os, "sched_getaffinity"):
        with suppress(OSError, ProcessLookupError, PermissionError):
            affinity = set(os.sched_getaffinity(pid))
            snapshot["cpu_affinity"] = format_cpu_set(affinity)
            snapshot["cpu_affinity_count"] = len(affinity)
    return snapshot


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

    def log_text(self) -> str:
        self.stdout_handle.flush()
        self.stderr_handle.flush()
        return "\n".join((
            self.stdout_path.read_text(encoding="utf-8", errors="replace"),
            self.stderr_path.read_text(encoding="utf-8", errors="replace"),
        ))

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


def wait_process_log(process: ManagedProcess, marker: str, timeout_seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if marker in process.log_text():
            return True
        if process.proc.poll() is not None:
            break
        time.sleep(0.1)
    return marker in process.log_text()


def redis_command(host: str, port: int, *parts: str, timeout_seconds: float = 3.0) -> str | int | None:
    """Execute the small RESP subset needed to prove benchmark persistence."""
    encoded_parts = [part.encode("utf-8") for part in parts]
    request = [f"*{len(encoded_parts)}\r\n".encode("ascii")]
    for part in encoded_parts:
        request.extend((f"${len(part)}\r\n".encode("ascii"), part, b"\r\n"))
    with socket.create_connection((host, port), timeout=timeout_seconds) as connection:
        connection.settimeout(timeout_seconds)
        connection.sendall(b"".join(request))
        prefix = recv_exact(connection, 1)
        line = bytearray()
        while not line.endswith(b"\r\n"):
            line.extend(recv_exact(connection, 1))
        value = bytes(line[:-2]).decode("utf-8", errors="strict")
        if prefix == b"+":
            return value
        if prefix == b":":
            return int(value)
        if prefix == b"$":
            length = int(value)
            if length < 0:
                return None
            return recv_exact(connection, length + 2)[:-2].decode("utf-8", errors="strict")
        if prefix == b"-":
            raise RuntimeError(f"Redis command failed: {value}")
        raise RuntimeError(f"unsupported Redis response prefix: {prefix!r}")


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
    for path in (stdout_path, stderr_path, json_path):
        with suppress(FileNotFoundError):
            path.unlink()
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


BUSINESS_OPERATION_SEQUENCES = {
    "matchmaking": (
        ("match_join", 6001, 6002),
        ("match_status", 6006, 6007),
        ("match_leave", 6004, 6005),
    ),
    "leaderboard": (
        ("leaderboard_submit", 7001, 7002),
        ("leaderboard_top", 7003, 7004),
        ("leaderboard_rank", 7005, 7006),
    ),
}


def encode_business_packet(
    message_id: int,
    request_id: int,
    body: str,
    *,
    version: int = 1,
    flags: int = 0,
) -> bytes:
    encoded_body = body.encode("utf-8")
    payload_length = 16 + len(encoded_body)
    return struct.pack("!IBHIIiB", payload_length, version, message_id, request_id, 0, 0, flags) + encoded_body


def recv_exact(sock: socket.socket, size: int, deadline: float | None = None) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while received < size:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("gateway response deadline exceeded")
            sock.settimeout(remaining)
        chunk = sock.recv(size - received)
        if not chunk:
            raise ConnectionError("gateway closed the connection")
        chunks.append(chunk)
        received += len(chunk)
    return b"".join(chunks)


def recv_business_packet(sock: socket.socket, deadline: float | None = None) -> dict[str, Any]:
    payload_length = struct.unpack("!I", recv_exact(sock, 4, deadline))[0]
    if payload_length < 16 or payload_length > 1024 * 1024:
        raise ValueError(f"invalid gateway frame length: {payload_length}")
    payload = recv_exact(sock, payload_length, deadline)
    version, message_id, request_id, sequence_number, error_code, flags = struct.unpack(
        "!BHIIiB", payload[:16]
    )
    return {
        "version": version,
        "message_id": message_id,
        "request_id": request_id,
        "sequence_number": sequence_number,
        "error_code": error_code,
        "flags": flags,
        "body_bytes": payload[16:],
        "body": payload[16:].decode("utf-8", errors="replace") if flags == 0 else "",
    }


class BusinessOperationClient:
    PUSH_MESSAGE_IDS = {1003, 1004, 3009, 4005, 4006, 6003}

    def __init__(self, host: str, port: int, timeout_seconds: float) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout_seconds)
        self.sock.settimeout(timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self.next_request_id = 1

    def close(self) -> None:
        with suppress(OSError):
            self.sock.close()

    def request(
        self,
        message_id: int,
        expected_message_id: int,
        body: str,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        request_timeout = timeout_seconds or self.timeout_seconds
        deadline = time.monotonic() + request_timeout
        self.sock.settimeout(request_timeout)
        request_id = self.next_request_id
        self.next_request_id += 1
        self.sock.sendall(encode_business_packet(message_id, request_id, body))
        while True:
            response = recv_business_packet(self.sock, deadline)
            if response["version"] != 1:
                raise ValueError(f"unsupported protocol version: {response['version']}")
            if response["flags"] & ~0x01:
                raise ValueError(
                    f"unsupported response flags: 0x{response['flags']:02x}"
                )
            if response["flags"] & 0x01:
                compressed = response["body_bytes"]
                if len(compressed) < 4:
                    raise ValueError("invalid compressed response: missing original length")
                expected_length = int.from_bytes(compressed[:4], "little")
                try:
                    decoded = zlib.decompress(compressed[4:])
                except zlib.error as exc:
                    raise ValueError(f"invalid compressed response: {exc}") from exc
                if len(decoded) != expected_length:
                    raise ValueError(
                        f"invalid compressed response length: expected {expected_length}, got {len(decoded)}"
                    )
                response["body"] = decoded.decode("utf-8", errors="strict")
            if response["message_id"] in self.PUSH_MESSAGE_IDS:
                continue
            if response["request_id"] != request_id:
                raise ValueError(
                    f"unexpected request id {response['request_id']}, expected {request_id}"
                )
            response["ok"] = (
                response["message_id"] == expected_message_id
                and response["error_code"] == 0
            )
            return response


def business_operation_body(
    scenario: str,
    operation: str,
    user_id: str,
    client_index: int,
    iteration: int,
) -> str:
    if scenario == "matchmaking":
        mmr = 1000 + (client_index % 20)
        return f"{user_id}|{mmr if operation == 'match_join' else 0}|1v1"
    if operation == "leaderboard_submit":
        score = 1_000_000_000 + client_index * 100_000 + iteration
        return f"{user_id}|perf-{client_index}|{score}"
    if operation == "leaderboard_top":
        return "20"
    return user_id


def run_business_operation_worker(
    host: str,
    port: int,
    scenario: str,
    client_index: int,
    iterations: int,
    timeout_seconds: float,
    run_id: str,
) -> dict[str, Any]:
    if scenario != "leaderboard":
        raise ValueError("generic business operation worker only supports leaderboard")
    user_id = f"perf_{scenario}_{run_id}_{client_index}"
    records: list[dict[str, Any]] = []
    client: BusinessOperationClient | None = None
    try:
        client = BusinessOperationClient(host, port, timeout_seconds)
        login = client.request(2001, 2002, f"{user_id}|token:{user_id}|{user_id}")
        if not login["ok"]:
            return {"client_index": client_index, "setup_error": f"login failed: {login['body'][:200]}", "records": []}

        for iteration in range(iterations):
            for operation, request_id, response_id in BUSINESS_OPERATION_SEQUENCES[scenario]:
                body = business_operation_body(scenario, operation, user_id, client_index, iteration)
                started = time.perf_counter()
                try:
                    response = client.request(request_id, response_id, body)
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    records.append({
                        "operation": operation,
                        "ok": response["ok"],
                        "latency_ms": latency_ms,
                        "error": "" if response["ok"] else f"error={response['error_code']} body={response['body'][:200]}",
                    })
                except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
                    records.append({
                        "operation": operation,
                        "ok": False,
                        "latency_ms": (time.perf_counter() - started) * 1000.0,
                        "error": str(exc)[:200],
                    })
        return {"client_index": client_index, "setup_error": "", "records": records}
    except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
        return {"client_index": client_index, "setup_error": str(exc)[:200], "records": records}
    finally:
        if client is not None:
            client.close()


def setup_matchmaking_client(
    host: str,
    port: int,
    client_index: int,
    timeout_seconds: float,
    run_id: str,
    iteration: int,
) -> dict[str, Any]:
    user_id = f"perf_matchmaking_{run_id}_{iteration}_{client_index}"
    client: BusinessOperationClient | None = None
    try:
        client = BusinessOperationClient(host, port, timeout_seconds)
        login = client.request(2001, 2002, f"{user_id}|token:{user_id}|{user_id}")
        if not login["ok"]:
            raise ValueError(f"login failed: {login['body'][:200]}")
        return {"client_index": client_index, "user_id": user_id, "client": client, "error": ""}
    except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
        if client is not None:
            client.close()
        return {"client_index": client_index, "user_id": user_id, "client": None, "error": str(exc)[:200]}


def execute_match_request(
    entry: dict[str, Any],
    operation: str,
    request_id: int,
    response_id: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        body = business_operation_body("matchmaking", operation, entry["user_id"], entry["client_index"], 0)
        response = entry["client"].request(request_id, response_id, body, timeout_seconds)
        return {
            "operation": operation,
            "ok": response["ok"],
            "latency_ms": (time.perf_counter() - started) * 1000.0,
            "error": "" if response["ok"] else f"error={response['error_code']} body={response['body'][:200]}",
        }
    except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
        return {
            "operation": operation,
            "ok": False,
            "latency_ms": (time.perf_counter() - started) * 1000.0,
            "error": str(exc)[:200],
        }


def poll_until_matched(
    entry: dict[str, Any],
    match_started: float,
    match_deadline: float,
) -> dict[str, Any]:
    polls = 0
    while time.monotonic() < match_deadline:
        remaining = match_deadline - time.monotonic()
        try:
            body = business_operation_body("matchmaking", "match_status", entry["user_id"], entry["client_index"], 0)
            response = entry["client"].request(6006, 6007, body, remaining)
            polls += 1
            if not response["ok"]:
                raise ValueError(f"error={response['error_code']} body={response['body'][:200]}")
            status = json.loads(response["body"])
            if not isinstance(status, dict):
                raise ValueError("match status response is not a JSON object")
            if status.get("matched") is True:
                return {
                    "operation": "match_status",
                    "ok": True,
                    "latency_ms": (time.perf_counter() - match_started) * 1000.0,
                    "poll_attempts": polls,
                    "error": "",
                }
        except (ConnectionError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return {
                "operation": "match_status",
                "ok": False,
                "latency_ms": (time.perf_counter() - match_started) * 1000.0,
                "poll_attempts": polls,
                "error": str(exc)[:200],
            }
        time.sleep(min(0.05, max(0.0, match_deadline - time.monotonic())))
    return {
        "operation": "match_status",
        "ok": False,
        "latency_ms": (time.perf_counter() - match_started) * 1000.0,
        "poll_attempts": polls,
        "error": "matchmaking deadline exceeded before matched=true",
    }


def run_matchmaking_scenario(
    host: str,
    port: int,
    clients: int,
    iterations: int,
    timeout_seconds: float,
    run_id: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    records: list[dict[str, Any]] = []
    setup_failures: list[dict[str, Any]] = []
    for iteration in range(iterations):
        with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as executor:
            entries = list(executor.map(
                lambda index: setup_matchmaking_client(host, port, index, timeout_seconds, run_id, iteration),
                range(clients),
            ))
        setup_failures.extend(
            {"iteration": iteration, "client_index": entry["client_index"], "error": entry["error"]}
            for entry in entries
            if entry["error"]
        )
        active = [entry for entry in entries if entry["client"] is not None]
        try:
            match_started = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(active))) as executor:
                join_records = list(executor.map(
                    lambda entry: execute_match_request(entry, "match_join", 6001, 6002, timeout_seconds),
                    active,
                ))
            records.extend(join_records)

            match_deadline = time.monotonic() + timeout_seconds
            joined = [entry for entry, record in zip(active, join_records, strict=True) if record["ok"]]
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(joined))) as executor:
                status_records = list(executor.map(
                    lambda entry: poll_until_matched(entry, match_started, match_deadline),
                    joined,
                ))
            records.extend(status_records)

            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(active))) as executor:
                records.extend(executor.map(
                    lambda entry: execute_match_request(entry, "match_leave", 6004, 6005, timeout_seconds),
                    active,
                ))
        finally:
            for entry in active:
                entry["client"].close()

    duration_seconds = round(time.perf_counter() - started, 6)
    operations = summarize_business_operations(
        ["match_join", "match_status", "match_leave"], records, duration_seconds
    )
    expected_per_operation = clients * iterations
    matched_latencies = [
        float(record["latency_ms"])
        for record in records
        if record["operation"] == "match_status" and record["ok"]
    ]
    passed = not setup_failures and all(
        operation["attempted"] == expected_per_operation and operation["failed"] == 0
        for operation in operations
    )
    return {
        "scenario": "matchmaking",
        "passed": passed,
        "clients": clients,
        "iterations_per_client": iterations,
        "duration_seconds": duration_seconds,
        "expected_per_operation": expected_per_operation,
        "setup_failures": setup_failures,
        "status_poll_attempts": sum(int(record.get("poll_attempts", 0)) for record in records),
        "time_to_match_samples_ms": matched_latencies,
        "time_to_match_p50_ms": latency_percentile(matched_latencies, 0.50),
        "time_to_match_p99_ms": latency_percentile(matched_latencies, 0.99),
        "operations": operations,
    }


def latency_percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def summarize_business_operations(
    operation_names: list[str],
    records: list[dict[str, Any]],
    duration_seconds: float,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for operation in operation_names:
        operation_records = [record for record in records if record["operation"] == operation]
        latencies = [float(record["latency_ms"]) for record in operation_records]
        succeeded = sum(1 for record in operation_records if record["ok"])
        errors: dict[str, int] = {}
        for record in operation_records:
            if not record["ok"]:
                error = str(record.get("error") or "unknown")
                errors[error] = errors.get(error, 0) + 1
        summaries.append({
            "operation": operation,
            "attempted": len(operation_records),
            "succeeded": succeeded,
            "failed": len(operation_records) - succeeded,
            "throughput_ops_per_sec": round(len(operation_records) / max(duration_seconds, 0.000001), 3),
            "latency_p50_ms": latency_percentile(latencies, 0.50),
            "latency_p99_ms": latency_percentile(latencies, 0.99),
            "errors": errors,
        })
    return summaries


def run_leaderboard_scenario(
    host: str,
    port: int,
    clients: int,
    iterations: int,
    timeout_seconds: float,
    run_id: str,
    persistence_mode: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as executor:
        futures = [
            executor.submit(
                run_business_operation_worker,
                host,
                port,
                "leaderboard",
                client_index,
                iterations,
                timeout_seconds,
                run_id,
            )
            for client_index in range(clients)
        ]
        workers = [future.result() for future in futures]
    duration_seconds = round(time.perf_counter() - started, 6)
    records = [record for worker in workers for record in worker["records"]]
    operation_names = [item[0] for item in BUSINESS_OPERATION_SEQUENCES["leaderboard"]]
    operations = summarize_business_operations(operation_names, records, duration_seconds)
    setup_failures = [
        {"client_index": worker["client_index"], "error": worker["setup_error"]}
        for worker in workers
        if worker["setup_error"]
    ]
    expected_per_operation = clients * iterations
    passed = not setup_failures and all(
        operation["attempted"] == expected_per_operation and operation["failed"] == 0
        for operation in operations
    )
    return {
        "scenario": "leaderboard",
        "passed": passed,
        "clients": clients,
        "iterations_per_client": iterations,
        "duration_seconds": duration_seconds,
        "expected_per_operation": expected_per_operation,
        "setup_failures": setup_failures,
        "persistence_mode": persistence_mode,
        "redis_comparison": False,
        "operations": operations,
    }


def metric_distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    return {
        "min": round(min(values), 3),
        "median": round(statistics.median(values), 3),
        "max": round(max(values), 3),
    }


def aggregate_business_operation_runs(
    scenarios: list[str],
    runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    aggregates: list[dict[str, Any]] = []
    for scenario_name in scenarios:
        scenario_runs = [
            scenario
            for run in runs
            for scenario in run["scenarios"]
            if scenario["scenario"] == scenario_name
        ]
        operation_names = [item[0] for item in BUSINESS_OPERATION_SEQUENCES[scenario_name]]
        operation_aggregates: list[dict[str, Any]] = []
        for operation_name in operation_names:
            operation_runs = [
                operation
                for scenario in scenario_runs
                for operation in scenario["operations"]
                if operation["operation"] == operation_name
            ]
            operation_aggregates.append({
                "operation": operation_name,
                "attempted": sum(int(operation["attempted"]) for operation in operation_runs),
                "succeeded": sum(int(operation["succeeded"]) for operation in operation_runs),
                "failed": sum(int(operation["failed"]) for operation in operation_runs),
                "throughput_ops_per_sec": metric_distribution([
                    float(operation["throughput_ops_per_sec"]) for operation in operation_runs
                ]),
                "latency_p50_ms": metric_distribution([
                    float(operation["latency_p50_ms"])
                    for operation in operation_runs
                    if operation["latency_p50_ms"] is not None
                ]),
                "latency_p99_ms": metric_distribution([
                    float(operation["latency_p99_ms"])
                    for operation in operation_runs
                    if operation["latency_p99_ms"] is not None
                ]),
            })
        aggregate: dict[str, Any] = {
            "scenario": scenario_name,
            "runs": len(scenario_runs),
            "passed_runs": sum(1 for scenario in scenario_runs if scenario["passed"]),
            "passed": len(scenario_runs) == len(runs) and all(scenario["passed"] for scenario in scenario_runs),
            "operations": operation_aggregates,
        }
        if scenario_name == "matchmaking":
            time_to_match_samples = [
                float(value)
                for scenario in scenario_runs
                for value in scenario.get("time_to_match_samples_ms", [])
            ]
            aggregate.update({
                "time_to_match_samples": len(time_to_match_samples),
                "time_to_match_p50_ms": latency_percentile(time_to_match_samples, 0.50),
                "time_to_match_p99_ms": latency_percentile(time_to_match_samples, 0.99),
            })
        if scenario_name == "leaderboard":
            persistence_modes = sorted({str(scenario["persistence_mode"]) for scenario in scenario_runs})
            aggregate.update({
                "persistence_mode": persistence_modes[0] if len(persistence_modes) == 1 else "mixed",
                "redis_comparison": False,
            })
        aggregates.append(aggregate)
    return aggregates


def run_business_operation_perf(
    host: str,
    port: int,
    scenarios: list[str],
    clients: int,
    iterations: int,
    timeout_seconds: float,
    repetitions: int = 1,
    leaderboard_persistence_mode: str = "in_memory_only",
) -> dict[str, Any]:
    selected_scenarios = list(dict.fromkeys(scenarios))
    if clients <= 0 or iterations <= 0 or repetitions <= 0 or timeout_seconds <= 0:
        raise ValueError("business operation clients, iterations, repetitions, and timeout must be positive")
    if "matchmaking" in selected_scenarios and clients % 2 != 0:
        raise ValueError("1v1 matchmaking requires an even client count")
    runs: list[dict[str, Any]] = []
    for repetition in range(repetitions):
        scenario_summaries: list[dict[str, Any]] = []
        for scenario in selected_scenarios:
            run_id = f"{time.monotonic_ns()}_{repetition + 1}"
            if scenario == "matchmaking":
                scenario_summaries.append(run_matchmaking_scenario(
                    host, port, clients, iterations, timeout_seconds, run_id
                ))
            else:
                scenario_summaries.append(run_leaderboard_scenario(
                    host,
                    port,
                    clients,
                    iterations,
                    timeout_seconds,
                    run_id,
                    leaderboard_persistence_mode,
                ))
        runs.append({
            "run": repetition + 1,
            "passed": all(scenario["passed"] for scenario in scenario_summaries),
            "scenarios": scenario_summaries,
        })
    scenario_aggregates = aggregate_business_operation_runs(selected_scenarios, runs)
    overall_pass = len(runs) == repetitions and all(run["passed"] for run in runs)
    return {
        "summary_version": 2,
        "overall_pass": overall_pass,
        "passed": overall_pass,
        "gateway_host": host,
        "gateway_port": port,
        "clients": clients,
        "iterations_per_client": iterations,
        "requested_runs": repetitions,
        "completed_runs": len(runs),
        "runs": runs,
        "scenario_aggregates": scenario_aggregates,
        "leaderboard_persistence": {
            "mode": leaderboard_persistence_mode,
            "source": "explicit collector backend configuration",
            "redis_comparison": False,
        } if "leaderboard" in selected_scenarios else None,
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
        duration_ms = int(case.get("duration_seconds", 0)) * 1000
        interval_ms = int(case.get("interval_ms") or 100)
        if duration_ms <= 0 or interval_ms <= 0:
            continue
        max_frames = max(max_frames, max(3, duration_ms // interval_ms))
    return max_frames


def fetch_json(url: str) -> Any:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_process_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


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


def distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    return {
        "min": round(min(values), 3),
        "median": round(statistics.median(values), 3),
        "max": round(max(values), 3),
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


def build_run_cases(run_preset: str) -> list[dict[str, Any]]:
    if run_preset == "capacity":
        return [
            {"name": "echo-1000-30s", "scenario": "echo", "clients": 1000, "duration_seconds": 30, "interval_ms": 50},
            {"name": "echo-5000-30s", "scenario": "echo", "clients": 5000, "duration_seconds": 30, "interval_ms": 50},
            {"name": "echo-10000-30s", "scenario": "echo", "clients": 10000, "duration_seconds": 30, "interval_ms": 50},
            {"name": "battle-100-30s", "scenario": "battle", "clients": 100, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_100", "room_group_size": 2},
            {"name": "battle-500-30s", "scenario": "battle", "clients": 500, "duration_seconds": 30, "interval_ms": 200, "room": "perf_battle_500", "room_group_size": 2},
        ]
    if run_preset == "business-capacity":
        return [
            {"name": "echo-1000-30s", "scenario": "echo", "clients": 1000, "duration_seconds": 30, "interval_ms": 50},
            {"name": "battle-100-30s", "scenario": "battle", "clients": 100, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_100", "room_group_size": 2},
            {"name": "battle-500-30s", "scenario": "battle", "clients": 500, "duration_seconds": 30, "interval_ms": 200, "room": "perf_battle_500", "room_group_size": 2},
        ]
    if run_preset == "baseline":
        return [
            {"name": "echo-100-30s", "scenario": "echo", "clients": 100, "duration_seconds": 30, "interval_ms": 50},
            {"name": "echo-1000-30s", "scenario": "echo", "clients": 1000, "duration_seconds": 30, "interval_ms": 50},
            {"name": "battle-20-30s", "scenario": "battle", "clients": 20, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_20", "room_group_size": 2},
            {"name": "battle-100-30s", "scenario": "battle", "clients": 100, "duration_seconds": 30, "interval_ms": 100, "room": "perf_battle_100", "room_group_size": 2},
        ]
    return [
        {"name": "echo-20-10s", "scenario": "echo", "clients": 20, "duration_seconds": 10, "interval_ms": 50},
        {"name": "battle-2-10s", "scenario": "battle", "clients": 2, "duration_seconds": 10, "interval_ms": 100, "room": "perf_smoke_battle"},
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect v2 performance baseline data.")
    parser.add_argument("--build-dir", default=str(Path("build/release").resolve()))
    parser.add_argument("--run-preset", choices=["smoke", "baseline", "capacity", "business-capacity"], default="smoke")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--gateway-port", type=int, default=9201)
    parser.add_argument("--login-port", type=int, default=9202)
    parser.add_argument("--room-port", type=int, default=9302)
    parser.add_argument("--battle-port", type=int, default=9303)
    parser.add_argument("--matchmaking-port", type=int, default=9304)
    parser.add_argument("--leaderboard-port", type=int, default=9305)
    parser.add_argument("--http-port", type=int, default=9080)
    parser.add_argument("--io-cores", type=int, default=4)
    parser.add_argument(
        "--cpu-set",
        default="",
        help="Linux CPU affinity list (for example 0 or 0-1,4); inherited by all benchmark child processes.",
    )
    parser.add_argument(
        "--include-business-flow",
        action="store_true",
        help="Run SDK full-flow business coverage after pressure cases and include it in the report.",
    )
    parser.add_argument(
        "--business-flow-clients",
        type=int,
        default=1,
        help="Number of sequential SDK full-flow business passes to include as an additional N1 business-flow evidence sample.",
    )
    parser.add_argument(
        "--backend-pool-size",
        type=int,
        default=0,
        help="Override V2_BACKEND_CONNECTION_POOL_SIZE for explicit backend pool experiments.",
    )
    parser.add_argument(
        "--battle-frame-push-every",
        type=int,
        default=0,
        help="Override V2_BATTLE_FRAME_PUSH_EVERY for battle state push downsampling experiments.",
    )
    parser.add_argument(
        "--battle-route-workers",
        type=int,
        default=0,
        help="Override V2_BATTLE_ROUTE_WORKERS for battle input backend route offload experiments.",
    )
    parser.add_argument(
        "--business-operation-scenario",
        action="append",
        choices=sorted(BUSINESS_OPERATION_SEQUENCES),
        default=[],
        help="Run an opt-in concurrent business operation scenario; repeat for matchmaking and leaderboard.",
    )
    parser.add_argument("--business-operation-clients", type=int, default=16)
    parser.add_argument("--business-operation-iterations", type=int, default=10)
    parser.add_argument("--business-operation-timeout-seconds", type=float, default=5.0)
    parser.add_argument(
        "--leaderboard-redis-comparison",
        action="store_true",
        help="Run three-or-more leaderboard repetitions in explicit in-memory and Redis-backed modes.",
    )
    parser.add_argument("--leaderboard-redis-host", default="127.0.0.1")
    parser.add_argument("--leaderboard-redis-port", type=int, default=6379)
    parser.add_argument("--leaderboard-redis-key", default="")
    parser.add_argument(
        "--otel-comparison",
        action="store_true",
        help="Run fresh-Gateway OTel off/on battle-100 comparisons with a loopback collector.",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="Run only this named preset case; repeat to select multiple cases.",
    )
    parser.add_argument("--output-root", default="")
    args = parser.parse_args()

    try:
        resource_constraint = apply_cpu_affinity(args.cpu_set)
    except (RuntimeError, ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.business_operation_scenario and (
        args.business_operation_clients <= 0
        or args.business_operation_iterations <= 0
        or args.business_operation_timeout_seconds <= 0
        or args.repetitions <= 0
    ):
        parser.error("business operation clients, iterations, and timeout must be positive")
    if "matchmaking" in args.business_operation_scenario and args.business_operation_clients % 2 != 0:
        parser.error("--business-operation-clients must be even for the 1v1 matchmaking profile")
    if args.leaderboard_redis_comparison:
        if "leaderboard" not in args.business_operation_scenario:
            parser.error("--leaderboard-redis-comparison requires --business-operation-scenario leaderboard")
        if args.repetitions < 3:
            parser.error("--leaderboard-redis-comparison requires --repetitions >= 3")
        if not 1 <= args.leaderboard_redis_port <= 65535:
            parser.error("--leaderboard-redis-port must be between 1 and 65535")
    if args.otel_comparison:
        if args.repetitions < 3:
            parser.error("--otel-comparison requires --repetitions >= 3")
        if not any(case["name"] == "battle-100-30s" for case in build_run_cases(args.run_preset)):
            parser.error("--otel-comparison requires a preset containing battle-100-30s")

    root = Path(__file__).resolve().parents[2]
    build_dir = Path(args.build_dir).resolve()
    output_root = Path(args.output_root).resolve() if args.output_root else root / "runtime" / "perf" / datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.output_root:
        for child in ("logs", "results"):
            shutil.rmtree(output_root / child, ignore_errors=True)
        for child in ("summary.json", "report.md"):
            with suppress(FileNotFoundError):
                (output_root / child).unlink()
    log_dir = output_root / "logs"
    result_dir = output_root / "results"
    log_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)

    executables = {
        "login": resolve_executable(build_dir, "v2_login_backend"),
        "room": resolve_executable(build_dir, "v2_room_backend"),
        "battle": resolve_executable(build_dir, "v2_battle_backend"),
        "matchmaking": resolve_executable(build_dir, "v2_match_backend"),
        "leaderboard": resolve_executable(build_dir, "v2_leaderboard_backend"),
        "gateway": resolve_executable(build_dir, "v2_gateway_demo"),
        "pressure": resolve_executable(build_dir, "v2_gateway_pressure"),
    }

    run_cases = build_run_cases(args.run_preset)
    if args.case:
        selected_cases = set(args.case)
        run_cases = [case for case in run_cases if case["name"] in selected_cases]
        if not run_cases:
            parser.error("--case did not match any case in the selected --run-preset")

    battle_max_frames = estimate_battle_max_frames(run_cases)
    managed: list[ManagedProcess] = []
    try:
        log_step("Starting v2 backend topology")
        managed.append(ManagedProcess("v2_login_backend", executables["login"], [str(args.login_port)], log_dir))
        wait_tcp_port("127.0.0.1", args.login_port)

        battle_env = {"V2_BATTLE_MAX_FRAMES": str(battle_max_frames)}

        managed.append(ManagedProcess("v2_room_backend", executables["room"], [str(args.room_port)], log_dir, battle_env))
        wait_tcp_port("127.0.0.1", args.room_port)

        battle_process = ManagedProcess(
            "v2_battle_backend", executables["battle"], [str(args.battle_port)], log_dir
        )
        managed.append(battle_process)
        wait_tcp_port("127.0.0.1", args.battle_port)

        managed.append(ManagedProcess(
            "v2_match_backend",
            executables["matchmaking"],
            [str(args.matchmaking_port)],
            log_dir,
            {"SERVICE_PORT": str(args.matchmaking_port), "MATCH_PORT": str(args.matchmaking_port)},
        ))
        wait_tcp_port("127.0.0.1", args.matchmaking_port)

        leaderboard_process = ManagedProcess(
            "v2_leaderboard_backend",
            executables["leaderboard"],
            [str(args.leaderboard_port)],
            log_dir,
            {
                "SERVICE_PORT": str(args.leaderboard_port),
                "LEADERBOARD_PORT": str(args.leaderboard_port),
                "LEADERBOARD_CONFIG_PATH": str(root / "config/environments/local/leaderboard.json"),
                "REDIS_HOST": "",
                "BOOST_DISABLE_REDIS_AUTO_CONNECT": "1",
                "BOOST_LOG_LEVEL": "info",
            },
        )
        managed.append(leaderboard_process)
        wait_tcp_port("127.0.0.1", args.leaderboard_port)
        in_memory_log_verified = wait_process_log(
            leaderboard_process,
            "Redis auto-connect disabled",
        )
        if not in_memory_log_verified:
            raise RuntimeError("in-memory leaderboard startup did not prove Redis auto-connect was disabled")

        gateway_args = [
            "--port", str(args.gateway_port),
            "--io-cores", str(args.io_cores),
            "--http-port", str(args.http_port),
            "--login-port", str(args.login_port),
            "--room-port", str(args.room_port),
            "--battle-port", str(args.battle_port),
            "--matchmaking-port", str(args.matchmaking_port),
            "--leaderboard-port", str(args.leaderboard_port),
        ]
        gateway_env = {
            "V2_RATE_LIMIT_CONNECTION": "100000",
            "V2_RATE_LIMIT_MESSAGE_TYPE": "200000",
            "V2_RATE_LIMIT_IP": "200000",
            "V2_RATE_LIMIT_USER": "100000",
            "V2_RATE_LIMIT_LOGIN": "50000",
            "V2_BATTLE_MAX_FRAMES": str(battle_max_frames),
            "OTEL_EXPORT_ENDPOINT": "",
        }
        if args.backend_pool_size > 0:
            gateway_env["V2_BACKEND_CONNECTION_POOL_SIZE"] = str(args.backend_pool_size)
        if args.battle_frame_push_every > 0:
            gateway_env["V2_BATTLE_FRAME_PUSH_EVERY"] = str(args.battle_frame_push_every)
        if args.battle_route_workers > 0:
            gateway_env["V2_BATTLE_ROUTE_WORKERS"] = str(args.battle_route_workers)
        gateway_process = ManagedProcess("v2_gateway_demo", executables["gateway"], gateway_args, log_dir, gateway_env)
        managed.append(gateway_process)
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
            "summary_version": 2,
            "resource_constraint": resource_constraint,
            "topology": {
                "gateway_port": args.gateway_port,
                "login_port": args.login_port,
                "room_port": args.room_port,
                "battle_port": args.battle_port,
                "matchmaking_port": args.matchmaking_port,
                "leaderboard_port": args.leaderboard_port,
                "http_port": args.http_port,
                "io_cores": args.io_cores,
                "battle_max_frames": battle_max_frames,
                "backend_connection_pool_size": args.backend_pool_size or int(os.environ.get("V2_BACKEND_CONNECTION_POOL_SIZE", "8")),
                "battle_frame_push_every": args.battle_frame_push_every or int(os.environ.get("V2_BATTLE_FRAME_PUSH_EVERY", "1")),
                "battle_route_workers": args.battle_route_workers or int(os.environ.get("V2_BATTLE_ROUTE_WORKERS", "0")),
            },
            "cases": [],
            "case_aggregates": [],
            "release_gates": {},
            "process_snapshots": {},
            "business_flow": None,
            "business_operation_perf": None,
            "leaderboard_persistence_comparison": None,
            "otel_comparison": None,
            "final_backend_metrics": {},
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
        if args.otel_comparison:
            log_step("Running fresh-Gateway OTel off/on performance comparison")
            comparison_case = next(case for case in run_cases if case["name"] == "battle-100-30s")
            gateway_process.stop()
            managed.remove(gateway_process)
            otel_collector = LoopbackOtelCollector()
            otel_collector.start()

            def run_otel_mode(mode: str, endpoint: str) -> tuple[dict[str, Any], bool, dict[str, int], dict[str, Any]]:
                nonlocal battle_process
                battle_process.stop()
                managed.remove(battle_process)
                battle_process = ManagedProcess(
                    f"v2_battle_backend.otel-{mode}",
                    executables["battle"],
                    [str(args.battle_port)],
                    log_dir,
                )
                managed.append(battle_process)
                wait_tcp_port("127.0.0.1", args.battle_port)
                mode_env = {**gateway_env, "OTEL_EXPORT_ENDPOINT": endpoint}
                process = ManagedProcess(
                    f"v2_gateway_demo.otel-{mode}",
                    executables["gateway"],
                    gateway_args,
                    log_dir,
                    mode_env,
                )
                managed.append(process)
                try:
                    wait_tcp_port("127.0.0.1", args.gateway_port)
                    wait_tcp_port("127.0.0.1", args.http_port)
                    time.sleep(2.0)
                    mode_initial_diagnostics = fetch_json(
                        f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
                    )
                    collector_before_mode = otel_collector.snapshot()
                    mode_runs: list[dict[str, Any]] = []
                    for repetition in range(args.repetitions):
                        diagnostics_before = fetch_json(
                            f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
                        )
                        process_before = process_snapshot(process.pid)
                        collector_before = otel_collector.snapshot()
                        run = invoke_bench_case(
                            executables["pressure"],
                            args.gateway_port,
                            {
                                **comparison_case,
                                "name": f"otel-{mode}.battle-100-30s.run{repetition + 1}",
                            },
                            result_dir,
                        )
                        diagnostics_after = fetch_json(
                            f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json"
                        )
                        process_after = process_snapshot(process.pid)
                        collector_after = otel_collector.snapshot()
                        cpu_before = process_before.get("cpu_seconds")
                        cpu_after = process_after.get("cpu_seconds")
                        run.update({
                            "case_name": f"otel-{mode}.battle-100-30s.run{repetition + 1}",
                            "base_case_name": "battle-100-30s",
                            "otel_mode": mode,
                            "gateway_resources": {
                                "cpu_seconds_before": cpu_before,
                                "cpu_seconds_after": cpu_after,
                                "cpu_seconds_delta": round(float(cpu_after) - float(cpu_before), 3)
                                if cpu_before is not None and cpu_after is not None else None,
                                "rss_mb_after": process_after.get("working_set_mb", 0.0),
                                "cpu_affinity": process_after.get("cpu_affinity", ""),
                                "pid": process.pid,
                            },
                            "backend_routed_requests": (
                                total_backend_requests(diagnostics_after)
                                - total_backend_requests(diagnostics_before)
                            ),
                            "collector_delta": counter_delta(collector_after, collector_before),
                            "exporter_metrics_after": otel_exporter_metrics(diagnostics_after),
                        })
                        mode_runs.append(run)
                    final_diagnostics = wait_for_otel_mode_quiescence(
                        f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json",
                        mode=mode,
                        initial_backend_requests=total_backend_requests(mode_initial_diagnostics),
                    )
                    log_text = process.log_text()
                    marker_present = "OTLP export enabled" in log_text
                    log_verified = marker_present if mode == "on" else not marker_present
                    collector_delta_mode = counter_delta(
                        otel_collector.snapshot(), collector_before_mode
                    )
                    mode_backend_routed_requests = (
                        total_backend_requests(final_diagnostics)
                        - total_backend_requests(mode_initial_diagnostics)
                    )
                    return (
                        aggregate_otel_mode(
                            mode,
                            mode_runs,
                            mode_backend_routed_requests,
                            battle_process.pid,
                        ),
                        log_verified,
                        collector_delta_mode,
                        otel_exporter_metrics(final_diagnostics),
                    )
                finally:
                    process.stop()
                    managed.remove(process)

            try:
                off_mode, off_log, off_collector, off_exporter = run_otel_mode("off", "")
                on_mode, on_log, on_collector, on_exporter = run_otel_mode(
                    "on", otel_collector.endpoint
                )
                summary["otel_comparison"] = build_otel_comparison(
                    off_mode,
                    on_mode,
                    repetitions=args.repetitions,
                    off_log_verified=off_log,
                    on_log_verified=on_log,
                    collector_off=off_collector,
                    collector_on=on_collector,
                    off_exporter=off_exporter,
                    on_exporter=on_exporter,
                )
            finally:
                otel_collector.stop()

            summary["release_gates"].setdefault("checks", []).append({
                "case": "otel-off-on-comparison",
                "passed": summary["otel_comparison"]["verified"] is True,
                "criteria": (
                    "fresh Gateway and Battle Backend per OTel mode, battle-100 at least three runs per process; absolute gate, "
                    "runtime exporter counters, backend route and loopback collector proof agree"
                ),
                "observed": {
                    "repetitions_per_mode": args.repetitions,
                    "performance_regression_policy": "observed_not_thresholded",
                    "proof": summary["otel_comparison"]["proof"],
                },
            })
            if summary["otel_comparison"]["verified"] is not True:
                summary["release_gates"]["overall_pass"] = False

            gateway_process = ManagedProcess(
                "v2_gateway_demo.post-otel",
                executables["gateway"],
                gateway_args,
                log_dir,
                gateway_env,
            )
            managed.append(gateway_process)
            wait_tcp_port("127.0.0.1", args.gateway_port)
            wait_tcp_port("127.0.0.1", args.http_port)
            time.sleep(2.0)
        if args.business_operation_scenario:
            selected_scenarios = list(dict.fromkeys(args.business_operation_scenario))
            log_step(f"Running concurrent business operation performance: {', '.join(selected_scenarios)}")
            summary["business_operation_perf"] = run_business_operation_perf(
                "127.0.0.1",
                args.gateway_port,
                selected_scenarios,
                args.business_operation_clients,
                args.business_operation_iterations,
                args.business_operation_timeout_seconds,
                args.repetitions,
                "in_memory_only",
            )
            if args.leaderboard_redis_comparison:
                redis_key = args.leaderboard_redis_key.strip() or (
                    f"lb:perf:{summary['git_commit'][:12]}:{os.getpid()}:{time.monotonic_ns()}"
                )
                ping_before = redis_command(
                    args.leaderboard_redis_host,
                    args.leaderboard_redis_port,
                    "PING",
                ) == "PONG"
                if not ping_before:
                    raise RuntimeError("Redis comparison endpoint did not respond to PING")
                redis_command(args.leaderboard_redis_host, args.leaderboard_redis_port, "DEL", redis_key)
                redis_command(args.leaderboard_redis_host, args.leaderboard_redis_port, "DEL", f"{redis_key}:names")

                log_step("Restarting leaderboard topology for Redis persistence comparison")
                gateway_process.stop()
                managed.remove(gateway_process)
                leaderboard_process.stop()
                managed.remove(leaderboard_process)

                leaderboard_process = ManagedProcess(
                    "v2_leaderboard_backend.redis",
                    executables["leaderboard"],
                    [str(args.leaderboard_port)],
                    log_dir,
                    {
                        "SERVICE_PORT": str(args.leaderboard_port),
                        "LEADERBOARD_PORT": str(args.leaderboard_port),
                        "LEADERBOARD_CONFIG_PATH": str(
                            root / "config/environments/local/leaderboard.json"
                        ),
                        "REDIS_HOST": args.leaderboard_redis_host,
                        "REDIS_PORT": str(args.leaderboard_redis_port),
                        "REDIS_LEADERBOARD_KEY": redis_key,
                        "BOOST_DISABLE_REDIS_AUTO_CONNECT": "0",
                        "BOOST_LOG_LEVEL": "info",
                    },
                )
                managed.append(leaderboard_process)
                wait_tcp_port("127.0.0.1", args.leaderboard_port)
                redis_log_marker = (
                    "Redis leaderboard and event store enabled "
                    f"({args.leaderboard_redis_host}:{args.leaderboard_redis_port})"
                )
                redis_log_verified = wait_process_log(leaderboard_process, redis_log_marker)
                if not redis_log_verified:
                    raise RuntimeError("Redis leaderboard startup did not emit the required enabled marker")

                gateway_process = ManagedProcess(
                    "v2_gateway_demo.redis",
                    executables["gateway"],
                    gateway_args,
                    log_dir,
                    gateway_env,
                )
                managed.append(gateway_process)
                wait_tcp_port("127.0.0.1", args.gateway_port)
                wait_tcp_port("127.0.0.1", args.http_port)
                time.sleep(2.0)
                redis_perf = run_business_operation_perf(
                    "127.0.0.1",
                    args.gateway_port,
                    ["leaderboard"],
                    args.business_operation_clients,
                    args.business_operation_iterations,
                    args.business_operation_timeout_seconds,
                    args.repetitions,
                    "redis_primary_with_memory_shadow",
                )
                ping_after = redis_command(
                    args.leaderboard_redis_host,
                    args.leaderboard_redis_port,
                    "PING",
                ) == "PONG"
                redis_zcard_raw = redis_command(
                    args.leaderboard_redis_host,
                    args.leaderboard_redis_port,
                    "ZCARD",
                    redis_key,
                )
                redis_zcard = redis_zcard_raw if isinstance(redis_zcard_raw, int) else -1
                comparison = build_leaderboard_persistence_comparison(
                    summary["business_operation_perf"],
                    redis_perf,
                    repetitions=args.repetitions,
                    redis_host=args.leaderboard_redis_host,
                    redis_port=args.leaderboard_redis_port,
                    redis_key=redis_key,
                    in_memory_log_verified=in_memory_log_verified,
                    redis_log_verified=redis_log_verified,
                    ping_before=ping_before,
                    ping_after=ping_after,
                    redis_zcard=redis_zcard,
                    expected_min_zcard=args.business_operation_clients * args.repetitions,
                )
                summary["leaderboard_persistence_comparison"] = comparison
                summary["business_operation_perf"]["leaderboard_persistence"]["redis_comparison"] = True
                summary["business_operation_perf"]["leaderboard_persistence"]["comparison_verified"] = comparison["verified"]
                summary["business_operation_perf"]["passed"] = (
                    summary["business_operation_perf"]["passed"] and comparison["verified"]
                )
                summary["business_operation_perf"]["overall_pass"] = summary["business_operation_perf"]["passed"]
            business_operation_path = result_dir / "business-operation-perf.json"
            business_operation_path.write_text(
                json.dumps(summary["business_operation_perf"], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            completed_business_runs = int(summary["business_operation_perf"]["completed_runs"])
            aggregate_run_counts = [
                int(item["runs"])
                for item in summary["business_operation_perf"]["scenario_aggregates"]
            ]
            business_operation_passed = (
                bool(summary["business_operation_perf"]["passed"])
                and completed_business_runs == args.repetitions
                and all(count == args.repetitions for count in aggregate_run_counts)
                and (
                    not args.leaderboard_redis_comparison
                    or summary["leaderboard_persistence_comparison"]["verified"] is True
                )
            )
            summary["release_gates"].setdefault("checks", []).append({
                "case": "concurrent-business-operations",
                "passed": business_operation_passed,
                "criteria": "all requested runs and matchmaking/leaderboard operations complete without failure",
                "observed": {
                    "scenarios": selected_scenarios,
                    "clients": args.business_operation_clients,
                    "iterations_per_client": args.business_operation_iterations,
                    "requested_runs": args.repetitions,
                    "completed_runs": completed_business_runs,
                    "aggregate_run_counts": aggregate_run_counts,
                    "leaderboard_redis_comparison": args.leaderboard_redis_comparison,
                },
            })
            if not business_operation_passed:
                summary["release_gates"]["overall_pass"] = False
            summary["process_snapshots"]["business-operation-perf"] = snapshot_processes(managed)
        summary["resource_analysis"] = analyze_resources(summary)
        final_diagnostics = fetch_json(f"http://127.0.0.1:{args.http_port}/metrics/diagnostics/json")
        summary["final_backend_metrics"] = final_diagnostics.get("backend_metrics", {})
        (result_dir / "final.gateway.diagnostics.json").write_text(
            json.dumps(final_diagnostics, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if args.include_business_flow:
            log_step("Running SDK full-flow business coverage")
            summary["business_flow"] = run_business_flow_case(
                root,
                build_dir,
                output_root,
                gateway_host="127.0.0.1",
                gateway_port=args.gateway_port,
                concurrent_clients=max(1, args.business_flow_clients),
            )
            if not summary["business_flow"].get("passed"):
                summary["release_gates"].setdefault("checks", []).append({
                    "case": "sdk-full-flow-business-path",
                    "passed": False,
                    "criteria": "SDK full-flow covers login/room/battle/matchmaking/leaderboard/settlement",
                    "observed": {"duration_seconds": summary["business_flow"].get("duration_seconds")},
                })
                summary["release_gates"]["overall_pass"] = False
        summary["n1_profiles"] = {
            "run_preset": args.run_preset,
            "business_flow_clients": max(1, args.business_flow_clients),
            "supports_long_soak_followup": True,
            "supports_capacity_followup": args.run_preset in {"capacity", "business-capacity"},
        }

        summary_path = output_root / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        report_path = output_root / "report.md"
        report_path.write_text(render_markdown_report(summary), encoding="utf-8")
        log_step(f"Baseline collection completed: {output_root}")
        log_step(f"Markdown report written: {report_path}")
        if (
            (args.run_preset == "smoke" and not args.business_operation_scenario)
            or summary["release_gates"].get("overall_pass")
        ):
            return 0
        log_step("Release performance gates failed")
        return 2
    finally:
        for proc in reversed(managed):
            proc.stop()


if __name__ == "__main__":
    sys.exit(main())
