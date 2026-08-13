#!/usr/bin/env python3
"""Create immutable observability records and verifiable off-host packages."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.operations_host import collect_operations_identity


DEFAULT_ROOT = Path("/var/lib/boost-gateway-evidence/observability")
DEFAULT_DEPLOYMENT = Path("/opt/boost-gateway/current/record.json")
RECORD_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
RECORD_KINDS = {"daily", "weekly", "incident", "final"}
REQUIRED_ATTRIBUTES = {
    "daily": {"checkpoint_date"},
    "weekly": {"period_start", "period_end"},
    "incident": {"title", "severity", "started_at", "status"},
    "final": {"report_title", "period_start", "period_end"},
}
SECRET_KEY_RE = re.compile(
    r"(?:password|secret|credential|api[_-]?key|access[_-]?token|webhook[_-]?url)",
    re.IGNORECASE,
)


class EvidenceError(RuntimeError):
    """Raised when evidence cannot be recorded without weakening provenance."""


class PackageVerificationError(EvidenceError):
    """Raised when an off-host evidence package cannot be trusted."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise EvidenceError(f"{label} must be a regular non-symlink file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be a JSON object")
    return value


def file_reference(path: Path, kind: str) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_file():
        raise EvidenceError(f"summary must be a regular non-symlink file: {path}")
    return {
        "kind": kind,
        "path": str(resolved),
        "sha256": sha256_file(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def deployment_binding(path: Path) -> dict[str, Any]:
    deployment = load_json_object(path, "deployment record")
    required = {
        "deployment_id",
        "tag",
        "commit",
        "runtime_asset_sha256",
        "image_ids",
        "configuration_sha256",
        "host",
        "operator",
        "result",
    }
    missing = sorted(required - set(deployment))
    if missing:
        raise EvidenceError(f"deployment record lacks identity fields: {missing}")
    return {key: deployment[key] for key in sorted(required)}


def _write_new_json(path: Path, value: dict[str, Any]) -> None:
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


def _safe_basename(path: Path) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name)
    return value or "summary"


def _raw_snapshot_path(ledger_root: Path, source: Path, digest: str) -> Path:
    return ledger_root / "raw" / f"{digest}-{_safe_basename(source)}"


def snapshot_summary(ledger_root: Path, source: Path) -> dict[str, Any]:
    reference = file_reference(source, "raw-summary-source")
    source_path = Path(reference["path"])
    destination = _raw_snapshot_path(ledger_root, source_path, reference["sha256"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if not destination.exists() and not destination.is_symlink():
        descriptor = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640
        )
        created = True
        try:
            with (
                os.fdopen(descriptor, "wb") as output,
                source_path.open("rb") as input_stream,
            ):
                for block in iter(lambda: input_stream.read(1024 * 1024), b""):
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    snapshot = file_reference(destination, "raw-summary")
    if (
        snapshot["sha256"] != reference["sha256"]
        or snapshot["size_bytes"] != reference["size_bytes"]
    ):
        if created:
            destination.unlink(missing_ok=True)
        raise EvidenceError(f"content-addressed raw snapshot drifted: {destination}")
    snapshot["source_path"] = reference["path"]
    return snapshot


def _resolve_recorded_summary(
    ledger_root: Path, summary: dict[str, Any]
) -> dict[str, Any]:
    expected_sha256 = str(summary.get("sha256", ""))
    expected_size = summary.get("size_bytes")
    recorded_path = Path(str(summary.get("path", "")))
    source_path = Path(str(summary.get("source_path", recorded_path)))
    candidates = [
        recorded_path,
        _raw_snapshot_path(ledger_root, source_path, expected_sha256),
    ]
    for candidate in candidates:
        try:
            reference = file_reference(candidate, "raw-summary")
        except (EvidenceError, OSError):
            continue
        if (
            reference["sha256"] == expected_sha256
            and reference["size_bytes"] == expected_size
        ):
            reference["source_path"] = str(source_path)
            return reference
    raise EvidenceError(f"raw summary drifted after record creation: {source_path}")


def seal_legacy_records(ledger_root: Path) -> dict[str, Any]:
    records = sorted((ledger_root / "records").glob("*/*.json"))
    sealed: list[dict[str, Any]] = []
    for record_path in records:
        record = load_json_object(record_path, "ledger record")
        summaries = record.get("raw_summaries")
        if not isinstance(summaries, list) or not summaries:
            raise EvidenceError(f"ledger record has no raw summaries: {record_path}")
        for summary in summaries:
            if not isinstance(summary, dict):
                raise EvidenceError(f"ledger record has an invalid summary: {record_path}")
            source = Path(str(summary.get("source_path", summary.get("path", ""))))
            recorded_path = Path(str(summary.get("path", "")))
            if "source_path" in summary or recorded_path.parent == ledger_root / "raw":
                snapshot = _resolve_recorded_summary(ledger_root, summary)
            else:
                snapshot = snapshot_summary(ledger_root, source)
            if (
                snapshot["sha256"] != summary.get("sha256")
                or snapshot["size_bytes"] != summary.get("size_bytes")
            ):
                raise EvidenceError(f"legacy raw summary already drifted: {source}")
            sealed.append(
                {
                    "record": str(record_path),
                    "source_path": str(source.resolve()),
                    "snapshot_path": snapshot["path"],
                    "sha256": snapshot["sha256"],
                }
            )
    return {
        "schema_version": 1,
        "overall_pass": True,
        "sealed_references": sealed,
        "sealed_count": len(sealed),
        "secret_material_recorded": False,
    }


def _validate_attributes(kind: str, attributes: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_ATTRIBUTES[kind] - set(attributes))
    if missing:
        raise EvidenceError(f"{kind} record attributes are missing: {missing}")

    def visit(value: object, location: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or SECRET_KEY_RE.search(key):
                    raise EvidenceError(f"secret-like attribute key is forbidden: {location}.{key}")
                visit(child, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{location}[{index}]")
        elif not isinstance(value, (str, int, float, bool, type(None))):
            raise EvidenceError(f"unsupported attribute value: {location}")

    visit(attributes, "attributes")


def create_record(
    ledger_root: Path,
    kind: str,
    record_id: str,
    summary_paths: list[Path],
    deployment_path: Path,
    *,
    attributes: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    if kind not in RECORD_KINDS:
        raise EvidenceError(f"unsupported record kind: {kind}")
    if RECORD_ID_RE.fullmatch(record_id) is None:
        raise EvidenceError("record ID is invalid")
    if not summary_paths:
        raise EvidenceError("at least one raw summary is required")
    record_attributes = attributes or {}
    _validate_attributes(kind, record_attributes)
    references = []
    for index, path in enumerate(summary_paths, 1):
        reference = snapshot_summary(ledger_root, path)
        reference["kind"] = f"raw-summary-{index}"
        references.append(reference)
    observed_identity = identity or collect_operations_identity()
    if not isinstance(observed_identity.get("host"), dict) or not isinstance(
        observed_identity.get("operator"), dict
    ):
        raise EvidenceError("host and operator identity are required")
    record = {
        "schema_version": 1,
        "kind": kind,
        "record_id": record_id,
        "recorded_at": now(),
        "host": observed_identity["host"],
        "operator": observed_identity["operator"],
        "deployment": deployment_binding(deployment_path),
        "raw_summaries": references,
        "attributes": record_attributes,
        "formal_30_day_claim": False,
        "secret_material_recorded": False,
    }
    destination = ledger_root / "records" / kind / f"{record_id}.json"
    try:
        _write_new_json(destination, record)
    except FileExistsError as exc:
        raise EvidenceError(f"record already exists and cannot be overwritten: {destination}") from exc
    return destination, record


def build_manifest(
    ledger_root: Path,
    manifest_id: str,
    *,
    identity: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    if RECORD_ID_RE.fullmatch(manifest_id) is None:
        raise EvidenceError("manifest ID is invalid")
    record_paths = sorted((ledger_root / "records").glob("*/*.json"))
    if not record_paths:
        raise EvidenceError("ledger has no records")
    entries: dict[str, dict[str, Any]] = {}
    for record_path in record_paths:
        record = load_json_object(record_path, "ledger record")
        relative = record_path.relative_to(ledger_root).as_posix()
        entries[f"ledger/{relative}"] = file_reference(record_path, "ledger-record")
        summaries = record.get("raw_summaries")
        if not isinstance(summaries, list) or not summaries:
            raise EvidenceError(f"ledger record has no raw summaries: {record_path}")
        for summary in summaries:
            if not isinstance(summary, dict):
                raise EvidenceError(f"ledger record has an invalid summary: {record_path}")
            reference = _resolve_recorded_summary(ledger_root, summary)
            source = Path(reference["path"])
            original = Path(str(reference.get("source_path", source)))
            archive_path = f"raw/{reference['sha256']}-{_safe_basename(original)}"
            entries.setdefault(archive_path, reference)
    observed_identity = identity or collect_operations_identity()
    manifest = {
        "schema_version": 1,
        "manifest_id": manifest_id,
        "generated_at": now(),
        "host": observed_identity["host"],
        "operator": observed_identity["operator"],
        "entries": [
            {"archive_path": archive_path, **reference}
            for archive_path, reference in sorted(entries.items())
        ],
        "entry_count": len(entries),
        "secret_material_recorded": False,
    }
    destination = ledger_root / "manifests" / f"{manifest_id}.json"
    try:
        _write_new_json(destination, manifest)
    except FileExistsError as exc:
        raise EvidenceError(f"manifest already exists and cannot be overwritten: {destination}") from exc
    return destination, manifest


def package_manifest(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = load_json_object(manifest_path, "evidence manifest")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise EvidenceError("evidence manifest has no entries")
    if output_path.exists() or output_path.is_symlink():
        raise EvidenceError(f"package already exists and cannot be overwritten: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sums: list[str] = []
    with tarfile.open(output_path, "x:gz", format=tarfile.PAX_FORMAT) as archive:
        for entry in entries:
            if not isinstance(entry, dict):
                raise EvidenceError("evidence manifest contains a non-object entry")
            source = Path(str(entry.get("path", "")))
            archive_path = str(entry.get("archive_path", ""))
            reference = file_reference(source, str(entry.get("kind", "evidence")))
            if (
                reference["sha256"] != entry.get("sha256")
                or reference["size_bytes"] != entry.get("size_bytes")
                or archive_path.startswith("/")
                or ".." in Path(archive_path).parts
            ):
                raise EvidenceError(f"manifest entry drift or unsafe path: {source}")
            archive.add(source, arcname=archive_path, recursive=False)
            sums.append(f"{reference['sha256']}  {archive_path}")
        manifest_bytes = manifest_path.read_bytes()
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_bytes)
        manifest_info.mode = 0o640
        archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
        sums.append(f"{hashlib.sha256(manifest_bytes).hexdigest()}  manifest.json")
        sums_bytes = ("\n".join(sums) + "\n").encode("utf-8")
        sums_info = tarfile.TarInfo("SHA256SUMS")
        sums_info.size = len(sums_bytes)
        sums_info.mode = 0o640
        archive.addfile(sums_info, io.BytesIO(sums_bytes))
    os.chmod(output_path, 0o640)
    return {
        "schema_version": 1,
        "package": str(output_path.resolve()),
        "sha256": sha256_file(output_path),
        "size_bytes": output_path.stat().st_size,
        "entry_count": len(entries),
        "off_host_copy_verified": False,
    }


def _extract_package(package: Path, root: Path) -> dict[str, str]:
    actual: dict[str, str] = {}
    total = 0
    with tarfile.open(package, "r:gz") as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            total += member.size
            if (relative.is_absolute() or ".." in relative.parts or not member.isfile()
                    or member.name in actual or len(actual) >= 10_000
                    or total > 4 * 1024**3):
                raise PackageVerificationError(f"unsafe, duplicate, or oversized member: {member.name}")
            source = archive.extractfile(member)
            if source is None:
                raise PackageVerificationError(f"cannot read package member: {member.name}")
            destination = root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            with source, os.fdopen(descriptor, "wb") as output:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            actual[member.name] = digest.hexdigest()
    return actual


def verify_package(package_path: Path, extraction_path: Path, receipt_path: Path, *,
                   identity: dict[str, Any] | None = None) -> dict[str, Any]:
    package = package_path.resolve(strict=True)
    if package_path.is_symlink() or not package.is_file():
        raise PackageVerificationError(f"package must be a regular non-symlink file: {package_path}")
    if extraction_path.exists() or extraction_path.is_symlink():
        raise PackageVerificationError(f"extraction directory already exists and cannot be reused: {extraction_path}")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise PackageVerificationError(f"receipt already exists and cannot be overwritten: {receipt_path}")
    observed = identity or collect_operations_identity()
    if not isinstance(observed.get("host"), dict) or not isinstance(observed.get("operator"), dict):
        raise PackageVerificationError("host and operator identity are required")
    extraction_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{extraction_path.name}.", dir=extraction_path.parent.resolve()))
    try:
        actual = _extract_package(package, temporary)
        if not {"SHA256SUMS", "manifest.json"} <= set(actual):
            raise PackageVerificationError("package must contain SHA256SUMS and manifest.json")
        controls = (temporary / "SHA256SUMS", temporary / "manifest.json")
        if any(path.stat().st_size > 16 * 1024**2 for path in controls):
            raise PackageVerificationError("package control file exceeds the size limit")
        expected: dict[str, str] = {}
        for line in controls[0].read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", line)
            if not match:
                raise PackageVerificationError(f"invalid SHA256SUMS line: {line!r}")
            digest, name = match.groups()
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or name in expected:
                raise PackageVerificationError(f"unsafe or duplicate SHA256SUMS path: {name}")
            expected[name] = digest
        if set(actual) != {*expected, "SHA256SUMS"}:
            raise PackageVerificationError("package members differ from SHA256SUMS")
        for name, digest in expected.items():
            if actual[name] != digest:
                raise PackageVerificationError(f"package checksum differs: {name}")
        manifest = json.loads(controls[1].read_text(encoding="utf-8"))
        entries = manifest.get("entries") if isinstance(manifest, dict) else None
        paths = [str(item.get("archive_path", "")) for item in entries or [] if isinstance(item, dict)]
        if (not isinstance(entries, list) or len(paths) != len(entries) or len(paths) != len(set(paths))
                or set(paths) != set(expected) - {"manifest.json"}
                or manifest.get("entry_count") != len(paths)
                or RECORD_ID_RE.fullmatch(str(manifest.get("manifest_id", ""))) is None):
            raise PackageVerificationError("manifest identity or entry set differs from SHA256SUMS")
        os.replace(temporary, extraction_path)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        raise PackageVerificationError(f"package control file is invalid: {exc}") from exc
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    receipt = {
        "schema_version": 1, "overall_pass": True, "off_host_copy_verified": True,
        "create_only": True, "verified_at": now(), "host": observed["host"],
        "operator": observed["operator"],
        "package": {"path": str(package), "sha256": sha256_file(package), "size_bytes": package.stat().st_size},
        "extraction_directory": str(extraction_path.resolve()),
        "manifest": {"manifest_id": manifest["manifest_id"], "sha256": actual["manifest.json"],
                     "entry_count": manifest["entry_count"]},
        "checksums": {"verified_entry_count": len(expected),
                      "verification_output": [f"{name}: OK" for name in sorted(expected)]},
        "secret_material_recorded": False,
    }
    _write_new_json(receipt_path, receipt)
    return {"receipt": str(receipt_path.resolve()), "receipt_sha256": sha256_file(receipt_path), **receipt}
