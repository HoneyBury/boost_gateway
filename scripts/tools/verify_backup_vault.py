#!/usr/bin/env python3
"""Verify one encrypted off-host backup without materializing its plaintext tar."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from scripts.lib.backup_recovery import (  # noqa: E402
        BackupError,
        canonical_json,
        load_json_object,
        require_directory,
        require_regular,
        sha256_file,
        validate_backup_id,
        validate_manifest_link_contract,
        validate_sha256,
        write_new,
    )
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from manage_backup_recovery import (  # type: ignore[no-redef]  # noqa: E402
        BackupError,
        canonical_json,
        load_json_object,
        require_directory,
        require_regular,
        sha256_file,
        validate_backup_id,
        validate_manifest_link_contract,
        validate_sha256,
        write_new,
    )

CHUNK_BYTES = 1024 * 1024
RunCommand = Callable[..., subprocess.CompletedProcess[Any]]
StartCommand = Callable[..., Any]


class VaultVerificationError(BackupError):
    """Raised when a vault backup does not satisfy the verification contract."""


class HashingReader(io.RawIOBase):
    """Track the exact plaintext consumed from an age stdout stream."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        block = self.stream.read(size)
        if block:
            self.digest.update(block)
            self.size += len(block)
        return block


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def require_private_identity(path: Path) -> Path:
    identity = require_regular(path, "age identity")
    if stat.S_IMODE(identity.stat().st_mode) & 0o077:
        raise VaultVerificationError("age identity must not be group/world accessible")
    return identity


def validate_metadata(
    vault_root: Path, backup_id: str
) -> tuple[Path, Path, Path, Path, dict[str, Any], dict[str, Any]]:
    root = require_directory(vault_root, "vault root")
    identity = require_regular(root / ".vault-identity", "vault identity")
    backup = require_directory(
        root / "backups" / validate_backup_id(backup_id), "vault backup"
    )
    archive = require_regular(backup / "payload.tar.age", "encrypted archive")
    manifest_path = require_regular(backup / "manifest.json", "backup manifest")
    receipt_path = require_regular(backup / "receipt.json", "remote receipt")
    manifest = load_json_object(manifest_path, "backup manifest")
    receipt = load_json_object(receipt_path, "remote receipt")

    archive_record = manifest.get("archive")
    source_host = manifest.get("source_host")
    deployment = manifest.get("deployment")
    deployment_host = deployment.get("host") if isinstance(deployment, dict) else None
    if (
        manifest.get("schema_version") != 2
        or manifest.get("backup_id") != backup_id
        or manifest.get("secret_material_recorded") is not False
        or manifest.get("consistent_redis_snapshot") is not True
        or manifest.get("encrypted_before_transfer") is not True
        or manifest.get("formal_todo0012_claim") is not False
        or not isinstance(archive_record, dict)
        or not isinstance(source_host, dict)
        or not isinstance(deployment_host, dict)
    ):
        raise VaultVerificationError("backup manifest identity is incomplete")

    source_host_id = validate_sha256(
        source_host.get("host_id_sha256"), "source host ID"
    )
    if deployment_host.get("host_id_sha256") != source_host_id:
        raise VaultVerificationError("deployment and source host identities differ")

    archive_sha256 = sha256_file(archive)
    archive_size = archive.stat().st_size
    manifest_sha256 = sha256_file(manifest_path)
    manifest_size = manifest_path.stat().st_size
    vault_host_id = sha256_file(identity)
    expected_receipt = {
        "schema_version": 1,
        "backup_id": backup_id,
        "archive_sha256": archive_sha256,
        "archive_size": archive_size,
        "manifest_sha256": manifest_sha256,
        "manifest_size": manifest_size,
        "vault_host_id_sha256": vault_host_id,
        "remote_readback_sha256": True,
        "create_only": True,
        "secret_material_recorded": False,
    }
    for field, value in expected_receipt.items():
        if receipt.get(field) != value:
            raise VaultVerificationError(f"remote receipt field differs: {field}")
    if source_host_id == vault_host_id:
        raise VaultVerificationError(
            "vault and source host identities are not distinct"
        )
    if (
        archive_record.get("sha256") != archive_sha256
        or archive_record.get("size_bytes") != archive_size
    ):
        raise VaultVerificationError("manifest archive binding differs")
    validate_sha256(archive_record.get("plaintext_sha256"), "plaintext archive digest")
    validate_sha256(manifest.get("backup_policy_sha256"), "backup policy digest")
    validate_manifest_link_contract(manifest)

    return (
        archive,
        manifest_path,
        receipt_path,
        identity,
        manifest,
        receipt,
    )


