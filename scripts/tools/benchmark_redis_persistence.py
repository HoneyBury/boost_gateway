#!/usr/bin/env python3
"""Benchmark RDB-only and AOF-everysec Redis profiles without production activation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

try:
    from scripts.tools import manage_backup_recovery as backup
except ModuleNotFoundError:  # pragma: no cover - direct installed-script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.tools import manage_backup_recovery as backup


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK = Path("/var/lib/boost-gateway/deployment-transactions/.lifecycle.lock")
DEFAULT_ACTIVE_VOLUME = "boost-gateway-production-redis-data"
DEFAULT_ACTIVE_CONTAINER = "boost-redis"
DEFAULT_POLICY = ROOT / "deploy/operations/backup-recovery-policy.example.json"
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
DOCKER_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
Runner = Callable[..., subprocess.CompletedProcess[Any]]
Starter = Callable[..., Any]

LUA_LEADERBOARD_WORKLOAD = (
    "local u=ARGV[1]; local n=ARGV[2]; local s=tonumber(ARGV[3]); "
    "redis.call('ZADD',KEYS[1],s,u); redis.call('HSET',KEYS[2],u,n); "
    "redis.call('ZREVRANGE',KEYS[1],0,19,'WITHSCORES'); "
    "redis.call('ZREVRANK',KEYS[1],u); return 1"
)


class BenchmarkError(RuntimeError):
    """Raised when persistence measurement cannot preserve its safety contract."""


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_identifier(value: str, label: str) -> str:
    if ID_RE.fullmatch(value) is None or value.startswith("."):
        raise BenchmarkError(f"{label} is invalid")
    return value


def checked(
    runner: Runner, command: list[str], **kwargs: Any
) -> subprocess.CompletedProcess[Any]:
    try:
        return runner(command, check=True, **kwargs)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise BenchmarkError(f"command failed ({command[0]}): {exc}") from exc


def docker_text(runner: Runner, command: list[str], *, timeout: int = 60) -> str:
    completed = checked(
        runner,
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return completed.stdout.strip()


def load_candidate_profile(path: Path) -> tuple[bytes, bytes]:
    try:
        profile = backup.require_regular(path, "Redis candidate profile")
    except backup.BackupError as exc:
        raise BenchmarkError(str(exc)) from exc
    try:
        candidate_bytes = profile.read_bytes()
        content = candidate_bytes.decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise BenchmarkError(f"cannot read Redis candidate profile: {exc}") from exc
    lines = content.splitlines(keepends=True)
    matches = [
        index
        for index, line in enumerate(lines)
        if line.strip()
        and not line.lstrip().startswith("#")
        and line.split(maxsplit=1)[0].lower() == "appendonly"
    ]
    if len(matches) != 1 or lines[matches[0]].strip().lower() != "appendonly yes":
        raise BenchmarkError("candidate profile must contain exactly appendonly yes")
    required = {
        "save 300 100",
        "save 60 10000",
        "stop-writes-on-bgsave-error yes",
        "rdbchecksum yes",
        "appendfsync everysec",
        "no-appendfsync-on-rewrite no",
        "aof-load-truncated no",
        "maxmemory-policy noeviction",
        "dir /data",
    }
    directives = {
        line.strip().lower()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }
    if not required <= directives:
        raise BenchmarkError("candidate profile lacks required persistence directives")
    baseline = list(lines)
    suffix = "\n" if lines[matches[0]].endswith("\n") else ""
    baseline[matches[0]] = f"appendonly no{suffix}"
    baseline_bytes = "".join(baseline).encode("ascii")
    if candidate_bytes == baseline_bytes:
        raise BenchmarkError("derived baseline did not change appendonly")
    return baseline_bytes, candidate_bytes


def write_config(path: Path, content: bytes) -> None:
    backup.write_new(path, content, 0o644)


def load_policy_binding(path: Path, profile_sha256: str) -> dict[str, Any]:
    try:
        policy_path = backup.require_regular(path, "backup recovery policy")
    except backup.BackupError as exc:
        raise BenchmarkError(str(exc)) from exc
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"cannot read backup recovery policy: {exc}") from exc
    if not isinstance(policy, dict):
        raise BenchmarkError("backup recovery policy must be a JSON object")
    activation = policy.get("activation")
    redis = policy.get("redis")
    performance = redis.get("performance_impact") if isinstance(redis, dict) else None
    if (
        policy.get("todo") != "TODO-0012"
        or not isinstance(activation, dict)
        or activation.get("state") != "candidate_only"
        or activation.get("production_compose_mount_enabled") is not False
        or not isinstance(redis, dict)
        or redis.get("profile_sha256") != profile_sha256
        or not isinstance(performance, dict)
        or performance.get("status") != "pending_measurement"
        or performance.get("baseline_mode") != "rdb_only"
        or performance.get("candidate_mode") != "aof_everysec_plus_rdb"
        or performance.get("minimum_repetitions_per_mode") != 3
    ):
        raise BenchmarkError("backup recovery policy binding differs")
    return {
        "path": str(policy_path.resolve()),
        "sha256": sha256_file(policy_path),
        "activation_state": activation["state"],
        "production_compose_mount_enabled": False,
        "profile_sha256": profile_sha256,
    }


def collect_controller_provenance(repo_root: Path = ROOT) -> dict[str, Any]:
    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            raise BenchmarkError("cannot inspect controller Git checkout")
        return completed.stdout.strip()

    commit = git("rev-parse", "HEAD")
    ref = git("branch", "--show-current")
    dirty = git("status", "--porcelain", "--untracked-files=all")
    runner_path = Path(__file__).resolve()
    if (
        COMMIT_RE.fullmatch(commit) is None
        or ref != "main"
        or dirty
        or not runner_path.is_relative_to(repo_root.resolve())
    ):
        raise BenchmarkError("controller checkout is not clean governed main")
    return {
        "commit": commit,
        "ref": ref,
        "worktree_clean": True,
        "runner_path": str(runner_path.relative_to(repo_root.resolve())),
        "runner_sha256": sha256_file(runner_path),
    }


def inspect_image(runner: Runner, docker: str, image: str) -> dict[str, Any]:
    if IMAGE_ID_RE.fullmatch(image) is None:
        raise BenchmarkError("Redis image must be an immutable sha256 image ID")
    output = docker_text(runner, [docker, "image", "inspect", image])
    try:
        values = json.loads(output)
    except json.JSONDecodeError as exc:
        raise BenchmarkError("Docker image inspection returned invalid JSON") from exc
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        raise BenchmarkError("Docker image inspection is incomplete")
    value = values[0]
    if (
        value.get("Id") != image
        or value.get("Os") != "linux"
        or value.get("Architecture") != "amd64"
    ):
        raise BenchmarkError("Redis image identity is not Linux amd64 immutable input")
    return value


def assert_local_docker(runner: Runner, docker: str) -> None:
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
        raise BenchmarkError("remote or overridden Docker endpoint is forbidden")
    context = docker_text(runner, [docker, "context", "show"], timeout=30)
    endpoint = docker_text(
        runner,
        [
            docker,
            "context",
            "inspect",
            "--format",
            "{{.Endpoints.docker.Host}}",
            "default",
        ],
        timeout=30,
    )
    if context != "default" or endpoint != "unix:///var/run/docker.sock":
        raise BenchmarkError("Docker endpoint is not the local system socket")


def inspect_volume(runner: Runner, docker: str, volume: str) -> dict[str, Any]:
    output = docker_text(runner, [docker, "volume", "inspect", volume])
    try:
        values = json.loads(output)
    except json.JSONDecodeError as exc:
        raise BenchmarkError("Docker volume inspection returned invalid JSON") from exc
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        raise BenchmarkError("Docker volume inspection is incomplete")
    return values[0]


def inspect_network(runner: Runner, docker: str, network: str) -> dict[str, Any]:
    output = docker_text(runner, [docker, "network", "inspect", network])
    try:
        values = json.loads(output)
    except json.JSONDecodeError as exc:
        raise BenchmarkError("Docker network inspection returned invalid JSON") from exc
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        raise BenchmarkError("Docker network inspection is incomplete")
    return values[0]


def inspect_active_redis(
    runner: Runner,
    docker: str,
    container: str,
    image: str,
    active_volume: str,
) -> dict[str, Any]:
    output = docker_text(runner, [docker, "inspect", container])
    try:
        values = json.loads(output)
    except json.JSONDecodeError as exc:
        raise BenchmarkError("active Redis inspection returned invalid JSON") from exc
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        raise BenchmarkError("active Redis inspection is incomplete")
    value = values[0]
    state = value.get("State")
    health = state.get("Health") if isinstance(state, dict) else None
    config = value.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    mounts = value.get("Mounts")
    data_mounts = (
        [
            item
            for item in mounts
            if isinstance(item, dict) and item.get("Destination") == "/data"
        ]
        if isinstance(mounts, list)
        else []
    )
    if (
        value.get("Name") != f"/{container}"
        or DOCKER_ID_RE.fullmatch(value.get("Id") or "") is None
        or value.get("Image") != image
        or not isinstance(state, dict)
        or state.get("Running") is not True
        or not isinstance(health, dict)
        or health.get("Status") != "healthy"
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.service") != "redis"
        or len(data_mounts) != 1
        or data_mounts[0].get("Type") != "volume"
        or data_mounts[0].get("Name") != active_volume
        or data_mounts[0].get("RW") is not True
    ):
        raise BenchmarkError("active Redis runtime binding differs")
    return value


def volume_identity(value: dict[str, Any]) -> str:
    selected = {
        field: value.get(field)
        for field in ("Name", "Driver", "Mountpoint", "Scope", "Labels")
    }
    return sha256_bytes(canonical_json(selected))


def ensure_volume_absent(runner: Runner, docker: str, volume: str) -> None:
    names = docker_text(
        runner, [docker, "volume", "ls", "--format", "{{.Name}}"], timeout=30
    ).splitlines()
    if volume in names:
        raise BenchmarkError(f"temporary benchmark volume already exists: {volume}")


def ensure_container_absent(runner: Runner, docker: str, container: str) -> None:
    names = docker_text(
        runner, [docker, "ps", "-a", "--format", "{{.Names}}"], timeout=30
    ).splitlines()
    if container in names:
        raise BenchmarkError(
            f"temporary benchmark container already exists: {container}"
        )


def ensure_network_absent(runner: Runner, docker: str, network: str) -> None:
    names = docker_text(
        runner, [docker, "network", "ls", "--format", "{{.Name}}"], timeout=30
    ).splitlines()
    if network in names:
        raise BenchmarkError(f"temporary benchmark network already exists: {network}")


def parse_info(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key] = value
    return fields


def redis_info(runner: Runner, docker: str, container: str) -> dict[str, str]:
    return parse_info(
        docker_text(
            runner,
            [docker, "exec", container, "redis-cli", "--raw", "INFO", "all"],
            timeout=30,
        )
    )


def numeric(info: dict[str, str], field: str, *, integer: bool = False) -> float | int:
    try:
        return int(info[field]) if integer else float(info[field])
    except (KeyError, ValueError) as exc:
        raise BenchmarkError(f"Redis INFO field is invalid: {field}") from exc


def cgroup_io(
    runner: Runner,
    docker: str,
    container: str,
    *,
    allow_empty: bool = False,
) -> dict[str, int]:
    text = docker_text(
        runner,
        [docker, "exec", container, "cat", "/sys/fs/cgroup/io.stat"],
        timeout=30,
    )
    write_bytes = 0
    devices = 0
    for raw_line in text.splitlines():
        fields = raw_line.split()
        if not fields or ":" not in fields[0]:
            continue
        values: dict[str, int] = {}
        for field in fields[1:]:
            if "=" not in field:
                continue
            key, raw_value = field.split("=", 1)
            try:
                values[key] = int(raw_value)
            except ValueError:
                continue
        if "wbytes" in values:
            devices += 1
            write_bytes += values["wbytes"]
    if devices == 0 and not allow_empty:
        raise BenchmarkError("Redis cgroup v2 io.stat write bytes are unavailable")
    return {"write_bytes": write_bytes, "devices": devices}


def total_cpu_seconds(info: dict[str, str]) -> float:
    return sum(
        float(numeric(info, field))
        for field in (
            "used_cpu_sys",
            "used_cpu_user",
            "used_cpu_sys_children",
            "used_cpu_user_children",
        )
    )


def config_get(runner: Runner, docker: str, container: str) -> dict[str, str]:
    output = docker_text(
        runner,
        [
            docker,
            "exec",
            container,
            "redis-cli",
            "--raw",
            "CONFIG",
            "GET",
            "appendonly",
            "appendfsync",
            "maxmemory-policy",
            "dir",
            "save",
            "stop-writes-on-bgsave-error",
            "rdbchecksum",
            "aof-load-truncated",
            "no-appendfsync-on-rewrite",
        ],
        timeout=30,
    ).splitlines()
    if len(output) % 2:
        raise BenchmarkError("Redis CONFIG GET response is invalid")
    return {output[index]: output[index + 1] for index in range(0, len(output), 2)}


def wait_for_redis(
    runner: Runner,
    docker: str,
    container: str,
    *,
    sleeper: Callable[[float], None],
) -> None:
    for _ in range(60):
        completed = runner(
            [docker, "exec", container, "redis-cli", "--raw", "PING"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
        if completed.returncode == 0 and completed.stdout.strip() == "PONG":
            return
        sleeper(0.25)
    raise BenchmarkError("temporary Redis did not become ready")


def benchmark_command(
    docker: str,
    client_container: str,
    network: str,
    image: str,
    labels: list[str],
    *,
    requests: int,
    clients: int,
    keyspace: int,
) -> list[str]:
    return [
        docker,
        "run",
        "--name",
        client_container,
        "--network",
        network,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        *labels,
        "--pids-limit",
        "128",
        "--memory",
        "256m",
        "--cpus",
        "1.0",
        "--user",
        "redis",
        "--entrypoint",
        "redis-benchmark",
        image,
        "-h",
        "redis-benchmark-target",
        "--csv",
        "-n",
        str(requests),
        "-c",
        str(clients),
        "-r",
        str(keyspace),
        "eval",
        LUA_LEADERBOARD_WORKLOAD,
        "2",
        "lb:global",
        "lb:global:names",
        "bench-user-__rand_int__",
        "Benchmark User __rand_int__",
        "__rand_int__",
    ]


def parse_benchmark_csv(text: str) -> dict[str, float]:
    rows = list(csv.reader(text.splitlines()))
    for row in reversed(rows):
        if len(row) < 8:
            continue
        try:
            values = [float(item) for item in row[1:8]]
        except ValueError:
            continue
        return {
            "throughput_requests_per_second": values[0],
            "average_latency_ms": values[1],
            "minimum_latency_ms": values[2],
            "p50_latency_ms": values[3],
            "p95_latency_ms": values[4],
            "p99_latency_ms": values[5],
            "maximum_latency_ms": values[6],
        }
    raise BenchmarkError("redis-benchmark CSV output is invalid")


def terminate_process(process: Any) -> None:
    try:
        running = process.poll() is None
    except Exception:
        running = True
    if running:
        try:
            process.kill()
        except Exception:
            pass
    try:
        process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        finally:
            process.communicate()


def wait_for_bgsave(
    runner: Runner,
    docker: str,
    container: str,
    *,
    timeout_seconds: float,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
    sample: Callable[[dict[str, str]], None],
) -> tuple[dict[str, str], float, bool]:
    started = monotonic()
    response = docker_text(
        runner,
        [docker, "exec", container, "redis-cli", "--raw", "BGSAVE"],
        timeout=30,
    )
    if response != "Background saving started":
        raise BenchmarkError(f"Redis BGSAVE did not start: {response}")
    deadline = monotonic() + timeout_seconds
    observed_in_progress = False
    while True:
        info = redis_info(runner, docker, container)
        sample(info)
        in_progress = int(numeric(info, "rdb_bgsave_in_progress", integer=True))
        if in_progress not in {0, 1}:
            raise BenchmarkError("Redis BGSAVE progress state is invalid")
        observed_in_progress = observed_in_progress or in_progress == 1
        if in_progress == 0:
            if info.get("rdb_last_bgsave_status") != "ok":
                raise BenchmarkError("Redis BGSAVE completed unsuccessfully")
            if int(numeric(info, "rdb_changes_since_last_save", integer=True)) != 0:
                raise BenchmarkError("Redis BGSAVE did not checkpoint workload changes")
            return info, monotonic() - started, observed_in_progress
        if monotonic() >= deadline:
            raise BenchmarkError("Redis BGSAVE exceeded checkpoint timeout")
        sleeper(0.1)


def inspect_owned_resource(
    runner: Runner,
    docker: str,
    kind: str,
    name: str,
    expected_labels: dict[str, str],
) -> bool:
    try:
        completed = runner(
            [docker, kind, "inspect", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BenchmarkError(f"cannot inspect temporary {kind}: {name}: {exc}") from exc
    if completed.returncode != 0:
        return False
    try:
        values = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"temporary {kind} inspection is invalid: {name}") from exc
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        raise BenchmarkError(f"temporary {kind} inspection is incomplete: {name}")
    value = values[0]
    observed_name = value.get("Name")
    if kind == "container":
        observed_name = str(observed_name or "").removeprefix("/")
        config = value.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
    else:
        labels = value.get("Labels")
    if observed_name != name or not isinstance(labels, dict):
        raise BenchmarkError(f"temporary {kind} identity is invalid: {name}")
    if any(labels.get(key) != expected for key, expected in expected_labels.items()):
        raise BenchmarkError(f"temporary {kind} is not benchmark-owned: {name}")
    return True


def cleanup_targets(
    runner: Runner,
    docker: str,
    *,
    server_container: str,
    client_container: str,
    volume: str,
    network: str,
    expected_labels: dict[str, str],
) -> list[str]:
    failures: list[str] = []
    targets = (
        ("container", client_container, [docker, "rm", "-f", client_container]),
        ("container", server_container, [docker, "rm", "-f", server_container]),
        ("volume", volume, [docker, "volume", "rm", volume]),
        ("network", network, [docker, "network", "rm", network]),
    )
    for kind, name, command in targets:
        try:
            if not inspect_owned_resource(runner, docker, kind, name, expected_labels):
                continue
            completed = runner(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=60,
            )
        except (BenchmarkError, OSError, subprocess.TimeoutExpired):
            failures.append(f"{kind}:{name}")
            continue
        if completed.returncode != 0:
            failures.append(f"{kind}:{name}")
            continue
        try:
            if inspect_owned_resource(runner, docker, kind, name, expected_labels):
                failures.append(f"{kind}:{name}")
        except BenchmarkError:
            failures.append(f"{kind}:{name}")
    return failures


def execute_round(
    *,
    benchmark_id: str,
    mode: str,
    repetition: int,
    config_path: Path,
    config_sha256: str,
    image: str,
    active_volume: str,
    requests: int,
    clients: int,
    keyspace: int,
    sample_interval_seconds: float,
    post_workload_settle_seconds: float,
    workload_timeout_seconds: float,
    docker: str,
    runner: Runner,
    starter: Starter,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    volume = f"boost-gateway-benchmark-{benchmark_id}-{mode}-{repetition}"
    server_container = f"boost-redis-benchmark-{benchmark_id}-{mode}-{repetition}"
    client_container = (
        f"boost-redis-benchmark-client-{benchmark_id}-{mode}-{repetition}"
    )
    network = f"boost-gateway-benchmark-net-{benchmark_id}-{mode}-{repetition}"
    if volume == active_volume:
        raise BenchmarkError("benchmark target is the active Redis volume")
    ensure_volume_absent(runner, docker, volume)
    ensure_container_absent(runner, docker, server_container)
    ensure_container_absent(runner, docker, client_container)
    ensure_network_absent(runner, docker, network)
    started_at = now()
    started = monotonic()
    result: dict[str, Any] = {}
    failure = ""
    cleanup_failures: list[str] = []
    process: Any | None = None
    process_reaped = False
    expected_labels = {
        "boost-gateway.todo": "TODO-0012",
        "boost-gateway.benchmark-id": benchmark_id,
        "boost-gateway.benchmark-mode": mode,
    }
    label_arguments = [
        argument
        for key, value in expected_labels.items()
        for argument in ("--label", f"{key}={value}")
    ]
    try:
        checked(
            runner,
            [
                docker,
                "network",
                "create",
                "--driver",
                "bridge",
                "--internal",
                *label_arguments,
                network,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        network_state = inspect_network(runner, docker, network)
        network_labels = network_state.get("Labels")
        if (
            network_state.get("Name") != network
            or network_state.get("Driver") != "bridge"
            or network_state.get("Internal") is not True
            or not isinstance(network_labels, dict)
            or any(
                network_labels.get(key) != expected
                for key, expected in expected_labels.items()
            )
        ):
            raise BenchmarkError("temporary internal network identity differs")
        checked(
            runner,
            [
                docker,
                "volume",
                "create",
                *label_arguments,
                volume,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        volume_state = inspect_volume(runner, docker, volume)
        labels = volume_state.get("Labels")
        if (
            volume_state.get("Name") != volume
            or not isinstance(labels, dict)
            or labels.get("boost-gateway.todo") != "TODO-0012"
            or labels.get("boost-gateway.benchmark-id") != benchmark_id
            or labels.get("boost-gateway.benchmark-mode") != mode
        ):
            raise BenchmarkError("temporary volume identity or labels differ")
        checked(
            runner,
            [
                docker,
                "run",
                "-d",
                "--name",
                server_container,
                "--network",
                network,
                "--network-alias",
                "redis-benchmark-target",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                *label_arguments,
                "--label",
                "boost-gateway.benchmark-role=server",
                "--pids-limit",
                "256",
                "--memory",
                "512m",
                "--cpus",
                "1.0",
                "--user",
                "redis",
                "--mount",
                f"type=volume,src={volume},dst=/data",
                "--mount",
                f"type=bind,src={config_path.resolve()},dst=/config/redis.conf,readonly",
                "--entrypoint",
                "redis-server",
                image,
                "/config/redis.conf",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        wait_for_redis(runner, docker, server_container, sleeper=sleeper)
        effective = config_get(runner, docker, server_container)
        expected_appendonly = "yes" if mode == "aof_everysec_rdb" else "no"
        save_tokens = effective.get("save", "").split()
        save_rules = (
            set(zip(save_tokens[::2], save_tokens[1::2], strict=True))
            if len(save_tokens) % 2 == 0
            else set()
        )
        if (
            effective.get("appendonly") != expected_appendonly
            or effective.get("appendfsync") != "everysec"
            or effective.get("maxmemory-policy") != "noeviction"
            or effective.get("dir") != "/data"
            or not {("300", "100"), ("60", "10000")} <= save_rules
            or effective.get("stop-writes-on-bgsave-error") != "yes"
            or effective.get("rdbchecksum") != "yes"
            or effective.get("aof-load-truncated") != "no"
            or effective.get("no-appendfsync-on-rewrite") != "no"
        ):
            raise BenchmarkError("temporary Redis effective configuration differs")

        rss_samples: list[dict[str, float | int | str]] = []

        def record_rss(info: dict[str, str], phase: str) -> None:
            rss_samples.append(
                {
                    "phase": phase,
                    "observed_at_monotonic": monotonic(),
                    "rss_bytes": int(numeric(info, "used_memory_rss", integer=True)),
                }
            )

        before_info = redis_info(runner, docker, server_container)
        before_io = cgroup_io(runner, docker, server_container, allow_empty=True)
        measurement_started = monotonic()
        cpu_before = total_cpu_seconds(before_info)
        children_cpu_before = float(
            numeric(before_info, "used_cpu_sys_children")
        ) + float(numeric(before_info, "used_cpu_user_children"))
        delayed_before = int(numeric(before_info, "aof_delayed_fsync", integer=True))
        record_rss(before_info, "before_workload")
        command = benchmark_command(
            docker,
            client_container,
            network,
            image,
            [
                *label_arguments,
                "--label",
                "boost-gateway.benchmark-role=client",
            ],
            requests=requests,
            clients=clients,
            keyspace=keyspace,
        )
        workload_started = monotonic()
        try:
            process = starter(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise BenchmarkError(f"redis-benchmark could not start: {exc}") from exc
        workload_deadline = monotonic() + workload_timeout_seconds
        while process.poll() is None:
            if monotonic() >= workload_deadline:
                terminate_process(process)
                process_reaped = True
                raise BenchmarkError("redis-benchmark exceeded workload timeout")
            sampled = redis_info(runner, docker, server_container)
            record_rss(sampled, "workload")
            sleeper(sample_interval_seconds)
        try:
            stdout, stderr = process.communicate(timeout=30)
            process_reaped = True
        except subprocess.TimeoutExpired as exc:
            terminate_process(process)
            process_reaped = True
            raise BenchmarkError("redis-benchmark did not terminate") from exc
        if process.returncode != 0:
            raise BenchmarkError(
                f"redis-benchmark failed: {(stderr or '').strip()[-300:]}"
            )
        workload = parse_benchmark_csv(stdout or "")
        workload_elapsed = monotonic() - workload_started
        if workload_elapsed <= 0:
            raise BenchmarkError("workload elapsed time is invalid")
        if (
            workload["throughput_requests_per_second"] <= 0
            or workload["p50_latency_ms"] < 0
            or workload["p99_latency_ms"] < 0
        ):
            raise BenchmarkError("redis-benchmark metrics are invalid")
        sleeper(post_workload_settle_seconds)
        workload_info = redis_info(runner, docker, server_container)
        workload_io = cgroup_io(runner, docker, server_container)
        record_rss(workload_info, "after_workload")
        workload_cpu_after = total_cpu_seconds(workload_info)
        children_cpu_workload_after = float(
            numeric(workload_info, "used_cpu_sys_children")
        ) + float(numeric(workload_info, "used_cpu_user_children"))

        after_bgsave, bgsave_elapsed, bgsave_observed_in_progress = wait_for_bgsave(
            runner,
            docker,
            server_container,
            timeout_seconds=workload_timeout_seconds,
            monotonic=monotonic,
            sleeper=sleeper,
            sample=lambda info: record_rss(info, "bgsave"),
        )
        sleeper(post_workload_settle_seconds)
        after_info = redis_info(runner, docker, server_container)
        after_io = cgroup_io(runner, docker, server_container)
        record_rss(after_info, "after_bgsave")
        cpu_after = total_cpu_seconds(after_info)
        children_cpu_after = float(
            numeric(after_info, "used_cpu_sys_children")
        ) + float(numeric(after_info, "used_cpu_user_children"))
        delayed_after = int(numeric(after_info, "aof_delayed_fsync", integer=True))
        elapsed = monotonic() - started
        measurement_elapsed = monotonic() - measurement_started
        if elapsed <= 0:
            raise BenchmarkError("benchmark elapsed time is invalid")
        if measurement_elapsed <= 0:
            raise BenchmarkError("Redis metric interval is invalid")
        workload_disk_write_bytes = (
            workload_io["write_bytes"] - before_io["write_bytes"]
        )
        bgsave_disk_write_bytes = after_io["write_bytes"] - workload_io["write_bytes"]
        disk_write_bytes = after_io["write_bytes"] - before_io["write_bytes"]
        workload_cpu_seconds = workload_cpu_after - cpu_before
        bgsave_cpu_seconds = cpu_after - workload_cpu_after
        cpu_seconds = cpu_after - cpu_before
        children_cpu_seconds = children_cpu_after - children_cpu_before
        bgsave_children_cpu_seconds = children_cpu_after - children_cpu_workload_after
        if (
            cpu_seconds <= 0
            or workload_cpu_seconds <= 0
            or bgsave_cpu_seconds < 0
            or children_cpu_seconds < 0
            or bgsave_children_cpu_seconds < 0
            or disk_write_bytes < 0
            or workload_disk_write_bytes < 0
            or bgsave_disk_write_bytes <= 0
            or delayed_after < delayed_before
        ):
            raise BenchmarkError("Redis cumulative metrics moved backwards")
        if mode == "aof_everysec_rdb" and after_info.get("aof_enabled") != "1":
            raise BenchmarkError("AOF mode did not report aof_enabled=1")
        if mode == "rdb_only" and after_info.get("aof_enabled") != "0":
            raise BenchmarkError("RDB-only mode unexpectedly enabled AOF")
        if (
            after_bgsave.get("rdb_last_bgsave_status") != "ok"
            or int(numeric(after_bgsave, "rdb_changes_since_last_save", integer=True))
            != 0
        ):
            raise BenchmarkError("Redis RDB persistence status is not healthy")
        if (
            mode == "aof_everysec_rdb"
            and after_info.get("aof_last_write_status") != "ok"
        ):
            raise BenchmarkError("Redis AOF write status is not healthy")
        delayed_delta = delayed_after - delayed_before
        if delayed_delta:
            raise BenchmarkError("Redis reported delayed AOF fsync events")
        if mode == "aof_everysec_rdb" and workload_disk_write_bytes <= 0:
            raise BenchmarkError("AOF mode produced no observable disk writes")
        dbsize_text = docker_text(
            runner,
            [docker, "exec", server_container, "redis-cli", "--raw", "DBSIZE"],
            timeout=30,
        )
        zcard_text = docker_text(
            runner,
            [
                docker,
                "exec",
                server_container,
                "redis-cli",
                "--raw",
                "ZCARD",
                "lb:global",
            ],
            timeout=30,
        )
        dbsize = int(dbsize_text)
        leaderboard_members = int(zcard_text)
        minimum_members = min(requests, keyspace) // 2
        if (
            dbsize != 2
            or leaderboard_members < minimum_members
            or leaderboard_members > min(requests, keyspace)
        ):
            raise BenchmarkError("leaderboard workload effects are incomplete")
        sample_times = [float(item["observed_at_monotonic"]) for item in rss_samples]
        observed_intervals = [
            current - previous
            for previous, current in zip(sample_times, sample_times[1:])
        ]
        rss_values = [int(item["rss_bytes"]) for item in rss_samples]
        result = {
            "mode": mode,
            "repetition": repetition,
            "started_at": started_at,
            "completed_at": now(),
            "elapsed_seconds": round(elapsed, 6),
            "workload_elapsed_seconds": round(workload_elapsed, 6),
            "metric_interval_seconds": round(measurement_elapsed, 6),
            "post_workload_settle_seconds": post_workload_settle_seconds,
            "config_sha256": config_sha256,
            "volume": volume,
            "server_container": server_container,
            "client_container": client_container,
            "network": network,
            "network_mode": "internal_bridge",
            "server_resource_limits": {
                "cpus": 1.0,
                "memory_bytes": 512 * 1024 * 1024,
                "pids": 256,
            },
            "client_resource_limits": {
                "cpus": 1.0,
                "memory_bytes": 256 * 1024 * 1024,
                "pids": 128,
            },
            "workload": workload,
            "redis_cpu_seconds": round(cpu_seconds, 6),
            "redis_workload_cpu_seconds": round(workload_cpu_seconds, 6),
            "redis_bgsave_cpu_seconds": round(bgsave_cpu_seconds, 6),
            "redis_children_cpu_seconds": round(children_cpu_seconds, 6),
            "redis_bgsave_children_cpu_seconds": round(bgsave_children_cpu_seconds, 6),
            "redis_cpu_percent_of_one_core": round(
                cpu_seconds / measurement_elapsed * 100.0, 4
            ),
            "redis_rss_sampled_peak_bytes": max(rss_values),
            "redis_rss_end_bytes": rss_values[-1],
            "redis_rss_sample_count": len(rss_samples),
            "redis_rss_sample_requested_interval_seconds": sample_interval_seconds,
            "redis_rss_sample_observed_interval_seconds": {
                "minimum": round(min(observed_intervals), 6),
                "median": round(float(statistics.median(observed_intervals)), 6),
                "maximum": round(max(observed_intervals), 6),
            },
            "redis_disk_write_bytes": disk_write_bytes,
            "redis_workload_disk_write_bytes": workload_disk_write_bytes,
            "redis_bgsave_disk_write_bytes": bgsave_disk_write_bytes,
            "redis_cgroup_io_devices_before": before_io["devices"],
            "redis_cgroup_io_devices_after": after_io["devices"],
            "redis_cgroup_io_empty_baseline_accepted": before_io["devices"] == 0,
            "redis_aof_delayed_fsync": delayed_delta,
            "redis_bgsave": {
                "elapsed_seconds": round(bgsave_elapsed, 6),
                "observed_in_progress": bgsave_observed_in_progress,
                "last_status": after_bgsave.get("rdb_last_bgsave_status"),
                "changes_since_last_save": int(
                    numeric(after_bgsave, "rdb_changes_since_last_save", integer=True)
                ),
            },
            "redis_dbsize": dbsize,
            "leaderboard_members": leaderboard_members,
            "minimum_leaderboard_members": minimum_members,
            "effective_configuration": effective,
            "workload_contract": "lua:zadd+hset+zrevrange+zrevrank:v1",
            "passed": True,
        }
    except Exception as exc:
        failure = str(exc)
    finally:
        if process is not None and not process_reaped:
            try:
                terminate_process(process)
            except Exception:
                cleanup_failures.append("client-process")
        cleanup_failures.extend(
            cleanup_targets(
                runner,
                docker,
                server_container=server_container,
                client_container=client_container,
                volume=volume,
                network=network,
                expected_labels=expected_labels,
            )
        )
    if cleanup_failures:
        failure = (
            f"{failure}; cleanup failed: {cleanup_failures}"
            if failure
            else f"cleanup failed: {cleanup_failures}"
        )
    if failure:
        raise BenchmarkError(f"{mode} repetition {repetition}: {failure}")
    result["cleanup_passed"] = True
    return result


def aggregate_mode(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    def median(path: tuple[str, ...]) -> float:
        values: list[float] = []
        for item in rounds:
            value: Any = item
            for part in path:
                value = value[part]
            values.append(float(value))
        return round(float(statistics.median(values)), 6)

    return {
        "repetitions": len(rounds),
        "throughput_requests_per_second_median": median(
            ("workload", "throughput_requests_per_second")
        ),
        "p50_latency_ms_median": median(("workload", "p50_latency_ms")),
        "p99_latency_ms_median": median(("workload", "p99_latency_ms")),
        "redis_cpu_seconds_median": median(("redis_cpu_seconds",)),
        "redis_workload_cpu_seconds_median": median(("redis_workload_cpu_seconds",)),
        "redis_bgsave_cpu_seconds_median": median(("redis_bgsave_cpu_seconds",)),
        "redis_cpu_percent_of_one_core_median": median(
            ("redis_cpu_percent_of_one_core",)
        ),
        "redis_rss_sampled_peak_bytes_median": median(
            ("redis_rss_sampled_peak_bytes",)
        ),
        "redis_disk_write_bytes_median": median(("redis_disk_write_bytes",)),
        "redis_workload_disk_write_bytes_median": median(
            ("redis_workload_disk_write_bytes",)
        ),
        "redis_bgsave_disk_write_bytes_median": median(
            ("redis_bgsave_disk_write_bytes",)
        ),
        "redis_children_cpu_seconds_median": median(("redis_children_cpu_seconds",)),
        "redis_aof_delayed_fsync_total": sum(
            int(item["redis_aof_delayed_fsync"]) for item in rounds
        ),
    }


def percent_change(candidate: float, baseline: float) -> float | None:
    if baseline == 0:
        return None
    return round((candidate - baseline) / baseline * 100.0, 6)


def benchmark_persistence(
    *,
    benchmark_id: str,
    candidate_profile: Path,
    redis_image: str,
    summary_path: Path,
    repetitions: int = 3,
    requests: int = 10000,
    clients: int = 16,
    keyspace: int = 100000,
    sample_interval_seconds: float = 0.2,
    post_workload_settle_seconds: float = 1.2,
    workload_timeout_seconds: float = 120.0,
    active_volume: str = DEFAULT_ACTIVE_VOLUME,
    active_container: str = DEFAULT_ACTIVE_CONTAINER,
    policy_path: Path = DEFAULT_POLICY,
    lock_path: Path = DEFAULT_LOCK,
    docker: str = "/usr/bin/docker",
    runner: Runner = subprocess.run,
    starter: Starter = subprocess.Popen,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    host_platform: tuple[str, str] | None = None,
    identity: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_identifier(benchmark_id, "benchmark ID")
    if repetitions < 3:
        raise BenchmarkError("at least three repetitions per mode are required")
    if requests < 1000 or clients < 1 or keyspace < 1000:
        raise BenchmarkError("benchmark workload dimensions are too small")
    if keyspace < requests:
        raise BenchmarkError("benchmark random keyspace must cover all requests")
    if sample_interval_seconds <= 0 or sample_interval_seconds > 5:
        raise BenchmarkError("sample interval is invalid")
    if post_workload_settle_seconds < 1 or post_workload_settle_seconds > 5:
        raise BenchmarkError("post-workload settle interval is invalid")
    if workload_timeout_seconds <= 0 or workload_timeout_seconds > 300:
        raise BenchmarkError("workload timeout is invalid")
    if summary_path.exists() or summary_path.is_symlink():
        raise BenchmarkError("create-only benchmark summary already exists")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if summary_path.parent.is_symlink() or not summary_path.parent.is_dir():
        raise BenchmarkError("benchmark summary parent must be a non-symlink directory")
    system, machine = host_platform or (platform.system(), platform.machine())
    if system != "Linux" or machine not in {"x86_64", "amd64"}:
        raise BenchmarkError("benchmark execution requires Linux amd64")
    if identity is None:
        if str(Path(__file__).resolve().parents[2]) not in sys.path:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from scripts.lib.operations_identity import collect_operations_identity

        observed_identity = collect_operations_identity()
    else:
        observed_identity = identity
    identity_host = observed_identity.get("host")
    identity_operator = observed_identity.get("operator")
    if not isinstance(identity_host, dict) or not isinstance(identity_operator, dict):
        raise BenchmarkError("operations identity is incomplete")
    assert_local_docker(runner, docker)
    baseline_config, candidate_config = load_candidate_profile(candidate_profile)
    candidate_profile_sha256 = sha256_bytes(candidate_config)
    policy_binding = load_policy_binding(policy_path, candidate_profile_sha256)
    controller = provenance or collect_controller_provenance()
    if (
        COMMIT_RE.fullmatch(str(controller.get("commit", ""))) is None
        or controller.get("ref") != "main"
        or controller.get("worktree_clean") is not True
        or SHA256_RE.fullmatch(str(controller.get("runner_sha256", ""))) is None
    ):
        raise BenchmarkError("controller provenance is incomplete")
    image_state = inspect_image(runner, docker, redis_image)
    active_before = ""
    started_at = now()
    rounds: list[dict[str, Any]] = []
    failure = ""
    active_after = ""
    leftover_volumes: list[str] = []
    leftover_containers: list[str] = []
    leftover_networks: list[str] = []
    with backup.lifecycle_lock(lock_path), tempfile.TemporaryDirectory(
        prefix=f".{benchmark_id}.", dir=summary_path.parent
    ) as temporary_text:
        temporary = Path(temporary_text)
        os.chmod(temporary, 0o700)
        configs = {
            "rdb_only": temporary / "redis-rdb-only.conf",
            "aof_everysec_rdb": temporary / "redis-aof-everysec-rdb.conf",
        }
        write_config(configs["rdb_only"], baseline_config)
        write_config(configs["aof_everysec_rdb"], candidate_config)
        config_digests = {
            "rdb_only": sha256_bytes(baseline_config),
            "aof_everysec_rdb": sha256_bytes(candidate_config),
        }
        active_runtime = inspect_active_redis(
            runner, docker, active_container, redis_image, active_volume
        )
        active_before = volume_identity(inspect_volume(runner, docker, active_volume))
        try:
            for repetition in range(1, repetitions + 1):
                modes = (
                    ("rdb_only", "aof_everysec_rdb")
                    if repetition % 2
                    else ("aof_everysec_rdb", "rdb_only")
                )
                for mode in modes:
                    rounds.append(
                        execute_round(
                            benchmark_id=benchmark_id,
                            mode=mode,
                            repetition=repetition,
                            config_path=configs[mode],
                            config_sha256=config_digests[mode],
                            image=redis_image,
                            active_volume=active_volume,
                            requests=requests,
                            clients=clients,
                            keyspace=keyspace,
                            sample_interval_seconds=sample_interval_seconds,
                            post_workload_settle_seconds=post_workload_settle_seconds,
                            workload_timeout_seconds=workload_timeout_seconds,
                            docker=docker,
                            runner=runner,
                            starter=starter,
                            monotonic=monotonic,
                            sleeper=sleeper,
                        )
                    )
        except Exception as exc:
            failure = str(exc)
        try:
            active_after = volume_identity(
                inspect_volume(runner, docker, active_volume)
            )
        except Exception as exc:
            failure = (
                f"{failure}; cannot recheck active volume: {exc}"
                if failure
                else str(exc)
            )
        if active_after and active_after != active_before:
            failure = (
                f"{failure}; active Redis volume identity changed"
                if failure
                else "active Redis volume identity changed"
            )
        volume_prefix = f"boost-gateway-benchmark-{benchmark_id}-"
        container_prefix = f"boost-redis-benchmark-{benchmark_id}-"
        client_container_prefix = f"boost-redis-benchmark-client-{benchmark_id}-"
        network_prefix = f"boost-gateway-benchmark-net-{benchmark_id}-"
        try:
            leftover_volumes = [
                name
                for name in docker_text(
                    runner,
                    [docker, "volume", "ls", "--format", "{{.Name}}"],
                    timeout=30,
                ).splitlines()
                if name.startswith(volume_prefix)
            ]
            leftover_containers = [
                name
                for name in docker_text(
                    runner,
                    [docker, "ps", "-a", "--format", "{{.Names}}"],
                    timeout=30,
                ).splitlines()
                if name.startswith(container_prefix)
                or name.startswith(client_container_prefix)
            ]
            leftover_networks = [
                name
                for name in docker_text(
                    runner,
                    [docker, "network", "ls", "--format", "{{.Name}}"],
                    timeout=30,
                ).splitlines()
                if name.startswith(network_prefix)
            ]
        except Exception as exc:
            failure = (
                f"{failure}; cannot audit temporary cleanup: {exc}"
                if failure
                else f"cannot audit temporary cleanup: {exc}"
            )
        if leftover_volumes or leftover_containers or leftover_networks:
            detail = (
                f"temporary targets remain: volumes={leftover_volumes} "
                f"containers={leftover_containers} networks={leftover_networks}"
            )
            failure = f"{failure}; {detail}" if failure else detail

    modes = {
        mode: [item for item in rounds if item["mode"] == mode]
        for mode in ("rdb_only", "aof_everysec_rdb")
    }
    complete = all(len(items) == repetitions for items in modes.values())
    if not complete and not failure:
        failure = "benchmark repetition set is incomplete"
    aggregates = {
        mode: aggregate_mode(items) if items else {} for mode, items in modes.items()
    }
    baseline = aggregates["rdb_only"]
    candidate = aggregates["aof_everysec_rdb"]
    impact = (
        {
            "throughput_percent": percent_change(
                candidate["throughput_requests_per_second_median"],
                baseline["throughput_requests_per_second_median"],
            ),
            "p50_latency_percent": percent_change(
                candidate["p50_latency_ms_median"], baseline["p50_latency_ms_median"]
            ),
            "p99_latency_percent": percent_change(
                candidate["p99_latency_ms_median"], baseline["p99_latency_ms_median"]
            ),
            "redis_cpu_percent": percent_change(
                candidate["redis_cpu_seconds_median"],
                baseline["redis_cpu_seconds_median"],
            ),
            "redis_workload_cpu_percent": percent_change(
                candidate["redis_workload_cpu_seconds_median"],
                baseline["redis_workload_cpu_seconds_median"],
            ),
            "redis_bgsave_cpu_percent": percent_change(
                candidate["redis_bgsave_cpu_seconds_median"],
                baseline["redis_bgsave_cpu_seconds_median"],
            ),
            "redis_children_cpu_percent": percent_change(
                candidate["redis_children_cpu_seconds_median"],
                baseline["redis_children_cpu_seconds_median"],
            ),
            "redis_rss_percent": percent_change(
                candidate["redis_rss_sampled_peak_bytes_median"],
                baseline["redis_rss_sampled_peak_bytes_median"],
            ),
            "redis_disk_write_bytes_percent": percent_change(
                candidate["redis_disk_write_bytes_median"],
                baseline["redis_disk_write_bytes_median"],
            ),
            "redis_workload_disk_write_bytes_percent": percent_change(
                candidate["redis_workload_disk_write_bytes_median"],
                baseline["redis_workload_disk_write_bytes_median"],
            ),
            "redis_bgsave_disk_write_bytes_percent": percent_change(
                candidate["redis_bgsave_disk_write_bytes_median"],
                baseline["redis_bgsave_disk_write_bytes_median"],
            ),
        }
        if complete
        else {}
    )
    overall_pass = complete and not failure
    summary = {
        "schema_version": 1,
        "benchmark_id": benchmark_id,
        "generated_at": now(),
        "started_at": started_at,
        "overall_pass": overall_pass,
        "status": "passed" if overall_pass else "failed",
        "failure": failure,
        "host": {**identity_host, "system": system, "architecture": machine},
        "operator": identity_operator,
        "controller": controller,
        "policy": policy_binding,
        "redis_image": {
            "id": redis_image,
            "architecture": image_state.get("Architecture"),
            "os": image_state.get("Os"),
        },
        "candidate_profile": {
            "path": str(candidate_profile.resolve()),
            "sha256": candidate_profile_sha256,
        },
        "derived_rdb_only_profile_sha256": sha256_bytes(baseline_config),
        "workload": {
            "contract": "lua:zadd+hset+zrevrange+zrevrank:v1",
            "requests_per_repetition": requests,
            "clients": clients,
            "random_keyspace": keyspace,
            "repetitions_per_mode": repetitions,
            "sample_interval_seconds": sample_interval_seconds,
            "post_workload_settle_seconds": post_workload_settle_seconds,
            "workload_timeout_seconds": workload_timeout_seconds,
            "execution_order": [
                {"mode": item["mode"], "repetition": item["repetition"]}
                for item in rounds
            ],
        },
        "rounds": rounds,
        "aggregates": aggregates,
        "candidate_impact_percent": impact,
        "active_volume": active_volume,
        "active_redis_runtime": {
            "container": active_container,
            "container_id": active_runtime.get("Id"),
            "image_id": active_runtime.get("Image"),
            "health": active_runtime.get("State", {}).get("Health", {}).get("Status"),
            "data_volume": active_volume,
            "data_volume_read_write": True,
        },
        "active_volume_identity_before_sha256": active_before,
        "active_volume_identity_after_sha256": active_after,
        "active_volume_mounted_by_benchmark": False,
        "temporary_targets_cleaned": not leftover_volumes
        and not leftover_containers
        and not leftover_networks,
        "leftover_temporary_volumes": leftover_volumes,
        "leftover_temporary_containers": leftover_containers,
        "leftover_temporary_networks": leftover_networks,
        "measurement_complete": overall_pass,
        "performance_review_required": True,
        "governed_change_record_required": True,
        "rollback_plan_required": True,
        "activation_ready": False,
        "production_compose_changed": False,
        "systemd_changed": False,
        "timer_changed": False,
        "formal_todo0012_claim": False,
        "secret_material_recorded": False,
    }
    try:
        backup.write_new(summary_path, canonical_json(summary), 0o640)
    except (backup.BackupError, OSError) as exc:
        raise BenchmarkError(
            f"cannot write create-only benchmark summary: {exc}"
        ) from exc
    if not overall_pass:
        raise BenchmarkError(f"Redis persistence benchmark failed: {failure}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-id", default="")
    parser.add_argument("--candidate-profile", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--redis-image", required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--requests", type=int, default=10000)
    parser.add_argument("--clients", type=int, default=16)
    parser.add_argument("--keyspace", type=int, default=100000)
    parser.add_argument("--sample-interval-seconds", type=float, default=0.2)
    parser.add_argument("--post-workload-settle-seconds", type=float, default=1.2)
    parser.add_argument("--workload-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--active-volume", default=DEFAULT_ACTIVE_VOLUME)
    parser.add_argument("--active-container", default=DEFAULT_ACTIVE_CONTAINER)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--docker", default="/usr/bin/docker")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    benchmark_id = args.benchmark_id or (
        f"redis-persistence-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    )
    try:
        summary = benchmark_persistence(
            benchmark_id=benchmark_id,
            candidate_profile=args.candidate_profile,
            policy_path=args.policy,
            redis_image=args.redis_image,
            summary_path=args.summary_path,
            repetitions=args.repetitions,
            requests=args.requests,
            clients=args.clients,
            keyspace=args.keyspace,
            sample_interval_seconds=args.sample_interval_seconds,
            post_workload_settle_seconds=args.post_workload_settle_seconds,
            workload_timeout_seconds=args.workload_timeout_seconds,
            active_volume=args.active_volume,
            active_container=args.active_container,
            lock_path=args.lock_path,
            docker=args.docker,
        )
    except BenchmarkError as exc:
        print(f"Redis persistence benchmark: FAIL: {exc}", file=sys.stderr)
        return 1
    print("Redis persistence benchmark: PASS")
    print(f"summary: {args.summary_path}")
    print(json.dumps(summary["candidate_impact_percent"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
