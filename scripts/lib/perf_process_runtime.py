"""Performance baseline responsibility module: perf_process_runtime."""

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

def completed_children_cpu_seconds() -> float | None:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return float(usage.ru_utime) + float(usage.ru_stime)

class ManagedProcess:
    def __init__(
        self,
        name: str,
        executable: Path,
        args: list[str],
        log_dir: Path,
        env: dict[str, str] | None = None,
        *,
        cpu_set: str = "",
    ) -> None:
        self.name = name
        self.stdout_path = log_dir / f"{name}.stdout.log"
        self.stderr_path = log_dir / f"{name}.stderr.log"
        self.stdout_handle = open(self.stdout_path, "w", encoding="utf-8")
        self.stderr_handle = open(self.stderr_path, "w", encoding="utf-8")
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        self.proc = subprocess.Popen(
            affinity_command(executable, args, cpu_set),
            cwd=executable.parent,
            stdout=self.stdout_handle,
            stderr=self.stderr_handle,
            stdin=subprocess.DEVNULL,
            env=merged_env,
        )
        self.startup_affinity = (
            verify_process_cpu_affinity(self.proc.pid, cpu_set)
            if cpu_set
            else {
                "pid": self.proc.pid,
                "requested_cpu_set": "",
                "effective_cpu_set": process_snapshot(self.proc.pid).get("cpu_affinity", ""),
                "verified": True,
            }
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

def fetch_json(url: str) -> Any:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))

def normalize_process_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output

def git_commit(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
    except Exception:
        return "unknown"


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


def start_perf_topology(
    args: argparse.Namespace,
    root: Path,
    layout: dict[str, Any],
    service_constraint: dict[str, Any],
) -> dict[str, Any]:
    """Start the baseline topology or clean up every partially started process."""
    executables, log_dir = layout["executables"], layout["log_dir"]
    battle_max_frames = layout["battle_max_frames"]
    managed: list[ManagedProcess] = []
    try:
        log_step("Starting v2 backend topology")
        managed.append(ManagedProcess("v2_login_backend", executables["login"], [str(args.login_port)], log_dir, cpu_set=args.cpu_set))
        wait_tcp_port("127.0.0.1", args.login_port)
        battle_env = {"V2_BATTLE_MAX_FRAMES": str(battle_max_frames)}
        managed.append(ManagedProcess("v2_room_backend", executables["room"], [str(args.room_port)], log_dir, battle_env, cpu_set=args.cpu_set))
        wait_tcp_port("127.0.0.1", args.room_port)
        battle_process = ManagedProcess("v2_battle_backend", executables["battle"], [str(args.battle_port)], log_dir, cpu_set=args.cpu_set)
        managed.append(battle_process)
        wait_tcp_port("127.0.0.1", args.battle_port)
        managed.append(ManagedProcess(
            "v2_match_backend", executables["matchmaking"], [str(args.matchmaking_port)], log_dir,
            {"SERVICE_PORT": str(args.matchmaking_port), "MATCH_PORT": str(args.matchmaking_port)}, cpu_set=args.cpu_set,
        ))
        wait_tcp_port("127.0.0.1", args.matchmaking_port)
        leaderboard_process = ManagedProcess(
            "v2_leaderboard_backend", executables["leaderboard"], [str(args.leaderboard_port)], log_dir,
            {
                "SERVICE_PORT": str(args.leaderboard_port), "LEADERBOARD_PORT": str(args.leaderboard_port),
                "LEADERBOARD_CONFIG_PATH": str(root / "config/environments/local/leaderboard.json"),
                "REDIS_HOST": "", "BOOST_DISABLE_REDIS_AUTO_CONNECT": "1", "BOOST_LOG_LEVEL": "info",
            }, cpu_set=args.cpu_set,
        )
        managed.append(leaderboard_process)
        wait_tcp_port("127.0.0.1", args.leaderboard_port)
        in_memory_log_verified = wait_process_log(leaderboard_process, "Redis auto-connect disabled")
        if not in_memory_log_verified:
            raise RuntimeError("in-memory leaderboard startup did not prove Redis auto-connect was disabled")
        gateway_args = [
            "--port", str(args.gateway_port), "--io-cores", str(args.io_cores),
            "--http-port", str(args.http_port), "--login-port", str(args.login_port),
            "--room-port", str(args.room_port), "--battle-port", str(args.battle_port),
            "--matchmaking-port", str(args.matchmaking_port), "--leaderboard-port", str(args.leaderboard_port),
        ]
        gateway_env = {
            "V2_RATE_LIMIT_CONNECTION": "100000", "V2_RATE_LIMIT_MESSAGE_TYPE": "200000",
            "V2_RATE_LIMIT_IP": "200000", "V2_RATE_LIMIT_USER": "100000",
            "V2_RATE_LIMIT_LOGIN": "50000", "V2_BATTLE_MAX_FRAMES": str(battle_max_frames),
            "OTEL_EXPORT_ENDPOINT": "",
        }
        if args.backend_pool_size > 0:
            gateway_env["V2_BACKEND_CONNECTION_POOL_SIZE"] = str(args.backend_pool_size)
        if args.battle_frame_push_every > 0:
            gateway_env["V2_BATTLE_FRAME_PUSH_EVERY"] = str(args.battle_frame_push_every)
        if args.battle_route_workers > 0:
            gateway_env["V2_BATTLE_ROUTE_WORKERS"] = str(args.battle_route_workers)
        gateway_process = ManagedProcess("v2_gateway_demo", executables["gateway"], gateway_args, log_dir, gateway_env, cpu_set=args.cpu_set)
        managed.append(gateway_process)
        wait_tcp_port("127.0.0.1", args.gateway_port)
        wait_tcp_port("127.0.0.1", args.http_port)
        time.sleep(2.0)
        if args.cpu_set:
            for process in managed:
                record_process_affinity(service_constraint, process, workload="initial_topology")
        return {
            "managed": managed, "battle_process": battle_process,
            "leaderboard_process": leaderboard_process, "gateway_process": gateway_process,
            "gateway_args": gateway_args, "gateway_env": gateway_env,
            "in_memory_log_verified": in_memory_log_verified,
        }
    except BaseException:
        for process in reversed(managed):
            process.stop()
        raise