def redis_reference(manifest: dict[str, Any]) -> dict[str, Any]:
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise VaultVerificationError("manifest sources are invalid")
    references = [
        item
        for item in sources
        if isinstance(item, dict) and item.get("id") == "redis_snapshot"
    ]
    if len(references) != 1:
        raise VaultVerificationError(
            "manifest must contain one Redis snapshot reference"
        )
    reference = references[0]
    if reference.get("archive_path") != "redis/dump.rdb":
        raise VaultVerificationError("Redis snapshot archive path differs")
    validate_sha256(reference.get("sha256"), "Redis snapshot digest")
    size = reference.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size < 9:
        raise VaultVerificationError("Redis snapshot size is invalid")
    return reference


def validate_member(member: tarfile.TarInfo, observed: set[str]) -> None:
    name = member.name
    path = PurePosixPath(name)
    if not name or name == "." or path.is_absolute() or ".." in path.parts:
        raise VaultVerificationError(f"unsafe archive member path: {name!r}")
    if name in observed:
        raise VaultVerificationError(f"duplicate archive member: {name}")
    observed.add(name)
    if member.issym() or member.islnk():
        raise VaultVerificationError(f"archive links are forbidden: {name}")
    if not (member.isfile() or member.isdir()):
        raise VaultVerificationError(f"archive special files are forbidden: {name}")


