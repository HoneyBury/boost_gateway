#!/usr/bin/env python3
"""Create encrypted backups and operate a create-only off-host backup vault."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import uuid
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_POLICY = ROOT / "deploy/operations/backup-recovery-policy.example.json"
DEFAULT_LOCK = Path("/var/lib/boost-gateway/deployment-transactions/.lifecycle.lock")
BACKUP_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
FRAME = struct.Struct("!Q")
MAX_HEADER_BYTES = 64 * 1024
CHUNK_BYTES = 1024 * 1024
CommandRunner = Callable[..., subprocess.CompletedProcess[Any]]
SourceRoot = tuple[str, Path]


class BackupError(RuntimeError):
    """Raised when a backup operation cannot preserve its safety contract."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BackupError(f"{label} must be a regular non-symlink file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise BackupError(f"{label} must be a JSON object")
    return value


def write_new(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def validate_backup_id(value: str) -> str:
    if BACKUP_ID_RE.fullmatch(value) is None or value.startswith("."):
        raise BackupError("backup ID is invalid")
    return value


def validate_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise BackupError(f"{label} is not a SHA-256 digest")
    return value


def read_exact(stream: BinaryIO, size: int, label: str) -> bytes:
    blocks: list[bytes] = []
    remaining = size
    while remaining:
        block = stream.read(min(remaining, CHUNK_BYTES))
        if not block:
            raise BackupError(f"truncated {label}")
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
                    raise BackupError(f"truncated {label}")
                output.write(block)
                remaining -= len(block)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def require_regular(path: Path, label: str) -> Path:
    resolved = path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_file():
        raise BackupError(f"{label} must be a regular non-symlink file: {path}")
    return resolved


def require_directory(path: Path, label: str) -> Path:
    resolved = path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_dir():
        raise BackupError(f"{label} must be a directory, not a symlink: {path}")
    return resolved


def ensure_directory(path: Path, label: str, mode: int = 0o700) -> Path:
    path.mkdir(mode=mode, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise BackupError(f"{label} must be a non-symlink directory: {path}")
    return path.resolve()


@contextmanager
def lifecycle_lock(path: Path) -> Iterable[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def run_checked(
    runner: CommandRunner, command: list[str], **kwargs: Any
) -> subprocess.CompletedProcess[Any]:
    try:
        return runner(command, check=True, **kwargs)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise BackupError(f"command failed ({command[0]}): {exc}") from exc


def stage_redis_snapshot(
    destination: Path,
    *,
    container: str,
    docker: str,
    runner: CommandRunner = subprocess.run,
) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container):
        raise BackupError("Redis container name is invalid")
    remote_path = f"/tmp/boost-gateway-backup-{uuid.uuid4().hex}.rdb"
    try:
        run_checked(
            runner,
            [docker, "exec", container, "redis-cli", "--rdb", remote_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
        )
        run_checked(
            runner,
            [docker, "cp", f"{container}:{remote_path}", str(destination)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
        )
    finally:
        runner(
            [docker, "exec", container, "rm", "-f", "--", remote_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    require_regular(destination, "Redis snapshot")
    with destination.open("rb") as snapshot_stream:
        header = snapshot_stream.read(5)
    if destination.stat().st_size < 9 or header != b"REDIS":
        raise BackupError("Redis snapshot does not have an RDB header")


def policy_sources(policy: dict[str, Any]) -> list[SourceRoot]:
    source_contracts = policy.get("backup", {}).get("source_contracts")
    if not isinstance(source_contracts, list):
        raise BackupError("policy backup source contracts are invalid")
    sources: list[SourceRoot] = []
    identifiers: set[str] = set()
    for contract in source_contracts:
        if not isinstance(contract, dict) or contract.get("required") is not True:
            continue
        if contract.get("kind") == "generated_redis_snapshot":
            continue
        identifier = contract.get("id")
        path = contract.get("path")
        if not isinstance(identifier, str) or not isinstance(path, str):
            raise BackupError("policy source contract is invalid")
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", identifier) is None:
            raise BackupError(f"policy source ID is invalid: {identifier!r}")
        if identifier in identifiers:
            raise BackupError(f"policy source ID is duplicated: {identifier}")
        identifiers.add(identifier)
        sources.append((identifier, require_directory(Path(path), identifier)))
    return sources


def source_relative_target(target: Path, sources: list[SourceRoot]) -> tuple[str, str]:
    matches: list[tuple[int, str, Path]] = []
    for identifier, root in sources:
        try:
            relative = target.relative_to(root)
        except ValueError:
            continue
        matches.append((len(root.parts), identifier, relative))
    if not matches:
        raise BackupError(
            f"symbolic link target escapes declared source roots: {target}"
        )
    _, identifier, relative = max(matches, key=lambda item: item[0])
    return identifier, relative.as_posix() or "."


def validated_symbolic_link(
    link: Path,
    *,
    archive_path: str,
    sources: list[SourceRoot],
) -> dict[str, Any]:
    try:
        original = os.readlink(link)
        if not original or any(ord(char) < 32 for char in original):
            raise BackupError(f"symbolic link text is invalid: {link}")
        unresolved = Path(original)
        candidate = unresolved if unresolved.is_absolute() else link.parent / unresolved
        target = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BackupError(f"symbolic link is broken or invalid: {link}: {exc}") from exc
    target_source_id, target_relative_path = source_relative_target(target, sources)
    if target.is_file():
        target_type = "file"
    elif target.is_dir():
        target_type = "directory"
    else:
        raise BackupError(f"symbolic link target has unsupported type: {link}")
    return {
        "archive_path": archive_path,
        "original_link_text": original,
        "target_source_id": target_source_id,
        "target_relative_path": target_relative_path,
        "target_type": target_type,
    }


def collect_source_entries(
    identifier: str,
    source: Path,
    sources: list[SourceRoot],
) -> tuple[list[tuple[Path, str]], list[dict[str, Any]]]:
    archive_root = f"sources/{identifier}"
    entries: list[tuple[Path, str]] = [(source, archive_root)]
    links: list[dict[str, Any]] = []

    def visit(directory: Path, archive_directory: str) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise BackupError(
                f"cannot enumerate backup source: {directory}: {exc}"
            ) from exc
        for child in children:
            if any(ord(char) < 32 for char in child.name):
                raise BackupError(f"backup source entry name is invalid: {child.path}")
            child_path = Path(child.path)
            child_archive = f"{archive_directory}/{child.name}"
            try:
                mode = child.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                raise BackupError(
                    f"cannot inspect backup source entry: {child_path}: {exc}"
                ) from exc
            if stat.S_ISLNK(mode):
                links.append(
                    validated_symbolic_link(
                        child_path, archive_path=child_archive, sources=sources
                    )
                )
                continue
            if stat.S_ISDIR(mode):
                entries.append((child_path, child_archive))
                visit(child_path, child_archive)
                continue
            if stat.S_ISREG(mode):
                entries.append((child_path, child_archive))
                continue
            raise BackupError(f"backup source entry has unsupported type: {child_path}")

    visit(source, archive_root)
    return entries, links


def link_free_tar_filter(member: tarfile.TarInfo) -> tarfile.TarInfo:
    if member.issym() or member.islnk():
        raise BackupError(f"archive link entry is forbidden: {member.name}")
    if not (member.isdir() or member.isreg()):
        raise BackupError(f"archive member type is forbidden: {member.name}")
    return member


def add_link_free_tar_entry(
    bundle: tarfile.TarFile, source: Path, archive_path: str
) -> None:
    try:
        member = link_free_tar_filter(
            bundle.gettarinfo(str(source), arcname=archive_path)
        )
    except OSError as exc:
        raise BackupError(f"cannot inspect archive input: {source}: {exc}") from exc
    if member.isdir():
        bundle.addfile(member)
        return

    descriptor = -1
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode):
            raise BackupError(f"archive input changed type while reading: {source}")
        current = os.lstat(source)
        if (observed.st_dev, observed.st_ino) != (current.st_dev, current.st_ino):
            raise BackupError(f"archive input changed identity while reading: {source}")
        member.size = observed.st_size
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            bundle.addfile(member, stream)
    except OSError as exc:
        raise BackupError(f"cannot safely read archive input: {source}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def verify_link_free_archive(archive: Path) -> None:
    with tarfile.open(archive, mode="r:") as bundle:
        for member in bundle:
            if member.issym() or member.islnk():
                raise BackupError(f"archive contains a link entry: {member.name}")
            if not (member.isdir() or member.isreg()):
                raise BackupError(f"archive contains unsupported entry: {member.name}")


def build_plain_archive(
    archive: Path,
    redis_snapshot: Path,
    sources: list[SourceRoot],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    references: list[dict[str, Any]] = []
    link_metadata: list[dict[str, Any]] = []
    with tarfile.open(archive, mode="x", dereference=True) as bundle:
        add_link_free_tar_entry(bundle, redis_snapshot, "redis/dump.rdb")
        references.append(
            {
                "id": "redis_snapshot",
                "archive_path": "redis/dump.rdb",
                "sha256": sha256_file(redis_snapshot),
                "size_bytes": redis_snapshot.stat().st_size,
            }
        )
        for identifier, source in sources:
            archive_path = f"sources/{identifier}"
            entries, links = collect_source_entries(identifier, source, sources)
            for entry, entry_archive_path in entries:
                add_link_free_tar_entry(bundle, entry, entry_archive_path)
            link_metadata.extend(links)
            references.append(
                {
                    "id": identifier,
                    "archive_path": archive_path,
                    "symbolic_link_count": len(links),
                }
            )
    verify_link_free_archive(archive)
    return references, sorted(link_metadata, key=lambda item: item["archive_path"])


def encrypt_archive(
    plaintext: Path,
    encrypted: Path,
    *,
    recipient_file: Path,
    age: str,
    runner: CommandRunner = subprocess.run,
) -> None:
    require_regular(recipient_file, "age recipient file")
    temporary = encrypted.parent / f".{encrypted.name}.{uuid.uuid4().hex}.tmp"
    try:
        run_checked(
            runner,
            [
                age,
                "--encrypt",
                "--recipients-file",
                str(recipient_file),
                "--output",
                str(temporary),
                str(plaintext),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=1800,
        )
        require_regular(temporary, "encrypted archive")
        os.link(temporary, encrypted)
    except FileExistsError as exc:
        raise BackupError(
            f"create-only encrypted archive already exists: {encrypted}"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def deployment_binding(path: Path) -> dict[str, Any]:
    record = load_json_object(path, "deployment record")
    required = {"deployment_id", "tag", "commit", "runtime_asset_sha256", "host"}
    missing = sorted(required - set(record))
    if missing:
        raise BackupError(f"deployment record lacks identity fields: {missing}")
    return {key: record[key] for key in sorted(required)}


def create_encrypted_backup(
    *,
    backup_id: str,
    policy_path: Path,
    redis_profile: Path,
    deployment_record: Path,
    recipient_file: Path,
    staging_root: Path,
    output_root: Path,
    lock_path: Path,
    redis_container: str,
    docker: str,
    age: str,
    retention_classes: list[str],
    runner: CommandRunner = subprocess.run,
    identity: dict[str, Any] | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    validate_backup_id(backup_id)
    if not retention_classes or not set(retention_classes) <= {"daily", "weekly"}:
        raise BackupError("retention classes must contain daily and/or weekly")
    policy = load_json_object(policy_path, "backup policy")
    profile = require_regular(redis_profile, "Redis profile")
    recipient = require_regular(recipient_file, "age recipient file")
    output_root.mkdir(parents=True, exist_ok=True)
    encrypted = output_root / f"{backup_id}.tar.age"
    manifest_path = output_root / f"{backup_id}.manifest.json"
    if (
        encrypted.exists()
        or encrypted.is_symlink()
        or manifest_path.exists()
        or manifest_path.is_symlink()
    ):
        raise BackupError(f"create-only backup artifacts already exist: {backup_id}")

    staging_root.mkdir(parents=True, exist_ok=True)
    with lifecycle_lock(lock_path), tempfile.TemporaryDirectory(
        prefix=f".{backup_id}.", dir=staging_root
    ) as temporary_text:
        temporary = Path(temporary_text)
        os.chmod(temporary, 0o700)
        snapshot = temporary / "redis.rdb"
        plaintext = temporary / "payload.tar"
        stage_redis_snapshot(
            snapshot, container=redis_container, docker=docker, runner=runner
        )
        sources = policy_sources(policy)
        references, source_links = build_plain_archive(plaintext, snapshot, sources)
        plaintext_sha256 = sha256_file(plaintext)
        encrypt_archive(
            plaintext,
            encrypted,
            recipient_file=recipient,
            age=age,
            runner=runner,
        )

    if identity is None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.lib.operations_identity import collect_operations_identity

        observed_identity = collect_operations_identity()
    else:
        observed_identity = identity
    host = observed_identity.get("host")
    operator = observed_identity.get("operator")
    if not isinstance(host, dict) or not isinstance(operator, dict):
        encrypted.unlink(missing_ok=True)
        raise BackupError("operations identity is incomplete")
    manifest = {
        "schema_version": 2,
        "backup_id": backup_id,
        "created_at": now(),
        "archive": {
            "name": encrypted.name,
            "sha256": sha256_file(encrypted),
            "size_bytes": encrypted.stat().st_size,
            "plaintext_sha256": plaintext_sha256,
        },
        "deployment": deployment_binding(deployment_record),
        "source_host": host,
        "operator": operator,
        "redis_profile_sha256": sha256_file(profile),
        "backup_policy_sha256": sha256_file(policy_path),
        "recipient_file_sha256": sha256_file(recipient),
        "policy_activation_state": policy.get("activation", {}).get("state"),
        "sources": references,
        "source_links": source_links,
        "archive_contract": {
            "format": "link_free_tar_v1",
            "symbolic_link_entries": 0,
            "hard_link_entries": 0,
            "symbolic_links_recorded": len(source_links),
        },
        "retention_classes": sorted(set(retention_classes)),
        "consistent_redis_snapshot": True,
        "encrypted_before_transfer": True,
        "formal_todo0012_claim": False,
        "secret_material_recorded": False,
    }
    try:
        write_new(manifest_path, canonical_json(manifest), 0o640)
    except Exception:
        encrypted.unlink(missing_ok=True)
        raise
    return encrypted, manifest_path, manifest


def write_upload_frame(
    stream: BinaryIO, backup_id: str, archive: Path, manifest: Path
) -> None:
    archive_path = require_regular(archive, "encrypted archive")
    manifest_path = require_regular(manifest, "backup manifest")
    header = canonical_json(
        {
            "schema_version": 1,
            "backup_id": validate_backup_id(backup_id),
            "archive_size": archive_path.stat().st_size,
            "archive_sha256": sha256_file(archive_path),
            "manifest_size": manifest_path.stat().st_size,
            "manifest_sha256": sha256_file(manifest_path),
        }
    )
    stream.write(FRAME.pack(len(header)))
    stream.write(header)
    for path in (archive_path, manifest_path):
        with path.open("rb") as source:
            shutil.copyfileobj(source, stream, length=CHUNK_BYTES)


def vault_host_id(identity_file: Path) -> str:
    identity = require_regular(identity_file, "vault identity file")
    if identity.stat().st_size < 16 or identity.stat().st_size > 4096:
        raise BackupError("vault identity file size is invalid")
    return sha256_file(identity)


def parse_upload_header(stream: BinaryIO) -> dict[str, Any]:
    length_bytes = read_exact(stream, FRAME.size, "upload header length")
    length = FRAME.unpack(length_bytes)[0]
    if length <= 0 or length > MAX_HEADER_BYTES:
        raise BackupError("upload header length is invalid")
    try:
        value = json.loads(read_exact(stream, length, "upload header").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"upload header is invalid: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise BackupError("upload header schema is invalid")
    validate_backup_id(str(value.get("backup_id", "")))
    validate_sha256(value.get("archive_sha256"), "archive digest")
    validate_sha256(value.get("manifest_sha256"), "manifest digest")
    for field in ("archive_size", "manifest_size"):
        size = value.get(field)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise BackupError(f"{field} is invalid")
    return value


def safe_manifest_path(value: object, *, prefix: str | None = None) -> bool:
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


def validate_manifest_link_contract(manifest: dict[str, Any]) -> None:
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise BackupError("backup manifest source inventory is invalid")
    source_ids: set[str] = set()
    archive_paths_by_source: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise BackupError("backup manifest source inventory is invalid")
        identifier = source.get("id")
        archive_path = source.get("archive_path")
        if (
            not isinstance(identifier, str)
            or identifier in source_ids
            or not safe_manifest_path(archive_path)
        ):
            raise BackupError("backup manifest source inventory is invalid")
        source_ids.add(identifier)
        archive_paths_by_source[identifier] = archive_path

    for identifier, archive_path in archive_paths_by_source.items():
        expected = (
            "redis/dump.rdb"
            if identifier == "redis_snapshot"
            else f"sources/{identifier}"
        )
        if archive_path != expected:
            raise BackupError("backup manifest source inventory is invalid")

    archive_contract = manifest.get("archive_contract")
    links = manifest.get("source_links")
    if (
        not isinstance(archive_contract, dict)
        or archive_contract.get("format") != "link_free_tar_v1"
        or archive_contract.get("symbolic_link_entries") != 0
        or archive_contract.get("hard_link_entries") != 0
        or not isinstance(links, list)
        or archive_contract.get("symbolic_links_recorded") != len(links)
    ):
        raise BackupError("backup manifest link-free archive contract is invalid")

    seen_archive_paths: set[str] = set()
    for link in links:
        if not isinstance(link, dict) or set(link) != {
            "archive_path",
            "original_link_text",
            "target_source_id",
            "target_relative_path",
            "target_type",
        }:
            raise BackupError("backup manifest symbolic link metadata is invalid")
        archive_path = link.get("archive_path")
        target_source_id = link.get("target_source_id")
        target_relative_path = link.get("target_relative_path")
        archive_parts = (
            PurePosixPath(archive_path).parts if isinstance(archive_path, str) else ()
        )
        if (
            not safe_manifest_path(archive_path, prefix="sources")
            or len(archive_parts) < 3
            or archive_parts[1] not in source_ids
            or archive_parts[1] == "redis_snapshot"
            or archive_path in seen_archive_paths
            or not isinstance(link.get("original_link_text"), str)
            or not link["original_link_text"]
            or any(ord(char) < 32 for char in link["original_link_text"])
            or target_source_id not in source_ids
            or (
                target_relative_path != "."
                and not safe_manifest_path(target_relative_path)
            )
            or link.get("target_type") not in {"file", "directory"}
        ):
            raise BackupError("backup manifest symbolic link metadata is invalid")
        seen_archive_paths.add(archive_path)


def validate_manifest_binding(
    manifest_path: Path, header: dict[str, Any]
) -> dict[str, Any]:
    manifest = load_json_object(manifest_path, "backup manifest")
    archive = manifest.get("archive")
    if (
        manifest.get("schema_version") != 2
        or manifest.get("backup_id") != header["backup_id"]
        or not isinstance(archive, dict)
        or archive.get("sha256") != header["archive_sha256"]
        or archive.get("size_bytes") != header["archive_size"]
        or manifest.get("secret_material_recorded") is not False
    ):
        raise BackupError("backup manifest does not bind the uploaded archive")
    validate_sha256(manifest.get("backup_policy_sha256"), "backup policy digest")
    validate_manifest_link_contract(manifest)
    classes = manifest.get("retention_classes")
    if (
        not isinstance(classes, list)
        or not classes
        or not set(classes) <= {"daily", "weekly"}
    ):
        raise BackupError("backup manifest retention classes are invalid")
    created_at = manifest.get("created_at")
    try:
        parsed_created_at = datetime.fromisoformat(
            str(created_at).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise BackupError("backup manifest created_at is invalid") from exc
    if (
        not isinstance(created_at, str)
        or not created_at.endswith("Z")
        or parsed_created_at.tzinfo != UTC
    ):
        raise BackupError("backup manifest created_at must be an RFC3339 UTC timestamp")
    return manifest


def remote_store(
    vault_root: Path,
    identity_file: Path,
    stream: BinaryIO,
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    root = ensure_directory(vault_root, "vault root")
    header = parse_upload_header(stream)
    backup_id = header["backup_id"]
    backups_root = ensure_directory(root / "backups", "vault backups root")
    final = backups_root / backup_id
    incoming_root = ensure_directory(root / ".incoming", "vault incoming root")
    if final.exists() or final.is_symlink():
        raise BackupError(f"create-only remote backup already exists: {backup_id}")
    temporary = incoming_root / f"{backup_id}.{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700)
    archive_path = temporary / "payload.tar.age"
    manifest_path = temporary / "manifest.json"
    try:
        copy_exact(stream, archive_path, header["archive_size"], "encrypted archive")
        copy_exact(stream, manifest_path, header["manifest_size"], "backup manifest")
        if stream.read(1):
            raise BackupError("upload contains trailing bytes")
        if sha256_file(archive_path) != header["archive_sha256"]:
            raise BackupError("remote archive readback digest differs")
        if sha256_file(manifest_path) != header["manifest_sha256"]:
            raise BackupError("remote manifest readback digest differs")
        validate_manifest_binding(manifest_path, header)
        receipt = {
            "schema_version": 1,
            "backup_id": backup_id,
            "stored_at": recorded_at or now(),
            "archive_sha256": header["archive_sha256"],
            "archive_size": header["archive_size"],
            "manifest_sha256": header["manifest_sha256"],
            "manifest_size": header["manifest_size"],
            "vault_host_id_sha256": vault_host_id(identity_file),
            "remote_readback_sha256": True,
            "create_only": True,
            "secret_material_recorded": False,
        }
        write_new(temporary / "receipt.json", canonical_json(receipt), 0o600)
        os.rename(temporary, final)
        return receipt
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def remote_receipt(vault_root: Path, backup_id: str) -> dict[str, Any]:
    root = require_directory(vault_root, "vault root")
    backups = require_directory(root / "backups", "vault backups root")
    path = backups / validate_backup_id(backup_id) / "receipt.json"
    return load_json_object(path, "remote receipt")


def verify_remote_receipt(
    receipt: dict[str, Any],
    *,
    backup_id: str,
    archive: Path,
    manifest: Path,
    source_host_id_sha256: str,
    expected_remote_host_id_sha256: str,
) -> None:
    expected = {
        "schema_version": 1,
        "backup_id": validate_backup_id(backup_id),
        "archive_sha256": sha256_file(require_regular(archive, "encrypted archive")),
        "archive_size": archive.stat().st_size,
        "manifest_sha256": sha256_file(require_regular(manifest, "backup manifest")),
        "manifest_size": manifest.stat().st_size,
        "vault_host_id_sha256": validate_sha256(
            expected_remote_host_id_sha256, "expected remote host ID"
        ),
        "remote_readback_sha256": True,
        "create_only": True,
        "secret_material_recorded": False,
    }
    validate_sha256(source_host_id_sha256, "source host ID")
    if source_host_id_sha256 == expected_remote_host_id_sha256:
        raise BackupError("remote vault identity is not distinct from source host")
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise BackupError(f"remote receipt field differs: {field}")


def upload_remote(
    *,
    backup_id: str,
    archive: Path,
    manifest: Path,
    remote_host: str,
    remote_command: str,
    ssh: str,
    ssh_identity_file: Path,
    ssh_known_hosts: Path,
    source_host_id_sha256: str,
    expected_remote_host_id_sha256: str,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    if remote_host.startswith("-") or any(ord(char) < 33 for char in remote_host):
        raise BackupError("remote SSH host is invalid")
    if remote_command != "boost-gateway-vault store":
        raise BackupError("remote command must use the fixed vault receiver surface")
    identity_file = require_regular(ssh_identity_file, "SSH identity file")
    known_hosts = require_regular(ssh_known_hosts, "SSH known_hosts file")
    command = [
        ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        f"IdentityFile={identity_file}",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "--",
        remote_host,
        remote_command,
    ]
    with tempfile.TemporaryFile() as framed:
        write_upload_frame(framed, backup_id, archive, manifest)
        framed.seek(0)
        completed = run_checked(
            runner,
            command,
            stdin=framed,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3600,
        )
    try:
        receipt = json.loads(completed.stdout.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"remote receipt output is invalid: {exc}") from exc
    if not isinstance(receipt, dict):
        raise BackupError("remote receipt output is not an object")
    verify_remote_receipt(
        receipt,
        backup_id=backup_id,
        archive=archive,
        manifest=manifest,
        source_host_id_sha256=source_host_id_sha256,
        expected_remote_host_id_sha256=expected_remote_host_id_sha256,
    )
    return receipt


def positive_number(value: object) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    )


def valid_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo == UTC


def evidence_record(path: Path, relative_path: str) -> dict[str, Any]:
    source = require_regular(path, relative_path)
    return {
        "path": relative_path,
        "sha256": sha256_file(source),
        "size_bytes": source.stat().st_size,
    }


def require_passed_rto(summary: dict[str, Any], label: str, maximum: float) -> None:
    elapsed = summary.get("elapsed_seconds")
    budget = summary.get("rto_budget_seconds")
    if (
        summary.get("overall_pass") is not True
        or summary.get("status") != "passed"
        or summary.get("rto_pass") is not True
        or not positive_number(elapsed)
        or not positive_number(budget)
        or budget <= 0
        or budget > maximum
        or elapsed > budget
    ):
        raise BackupError(f"{label} is not an eligible RTO pass")


def validate_known_good_sources(
    vault_root: Path,
    backup_id: str,
    vault_validation_path: Path,
    restore_summary_path: Path,
    business_summary_path: Path,
) -> dict[str, Any]:
    root = require_directory(vault_root, "vault root")
    identifier = validate_backup_id(backup_id)
    vault_identity = require_regular(root / ".vault-identity", "vault identity")
    backup_directory = require_directory(
        root / "backups" / identifier, "known-good backup"
    )
    archive = require_regular(backup_directory / "payload.tar.age", "encrypted archive")
    manifest_path = require_regular(
        backup_directory / "manifest.json", "backup manifest"
    )
    receipt_path = require_regular(backup_directory / "receipt.json", "remote receipt")
    validation_path = require_regular(vault_validation_path, "vault validation summary")
    restore_path = require_regular(restore_summary_path, "restore summary")
    business_path = require_regular(business_summary_path, "business summary")
    manifest = load_json_object(manifest_path, "backup manifest")
    receipt = load_json_object(receipt_path, "remote receipt")
    validation = load_json_object(validation_path, "vault validation summary")
    restore = load_json_object(restore_path, "restore summary")
    business = load_json_object(business_path, "business summary")

    header = {
        "backup_id": identifier,
        "archive_sha256": sha256_file(archive),
        "archive_size": archive.stat().st_size,
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_size": manifest_path.stat().st_size,
    }
    validate_manifest_binding(manifest_path, header)
    source_host = manifest.get("source_host")
    deployment = manifest.get("deployment")
    deployment_host = deployment.get("host") if isinstance(deployment, dict) else None
    archive_binding = manifest.get("archive")
    if (
        manifest.get("formal_todo0012_claim") is not False
        or manifest.get("secret_material_recorded") is not False
        or manifest.get("consistent_redis_snapshot") is not True
        or manifest.get("encrypted_before_transfer") is not True
        or not isinstance(source_host, dict)
        or not isinstance(deployment, dict)
        or not isinstance(deployment_host, dict)
        or not isinstance(archive_binding, dict)
    ):
        raise BackupError("backup manifest is not eligible for known-good attestation")
    source_host_id = validate_sha256(
        source_host.get("host_id_sha256"), "source host ID"
    )
    vault_host_id = sha256_file(vault_identity)
    if deployment_host.get("host_id_sha256") != source_host_id:
        raise BackupError("deployment and source host identities differ")
    if source_host_id == vault_host_id:
        raise BackupError("vault and source host identities are not distinct")
    required_receipt = {
        "schema_version": 1,
        **header,
        "vault_host_id_sha256": vault_host_id,
        "remote_readback_sha256": True,
        "create_only": True,
        "secret_material_recorded": False,
    }
    if any(
        receipt.get(field) != value for field, value in required_receipt.items()
    ) or not valid_utc_timestamp(receipt.get("stored_at")):
        raise BackupError("remote receipt is not eligible for known-good attestation")

    validation_checks = validation.get("checks")
    validation_artifacts = validation.get("artifacts")
    required_validation_checks = {
        "metadata_binding",
        "distinct_host_identity",
        "age_decryption",
        "safe_archive_members",
        "redis_manifest_binding",
        "redis_check_rdb",
    }
    if (
        validation.get("schema_version") != 1
        or validation.get("backup_id") != identifier
        or validation.get("overall_pass") is not True
        or validation.get("restore_known_good") is not False
        or validation.get("formal_todo0012_claim") is not False
        or validation.get("secret_material_recorded") is not False
        or not isinstance(validation_checks, dict)
        or any(
            validation_checks.get(check) is not True
            for check in required_validation_checks
        )
        or not isinstance(validation_artifacts, dict)
    ):
        raise BackupError("vault validation is not eligible for known-good attestation")
    redis_sources = [
        source
        for source in manifest.get("sources", [])
        if isinstance(source, dict) and source.get("id") == "redis_snapshot"
    ]
    if len(redis_sources) != 1:
        raise BackupError("backup manifest Redis source binding is invalid")
    redis_source = redis_sources[0]
    plaintext_sha256 = validate_sha256(
        archive_binding.get("plaintext_sha256"), "plaintext archive digest"
    )
    redis_sha256 = validate_sha256(redis_source.get("sha256"), "Redis snapshot digest")
    redis_size = redis_source.get("size_bytes")
    if (
        not isinstance(redis_size, int)
        or isinstance(redis_size, bool)
        or redis_size < 9
        or not isinstance(validation_artifacts.get("plaintext_size_bytes"), int)
        or isinstance(validation_artifacts.get("plaintext_size_bytes"), bool)
        or validation_artifacts["plaintext_size_bytes"] <= 0
        or not isinstance(validation_artifacts.get("member_count"), int)
        or isinstance(validation_artifacts.get("member_count"), bool)
        or validation_artifacts["member_count"] <= 0
    ):
        raise BackupError("vault validation size binding is invalid")
    validation_bindings = {
        "archive_sha256": header["archive_sha256"],
        "manifest_sha256": header["manifest_sha256"],
        "receipt_sha256": sha256_file(receipt_path),
        "vault_host_id_sha256": vault_host_id,
        "plaintext_sha256": plaintext_sha256,
        "redis_sha256": redis_sha256,
        "redis_size_bytes": redis_size,
    }
    if any(
        validation_artifacts.get(field) != value
        for field, value in validation_bindings.items()
    ):
        raise BackupError("vault validation artifact binding differs")

    require_passed_rto(restore, "restore summary", 600.0)
    expected_deployment = {
        field: deployment.get(field)
        for field in ("deployment_id", "tag", "commit", "runtime_asset_sha256")
    }
    for field in ("deployment_id", "tag"):
        value = expected_deployment[field]
        if (
            not isinstance(value, str)
            or not value
            or any(ord(char) < 32 for char in value)
        ):
            raise BackupError(f"deployment identity is invalid: {field}")
    commit = expected_deployment["commit"]
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise BackupError("deployment identity is invalid: commit")
    validate_sha256(expected_deployment["runtime_asset_sha256"], "runtime asset digest")
    validate_sha256(
        restore.get("transport_receipt_sha256"), "restore transport receipt digest"
    )
    if (
        restore.get("schema_version") != 1
        or restore.get("backup_id") != identifier
        or restore.get("deployment") != expected_deployment
        or restore.get("backup_manifest_sha256") != header["manifest_sha256"]
        or restore.get("remote_receipt_sha256") != sha256_file(receipt_path)
        or restore.get("vault_validation_sha256") != sha256_file(validation_path)
        or restore.get("backup_policy_sha256") != manifest.get("backup_policy_sha256")
        or restore.get("redis_profile_sha256") != manifest.get("redis_profile_sha256")
        or restore.get("source_host_id_sha256") != source_host_id
        or restore.get("vault_host_id_sha256") != vault_host_id
        or restore.get("redis_snapshot_sha256") != redis_sha256
        or restore.get("leaderboard_seed_exact") is not True
        or restore.get("redis_ping") is not True
        or restore.get("transport_remote_readback_bound") is not True
        or restore.get("offline_redis_check_rdb") is not True
        or restore.get("restore_payload_copy_verified") is not True
        or restore.get("vault_link_free_validation_bound") is not True
        or restore.get("active_volume_preserved") is not True
        or restore.get("target_volume_retained") is not True
        or restore.get("production_switched") is not False
        or restore.get("active_volume_mounted_by_drill") is not False
        or restore.get("restore_known_good") is not False
        or restore.get("formal_todo0012_claim") is not False
        or restore.get("secret_material_recorded") is not False
        or restore.get("cleanup_failures") != []
    ):
        raise BackupError("restore summary is not eligible for known-good attestation")
    restore_id = validate_backup_id(str(restore.get("restore_id", "")))
    target_volume = validate_backup_id(str(restore.get("target_volume", "")))
    restore_target_identity = validate_sha256(
        restore.get("target_volume_identity_sha256"), "restore target volume identity"
    )
    restored_seed = validate_sha256(
        restore.get("canonical_seed_restored_sha256"), "restored seed digest"
    )

    require_passed_rto(business, "business summary", 300.0)
    business_id = validate_backup_id(str(business.get("business_validation_id", "")))
    for field in (
        "deployment_record_sha256",
        "release_manifest_sha256",
        "release_sdk_full_flow_sha256",
        "work_volume_snapshot_sha256",
    ):
        validate_sha256(business.get(field), f"business {field}")
    if (
        business.get("schema_version") != 1
        or business.get("backup_id") != identifier
        or business.get("restore_id") != restore_id
        or business.get("deployment") != expected_deployment
        or business.get("restore_summary_sha256") != sha256_file(restore_path)
        or business.get("retained_volume") != target_volume
        or business.get("retained_volume_identity_sha256") != restore_target_identity
        or business.get("retained_volume_identity_after_sha256")
        != restore_target_identity
        or business.get("active_volume") != restore.get("active_volume")
        or business.get("active_volume_identity_after_sha256")
        != restore.get("active_volume_identity_sha256")
        or business.get("active_volume_unchanged") is not True
        or business.get("redis_image") != restore.get("redis_image")
        or business.get("restore_volume_identity_binding_verified") is not True
        or business.get("restore_snapshot_binding_verified") is not True
        or business.get("restore_redis_image_binding_verified") is not True
        or business.get("gateway_runtime_binding_verified") is not True
        or business.get("retained_seed_before_sha256") != restored_seed
        or business.get("retained_seed_after_sha256") != restored_seed
        or business.get("retained_seed_unchanged") is not True
        or business.get("leaderboard_submit") is not True
        or business.get("leaderboard_top") is not True
        or business.get("leaderboard_rank") is not True
        or business.get("sdk_full_flow_checked") is not True
        or business.get("source_build_performed") is not False
        or business.get("public_conan_access_performed") is not False
        or business.get("retained_volume_mounted_readonly") is not True
        or business.get("work_seed_mutated_by_business_checks") is not True
        or business.get("work_volume_created") is not True
        or business.get("work_volume_removed") is not True
        or business.get("isolated_network_created") is not True
        or business.get("isolated_network_internal") is not True
        or business.get("isolated_network_removed") is not True
        or business.get("production_switched") is not False
        or business.get("restore_known_good") is not False
        or business.get("formal_todo0012_claim") is not False
        or business.get("secret_material_recorded") is not False
        or business.get("cleanup_failures") != []
        or business.get("active_volume_identity_sha256")
        != restore.get("active_volume_identity_sha256")
    ):
        raise BackupError("business summary is not eligible for known-good attestation")

    return {
        "backup_id": identifier,
        "restore_id": restore_id,
        "business_validation_id": business_id,
        "identities": {
            "source_host_id_sha256": source_host_id,
            "vault_host_id_sha256": vault_host_id,
            "deployment": expected_deployment,
            "target_volume": target_volume,
            "target_volume_identity_sha256": restore_target_identity,
        },
        "evidence": {
            "backup_manifest": evidence_record(
                manifest_path, f"backups/{identifier}/manifest.json"
            ),
            "remote_receipt": evidence_record(
                receipt_path, f"backups/{identifier}/receipt.json"
            ),
            "vault_validation": evidence_record(
                validation_path, "vault-validation.json"
            ),
            "restore_summary": evidence_record(restore_path, "restore-summary.json"),
            "recovery_aggregate": evidence_record(
                business_path, "business-summary.json"
            ),
        },
        "formal_flags": {
            "backup_manifest": False,
            "vault_validation": False,
            "restore_summary": False,
            "recovery_aggregate": False,
            "attestation": False,
        },
    }


def copy_regular_new(source: Path, destination: Path, label: str) -> None:
    path = require_regular(source, label)
    write_new(destination, path.read_bytes(), 0o600)
    os.chmod(destination, 0o600)


def create_known_good_attestation(
    vault_root: Path,
    *,
    backup_id: str,
    vault_validation_summary: Path,
    restore_summary: Path,
    business_summary: Path,
    attested_at: str | None = None,
) -> dict[str, Any]:
    root = require_directory(vault_root, "vault root")
    initial = validate_known_good_sources(
        root,
        backup_id,
        vault_validation_summary,
        restore_summary,
        business_summary,
    )
    known_good_root = ensure_directory(root / "known-good", "known-good root")
    target = known_good_root / validate_backup_id(backup_id)
    try:
        target.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise BackupError(
            f"create-only known-good attestation already exists: {backup_id}"
        ) from exc
    marker = target / ".incomplete"
    try:
        os.chmod(target, 0o700)
        write_new(marker, b"known-good attestation in progress\n", 0o600)
        copy_regular_new(
            vault_validation_summary,
            target / "vault-validation.json",
            "vault validation summary",
        )
        copy_regular_new(
            restore_summary, target / "restore-summary.json", "restore summary"
        )
        copy_regular_new(
            business_summary, target / "business-summary.json", "business summary"
        )
        copied = validate_known_good_sources(
            root,
            backup_id,
            target / "vault-validation.json",
            target / "restore-summary.json",
            target / "business-summary.json",
        )
        if copied != initial:
            raise BackupError("known-good evidence changed while being copied")
        timestamp = attested_at or now()
        if not valid_utc_timestamp(timestamp):
            raise BackupError("known-good attestation timestamp is invalid")
        attestation = {
            "schema_version": 1,
            "attested_at": timestamp,
            **copied,
            "overall_pass": True,
            "restore_known_good": True,
            "formal_todo0012_claim": False,
            "secret_material_recorded": False,
        }
        write_new(target / "attestation.json", canonical_json(attestation), 0o600)
        marker.unlink()
        return attestation
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def load_known_good_attestation(vault_root: Path, backup_id: str) -> dict[str, Any]:
    root = require_directory(vault_root, "vault root")
    identifier = validate_backup_id(backup_id)
    directory = require_directory(
        root / "known-good" / identifier, "known-good attestation"
    )
    expected_names = {
        "attestation.json",
        "vault-validation.json",
        "restore-summary.json",
        "business-summary.json",
    }
    if {entry.name for entry in directory.iterdir()} != expected_names:
        raise BackupError("known-good attestation inventory is invalid")
    attestation_path = require_regular(
        directory / "attestation.json", "known-good attestation"
    )
    attestation = load_json_object(attestation_path, "known-good attestation")
    binding = validate_known_good_sources(
        root,
        identifier,
        directory / "vault-validation.json",
        directory / "restore-summary.json",
        directory / "business-summary.json",
    )
    expected = {
        "schema_version": 1,
        "attested_at": attestation.get("attested_at"),
        **binding,
        "overall_pass": True,
        "restore_known_good": True,
        "formal_todo0012_claim": False,
        "secret_material_recorded": False,
    }
    if (
        not valid_utc_timestamp(attestation.get("attested_at"))
        or attestation != expected
    ):
        raise BackupError("known-good attestation binding is invalid")
    return {
        **attestation,
        "attestation_sha256": sha256_file(attestation_path),
    }


def verified_vault_records(vault_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    root = require_directory(vault_root, "vault root")
    backups = require_directory(root / "backups", "vault backups root")
    for directory in sorted(backups.glob("*")):
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or BACKUP_ID_RE.fullmatch(directory.name) is None
        ):
            continue
        archive = directory / "payload.tar.age"
        manifest_path = directory / "manifest.json"
        receipt_path = directory / "receipt.json"
        try:
            manifest = load_json_object(manifest_path, "backup manifest")
            receipt = load_json_object(receipt_path, "remote receipt")
            header = {
                "backup_id": directory.name,
                "archive_sha256": sha256_file(
                    require_regular(archive, "encrypted archive")
                ),
                "archive_size": archive.stat().st_size,
                "manifest_sha256": sha256_file(manifest_path),
                "manifest_size": manifest_path.stat().st_size,
            }
            validate_manifest_binding(manifest_path, header)
            if any(receipt.get(key) != value for key, value in header.items()):
                continue
            if receipt.get("remote_readback_sha256") is not True:
                continue
            records.append(
                {
                    "backup_id": directory.name,
                    "directory": directory,
                    "created_at": manifest.get("created_at"),
                    "classes": set(manifest["retention_classes"]),
                    "receipt_sha256": sha256_file(receipt_path),
                }
            )
        except (BackupError, OSError, KeyError, TypeError):
            continue
    return records


def prune_remote(
    vault_root: Path,
    *,
    anchor_backup_id: str,
    anchor_receipt_sha256: str,
    daily_copies: int,
    weekly_copies: int,
    minimum_known_good: int,
    deletion_id: str | None = None,
) -> dict[str, Any]:
    validate_backup_id(anchor_backup_id)
    validate_sha256(anchor_receipt_sha256, "anchor receipt digest")
    if daily_copies < 1 or weekly_copies < 1 or minimum_known_good < 2:
        raise BackupError("retention counts are invalid")
    root = require_directory(vault_root, "vault root")
    records = verified_vault_records(root)
    by_id = {record["backup_id"]: record for record in records}
    anchor = by_id.get(anchor_backup_id)
    if anchor is None or anchor["receipt_sha256"] != anchor_receipt_sha256:
        raise BackupError("retention anchor is not a verified remote copy")
    newest = sorted(
        records,
        key=lambda item: (str(item["created_at"]), item["backup_id"]),
        reverse=True,
    )
    known_good: list[dict[str, Any]] = []
    seen_restore_ids: set[str] = set()
    seen_target_identities: set[str] = set()
    for record in newest:
        try:
            attestation = load_known_good_attestation(root, record["backup_id"])
        except (BackupError, OSError, KeyError, TypeError):
            continue
        restore_id = attestation["restore_id"]
        target_identity = attestation["identities"]["target_volume_identity_sha256"]
        if restore_id in seen_restore_ids or target_identity in seen_target_identities:
            continue
        seen_restore_ids.add(restore_id)
        seen_target_identities.add(target_identity)
        known_good.append({**record, "attestation": attestation})
    if len(known_good) < minimum_known_good:
        raise BackupError(
            "retention requires at least "
            f"{minimum_known_good} valid known-good attestations"
        )
    retained_known_good = known_good[:minimum_known_good]
    keep = {item["backup_id"] for item in retained_known_good}
    known_good_attestations = {
        item["backup_id"]: item["attestation"]["attestation_sha256"]
        for item in retained_known_good
    }
    for retention_class, count in (("daily", daily_copies), ("weekly", weekly_copies)):
        keep.update(
            item["backup_id"]
            for item in [
                entry for entry in newest if retention_class in entry["classes"]
            ][:count]
        )
    deleting = [item for item in newest if item["backup_id"] not in keep]
    identifier = validate_backup_id(
        deletion_id
        or f"prune-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    )
    deletion_root = ensure_directory(root / "deletions", "vault deletion records")
    trash_root = ensure_directory(root / ".trash", "vault trash root")
    trash = trash_root / identifier
    trash.mkdir(mode=0o700)
    moved: list[str] = []
    try:
        for record in deleting:
            os.rename(record["directory"], trash / record["backup_id"])
            moved.append(record["backup_id"])
        deletion_intent = {
            "schema_version": 1,
            "deletion_id": identifier,
            "recorded_at": now(),
            "state": "quarantined_before_delete",
            "anchor_backup_id": anchor_backup_id,
            "anchor_receipt_sha256": anchor_receipt_sha256,
            "quarantined_backup_ids": sorted(moved),
            "retained_backup_ids": sorted(keep),
            "daily_copies": daily_copies,
            "weekly_copies": weekly_copies,
            "minimum_known_good_copies": minimum_known_good,
            "retained_known_good_backup_ids": sorted(known_good_attestations),
            "known_good_attestation_sha256s": known_good_attestations,
            "delete_only_after_verified_remote_copy": True,
            "secret_material_recorded": False,
        }
        intent_path = deletion_root / f"{identifier}.intent.json"
        write_new(intent_path, canonical_json(deletion_intent), 0o600)
    except Exception:
        for backup_id in reversed(moved):
            source = trash / backup_id
            if source.exists() and not (root / "backups" / backup_id).exists():
                os.rename(source, root / "backups" / backup_id)
        try:
            trash.rmdir()
        except OSError:
            pass
        raise
    try:
        shutil.rmtree(trash)
    except Exception as exc:
        raise BackupError(
            f"retention quarantine remains for manual recovery: {trash}"
        ) from exc
    completion = {
        "schema_version": 1,
        "deletion_id": identifier,
        "recorded_at": now(),
        "state": "deleted",
        "deletion_intent_sha256": sha256_file(intent_path),
        "deleted_backup_ids": sorted(moved),
        "retained_backup_ids": sorted(keep),
        "anchor_backup_id": anchor_backup_id,
        "anchor_receipt_sha256": anchor_receipt_sha256,
        "retained_known_good_backup_ids": sorted(known_good_attestations),
        "known_good_attestation_sha256s": known_good_attestations,
        "delete_only_after_verified_remote_copy": True,
        "secret_material_recorded": False,
    }
    write_new(deletion_root / f"{identifier}.json", canonical_json(completion), 0o600)
    return completion


def parse_expected_digest(path: Path) -> str:
    value = (
        require_regular(path, "remote host identity attestation")
        .read_text(encoding="ascii")
        .strip()
    )
    return validate_sha256(value, "expected remote host ID")


def default_backup_id() -> str:
    return (
        f"backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    )
