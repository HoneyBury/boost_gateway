#!/usr/bin/env python3
"""Forced-command receiver for create-only restore bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import struct
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

FRAME = struct.Struct("!Q")
CHUNK_BYTES = 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_RDB_BYTES = 64 * 1024 * 1024 * 1024
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
FILES = (
    "dump.rdb",
    "bundle.json",
    "manifest.json",
    "receipt.json",
    "vault-validation.json",
)
JSON_FILES = frozenset(FILES[1:])


class RestoreTransportError(RuntimeError):
    """Raised when a restore bundle violates the transport contract."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def valid_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo == UTC


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def validate_id(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or ID_RE.fullmatch(value) is None
        or value.startswith(".")
    ):
        raise RestoreTransportError(f"{label} is invalid")
    return value


def validate_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RestoreTransportError(f"{label} is not a SHA-256 digest")
    return value


def require_regular(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RestoreTransportError(f"cannot resolve {label}: {exc}") from exc
    if path.is_symlink() or not resolved.is_file():
        raise RestoreTransportError(f"{label} must be a regular non-symlink file")
    return resolved


def require_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RestoreTransportError(f"cannot resolve {label}: {exc}") from exc
    if path.is_symlink() or not resolved.is_dir():
        raise RestoreTransportError(f"{label} must be a non-symlink directory")
    return resolved


def ensure_directory(path: Path, label: str) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = require_directory(path, label)
    os.chmod(resolved, 0o700)
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with require_regular(path, "artifact").open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict[str, Any]:
    regular = require_regular(path, label)
    if regular.stat().st_size <= 0 or regular.stat().st_size > MAX_JSON_BYTES:
        raise RestoreTransportError(f"{label} size is invalid")
    try:
        value = json.loads(regular.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RestoreTransportError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RestoreTransportError(f"{label} must be a JSON object")
    return value


def write_new(path: Path, content: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_exact(stream: BinaryIO, size: int, label: str) -> bytes:
    blocks: list[bytes] = []
    remaining = size
    while remaining:
        block = stream.read(min(remaining, CHUNK_BYTES))
        if not block:
            raise RestoreTransportError(f"truncated {label}")
        blocks.append(block)
        remaining -= len(block)
    return b"".join(blocks)


def copy_exact(stream: BinaryIO, destination: Path, size: int, label: str) -> None:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            remaining = size
            while remaining:
                block = stream.read(min(remaining, CHUNK_BYTES))
                if not block:
                    raise RestoreTransportError(f"truncated {label}")
                output.write(block)
                remaining -= len(block)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def bundle_files(
    bundle_dir: Path, *, allow_transport_receipt: bool = False
) -> dict[str, Path]:
    root = require_directory(bundle_dir, "restore bundle")
    observed = {entry.name for entry in os.scandir(root)}
    expected = set(FILES)
    if allow_transport_receipt:
        expected.add("transport-receipt.json")
    if observed != expected:
        extra = sorted(observed - expected)
        missing = sorted(expected - observed)
        raise RestoreTransportError(
            f"restore bundle inventory differs: extra={extra} missing={missing}"
        )
    return {name: require_regular(root / name, name) for name in FILES}


def file_records(paths: dict[str, Path]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for name in FILES:
        path = paths[name]
        size = path.stat().st_size
        maximum = MAX_RDB_BYTES if name == "dump.rdb" else MAX_JSON_BYTES
        if size <= 0 or size > maximum:
            raise RestoreTransportError(f"restore bundle file size is invalid: {name}")
        records.append({"name": name, "size_bytes": size, "sha256": sha256_file(path)})
    return records


def _required_true(
    document: dict[str, Any], fields: tuple[str, ...], label: str
) -> None:
    if any(document.get(field) is not True for field in fields):
        raise RestoreTransportError(f"{label} lacks a required pass")


def safe_relative_path(value: object, *, prefix: str | None = None) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        return False
    return prefix is None or value.startswith(f"{prefix}/")


def validate_manifest_archive_contract(manifest: dict[str, Any]) -> None:
    sources = manifest.get("sources")
    contract = manifest.get("archive_contract")
    links = manifest.get("source_links")
    if (
        not isinstance(sources, list)
        or not isinstance(contract, dict)
        or not isinstance(links, list)
    ):
        raise RestoreTransportError("backup manifest archive contract is incomplete")
    source_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise RestoreTransportError("backup manifest source inventory is invalid")
        identifier = source.get("id")
        archive_path = source.get("archive_path")
        if (
            not isinstance(identifier, str)
            or identifier in source_ids
            or not safe_relative_path(archive_path)
        ):
            raise RestoreTransportError("backup manifest source inventory is invalid")
        expected_path = (
            "redis/dump.rdb"
            if identifier == "redis_snapshot"
            else f"sources/{identifier}"
        )
        if archive_path != expected_path:
            raise RestoreTransportError("backup manifest source inventory is invalid")
        source_ids.add(identifier)
    if (
        contract.get("format") != "link_free_tar_v1"
        or contract.get("symbolic_link_entries") != 0
        or contract.get("hard_link_entries") != 0
        or contract.get("symbolic_links_recorded") != len(links)
    ):
        raise RestoreTransportError("backup manifest link-free contract is invalid")
    observed: set[str] = set()
    for link in links:
        if not isinstance(link, dict) or set(link) != {
            "archive_path",
            "original_link_text",
            "target_source_id",
            "target_relative_path",
            "target_type",
        }:
            raise RestoreTransportError("backup manifest link metadata is invalid")
        archive_path = link.get("archive_path")
        parts = (
            PurePosixPath(archive_path).parts if isinstance(archive_path, str) else ()
        )
        relative = link.get("target_relative_path")
        original = link.get("original_link_text")
        if (
            not safe_relative_path(archive_path, prefix="sources")
            or len(parts) < 3
            or parts[1] not in source_ids
            or archive_path in observed
            or not isinstance(original, str)
            or not original
            or any(ord(character) < 32 for character in original)
            or link.get("target_source_id") not in source_ids
            or (relative != "." and not safe_relative_path(relative))
            or link.get("target_type") not in {"file", "directory"}
        ):
            raise RestoreTransportError("backup manifest link metadata is invalid")
        observed.add(archive_path)


def required_text(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise RestoreTransportError(f"{label} is invalid")
    return value


def validate_bundle_binding(
    bundle_dir: Path, *, allow_transport_receipt: bool = False
) -> dict[str, Any]:
    paths = bundle_files(bundle_dir, allow_transport_receipt=allow_transport_receipt)
    rdb = paths["dump.rdb"]
    with rdb.open("rb") as stream:
        if stream.read(5) != b"REDIS":
            raise RestoreTransportError("restore RDB header is invalid")

    bundle = load_json(paths["bundle.json"], "bundle manifest")
    manifest = load_json(paths["manifest.json"], "backup manifest")
    receipt = load_json(paths["receipt.json"], "vault receipt")
    validation = load_json(paths["vault-validation.json"], "vault validation")
    backup_id = validate_id(bundle.get("backup_id"), "backup ID")
    if (
        bundle.get("schema_version") != 1
        or bundle.get("overall_pass") is not True
        or bundle.get("create_only") is not True
        or bundle.get("formal_todo0012_claim") is not False
        or bundle.get("restore_known_good") is not False
        or bundle.get("secret_material_recorded") is not False
    ):
        raise RestoreTransportError("bundle manifest is not an eligible restore export")

    artifacts = bundle.get("artifacts")
    payload = bundle.get("restore_payload")
    if not isinstance(artifacts, dict) or not isinstance(payload, dict):
        raise RestoreTransportError("bundle artifact binding is incomplete")
    expected_digests = {
        "manifest_sha256": sha256_file(paths["manifest.json"]),
        "receipt_sha256": sha256_file(paths["receipt.json"]),
        "validation_summary_sha256": sha256_file(paths["vault-validation.json"]),
        "redis_sha256": sha256_file(rdb),
    }
    for field, value in expected_digests.items():
        if artifacts.get(field) != value:
            raise RestoreTransportError(f"bundle artifact digest differs: {field}")
    if (
        payload.get("path") != "dump.rdb"
        or payload.get("sha256") != expected_digests["redis_sha256"]
        or payload.get("size_bytes") != rdb.stat().st_size
        or payload.get("header") != "REDIS"
        or artifacts.get("redis_size_bytes") != rdb.stat().st_size
    ):
        raise RestoreTransportError("bundle restore payload binding differs")

    manifest_archive = manifest.get("archive")
    sources = manifest.get("sources")
    deployment = manifest.get("deployment")
    source_host = manifest.get("source_host")
    if (
        manifest.get("schema_version") != 2
        or manifest.get("backup_id") != backup_id
        or manifest.get("consistent_redis_snapshot") is not True
        or manifest.get("encrypted_before_transfer") is not True
        or manifest.get("formal_todo0012_claim") is not False
        or manifest.get("secret_material_recorded") is not False
        or not isinstance(manifest_archive, dict)
        or not isinstance(sources, list)
        or not isinstance(deployment, dict)
        or not isinstance(source_host, dict)
    ):
        raise RestoreTransportError("backup manifest binding is invalid")
    deployment_host = deployment.get("host")
    if not isinstance(deployment_host, dict):
        raise RestoreTransportError("backup deployment host binding is invalid")
    source_host_id = validate_sha256(
        source_host.get("host_id_sha256"), "source host identity"
    )
    if deployment_host.get("host_id_sha256") != source_host_id:
        raise RestoreTransportError("backup deployment and source hosts differ")
    manifest_deployment = {
        "deployment_id": required_text(
            deployment.get("deployment_id"), "deployment ID"
        ),
        "tag": required_text(deployment.get("tag"), "deployment tag"),
        "commit": required_text(deployment.get("commit"), "deployment commit"),
        "runtime_asset_sha256": validate_sha256(
            deployment.get("runtime_asset_sha256"), "runtime asset digest"
        ),
    }
    identities = bundle.get("identities")
    policy = bundle.get("policy")
    if (
        not isinstance(identities, dict)
        or identities.get("source_host_id_sha256") != source_host_id
        or identities.get("deployment") != manifest_deployment
        or not isinstance(policy, dict)
        or policy.get("backup_policy_sha256")
        != validate_sha256(manifest.get("backup_policy_sha256"), "backup policy digest")
        or policy.get("redis_profile_sha256")
        != validate_sha256(manifest.get("redis_profile_sha256"), "Redis profile digest")
    ):
        raise RestoreTransportError("bundle identity or policy binding differs")
    validate_manifest_archive_contract(manifest)
    redis_sources = [
        item
        for item in sources
        if isinstance(item, dict) and item.get("id") == "redis_snapshot"
    ]
    if (
        len(redis_sources) != 1
        or redis_sources[0].get("archive_path") != "redis/dump.rdb"
        or redis_sources[0].get("sha256") != expected_digests["redis_sha256"]
        or redis_sources[0].get("size_bytes") != rdb.stat().st_size
    ):
        raise RestoreTransportError("backup manifest Redis binding differs")

    archive_sha256 = validate_sha256(manifest_archive.get("sha256"), "archive digest")
    archive_size = manifest_archive.get("size_bytes")
    if (
        not isinstance(archive_size, int)
        or isinstance(archive_size, bool)
        or archive_size <= 0
    ):
        raise RestoreTransportError("backup manifest archive size is invalid")
    for field in (
        "archive_sha256",
        "plaintext_archive_sha256",
        "vault_host_id_sha256",
    ):
        validate_sha256(artifacts.get(field), f"bundle {field}")
    if artifacts["archive_sha256"] != archive_sha256:
        raise RestoreTransportError("bundle archive digest differs")
    if (
        manifest_archive.get("plaintext_sha256")
        != artifacts["plaintext_archive_sha256"]
    ):
        raise RestoreTransportError("bundle plaintext archive digest differs")
    expected_sizes = {
        "archive_size_bytes": archive_size,
        "manifest_size_bytes": paths["manifest.json"].stat().st_size,
        "receipt_size_bytes": paths["receipt.json"].stat().st_size,
        "validation_summary_size_bytes": paths["vault-validation.json"].stat().st_size,
    }
    for field, expected in expected_sizes.items():
        if artifacts.get(field) != expected:
            raise RestoreTransportError(f"bundle artifact size differs: {field}")
    vault_host_id = validate_sha256(
        identities.get("vault_host_id_sha256"), "vault host identity"
    )
    if artifacts["vault_host_id_sha256"] != vault_host_id:
        raise RestoreTransportError("bundle vault host identity differs")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("backup_id") != backup_id
        or receipt.get("archive_sha256") != archive_sha256
        or receipt.get("archive_size") != archive_size
        or receipt.get("manifest_sha256") != expected_digests["manifest_sha256"]
        or receipt.get("manifest_size") != paths["manifest.json"].stat().st_size
        or receipt.get("vault_host_id_sha256") != vault_host_id
        or receipt.get("remote_readback_sha256") is not True
        or receipt.get("create_only") is not True
        or receipt.get("secret_material_recorded") is not False
    ):
        raise RestoreTransportError("vault receipt binding differs")

    checks = validation.get("checks")
    validation_artifacts = validation.get("artifacts")
    if (
        validation.get("schema_version") != 1
        or validation.get("backup_id") != backup_id
        or validation.get("overall_pass") is not True
        or validation.get("formal_todo0012_claim") is not False
        or validation.get("restore_known_good") is not False
        or validation.get("secret_material_recorded") is not False
        or not isinstance(checks, dict)
        or not isinstance(validation_artifacts, dict)
    ):
        raise RestoreTransportError("vault validation binding is invalid")
    _required_true(
        checks,
        (
            "metadata_binding",
            "distinct_host_identity",
            "age_decryption",
            "safe_archive_members",
            "redis_manifest_binding",
            "redis_check_rdb",
        ),
        "vault validation",
    )
    for field, expected in (
        ("archive_sha256", archive_sha256),
        ("manifest_sha256", expected_digests["manifest_sha256"]),
        ("receipt_sha256", expected_digests["receipt_sha256"]),
        ("vault_host_id_sha256", artifacts["vault_host_id_sha256"]),
        ("plaintext_sha256", artifacts["plaintext_archive_sha256"]),
        ("redis_sha256", expected_digests["redis_sha256"]),
    ):
        if validation_artifacts.get(field) != expected:
            raise RestoreTransportError(f"vault validation artifact differs: {field}")
    if validation_artifacts.get("redis_size_bytes") != rdb.stat().st_size:
        raise RestoreTransportError("vault validation Redis size differs")
    return {
        "backup_id": backup_id,
        "source_host_id_sha256": source_host_id,
        "files": file_records(paths),
        "bundle_sha256": sha256_file(paths["bundle.json"]),
    }


def write_frame(stream: BinaryIO, restore_id: str, bundle_dir: Path) -> dict[str, Any]:
    binding = validate_bundle_binding(bundle_dir)
    header = {
        "schema_version": 1,
        "restore_id": validate_id(restore_id, "restore ID"),
        "backup_id": binding["backup_id"],
        "files": binding["files"],
    }
    encoded = canonical_json(header)
    if len(encoded) > MAX_HEADER_BYTES:
        raise RestoreTransportError("restore frame header is too large")
    stream.write(FRAME.pack(len(encoded)))
    stream.write(encoded)
    paths = bundle_files(bundle_dir)
    for name in FILES:
        with paths[name].open("rb") as source:
            shutil.copyfileobj(source, stream, length=CHUNK_BYTES)
    return header


def parse_header(stream: BinaryIO) -> dict[str, Any]:
    size = FRAME.unpack(read_exact(stream, FRAME.size, "frame header length"))[0]
    if size <= 0 or size > MAX_HEADER_BYTES:
        raise RestoreTransportError("restore frame header length is invalid")
    try:
        header = json.loads(read_exact(stream, size, "frame header").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RestoreTransportError(f"restore frame header is invalid: {exc}") from exc
    if (
        not isinstance(header, dict)
        or set(header) != {"schema_version", "restore_id", "backup_id", "files"}
        or header.get("schema_version") != 1
    ):
        raise RestoreTransportError("restore frame header schema is invalid")
    validate_id(header.get("restore_id"), "restore ID")
    validate_id(header.get("backup_id"), "backup ID")
    records = header.get("files")
    if not isinstance(records, list) or len(records) != len(FILES):
        raise RestoreTransportError("restore frame file inventory is invalid")
    for expected_name, record in zip(FILES, records, strict=True):
        maximum = MAX_RDB_BYTES if expected_name == "dump.rdb" else MAX_JSON_BYTES
        if (
            not isinstance(record, dict)
            or set(record) != {"name", "size_bytes", "sha256"}
            or record.get("name") != expected_name
            or not isinstance(record.get("size_bytes"), int)
            or isinstance(record.get("size_bytes"), bool)
            or record["size_bytes"] <= 0
            or record["size_bytes"] > maximum
        ):
            raise RestoreTransportError("restore frame file inventory is invalid")
        validate_sha256(record.get("sha256"), f"{expected_name} digest")
    return header


def store_bundle(
    staging_root: Path,
    receiver_identity_file: Path,
    stream: BinaryIO,
    *,
    received_at: str | None = None,
) -> dict[str, Any]:
    root = ensure_directory(staging_root, "restore staging root")
    receiver_host_id = sha256_file(
        require_regular(receiver_identity_file, "receiver identity file")
    )
    header = parse_header(stream)
    restore_id = header["restore_id"]
    final = root / restore_id
    if final.exists() or final.is_symlink():
        raise RestoreTransportError(
            f"create-only restore staging already exists: {restore_id}"
        )
    incoming = ensure_directory(root / ".incoming", "restore incoming root")
    temporary = incoming / f"{restore_id}.{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700)
    try:
        for record in header["files"]:
            copy_exact(
                stream, temporary / record["name"], record["size_bytes"], record["name"]
            )
        if stream.read(1):
            raise RestoreTransportError("restore upload contains trailing bytes")
        for record in header["files"]:
            if sha256_file(temporary / record["name"]) != record["sha256"]:
                raise RestoreTransportError(
                    f"restore file readback digest differs: {record['name']}"
                )
        binding = validate_bundle_binding(temporary)
        if (
            binding["backup_id"] != header["backup_id"]
            or binding["files"] != header["files"]
        ):
            raise RestoreTransportError("restore header and bundle binding differ")
        if binding["source_host_id_sha256"] != receiver_host_id:
            raise RestoreTransportError(
                "restore receiver is not the backup source host"
            )
        timestamp = received_at or utc_now()
        if not valid_utc_timestamp(timestamp):
            raise RestoreTransportError("restore receipt timestamp is invalid")
        receipt = {
            "schema_version": 1,
            "restore_id": restore_id,
            "backup_id": binding["backup_id"],
            "received_at": timestamp,
            "files": binding["files"],
            "bundle_sha256": binding["bundle_sha256"],
            "receiver_host_id_sha256": receiver_host_id,
            "remote_readback_sha256": True,
            "create_only": True,
            "secret_material_recorded": False,
        }
        write_new(temporary / "transport-receipt.json", canonical_json(receipt))
        fsync_directory(temporary)
        os.rename(temporary, final)
        fsync_directory(root)
        return receipt
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def read_receipt(
    staging_root: Path, receiver_identity_file: Path, restore_id: str
) -> dict[str, Any]:
    root = require_directory(staging_root, "restore staging root")
    directory = require_directory(
        root / validate_id(restore_id, "restore ID"), "restore staging"
    )
    receipt = load_json(
        directory / "transport-receipt.json", "restore transport receipt"
    )
    binding = validate_bundle_binding(directory, allow_transport_receipt=True)
    receiver_host_id = sha256_file(
        require_regular(receiver_identity_file, "receiver identity file")
    )
    if receiver_host_id != binding["source_host_id_sha256"]:
        raise RestoreTransportError("restore receiver is not the backup source host")
    expected = {
        "schema_version": 1,
        "restore_id": restore_id,
        "backup_id": binding["backup_id"],
        "files": binding["files"],
        "bundle_sha256": binding["bundle_sha256"],
        "receiver_host_id_sha256": receiver_host_id,
        "remote_readback_sha256": True,
        "create_only": True,
        "secret_material_recorded": False,
    }
    if set(receipt) != set(expected) | {"received_at"} or not valid_utc_timestamp(
        receipt.get("received_at")
    ):
        raise RestoreTransportError("restore transport receipt schema is invalid")
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise RestoreTransportError(
                f"restore transport receipt field differs: {field}"
            )
    return receipt


def parse_original_command(value: str) -> tuple[str, list[str]]:
    try:
        arguments = shlex.split(value, posix=True)
    except ValueError as exc:
        raise RestoreTransportError(f"invalid SSH original command: {exc}") from exc
    if arguments == ["boost-gateway-restore", "store"]:
        return "store", []
    if (
        len(arguments) == 3
        and arguments[:2] == ["boost-gateway-restore", "receipt"]
        and ID_RE.fullmatch(arguments[2]) is not None
        and not arguments[2].startswith(".")
    ):
        return "receipt", [arguments[2]]
    raise RestoreTransportError("SSH command is outside the restore transport surface")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--receiver-identity-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        operation, values = parse_original_command(
            os.environ.get("SSH_ORIGINAL_COMMAND", "")
        )
        result = (
            store_bundle(
                args.staging_root, args.receiver_identity_file, sys.stdin.buffer
            )
            if operation == "store"
            else read_receipt(args.staging_root, args.receiver_identity_file, values[0])
        )
        sys.stdout.buffer.write(canonical_json(result))
        return 0
    except (RestoreTransportError, OSError, ValueError) as exc:
        print(f"restore bundle receiver: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
