#!/usr/bin/env python3
"""Run release business checks against a disposable clone of a restored Redis volume."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from scripts.lib import backup_recovery as backup  # noqa: E402
    from scripts.lib import isolated_restore as restore  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.lib import backup_recovery as backup  # type: ignore[no-redef]  # noqa: E402
    from scripts.lib import isolated_restore as restore  # type: ignore[no-redef]  # noqa: E402


Runner = Callable[..., subprocess.CompletedProcess[Any]]
IMAGE_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
DOCKER_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
DEFAULT_LOCK = Path("/var/lib/boost-gateway/deployment-transactions/.lifecycle.lock")
DEFAULT_ACTIVE_VOLUME = "boost-gateway-production-redis-data"
IMAGE_KEYS = {
    "gateway": "GATEWAY_IMAGE_ID",
    "login": "LOGIN_IMAGE_ID",
    "room": "ROOM_IMAGE_ID",
    "battle": "BATTLE_IMAGE_ID",
    "matchmaking": "MATCHMAKING_IMAGE_ID",
    "leaderboard": "LEADERBOARD_IMAGE_ID",
}
BACKENDS = {
    "login": ("login-backend", "9202", {}),
    "room": ("room-backend", "9302", {}),
    "battle": ("battle-backend", "9303", {}),
    "matchmaking": ("matchmaking-backend", "9304", {}),
    "leaderboard": (
        "leaderboard-backend",
        "9305",
        {"REDIS_HOST": "redis", "REDIS_PORT": "6379"},
    ),
}
SDK_OUTPUT = (
    "Both connected.",
    "Manual leaderboard submit path OK.",
    "Leaderboard rank query path OK.",
    "Both left room.",
    "=== ALL TESTS PASSED ===",
)


class BusinessValidationError(RuntimeError):
    """Raised when isolated business verification cannot preserve its boundary."""


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BusinessValidationError(f"{label} must be a regular non-symlink file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BusinessValidationError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise BusinessValidationError(f"{label} must be a JSON object")
    return value


def validate_id(value: str, label: str) -> str:
    if ID_RE.fullmatch(value) is None or value.startswith("."):
        raise BusinessValidationError(f"{label} is invalid")
    return value


def checked(
    runner: Runner, command: list[str], **kwargs: Any
) -> subprocess.CompletedProcess[Any]:
    try:
        completed = runner(command, check=False, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BusinessValidationError(f"command failed ({command[0]}): {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr
        stdout = completed.stdout
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        detail = str(stderr or stdout or "").strip()[-1000:]
        raise BusinessValidationError(f"command failed ({command[0]}): {detail}")
    return completed


def docker_text(
    runner: Runner, docker: str, arguments: list[str], *, timeout: int = 60
) -> str:
    completed = checked(
        runner,
        [docker, *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return completed.stdout.strip()


def load_release_context(
    restore_summary_path: Path,
    deployment_record_path: Path,
    release_dir: Path,
    retained_volume: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str], Path]:
    summary = load_json(restore_summary_path, "isolated restore summary")
    if (
        summary.get("schema_version") != 1
        or summary.get("overall_pass") is not True
        or summary.get("status") != "passed"
        or summary.get("target_volume") != retained_volume
        or summary.get("target_volume_retained") is not True
        or summary.get("leaderboard_seed_exact") is not True
        or summary.get("redis_ping") is not True
        or summary.get("production_switched") is not False
        or summary.get("active_volume_mounted_by_drill") is not False
        or summary.get("restore_known_good") is not False
        or summary.get("formal_todo0012_claim") is not False
        or SHA256_RE.fullmatch(str(summary.get("active_volume_identity_sha256", "")))
        is None
        or SHA256_RE.fullmatch(str(summary.get("target_volume_identity_sha256", "")))
        is None
        or SHA256_RE.fullmatch(str(summary.get("canonical_seed_restored_sha256", "")))
        is None
        or SHA256_RE.fullmatch(str(summary.get("redis_snapshot_sha256", ""))) is None
        or IMAGE_RE.fullmatch(str(summary.get("redis_image", ""))) is None
        or not isinstance(summary.get("canonical_seed_key_count"), int)
        or isinstance(summary.get("canonical_seed_key_count"), bool)
        or summary.get("canonical_seed_key_count", 0) <= 0
    ):
        raise BusinessValidationError(
            "restore summary is not an eligible retained seed"
        )
    validate_id(str(summary.get("restore_id", "")), "restore ID")

    record = load_json(deployment_record_path, "deployment record")
    deployment_id = validate_id(str(record.get("deployment_id", "")), "deployment ID")
    summary_deployment = summary.get("deployment")
    if (
        not isinstance(summary_deployment, dict)
        or summary_deployment.get("deployment_id") != deployment_id
        or summary_deployment.get("tag") != record.get("tag")
        or summary_deployment.get("commit") != record.get("commit")
        or summary_deployment.get("runtime_asset_sha256")
        != record.get("runtime_asset_sha256")
        or record.get("status") != "verified"
    ):
        raise BusinessValidationError("restore and deployment identities differ")
    resolved_release = release_dir.resolve(strict=True)
    if (
        release_dir.is_symlink()
        or not resolved_release.is_dir()
        or record.get("release_path") != str(resolved_release)
    ):
        raise BusinessValidationError("release directory binding differs")
    manifest = load_json(resolved_release / "manifest.json", "release manifest")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("tag") != record.get("tag")
        or manifest.get("commit") != record.get("commit")
        or COMMIT_RE.fullmatch(str(manifest.get("commit", ""))) is None
        or manifest.get("platform") != "linux-x64"
        or manifest.get("source_build_performed") is not False
    ):
        raise BusinessValidationError("release manifest identity differs")
    client = resolved_release / "bin/sdk_full_flow_client"
    if client.is_symlink() or not client.is_file() or not os.access(client, os.X_OK):
        raise BusinessValidationError("release SDK full-flow client is unavailable")
    binaries = manifest.get("binaries")
    client_records = (
        [
            item
            for item in binaries
            if isinstance(item, dict) and item.get("name") == client.name
        ]
        if isinstance(binaries, list)
        else []
    )
    if len(client_records) != 1 or client_records[0].get("sha256") != sha256_file(
        client
    ):
        raise BusinessValidationError("release SDK full-flow client digest differs")
    image_ids = record.get("image_ids")
    if not isinstance(image_ids, dict):
        raise BusinessValidationError("deployment image identity is incomplete")
    images = {
        service: str(image_ids.get(key, "")) for service, key in IMAGE_KEYS.items()
    }
    if any(IMAGE_RE.fullmatch(image) is None for image in images.values()):
        raise BusinessValidationError("deployment image identity is invalid")
    return summary, record, manifest, images, client


def assert_image_ids(runner: Runner, docker: str, images: list[str]) -> None:
    for image in images:
        document = docker_text(runner, docker, ["image", "inspect", image], timeout=30)
        try:
            values = json.loads(document)
        except json.JSONDecodeError as exc:
            raise BusinessValidationError("Docker image inspection is invalid") from exc
        if (
            not isinstance(values, list)
            or len(values) != 1
            or not isinstance(values[0], dict)
            or values[0].get("Id") != image
        ):
            raise BusinessValidationError(f"Docker image ID differs: {image}")


def ensure_unused_volume(runner: Runner, docker: str, volume: str) -> dict[str, Any]:
    users = docker_text(
        runner, docker, ["ps", "-aq", "--filter", f"volume={volume}"], timeout=30
    )
    if users:
        raise BusinessValidationError(
            f"retained volume is referenced by a container: {volume}"
        )
    return restore.inspect_volume(runner, docker, volume)


def ensure_absent(runner: Runner, docker: str, kind: str, name: str) -> None:
    if kind == "volume":
        names = docker_text(runner, docker, ["volume", "ls", "--format", "{{.Name}}"])
    elif kind == "network":
        names = docker_text(runner, docker, ["network", "ls", "--format", "{{.Name}}"])
    else:
        names = docker_text(runner, docker, ["ps", "-a", "--format", "{{.Names}}"])
    if name in names.splitlines():
        raise BusinessValidationError(f"create-only {kind} already exists: {name}")


def inspect_internal_network(
    runner: Runner,
    docker: str,
    network: str,
    business_id: str,
    expected_members: set[str] | None = None,
) -> tuple[dict[str, Any], ipaddress.IPv4Network]:
    output = docker_text(runner, docker, ["network", "inspect", network], timeout=30)
    try:
        values = json.loads(output)
    except json.JSONDecodeError as exc:
        raise BusinessValidationError("Docker network inspection is invalid") from exc
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        raise BusinessValidationError("Docker network inspection is incomplete")
    value = values[0]
    labels = value.get("Labels")
    network_id = value.get("Id")
    ipam = value.get("IPAM")
    configs = ipam.get("Config") if isinstance(ipam, dict) else None
    if not isinstance(configs, list) or len(configs) != 1:
        raise BusinessValidationError("isolated network IPv4 IPAM is incomplete")
    subnet_raw = configs[0].get("Subnet") if isinstance(configs[0], dict) else None
    try:
        subnet = ipaddress.ip_network(subnet_raw, strict=False)
    except (TypeError, ValueError) as exc:
        raise BusinessValidationError(
            "isolated network IPv4 subnet is invalid"
        ) from exc
    members = value.get("Containers")
    if not isinstance(members, dict):
        raise BusinessValidationError("isolated network members are invalid")
    member_names = {
        member.get("Name")
        for member in members.values()
        if isinstance(member, dict) and isinstance(member.get("Name"), str)
    }
    if (
        value.get("Name") != network
        or value.get("Driver") != "bridge"
        or value.get("Internal") is not True
        or DOCKER_ID_RE.fullmatch(network_id or "") is None
        or not isinstance(subnet, ipaddress.IPv4Network)
        or not isinstance(labels, dict)
        or labels.get("boost-gateway.todo") != "TODO-0012"
        or labels.get("boost-gateway.business-id") != business_id
    ):
        raise BusinessValidationError("isolated internal network binding differs")
    if len(member_names) != len(members):
        raise BusinessValidationError("isolated network member identity is incomplete")
    if expected_members is None:
        if members:
            raise BusinessValidationError("new isolated network is not empty")
    elif member_names != expected_members:
        raise BusinessValidationError("isolated network member set differs")
    return value, subnet


def assert_local_docker(runner: Runner, docker: str) -> None:
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
        raise BusinessValidationError(
            "remote or overridden Docker endpoint is forbidden"
        )
    context = docker_text(runner, docker, ["context", "show"], timeout=30)
    endpoint = docker_text(
        runner,
        docker,
        [
            "context",
            "inspect",
            "--format",
            "{{.Endpoints.docker.Host}}",
            "default",
        ],
        timeout=30,
    )
    if context != "default" or endpoint != "unix:///var/run/docker.sock":
        raise BusinessValidationError("Docker endpoint is not the local system socket")


def clone_retained_volume(
    runner: Runner,
    docker: str,
    redis_image: str,
    retained_volume: str,
    work_volume: str,
) -> str:
    command = [
        docker,
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "redis",
        "--mount",
        f"type=volume,src={retained_volume},dst=/source,readonly",
        "--mount",
        f"type=volume,src={work_volume},dst=/data",
        "--entrypoint",
        "sh",
        redis_image,
        "-eu",
        "-c",
        (
            "umask 077; test -s /source/dump.rdb; test ! -e /data/dump.rdb; "
            "cat /source/dump.rdb > /data/dump.rdb; chmod 0600 /data/dump.rdb; "
            "test \"$(sha256sum /source/dump.rdb | cut -d' ' -f1)\" = "
            "\"$(sha256sum /data/dump.rdb | cut -d' ' -f1)\"; "
            "sha256sum /data/dump.rdb"
        ),
    ]
    completed = checked(
        runner,
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="ascii",
        errors="strict",
        timeout=300,
    )
    digest = completed.stdout.split()[0] if completed.stdout.split() else ""
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise BusinessValidationError("cloned Redis snapshot digest is invalid")
    return digest


def base_container_command(
    docker: str,
    name: str,
    network: str,
    alias: str,
    image: str,
    business_id: str,
) -> list[str]:
    return [
        docker,
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "--network-alias",
        alias,
        "--pull",
        "never",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--label",
        "boost-gateway.todo=TODO-0012",
        "--label",
        f"boost-gateway.business-id={business_id}",
        "--tmpfs",
        "/app/logs:rw,noexec,nosuid,size=16m",
        "--tmpfs",
        "/app/runtime:rw,noexec,nosuid,size=32m",
        image,
    ]


def start_redis_networked(
    runner: Runner,
    docker: str,
    image: str,
    name: str,
    network: str,
    volume: str,
    business_id: str,
    on_started: Callable[[str], None] | None = None,
) -> None:
    command = [
        docker,
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "--network-alias",
        "redis",
        "--pull",
        "never",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--label",
        "boost-gateway.todo=TODO-0012",
        "--label",
        f"boost-gateway.business-id={business_id}",
        "--user",
        "redis",
        "--mount",
        f"type=volume,src={volume},dst=/data",
        "--entrypoint",
        "redis-server",
        image,
        "--appendonly",
        "no",
        "--save",
        "",
        "--protected-mode",
        "no",
    ]
    checked(runner, command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if on_started is not None:
        on_started(name)
    restore.ping_redis(runner, docker, name)


def start_backend(
    runner: Runner,
    docker: str,
    image: str,
    name: str,
    network: str,
    alias: str,
    port: str,
    environment: dict[str, str],
    business_id: str,
    on_started: Callable[[str], None] | None = None,
) -> None:
    command = base_container_command(docker, name, network, alias, image, business_id)
    image_index = command.index(image)
    values = {
        "CONFIG_PATH": f"/app/config/environments/docker/{alias.removesuffix('-backend')}.json",
        "SERVICE_PORT": port,
        "BOOST_LOG_LEVEL": "info",
        **environment,
    }
    arguments: list[str] = []
    for key, value in sorted(values.items()):
        arguments.extend(["--env", f"{key}={value}"])
    command[image_index:image_index] = arguments
    checked(runner, command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if on_started is not None:
        on_started(name)
    wait_healthy(runner, docker, name, 60.0)


def start_gateway(
    runner: Runner,
    docker: str,
    image: str,
    name: str,
    network: str,
    business_id: str,
    network_state: dict[str, Any],
    network_subnet: ipaddress.IPv4Network,
    on_started: Callable[[str], None] | None = None,
) -> str:
    command = base_container_command(
        docker, name, network, "gateway", image, business_id
    )
    image_index = command.index(image)
    command[image_index:image_index] = [
        "--tmpfs",
        "/app/v2_archive:rw,noexec,nosuid,size=32m",
        "--env",
        "CONFIG_PATH=/app/config/environments/docker/gateway.json",
        "--env",
        "MANAGEMENT_PORT=9080",
        "--env",
        "BOOST_LOG_LEVEL=info",
    ]
    command.extend(
        [
            "--http-port",
            "9080",
            "--login-host",
            "login-backend",
            "--login-port",
            "9202",
            "--room-host",
            "room-backend",
            "--room-port",
            "9302",
            "--battle-host",
            "battle-backend",
            "--battle-port",
            "9303",
            "--matchmaking-host",
            "matchmaking-backend",
            "--matchmaking-port",
            "9304",
            "--leaderboard-host",
            "leaderboard-backend",
            "--leaderboard-port",
            "9305",
        ]
    )
    checked(runner, command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if on_started is not None:
        on_started(name)
    wait_healthy(runner, docker, name, 90.0)
    inspection_raw = docker_text(runner, docker, ["inspect", name], timeout=30)
    try:
        inspections = json.loads(inspection_raw)
    except json.JSONDecodeError as exc:
        raise BusinessValidationError("isolated gateway inspection is invalid") from exc
    if (
        not isinstance(inspections, list)
        or len(inspections) != 1
        or not isinstance(inspections[0], dict)
    ):
        raise BusinessValidationError("isolated gateway inspection is incomplete")
    inspection = inspections[0]
    state = inspection.get("State")
    health = state.get("Health") if isinstance(state, dict) else None
    config = inspection.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    host_config = inspection.get("HostConfig")
    port_bindings = (
        host_config.get("PortBindings") if isinstance(host_config, dict) else None
    )
    network_settings = inspection.get("NetworkSettings")
    attachments = (
        network_settings.get("Networks") if isinstance(network_settings, dict) else None
    )
    ports = (
        network_settings.get("Ports") if isinstance(network_settings, dict) else None
    )
    if (
        inspection.get("Image") != image
        or not isinstance(state, dict)
        or state.get("Running") is not True
        or not isinstance(health, dict)
        or health.get("Status") != "healthy"
        or not isinstance(labels, dict)
        or labels.get("boost-gateway.todo") != "TODO-0012"
        or labels.get("boost-gateway.business-id") != business_id
        or port_bindings not in (None, {})
        or not isinstance(ports, dict)
        or any(value not in (None, []) for value in ports.values())
        or not isinstance(attachments, dict)
        or set(attachments) != {network}
    ):
        raise BusinessValidationError("isolated gateway runtime binding differs")
    attachment = attachments[network]
    address_raw = attachment.get("IPAddress") if isinstance(attachment, dict) else None
    try:
        address = ipaddress.ip_address(address_raw)
    except (TypeError, ValueError) as exc:
        raise BusinessValidationError(
            "isolated gateway IPv4 address is invalid"
        ) from exc
    if (
        not isinstance(address, ipaddress.IPv4Address)
        or address.is_unspecified
        or address.is_loopback
        or address.is_multicast
        or address not in network_subnet
        or not isinstance(attachment, dict)
        or attachment.get("NetworkID") != network_state.get("Id")
    ):
        raise BusinessValidationError("isolated gateway IPv4 address is unsafe")
    return str(address)


def wait_healthy(
    runner: Runner, docker: str, container: str, timeout_seconds: float
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status = "unknown"
    while time.monotonic() < deadline:
        completed = runner(
            [docker, "inspect", "--format", "{{.State.Health.Status}}", container],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
        if completed.returncode == 0:
            last_status = completed.stdout.strip() or "empty"
        if completed.returncode == 0 and last_status == "healthy":
            return
        if completed.returncode == 0 and last_status == "unhealthy":
            break
        time.sleep(0.25)
    details: list[str] = []
    for label, command in (
        (
            "state",
            [docker, "inspect", "--format", "{{json .State}}", container],
        ),
        ("logs", [docker, "logs", "--tail", "100", container]),
    ):
        try:
            diagnostic = runner(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=10,
            )
            text = (diagnostic.stdout or "").strip()[-2000:]
            details.append(f"{label}={text or '<empty>'}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            details.append(f"{label}=unavailable:{exc}")
    raise BusinessValidationError(
        f"isolated container did not become healthy: {container}; "
        f"health={last_status}; {'; '.join(details)}"
    )


def run_sdk(
    client: Path, gateway_host: str, port: int, timeout_seconds: int
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(client), gateway_host, str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BusinessValidationError(f"release SDK full-flow failed: {exc}") from exc
    missing = [fragment for fragment in SDK_OUTPUT if fragment not in completed.stdout]
    if completed.returncode != 0 or missing:
        raise BusinessValidationError(
            "release SDK full-flow rejected: "
            f"exit={completed.returncode} missing={missing} stderr={completed.stderr[-500:]}"
        )
    alice = re.findall(r"Alice logged in as: (alice_[0-9]+)", completed.stdout)
    bob = re.findall(r"Bob logged in as: (bob_[0-9]+)", completed.stdout)
    if len(alice) != 1 or len(bob) != 1:
        raise BusinessValidationError(
            "release SDK full-flow user evidence is incomplete"
        )
    return {
        "exit_code": completed.returncode,
        "alice_user_id": alice[0],
        "bob_user_id": bob[0],
        "stdout_tail": completed.stdout[-8000:],
        "stderr_tail": completed.stderr[-2000:],
        "source_build_performed": False,
    }


def verify_leaderboard_effects(
    runner: Runner,
    docker: str,
    redis_container: str,
    alice_user_id: str,
    bob_user_id: str,
) -> dict[str, Any]:
    users = [alice_user_id, bob_user_id]
    top = restore.redis_json(
        runner, docker, redis_container, "ZREVRANGE", "lb:global", "0", "19"
    )
    if not isinstance(top, list) or any(not isinstance(item, str) for item in top):
        raise BusinessValidationError("leaderboard top response is invalid")
    ranks: dict[str, int] = {}
    for user, expected_name in zip(users, ("Alice", "Bob"), strict=True):
        score = restore.redis_json(
            runner, docker, redis_container, "ZSCORE", "lb:global", user
        )
        rank = restore.redis_json(
            runner, docker, redis_container, "ZREVRANK", "lb:global", user
        )
        name = restore.redis_json(
            runner, docker, redis_container, "HGET", "lb:global:names", user
        )
        try:
            score_value = Decimal(str(score))
            valid_score = (
                score is not None
                and not isinstance(score, bool)
                and score_value.is_finite()
            )
        except InvalidOperation:
            valid_score = False
        if (
            not valid_score
            or not isinstance(rank, int)
            or isinstance(rank, bool)
            or name != expected_name
        ):
            raise BusinessValidationError(f"leaderboard submit/rank differs: {user}")
        ranks[user] = rank + 1
    users_in_top = [user for user in users if user in top]
    return {
        "submitted_users": users,
        "one_based_ranks": ranks,
        "top_20": top,
        "submitted_users_in_top_20": users_in_top,
        "submitted_users_all_in_top_20": len(users_in_top) == len(users),
        "leaderboard_submit": True,
        "leaderboard_top": True,
        "leaderboard_rank": True,
    }


def remove_resource(runner: Runner, docker: str, kind: str, name: str) -> bool:
    command = (
        [docker, "rm", "-f", name]
        if kind == "container"
        else [docker, kind, "rm", name]
    )
    try:
        completed = runner(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def audit_retained_seed(
    runner: Runner,
    docker: str,
    redis_image: str,
    retained_volume: str,
    container: str,
) -> tuple[str, int, set[str]]:
    restore.start_redis(
        runner,
        docker,
        redis_image,
        container,
        f"type=volume,src={retained_volume},dst=/data,readonly",
        user="redis",
    )
    try:
        restore.ping_redis(runner, docker, container)
        return restore.canonical_keyspace(runner, docker, container)
    finally:
        if not remove_resource(runner, docker, "container", container):
            raise BusinessValidationError("cannot remove retained seed audit container")


def run_business_validation(
    *,
    business_id: str,
    restore_summary_path: Path,
    deployment_record_path: Path,
    release_dir: Path,
    retained_volume: str,
    work_volume: str,
    network: str,
    redis_image: str,
    summary_path: Path,
    active_volume: str = DEFAULT_ACTIVE_VOLUME,
    lock_path: Path = DEFAULT_LOCK,
    docker: str = "/usr/bin/docker",
    rto_seconds: float = 300.0,
    sdk_timeout_seconds: int = 180,
    runner: Runner = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    identifier = validate_id(business_id, "business validation ID")
    for value, label in (
        (retained_volume, "retained volume"),
        (work_volume, "work volume"),
        (network, "network"),
        (active_volume, "active volume"),
    ):
        validate_id(value, label)
    if len({retained_volume, work_volume, active_volume}) != 3:
        raise BusinessValidationError(
            "active, retained and work volumes must be distinct"
        )
    if IMAGE_RE.fullmatch(redis_image) is None:
        raise BusinessValidationError("Redis image must use an immutable image ID")
    if rto_seconds <= 0 or rto_seconds > 300:
        raise BusinessValidationError("business RTO budget must be within 300 seconds")
    if sdk_timeout_seconds <= 0 or sdk_timeout_seconds > 240:
        raise BusinessValidationError("SDK timeout must be within 240 seconds")
    if summary_path.exists() or summary_path.is_symlink():
        raise BusinessValidationError("create-only business summary already exists")
    summary_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)

    summary_source, record, _manifest, images, client = load_release_context(
        restore_summary_path,
        deployment_record_path,
        release_dir,
        retained_volume,
    )
    if redis_image != summary_source.get("redis_image"):
        raise BusinessValidationError("Redis image differs from restore summary")
    prefix = f"boost-business-{identifier}"
    containers = {
        service: f"{prefix}-{service}"
        for service in (*BACKENDS.keys(), "redis", "gateway")
    }
    audit_before = f"{prefix}-audit-before"
    audit_after = f"{prefix}-audit-after"
    started = monotonic()
    started_at = backup.now()
    active_identity_before = ""
    active_identity_after = ""
    retained_identity_before = ""
    retained_identity_after = ""
    retained_seed_before = ""
    retained_seed_after = ""
    retained_key_count = 0
    work_seed_before = ""
    work_seed_after = ""
    work_snapshot_sha = ""
    work_volume_identity = ""
    gateway_host = ""
    gateway_ports_published = False
    gateway_runtime_binding_verified = False
    network_id = ""
    network_ipv4_subnet = ""
    sdk: dict[str, Any] = {}
    leaderboard: dict[str, Any] = {}
    created_containers: list[str] = []
    work_created = False
    work_created_once = False
    network_created = False
    network_created_once = False
    retained_audit_before_passed = False
    retained_audit_after_passed = False
    cleanup_failures: list[str] = []
    error = ""
    success = False

    try:
        with backup.lifecycle_lock(lock_path):
            assert_local_docker(runner, docker)
            assert_image_ids(runner, docker, [*images.values(), redis_image])
            active_identity_before = restore.volume_identity(
                restore.inspect_volume(runner, docker, active_volume)
            )
            if active_identity_before != summary_source.get(
                "active_volume_identity_sha256"
            ):
                raise BusinessValidationError(
                    "active volume identity differs from restore summary"
                )
            retained_state = ensure_unused_volume(runner, docker, retained_volume)
            retained_identity_before = restore.volume_identity(retained_state)
            if retained_identity_before != summary_source.get(
                "target_volume_identity_sha256"
            ):
                raise BusinessValidationError(
                    "retained volume identity differs from restore summary"
                )
            labels = retained_state.get("Labels")
            if not isinstance(labels, dict) or labels.get(
                "boost-gateway.restore-id"
            ) != summary_source.get("restore_id"):
                raise BusinessValidationError("retained volume label binding differs")
            ensure_absent(runner, docker, "volume", work_volume)
            ensure_absent(runner, docker, "network", network)
            for name in (*containers.values(), audit_before, audit_after):
                ensure_absent(runner, docker, "container", name)

            retained_seed_before, retained_key_count, retained_keys = (
                audit_retained_seed(
                    runner,
                    docker,
                    redis_image,
                    retained_volume,
                    audit_before,
                )
            )
            retained_audit_before_passed = True
            if retained_seed_before != summary_source.get(
                "canonical_seed_restored_sha256"
            ) or retained_key_count != summary_source.get("canonical_seed_key_count"):
                raise BusinessValidationError(
                    "retained seed differs from restore summary"
                )
            required = {"lb:global", "lb:global:names"}
            if not required <= retained_keys:
                raise BusinessValidationError(
                    "retained volume lacks leaderboard seed keys"
                )

            checked(
                runner,
                [
                    docker,
                    "volume",
                    "create",
                    "--label",
                    "boost-gateway.todo=TODO-0012",
                    "--label",
                    f"boost-gateway.business-id={identifier}",
                    "--label",
                    f"boost-gateway.source-volume={retained_volume}",
                    work_volume,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            work_created = True
            work_created_once = True
            work_state = restore.inspect_volume(runner, docker, work_volume)
            work_labels = work_state.get("Labels")
            if (
                work_state.get("Name") != work_volume
                or not isinstance(work_labels, dict)
                or work_labels.get("boost-gateway.todo") != "TODO-0012"
                or work_labels.get("boost-gateway.business-id") != identifier
                or work_labels.get("boost-gateway.source-volume") != retained_volume
            ):
                raise BusinessValidationError("disposable work volume binding differs")
            work_volume_identity = restore.volume_identity(work_state)
            if work_volume_identity in {
                active_identity_before,
                retained_identity_before,
            }:
                raise BusinessValidationError(
                    "disposable work volume identity is not distinct"
                )
            work_snapshot_sha = clone_retained_volume(
                runner, docker, redis_image, retained_volume, work_volume
            )
            if work_snapshot_sha != summary_source.get("redis_snapshot_sha256"):
                raise BusinessValidationError(
                    "work snapshot differs from restore summary"
                )
            checked(
                runner,
                [
                    docker,
                    "network",
                    "create",
                    "--internal",
                    "--label",
                    "boost-gateway.todo=TODO-0012",
                    "--label",
                    f"boost-gateway.business-id={identifier}",
                    network,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            network_created = True
            network_created_once = True
            network_state, network_subnet = inspect_internal_network(
                runner, docker, network, identifier
            )
            network_id = network_state["Id"]
            network_ipv4_subnet = str(network_subnet)
            start_redis_networked(
                runner,
                docker,
                redis_image,
                containers["redis"],
                network,
                work_volume,
                identifier,
                created_containers.append,
            )
            work_seed_before, work_count, work_keys = restore.canonical_keyspace(
                runner, docker, containers["redis"]
            )
            if (
                work_seed_before != retained_seed_before
                or work_count != retained_key_count
                or work_keys != retained_keys
            ):
                raise BusinessValidationError(
                    "work volume seed differs before business writes"
                )

            for service, (alias, port, environment) in BACKENDS.items():
                start_backend(
                    runner,
                    docker,
                    images[service],
                    containers[service],
                    network,
                    alias,
                    port,
                    environment,
                    identifier,
                    created_containers.append,
                )
            gateway_host = start_gateway(
                runner,
                docker,
                images["gateway"],
                containers["gateway"],
                network,
                identifier,
                network_state,
                network_subnet,
                created_containers.append,
            )
            gateway_runtime_binding_verified = True
            inspect_internal_network(
                runner,
                docker,
                network,
                identifier,
                expected_members=set(containers.values()),
            )
            sdk = run_sdk(client, gateway_host, 9201, sdk_timeout_seconds)
            leaderboard = verify_leaderboard_effects(
                runner,
                docker,
                containers["redis"],
                sdk["alice_user_id"],
                sdk["bob_user_id"],
            )
            work_seed_after, _, _ = restore.canonical_keyspace(
                runner, docker, containers["redis"]
            )
            if work_seed_after == work_seed_before:
                raise BusinessValidationError(
                    "business flow did not mutate disposable Redis"
                )

            for name in reversed(created_containers):
                if not remove_resource(runner, docker, "container", name):
                    cleanup_failures.append(f"container:{name}")
            created_containers.clear()
            if network_created:
                if not remove_resource(runner, docker, "network", network):
                    cleanup_failures.append(f"network:{network}")
                else:
                    network_created = False
            if work_created:
                if not remove_resource(runner, docker, "volume", work_volume):
                    cleanup_failures.append(f"volume:{work_volume}")
                else:
                    work_created = False
            if cleanup_failures:
                raise BusinessValidationError("isolated business cleanup failed")

            retained_seed_after, after_count, after_keys = audit_retained_seed(
                runner,
                docker,
                redis_image,
                retained_volume,
                audit_after,
            )
            retained_audit_after_passed = True
            if (
                retained_seed_after != retained_seed_before
                or after_count != retained_key_count
                or after_keys != retained_keys
            ):
                raise BusinessValidationError(
                    "retained Redis seed changed during business validation"
                )
            retained_after = ensure_unused_volume(runner, docker, retained_volume)
            retained_identity_after = restore.volume_identity(retained_after)
            if retained_identity_after != retained_identity_before:
                raise BusinessValidationError("retained volume identity changed")
            active_identity_after = restore.volume_identity(
                restore.inspect_volume(runner, docker, active_volume)
            )
            if active_identity_after != active_identity_before:
                raise BusinessValidationError(
                    "active production volume identity changed"
                )
            if monotonic() - started > rto_seconds:
                raise BusinessValidationError(
                    "isolated business validation exceeded RTO"
                )
            success = True
    except Exception as exc:
        error = str(exc)
        for name in reversed(created_containers):
            if not remove_resource(runner, docker, "container", name):
                cleanup_failures.append(f"container:{name}")
        for name in (audit_before, audit_after):
            remove_resource(runner, docker, "container", name)
        if network_created and not remove_resource(runner, docker, "network", network):
            cleanup_failures.append(f"network:{network}")
        if work_created and not remove_resource(runner, docker, "volume", work_volume):
            cleanup_failures.append(f"volume:{work_volume}")

    elapsed = round(monotonic() - started, 3)
    summary = {
        "schema_version": 1,
        "business_validation_id": identifier,
        "restore_id": summary_source.get("restore_id", ""),
        "backup_id": summary_source.get("backup_id", ""),
        "deployment": summary_source.get("deployment", {}),
        "started_at": started_at,
        "completed_at": backup.now(),
        "elapsed_seconds": elapsed,
        "rto_budget_seconds": rto_seconds,
        "rto_pass": success and elapsed <= rto_seconds,
        "overall_pass": success,
        "status": "passed" if success else "failed",
        "failure": error,
        "cleanup_failures": cleanup_failures,
        "restore_summary_sha256": sha256_file(restore_summary_path),
        "deployment_record_sha256": sha256_file(deployment_record_path),
        "release_manifest_sha256": sha256_file(release_dir / "manifest.json"),
        "release_sdk_full_flow_sha256": sha256_file(client),
        "release_image_ids": images,
        "redis_image": redis_image,
        "active_volume": active_volume,
        "active_volume_identity_sha256": active_identity_before,
        "active_volume_identity_after_sha256": active_identity_after,
        "active_volume_unchanged": success,
        "retained_volume": retained_volume,
        "retained_volume_identity_sha256": retained_identity_before,
        "retained_volume_identity_after_sha256": retained_identity_after,
        "restore_volume_identity_binding_verified": success,
        "retained_volume_mounted_readonly": retained_audit_before_passed,
        "retained_seed_before_sha256": retained_seed_before,
        "retained_seed_after_sha256": retained_seed_after,
        "retained_seed_unchanged": success and retained_audit_after_passed,
        "retained_seed_key_count": retained_key_count,
        "work_volume": work_volume,
        "work_volume_identity_sha256": work_volume_identity,
        "work_volume_created": work_created_once,
        "work_volume_snapshot_sha256": work_snapshot_sha,
        "restore_snapshot_binding_verified": success,
        "work_seed_before_sha256": work_seed_before,
        "work_seed_after_sha256": work_seed_after,
        "work_seed_mutated_by_business_checks": success,
        "work_volume_removed": success and not work_created,
        "isolated_network": network,
        "isolated_network_created": network_created_once,
        "isolated_network_internal": success,
        "isolated_network_id": network_id,
        "isolated_network_ipv4_subnet": network_ipv4_subnet,
        "isolated_network_removed": success and not network_created,
        "gateway_endpoint_mode": "host-direct-internal-bridge",
        "gateway_internal_ipv4": gateway_host,
        "gateway_container_port": 9201,
        "gateway_host_port_published": gateway_ports_published,
        "gateway_runtime_binding_verified": gateway_runtime_binding_verified,
        "leaderboard": leaderboard,
        "leaderboard_submit": bool(success and leaderboard.get("leaderboard_submit")),
        "leaderboard_top": bool(success and leaderboard.get("leaderboard_top")),
        "leaderboard_rank": bool(success and leaderboard.get("leaderboard_rank")),
        "sdk_full_flow": sdk,
        "sdk_full_flow_checked": success,
        "source_build_performed": False,
        "public_conan_access_performed": False,
        "restore_redis_image_binding_verified": success,
        "shared_lifecycle_lock": str(lock_path),
        "production_switched": False,
        "compose_changed": False,
        "host_units_changed": False,
        "timer_changed": False,
        "restore_known_good": False,
        "formal_todo0012_claim": False,
        "secret_material_recorded": False,
    }
    try:
        backup.write_new(summary_path, canonical_json(summary), 0o640)
    except (backup.BackupError, OSError) as exc:
        raise BusinessValidationError(f"cannot write business summary: {exc}") from exc
    if not success:
        suffix = f"; cleanup failures: {cleanup_failures}" if cleanup_failures else ""
        raise BusinessValidationError(
            f"isolated business validation failed: {error}{suffix}"
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--restore-summary", type=Path, required=True)
    parser.add_argument("--deployment-record", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--retained-volume", required=True)
    parser.add_argument("--work-volume", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--redis-image", required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("--active-volume", default=DEFAULT_ACTIVE_VOLUME)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--docker", default="/usr/bin/docker")
    parser.add_argument("--rto-seconds", type=float, default=300.0)
    parser.add_argument("--sdk-timeout-seconds", type=int, default=180)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_business_validation(
            business_id=args.business_id,
            restore_summary_path=args.restore_summary,
            deployment_record_path=args.deployment_record,
            release_dir=args.release_dir,
            retained_volume=args.retained_volume,
            work_volume=args.work_volume,
            network=args.network,
            redis_image=args.redis_image,
            summary_path=args.summary_path,
            active_volume=args.active_volume,
            lock_path=args.lock_path,
            docker=args.docker,
            rto_seconds=args.rto_seconds,
            sdk_timeout_seconds=args.sdk_timeout_seconds,
        )
    except BusinessValidationError as exc:
        print(f"isolated restore business verification: FAIL: {exc}", file=sys.stderr)
        return 1
    print("isolated restore business verification: PASS")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
