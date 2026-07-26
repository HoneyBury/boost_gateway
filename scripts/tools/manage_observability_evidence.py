#!/usr/bin/env python3
"""Create immutable observability records and verifiable off-host packages."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.operations_identity import collect_operations_identity


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--ledger-root", type=Path, default=DEFAULT_ROOT)
    record_parser.add_argument("--kind", choices=sorted(RECORD_KINDS), required=True)
    record_parser.add_argument("--record-id", required=True)
    record_parser.add_argument("--summary", type=Path, action="append", required=True)
    record_parser.add_argument("--deployment-record", type=Path, default=DEFAULT_DEPLOYMENT)
    record_parser.add_argument("--attributes-json", type=Path)

    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument("--ledger-root", type=Path, default=DEFAULT_ROOT)
    manifest_parser.add_argument("--manifest-id", required=True)

    package_parser = subparsers.add_parser("package")
    package_parser.add_argument("--manifest", type=Path, required=True)
    package_parser.add_argument("--output", type=Path, required=True)

    seal_parser = subparsers.add_parser("seal")
    seal_parser.add_argument("--ledger-root", type=Path, default=DEFAULT_ROOT)

    args = parser.parse_args()
    try:
        if args.command == "record":
            attributes = (
                load_json_object(args.attributes_json, "record attributes")
                if args.attributes_json
                else None
            )
            path, value = create_record(
                args.ledger_root,
                args.kind,
                args.record_id,
                args.summary,
                args.deployment_record,
                attributes=attributes,
            )
            result = {"record": str(path), "record_sha256": sha256_file(path), **value}
        elif args.command == "manifest":
            path, value = build_manifest(args.ledger_root, args.manifest_id)
            result = {"manifest": str(path), "manifest_sha256": sha256_file(path), **value}
        elif args.command == "package":
            result = package_manifest(args.manifest, args.output)
        else:
            result = seal_legacy_records(args.ledger_root)
    except (EvidenceError, OSError, ValueError) as exc:
        print(f"observability evidence: FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
