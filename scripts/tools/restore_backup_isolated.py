#!/usr/bin/env python3
"""Restore a Mac-exported Redis bundle into a new isolated Docker volume."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

try:
    from scripts.tools import manage_backup_recovery as backup
except ModuleNotFoundError:  # pragma: no cover - direct installed-script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.tools import manage_backup_recovery as backup


DEFAULT_LOCK = Path("/var/lib/boost-gateway/deployment-transactions/.lifecycle.lock")
DEFAULT_ACTIVE_VOLUME = "boost-gateway-production-redis-data"
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
VOLUME_RE = re.compile(r"boost-gateway-recovery-[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
IMAGE_RE = re.compile(r"(?:[A-Za-z0-9./:_-]+@)?sha256:[0-9a-f]{64}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
Runner = Callable[..., subprocess.CompletedProcess[Any]]


class RestoreError(RuntimeError):
    """Raised when an isolated restore cannot preserve its safety contract."""


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def optional_sha256(path: Path) -> str:
    try:
        return sha256_file(require_regular(path, "summary-bound artifact"))
    except RestoreError:
        return ""


def require_regular(path: Path, label: str) -> Path:
    try:
        observed = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RestoreError(f"cannot read {label}: {exc}") from exc
    if stat.S_ISLNK(observed.st_mode) or not resolved.is_file():
        raise RestoreError(f"{label} must be a regular non-symlink file: {path}")
    return resolved


def load_json(path: Path, label: str) -> dict[str, Any]:
    source = require_regular(path, label)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RestoreError(f"cannot parse {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RestoreError(f"{label} must be a JSON object")
    return value


def validate_identifier(value: str, label: str) -> str:
    if IDENTIFIER_RE.fullmatch(value) is None or value.startswith("."):
        raise RestoreError(f"{label} is invalid")
    return value


def validate_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RestoreError(f"{label} is not a SHA-256 digest")
    return value


def checked(
    runner: Runner, command: list[str], **kwargs: Any
) -> subprocess.CompletedProcess[Any]:
    try:
        return runner(command, check=True, **kwargs)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RestoreError(f"command failed ({command[0]}): {exc}") from exc


def validate_bundle(
    bundle_dir: Path,
    policy_path: Path,
    redis_profile_path: Path,
    *,
    expected_restore_id: str = "",
    require_transport_receipt: bool = True,
) -> tuple[Path, Path, dict[str, Any]]:
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise RestoreError("restore bundle must be a non-symlink directory")
    bundle = bundle_dir.resolve(strict=True)
    entries = sorted(item.name for item in bundle.iterdir())
    base_entries = [
        "bundle.json",
        "dump.rdb",
        "manifest.json",
        "receipt.json",
        "vault-validation.json",
    ]
    expected_entries = sorted(
        [*base_entries, "transport-receipt.json"]
        if require_transport_receipt
        else base_entries
    )
    if entries != expected_entries:
        raise RestoreError("restore bundle file inventory is invalid")
    bundle_manifest = require_regular(bundle / "bundle.json", "bundle manifest")
    rdb = require_regular(bundle / "dump.rdb", "Redis snapshot")
    manifest_path = require_regular(bundle / "manifest.json", "backup manifest")
    receipt_path = require_regular(bundle / "receipt.json", "remote receipt")
    validation_path = require_regular(
        bundle / "vault-validation.json", "vault validation summary"
    )
    transport_receipt_path = (
        require_regular(bundle / "transport-receipt.json", "restore transport receipt")
        if require_transport_receipt
        else None
    )
    policy = require_regular(policy_path, "backup policy")
    redis_profile = require_regular(redis_profile_path, "Redis profile")
    value = load_json(bundle_manifest, "bundle manifest")
    manifest = load_json(manifest_path, "backup manifest")
    receipt = load_json(receipt_path, "remote receipt")
    validation = load_json(validation_path, "vault validation summary")
    transport_receipt = (
        load_json(transport_receipt_path, "restore transport receipt")
        if transport_receipt_path is not None
        else None
    )
    artifacts = value.get("artifacts")
    payload = value.get("restore_payload")
    identities = value.get("identities")
    policy_binding = value.get("policy")
    if (
        value.get("schema_version") != 1
        or value.get("overall_pass") is not True
        or value.get("create_only") is not True
        or value.get("formal_todo0012_claim") is not False
        or value.get("restore_known_good") is not False
        or value.get("secret_material_recorded") is not False
        or not isinstance(artifacts, dict)
        or not isinstance(payload, dict)
        or not isinstance(identities, dict)
        or not isinstance(policy_binding, dict)
    ):
        raise RestoreError("restore bundle contract is incomplete")
    deployment = identities.get("deployment")
    if not isinstance(deployment, dict):
        raise RestoreError("restore bundle deployment identity is incomplete")
    backup_id = value.get("backup_id")
    if not isinstance(backup_id, str):
        raise RestoreError("restore bundle backup ID is invalid")
    validate_identifier(backup_id, "backup ID")
    if (
        payload.get("path") != "dump.rdb"
        or payload.get("header") != "REDIS"
        or payload.get("sha256") != sha256_file(rdb)
        or payload.get("size_bytes") != rdb.stat().st_size
        or artifacts.get("redis_sha256") != payload.get("sha256")
        or artifacts.get("redis_size_bytes") != payload.get("size_bytes")
    ):
        raise RestoreError("Redis payload differs from bundle binding")
    for field in (
        "archive_sha256",
        "manifest_sha256",
        "receipt_sha256",
        "validation_summary_sha256",
        "vault_host_id_sha256",
        "plaintext_archive_sha256",
        "redis_sha256",
    ):
        validate_sha256(artifacts.get(field), f"bundle artifact {field}")
    for field, path in (
        ("manifest", manifest_path),
        ("receipt", receipt_path),
        ("validation_summary", validation_path),
    ):
        if (
            artifacts.get(f"{field}_sha256") != sha256_file(path)
            or artifacts.get(f"{field}_size_bytes") != path.stat().st_size
        ):
            raise RestoreError(f"copied {field} evidence differs from bundle")
    for field in (
        "manifest_size_bytes",
        "receipt_size_bytes",
        "validation_summary_size_bytes",
        "redis_size_bytes",
    ):
        size = artifacts.get(field)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise RestoreError(f"bundle artifact size is invalid: {field}")
    source_host_id = validate_sha256(
        identities.get("source_host_id_sha256"), "source host ID"
    )
    vault_host_id = validate_sha256(
        identities.get("vault_host_id_sha256"), "vault host ID"
    )
    if source_host_id == vault_host_id:
        raise RestoreError("source and vault host identities are not distinct")
    for field in ("runtime_asset_sha256",):
        validate_sha256(deployment.get(field), f"deployment {field}")
    for field in ("deployment_id", "tag", "commit"):
        item = deployment.get(field)
        if (
            not isinstance(item, str)
            or not item
            or any(ord(char) < 32 for char in item)
        ):
            raise RestoreError(f"deployment {field} is invalid")
    if policy_binding.get("backup_policy_sha256") != sha256_file(policy):
        raise RestoreError("backup policy differs from restore bundle binding")
    if policy_binding.get("redis_profile_sha256") != sha256_file(redis_profile):
        raise RestoreError("Redis profile differs from restore bundle binding")

    archive = manifest.get("archive")
    manifest_deployment = manifest.get("deployment")
    manifest_source = manifest.get("source_host")
    if (
        manifest.get("schema_version") != 2
        or manifest.get("backup_id") != backup_id
        or manifest.get("consistent_redis_snapshot") is not True
        or manifest.get("encrypted_before_transfer") is not True
        or manifest.get("formal_todo0012_claim") is not False
        or manifest.get("secret_material_recorded") is not False
        or not isinstance(archive, dict)
        or archive.get("sha256") != artifacts.get("archive_sha256")
        or archive.get("plaintext_sha256") != artifacts.get("plaintext_archive_sha256")
        or manifest.get("backup_policy_sha256")
        != policy_binding.get("backup_policy_sha256")
        or manifest.get("redis_profile_sha256")
        != policy_binding.get("redis_profile_sha256")
        or not isinstance(manifest_deployment, dict)
        or not isinstance(manifest_source, dict)
        or manifest_source.get("host_id_sha256") != source_host_id
        or not isinstance(manifest_deployment.get("host"), dict)
        or manifest_deployment["host"].get("host_id_sha256") != source_host_id
    ):
        raise RestoreError("copied backup manifest binding is incomplete")
    archive_size = archive.get("size_bytes")
    if (
        not isinstance(archive_size, int)
        or isinstance(archive_size, bool)
        or archive_size <= 0
    ):
        raise RestoreError("copied backup manifest archive size is invalid")
    try:
        backup.validate_manifest_link_contract(manifest)
    except backup.BackupError as exc:
        raise RestoreError(str(exc)) from exc
    redis_sources = [
        item
        for item in manifest.get("sources", [])
        if isinstance(item, dict) and item.get("id") == "redis_snapshot"
    ]
    if (
        len(redis_sources) != 1
        or redis_sources[0].get("sha256") != sha256_file(rdb)
        or redis_sources[0].get("size_bytes") != rdb.stat().st_size
    ):
        raise RestoreError("backup manifest Redis source binding differs")
    for field in ("deployment_id", "tag", "commit", "runtime_asset_sha256"):
        if manifest_deployment.get(field) != deployment.get(field):
            raise RestoreError(f"deployment identity differs: {field}")

    required_receipt = {
        "schema_version": 1,
        "backup_id": backup_id,
        "archive_sha256": artifacts["archive_sha256"],
        "archive_size": archive.get("size_bytes"),
        "manifest_sha256": artifacts["manifest_sha256"],
        "manifest_size": artifacts["manifest_size_bytes"],
        "vault_host_id_sha256": vault_host_id,
        "remote_readback_sha256": True,
        "create_only": True,
        "secret_material_recorded": False,
    }
    if any(
        receipt.get(field) != expected for field, expected in required_receipt.items()
    ):
        raise RestoreError("copied remote receipt binding is incomplete")

    checks = validation.get("checks")
    validation_artifacts = validation.get("artifacts")
    required_checks = {
        "metadata_binding",
        "distinct_host_identity",
        "age_decryption",
        "safe_archive_members",
        "redis_manifest_binding",
        "redis_check_rdb",
    }
    if (
        validation.get("schema_version") != 1
        or validation.get("backup_id") != backup_id
        or validation.get("overall_pass") is not True
        or validation.get("formal_todo0012_claim") is not False
        or validation.get("restore_known_good") is not False
        or validation.get("secret_material_recorded") is not False
        or not isinstance(checks, dict)
        or any(checks.get(check) is not True for check in required_checks)
        or not isinstance(validation_artifacts, dict)
    ):
        raise RestoreError("copied vault validation is not an eligible pass")
    for field in ("plaintext_size_bytes", "member_count"):
        size = validation_artifacts.get(field)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise RestoreError(f"vault validation {field} is invalid")
    validation_bindings = {
        "archive_sha256": artifacts["archive_sha256"],
        "manifest_sha256": artifacts["manifest_sha256"],
        "receipt_sha256": artifacts["receipt_sha256"],
        "vault_host_id_sha256": vault_host_id,
        "plaintext_sha256": artifacts["plaintext_archive_sha256"],
        "redis_sha256": artifacts["redis_sha256"],
        "redis_size_bytes": artifacts["redis_size_bytes"],
    }
    if any(
        validation_artifacts.get(field) != expected
        for field, expected in validation_bindings.items()
    ):
        raise RestoreError("copied vault validation artifact binding differs")
    if transport_receipt is not None:
        files = []
        for name in (
            "dump.rdb",
            "bundle.json",
            "manifest.json",
            "receipt.json",
            "vault-validation.json",
        ):
            path = bundle / name
            files.append(
                {
                    "name": name,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        required_transport = {
            "schema_version": 1,
            "restore_id": expected_restore_id,
            "backup_id": backup_id,
            "files": files,
            "bundle_sha256": sha256_file(bundle_manifest),
            "receiver_host_id_sha256": source_host_id,
            "remote_readback_sha256": True,
            "create_only": True,
            "secret_material_recorded": False,
        }
        if any(
            transport_receipt.get(field) != expected
            for field, expected in required_transport.items()
        ):
            raise RestoreError("restore transport receipt binding differs")
        received_at = transport_receipt.get("received_at")
        try:
            parsed_received_at = datetime.fromisoformat(
                str(received_at).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise RestoreError(
                "restore transport receipt timestamp is invalid"
            ) from exc
        if (
            not expected_restore_id
            or not isinstance(received_at, str)
            or not received_at.endswith("Z")
            or parsed_received_at.tzinfo != UTC
        ):
            raise RestoreError("restore transport receipt identity is invalid")
    with rdb.open("rb") as stream:
        rdb_header = stream.read(5)
    if rdb.stat().st_size < 9 or rdb_header != b"REDIS":
        raise RestoreError("Redis snapshot does not have an RDB header")
    return bundle_manifest, rdb, value


def require_immutable_image(image: str) -> str:
    if IMAGE_RE.fullmatch(image) is None:
        raise RestoreError("Redis image must use an immutable sha256 identity")
    return image


def docker_output(
    runner: Runner,
    command: list[str],
    *,
    timeout: int = 60,
    binary: bool = False,
) -> bytes | str:
    kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "timeout": timeout,
    }
    if not binary:
        kwargs.update(text=True, encoding="utf-8", errors="strict")
    completed = checked(runner, command, **kwargs)
    return completed.stdout if binary else completed.stdout.strip()


def inspect_volume(runner: Runner, docker: str, volume: str) -> dict[str, Any]:
    output = docker_output(runner, [docker, "volume", "inspect", volume])
    try:
        value = json.loads(str(output))
    except json.JSONDecodeError as exc:
        raise RestoreError("Docker volume inspection returned invalid JSON") from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RestoreError("Docker volume inspection is incomplete")
    return value[0]


def volume_identity(value: dict[str, Any]) -> str:
    selected = {
        key: value.get(key)
        for key in ("Name", "Driver", "Mountpoint", "Scope", "Labels")
    }
    return hashlib.sha256(canonical_json(selected)).hexdigest()


def ensure_target_absent(runner: Runner, docker: str, target: str, active: str) -> None:
    if target == active or VOLUME_RE.fullmatch(target) is None:
        raise RestoreError("target volume is invalid or is the active volume")
    names = docker_output(
        runner,
        [docker, "volume", "ls", "--format", "{{.Name}}"],
        timeout=30,
    )
    if target in str(names).splitlines():
        raise RestoreError("target volume already exists")
    users = docker_output(
        runner, [docker, "ps", "-aq", "--filter", f"volume={target}"], timeout=30
    )
    if users:
        raise RestoreError("target volume is referenced by an existing container")


def offline_rdb_check(
    runner: Runner, docker: str, image: str, bundle_dir: Path
) -> None:
    checked(
        runner,
        [
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
            "--mount",
            f"type=bind,src={bundle_dir.resolve()},dst=/restore,readonly",
            image,
            "redis-check-rdb",
            "/restore/dump.rdb",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )


def start_redis(
    runner: Runner,
    docker: str,
    image: str,
    container: str,
    mount: str,
    *,
    user: str,
) -> None:
    command = [
        docker,
        "run",
        "-d",
        "--name",
        container,
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--user",
        user,
        "--entrypoint",
        "redis-server",
    ]
    command.extend(
        [
            "--mount",
            mount,
            image,
            "--appendonly",
            "no",
            "--save",
            "",
            "--protected-mode",
            "yes",
        ]
    )
    checked(
        runner,
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def redis_json(runner: Runner, docker: str, container: str, *arguments: str) -> Any:
    output = docker_output(
        runner,
        [docker, "exec", container, "redis-cli", "--json", *arguments],
        timeout=30,
    )
    try:
        return json.loads(str(output))
    except json.JSONDecodeError as exc:
        raise RestoreError("redis-cli returned invalid JSON") from exc


def ping_redis(runner: Runner, docker: str, container: str) -> None:
    for _ in range(30):
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
        time.sleep(0.25)
    raise RestoreError("isolated Redis PING failed")


def canonical_keyspace(
    runner: Runner, docker: str, container: str
) -> tuple[str, int, set[str]]:
    cursor = "0"
    keys: set[str] = set()
    while True:
        response = redis_json(
            runner, docker, container, "SCAN", cursor, "COUNT", "1000"
        )
        if (
            not isinstance(response, list)
            or len(response) != 2
            or not isinstance(response[0], str)
            or not isinstance(response[1], list)
            or any(not isinstance(key, str) for key in response[1])
        ):
            raise RestoreError("Redis SCAN response is invalid")
        cursor = response[0]
        keys.update(response[1])
        if cursor == "0":
            break
    if not keys:
        raise RestoreError("restored Redis seed keyspace is empty")
    records: list[dict[str, str]] = []
    for key in sorted(keys):
        key_type = redis_json(runner, docker, container, "TYPE", key)
        serialized = docker_output(
            runner,
            [docker, "exec", container, "redis-cli", "--raw", "DUMP", key],
            timeout=30,
            binary=True,
        )
        if (
            not isinstance(key_type, str)
            or key_type == "none"
            or not isinstance(serialized, bytes)
            or not serialized
        ):
            raise RestoreError(f"cannot canonicalize Redis seed key: {key}")
        records.append(
            {
                "key": key,
                "type": key_type,
                "dump_base64": base64.b64encode(serialized).decode("ascii"),
            }
        )
    digest = hashlib.sha256(
        canonical_json(
            {
                "schema_version": 1,
                "dump_encoding": "base64(redis-cli --raw DUMP stdout)",
                "keys": records,
            }
        )
    ).hexdigest()
    return digest, len(records), keys


def remove_container(runner: Runner, docker: str, container: str) -> bool:
    try:
        completed = runner(
            [docker, "rm", "-f", container],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def remove_volume(runner: Runner, docker: str, volume: str) -> bool:
    try:
        completed = runner(
            [docker, "volume", "rm", volume],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_isolated_restore(
    *,
    restore_id: str,
    bundle_dir: Path,
    policy_path: Path,
    redis_profile_path: Path,
    target_volume: str,
    baseline_container: str,
    target_container: str,
    active_volume: str,
    redis_image: str,
    summary_path: Path,
    required_seed_keys: list[str] | None = None,
    require_transport_receipt: bool = True,
    lock_path: Path = DEFAULT_LOCK,
    docker: str = "/usr/bin/docker",
    rto_seconds: float = 600.0,
    runner: Runner = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    validate_identifier(restore_id, "restore ID")
    validate_identifier(baseline_container, "baseline container")
    validate_identifier(target_container, "target container")
    if baseline_container == target_container:
        raise RestoreError("baseline and target containers must be distinct")
    require_immutable_image(redis_image)
    if rto_seconds <= 0 or rto_seconds > 600:
        raise RestoreError("RTO budget must be within 600 seconds")
    if summary_path.exists() or summary_path.is_symlink():
        raise RestoreError("create-only restore summary already exists")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    required_keys = sorted(
        {"lb:global", "lb:global:names", *(required_seed_keys or [])}
    )
    if any(
        not isinstance(key, str) or not key or "\x00" in key for key in required_keys
    ):
        raise RestoreError("required seed key is invalid")

    started = monotonic()
    started_at = backup.now()
    bundle_manifest: Path | None = None
    rdb: Path | None = None
    bundle: dict[str, Any] = {}
    baseline_sha = ""
    target_sha = ""
    target_volume_sha = ""
    seed_count = 0
    active_before = ""
    volume_created = False
    baseline_started = False
    target_started = False
    staging: Path | None = None
    cleanup_failures: list[str] = []
    error = ""
    success = False
    try:
        with backup.lifecycle_lock(lock_path):
            bundle_manifest, rdb, bundle = validate_bundle(
                bundle_dir,
                policy_path,
                redis_profile_path,
                expected_restore_id=restore_id,
                require_transport_receipt=require_transport_receipt,
            )
            staging = Path(
                tempfile.mkdtemp(prefix=f".{restore_id}.", dir=summary_path.parent)
            )
            os.chmod(staging, 0o700)
            staged_rdb = staging / "dump.rdb"
            shutil.copyfile(rdb, staged_rdb)
            os.chmod(staged_rdb, 0o600)
            if sha256_file(staged_rdb) != sha256_file(rdb):
                raise RestoreError("staged Redis snapshot checksum differs")
            offline_rdb_check(runner, docker, redis_image, staging)
            active_before = volume_identity(
                inspect_volume(runner, docker, active_volume)
            )
            ensure_target_absent(runner, docker, target_volume, active_volume)

            start_redis(
                runner,
                docker,
                redis_image,
                baseline_container,
                f"type=bind,src={staging},dst=/data,readonly",
                user="0",
            )
            baseline_started = True
            ping_redis(runner, docker, baseline_container)
            baseline_sha, seed_count, observed_keys = canonical_keyspace(
                runner, docker, baseline_container
            )
            missing = sorted(set(required_keys) - observed_keys)
            if missing:
                raise RestoreError(f"required Redis seed keys are absent: {missing}")
            if not remove_container(runner, docker, baseline_container):
                raise RestoreError("cannot remove baseline Redis container")
            baseline_started = False

            checked(
                runner,
                [
                    docker,
                    "volume",
                    "create",
                    "--label",
                    "boost-gateway.todo=TODO-0012",
                    "--label",
                    f"boost-gateway.restore-id={restore_id}",
                    target_volume,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            volume_created = True
            target_volume_state = inspect_volume(runner, docker, target_volume)
            labels = target_volume_state.get("Labels")
            if (
                target_volume_state.get("Name") != target_volume
                or not isinstance(labels, dict)
                or labels.get("boost-gateway.todo") != "TODO-0012"
                or labels.get("boost-gateway.restore-id") != restore_id
            ):
                raise RestoreError("new target volume identity or labels differ")
            target_volume_sha = volume_identity(target_volume_state)
            if target_volume_sha == active_before:
                raise RestoreError("new target volume identity equals active volume")
            checked(
                runner,
                [
                    docker,
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--user",
                    "0",
                    "--mount",
                    f"type=volume,src={target_volume},dst=/data",
                    "--mount",
                    f"type=bind,src={staging},dst=/restore,readonly",
                    redis_image,
                    "sh",
                    "-eu",
                    "-c",
                    "test ! -e /data/dump.rdb; cp /restore/dump.rdb /data/dump.rdb; chown redis:redis /data/dump.rdb; chmod 0600 /data/dump.rdb",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=300,
            )
            start_redis(
                runner,
                docker,
                redis_image,
                target_container,
                f"type=volume,src={target_volume},dst=/data",
                user="redis",
            )
            target_started = True
            ping_redis(runner, docker, target_container)
            target_sha, target_count, target_keys = canonical_keyspace(
                runner, docker, target_container
            )
            if (
                target_sha != baseline_sha
                or target_count != seed_count
                or target_keys != observed_keys
            ):
                raise RestoreError(
                    "fresh-volume Redis seed differs from bundle baseline"
                )
            if not remove_container(runner, docker, target_container):
                raise RestoreError("cannot remove target Redis container")
            target_started = False
            active_after = volume_identity(
                inspect_volume(runner, docker, active_volume)
            )
            if active_after != active_before:
                raise RestoreError("active Redis volume identity changed during drill")
            elapsed = monotonic() - started
            if elapsed > rto_seconds:
                raise RestoreError("isolated Redis restore exceeded the RTO budget")
            success = True
    except Exception as exc:
        error = str(exc)
        if baseline_started and not remove_container(
            runner, docker, baseline_container
        ):
            cleanup_failures.append("baseline_container")
        if target_started and not remove_container(runner, docker, target_container):
            cleanup_failures.append("target_container")
        if (
            volume_created
            and not cleanup_failures
            and not remove_volume(runner, docker, target_volume)
        ):
            cleanup_failures.append("target_volume")
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    elapsed = round(monotonic() - started, 3)
    summary = {
        "schema_version": 1,
        "restore_id": restore_id,
        "backup_id": bundle.get("backup_id", ""),
        "deployment": bundle.get("identities", {}).get("deployment", {}),
        "started_at": started_at,
        "completed_at": backup.now(),
        "elapsed_seconds": elapsed,
        "rto_budget_seconds": rto_seconds,
        "rto_pass": success and elapsed <= rto_seconds,
        "overall_pass": success,
        "status": "passed" if success else "failed",
        "failure": error,
        "cleanup_failures": cleanup_failures,
        "bundle_manifest_sha256": (
            optional_sha256(bundle_manifest) if bundle_manifest else ""
        ),
        "redis_snapshot_sha256": optional_sha256(rdb) if rdb else "",
        "backup_manifest_sha256": bundle.get("artifacts", {}).get(
            "manifest_sha256", ""
        ),
        "remote_receipt_sha256": bundle.get("artifacts", {}).get("receipt_sha256", ""),
        "vault_validation_sha256": bundle.get("artifacts", {}).get(
            "validation_summary_sha256", ""
        ),
        "transport_receipt_sha256": (
            optional_sha256(bundle_dir / "transport-receipt.json")
            if require_transport_receipt
            else ""
        ),
        "transport_remote_readback_bound": success and require_transport_receipt,
        "backup_policy_sha256": bundle.get("policy", {}).get(
            "backup_policy_sha256", ""
        ),
        "redis_profile_sha256": bundle.get("policy", {}).get(
            "redis_profile_sha256", ""
        ),
        "source_host_id_sha256": bundle.get("identities", {}).get(
            "source_host_id_sha256", ""
        ),
        "vault_host_id_sha256": bundle.get("identities", {}).get(
            "vault_host_id_sha256", ""
        ),
        "target_volume": target_volume,
        "target_container": target_container,
        "baseline_container": baseline_container,
        "redis_image": redis_image,
        "active_volume": active_volume,
        "active_volume_identity_sha256": active_before,
        "target_volume_identity_sha256": target_volume_sha,
        "active_volume_mounted_by_drill": False,
        "active_volume_preserved": bool(success and active_before),
        "target_volume_retained": success,
        "target_removed_on_failure": bool(
            not success and volume_created and not cleanup_failures
        ),
        "network_mode": "none",
        "shared_lifecycle_lock": str(lock_path),
        "offline_redis_check_rdb": success,
        "restore_payload_copy_verified": success,
        "redis_ping": success,
        "canonical_seed_baseline_sha256": baseline_sha,
        "canonical_seed_restored_sha256": target_sha,
        "canonical_seed_key_count": seed_count,
        "leaderboard_seed_exact": success,
        "canonical_seed_method": "SCAN+TYPE+DUMP",
        "canonical_seed_dump_encoding": "base64(redis-cli --raw stdout)",
        "baseline_from_same_bundle_rdb": success,
        "required_seed_keys": required_keys,
        "required_seed_key_count": len(required_keys),
        "vault_link_free_validation_bound": success,
        "full_host_archive_received": False,
        "host_links_reconstructed": False,
        "full_host_link_reconstruction_future_boundary": True,
        "production_switched": False,
        "compose_changed": False,
        "host_units_changed": False,
        "timer_changed": False,
        "submit_top_rank_checked": False,
        "sdk_full_flow_checked": False,
        "restore_known_good": False,
        "formal_todo0012_claim": False,
        "secret_material_recorded": False,
    }
    try:
        backup.write_new(summary_path, canonical_json(summary), 0o640)
    except (backup.BackupError, OSError) as exc:
        raise RestoreError(f"cannot write create-only restore summary: {exc}") from exc
    if not success:
        suffix = f"; cleanup failures: {cleanup_failures}" if cleanup_failures else ""
        raise RestoreError(f"isolated restore failed: {error}{suffix}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore-id", required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--redis-profile", type=Path, required=True)
    parser.add_argument("--target-volume", required=True)
    parser.add_argument("--baseline-container", required=True)
    parser.add_argument("--target-container", required=True)
    parser.add_argument("--active-volume", default=DEFAULT_ACTIVE_VOLUME)
    parser.add_argument("--redis-image", required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("--required-seed-key", action="append", default=[])
    parser.add_argument(
        "--allow-local-bundle-without-transport-receipt",
        action="store_true",
        help="allow a directly controlled local bundle; governed remote drills must not use this",
    )
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--docker", default="/usr/bin/docker")
    parser.add_argument("--rto-seconds", type=float, default=600.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run_isolated_restore(
            restore_id=args.restore_id,
            bundle_dir=args.bundle_dir,
            policy_path=args.policy,
            redis_profile_path=args.redis_profile,
            target_volume=args.target_volume,
            baseline_container=args.baseline_container,
            target_container=args.target_container,
            active_volume=args.active_volume,
            redis_image=args.redis_image,
            summary_path=args.summary_path,
            required_seed_keys=args.required_seed_key,
            require_transport_receipt=not args.allow_local_bundle_without_transport_receipt,
            lock_path=args.lock_path,
            docker=args.docker,
            rto_seconds=args.rto_seconds,
        )
    except RestoreError as exc:
        print(f"isolated backup restore: FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