def extract_and_verify_stream(
    plaintext: BinaryIO,
    *,
    manifest: dict[str, Any],
    redis_destination: Path,
) -> dict[str, Any]:
    reader = HashingReader(plaintext)
    reference = redis_reference(manifest)
    observed: set[str] = set()
    redis_count = 0
    redis_digest = hashlib.sha256()
    redis_size = 0
    redis_header = b""

    try:
        with tarfile.open(fileobj=reader, mode="r|*") as archive:
            for member in archive:
                validate_member(member, observed)
                if member.name != "redis/dump.rdb":
                    continue
                redis_count += 1
                source = archive.extractfile(member)
                if source is None:
                    raise VaultVerificationError("Redis snapshot is not extractable")
                descriptor = os.open(
                    redis_destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as output:
                    while True:
                        block = source.read(CHUNK_BYTES)
                        if not block:
                            break
                        if not redis_header:
                            redis_header = block[:5]
                        redis_digest.update(block)
                        redis_size += len(block)
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
    except (tarfile.TarError, OSError) as exc:
        raise VaultVerificationError(
            f"cannot inspect plaintext archive: {exc}"
        ) from exc

    while reader.read(CHUNK_BYTES):
        pass
    if redis_count != 1:
        raise VaultVerificationError(
            "plaintext archive must contain one Redis snapshot"
        )
    if redis_header != b"REDIS":
        raise VaultVerificationError("Redis snapshot header is invalid")
    if redis_size != reference["size_bytes"]:
        raise VaultVerificationError("Redis snapshot size differs from manifest")
    if redis_digest.hexdigest() != reference["sha256"]:
        raise VaultVerificationError("Redis snapshot digest differs from manifest")
    expected_plaintext = manifest["archive"]["plaintext_sha256"]
    if reader.digest.hexdigest() != expected_plaintext:
        raise VaultVerificationError("plaintext archive digest differs from manifest")
    return {
        "plaintext_sha256": reader.digest.hexdigest(),
        "plaintext_size_bytes": reader.size,
        "member_count": len(observed),
        "redis_sha256": redis_digest.hexdigest(),
        "redis_size_bytes": redis_size,
    }


def run_redis_check(
    rdb: Path,
    *,
    docker: str,
    redis_image: str,
    runner: RunCommand,
) -> None:
    if re.fullmatch(r"(?:sha256:|[^@\s]+@sha256:)[0-9a-f]{64}", redis_image) is None:
        raise VaultVerificationError(
            "Redis validation image must use an immutable digest"
        )
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
        "--mount",
        f"type=bind,source={rdb},target=/audit/dump.rdb,readonly",
        redis_image,
        "redis-check-rdb",
        "/audit/dump.rdb",
    ]
    try:
        completed = runner(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VaultVerificationError(f"redis-check-rdb could not run: {exc}") from exc
    if completed.returncode != 0:
        raise VaultVerificationError("redis-check-rdb rejected the snapshot")


def stop_failed_decryptor(process: Any) -> None:
    """Best-effort cleanup when tar validation stops consuming age stdout."""
    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
        process.wait(timeout=5)


def verify_backup(
    *,
    vault_root: Path,
    backup_id: str,
    age_identity: Path,
    age: str,
    docker: str,
    redis_image: str,
    runner: RunCommand = subprocess.run,
    starter: StartCommand = subprocess.Popen,
    generated_at: str | None = None,
) -> dict[str, Any]:
    (
        encrypted,
        manifest_path,
        receipt_path,
        vault_identity,
        manifest,
        _receipt,
    ) = validate_metadata(vault_root, backup_id)
    identity = require_private_identity(age_identity)

    with tempfile.TemporaryDirectory(prefix="boost-gateway-vault-verify-") as text:
        temporary = Path(text)
        os.chmod(temporary, 0o700)
        rdb = temporary / "dump.rdb"
        try:
            process = starter(
                [age, "--decrypt", "--identity", str(identity), str(encrypted)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise VaultVerificationError(f"age could not start: {exc}") from exc
        if process.stdout is None:
            raise VaultVerificationError("age stdout is unavailable")
        try:
            archive_result = extract_and_verify_stream(
                process.stdout, manifest=manifest, redis_destination=rdb
            )
        except Exception:
            process.stdout.close()
            stop_failed_decryptor(process)
            raise
        process.stdout.close()
        try:
            return_code = process.wait(timeout=300)
        except subprocess.TimeoutExpired as exc:
            stop_failed_decryptor(process)
            raise VaultVerificationError("age decryption timed out") from exc
        stderr = process.stderr.read() if process.stderr is not None else b""
        if return_code != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:200]
            raise VaultVerificationError(f"age decryption failed: {detail}")
        run_redis_check(rdb, docker=docker, redis_image=redis_image, runner=runner)

    return {
        "schema_version": 1,
        "generated_at": generated_at or utc_now(),
        "backup_id": backup_id,
        "overall_pass": True,
        "checks": {
            "metadata_binding": True,
            "distinct_host_identity": True,
            "age_decryption": True,
            "safe_archive_members": True,
            "redis_manifest_binding": True,
            "redis_check_rdb": True,
        },
        "artifacts": {
            "archive_sha256": sha256_file(encrypted),
            "manifest_sha256": sha256_file(manifest_path),
            "receipt_sha256": sha256_file(receipt_path),
            "vault_host_id_sha256": sha256_file(vault_identity),
            **archive_result,
        },
        "formal_todo0012_claim": False,
        "restore_known_good": False,
        "secret_material_recorded": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--age-identity", type=Path, required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("--age", default="age")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--redis-image", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = verify_backup(
            vault_root=args.vault_root,
            backup_id=args.backup_id,
            age_identity=args.age_identity,
            age=args.age,
            docker=args.docker,
            redis_image=args.redis_image,
        )
        write_new(args.summary_path, canonical_json(summary), 0o600)
    except (BackupError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"backup vault verification: FAIL: {exc}", file=sys.stderr)
        return 1
    print("backup vault verification: PASS")
    print(f"summary: {args.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
