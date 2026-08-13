#!/usr/bin/env python3
"""Safely verify observability evidence packages on a different host."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.lib.operations_identity import collect_operations_identity


RECORD_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
CHECKSUM_LINE_RE = re.compile(r"([0-9a-f]{64})  ([^\r\n]+)\Z")
MAX_PACKAGE_ENTRIES = 10_000
MAX_PACKAGE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_CONTROL_FILE_BYTES = 16 * 1024 * 1024


class PackageVerificationError(RuntimeError):
    """Raised when an off-host evidence package cannot be trusted."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _extract_regular_members(package: Path, temporary_root: Path) -> dict[str, str]:
    actual: dict[str, str] = {}
    total_size = 0
    with tarfile.open(package, "r:gz") as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            total_size += member.size
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not member.isfile()
                or member.name in actual
                or len(actual) >= MAX_PACKAGE_ENTRIES
                or total_size > MAX_PACKAGE_UNCOMPRESSED_BYTES
            ):
                raise PackageVerificationError(
                    f"package contains an unsafe, duplicate, or oversized member: {member.name}"
                )
            source = archive.extractfile(member)
            if source is None:
                raise PackageVerificationError(
                    f"cannot read package member: {member.name}"
                )
            destination = temporary_root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640
            )
            digest = hashlib.sha256()
            with source, os.fdopen(descriptor, "wb") as stream:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                    stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
            actual[member.name] = digest.hexdigest()
    return actual


def _load_checksums(root: Path, actual: dict[str, str]) -> dict[str, str]:
    if "SHA256SUMS" not in actual or "manifest.json" not in actual:
        raise PackageVerificationError(
            "package must contain SHA256SUMS and manifest.json"
        )
    checksum_path = root / "SHA256SUMS"
    manifest_path = root / "manifest.json"
    if (
        checksum_path.stat().st_size > MAX_CONTROL_FILE_BYTES
        or manifest_path.stat().st_size > MAX_CONTROL_FILE_BYTES
    ):
        raise PackageVerificationError("package control file exceeds the size limit")
    try:
        checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise PackageVerificationError("SHA256SUMS is not valid UTF-8") from exc
    expected: dict[str, str] = {}
    for line in checksum_lines:
        match = CHECKSUM_LINE_RE.fullmatch(line)
        if match is None:
            raise PackageVerificationError(f"invalid SHA256SUMS line: {line!r}")
        digest, name = match.groups()
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or name in expected:
            raise PackageVerificationError(
                f"unsafe or duplicate SHA256SUMS path: {name}"
            )
        expected[name] = digest
    if set(actual) != {*expected, "SHA256SUMS"}:
        raise PackageVerificationError("package members differ from SHA256SUMS")
    for name, digest in expected.items():
        if actual[name] != digest:
            raise PackageVerificationError(f"package checksum differs: {name}")
    return expected


def _load_manifest(root: Path, expected: dict[str, str]) -> dict[str, Any]:
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageVerificationError(f"manifest.json is invalid: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(
        manifest.get("entries"), list
    ):
        raise PackageVerificationError("manifest.json must contain an entries array")
    manifest_paths = {
        str(entry.get("archive_path", ""))
        for entry in manifest["entries"]
        if isinstance(entry, dict)
    }
    if (
        len(manifest_paths) != len(manifest["entries"])
        or manifest_paths != set(expected) - {"manifest.json"}
        or manifest.get("entry_count") != len(manifest_paths)
        or RECORD_ID_RE.fullmatch(str(manifest.get("manifest_id", ""))) is None
    ):
        raise PackageVerificationError(
            "manifest identity or entry set differs from SHA256SUMS"
        )
    return manifest


def verify_package(
    package_path: Path,
    extraction_path: Path,
    receipt_path: Path,
    *,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    package = package_path.resolve(strict=True)
    if package_path.is_symlink() or not package.is_file():
        raise PackageVerificationError(
            f"package must be a regular non-symlink file: {package_path}"
        )
    if extraction_path.exists() or extraction_path.is_symlink():
        raise PackageVerificationError(
            f"extraction directory already exists and cannot be reused: {extraction_path}"
        )
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PackageVerificationError(
            f"receipt already exists and cannot be overwritten: {receipt_path}"
        )
    observed_identity = identity or collect_operations_identity()
    if not isinstance(observed_identity.get("host"), dict) or not isinstance(
        observed_identity.get("operator"), dict
    ):
        raise PackageVerificationError("host and operator identity are required")

    extraction_parent = extraction_path.parent.resolve()
    extraction_parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f".{extraction_path.name}.", dir=extraction_parent)
    )
    try:
        actual = _extract_regular_members(package, temporary_root)
        expected = _load_checksums(temporary_root, actual)
        manifest = _load_manifest(temporary_root, expected)
        os.replace(temporary_root, extraction_path)
    except Exception:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise

    receipt = {
        "schema_version": 1,
        "overall_pass": True,
        "off_host_copy_verified": True,
        "create_only": True,
        "verified_at": now(),
        "host": observed_identity["host"],
        "operator": observed_identity["operator"],
        "package": {
            "path": str(package),
            "sha256": sha256_file(package),
            "size_bytes": package.stat().st_size,
        },
        "extraction_directory": str(extraction_path.resolve()),
        "manifest": {
            "manifest_id": manifest["manifest_id"],
            "sha256": actual["manifest.json"],
            "entry_count": manifest["entry_count"],
        },
        "checksums": {
            "verified_entry_count": len(expected),
            "verification_output": [f"{name}: OK" for name in sorted(expected)],
        },
        "secret_material_recorded": False,
    }
    write_new_json(receipt_path, receipt)
    return {
        "receipt": str(receipt_path.resolve()),
        "receipt_sha256": sha256_file(receipt_path),
        **receipt,
    }
