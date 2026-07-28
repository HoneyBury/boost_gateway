#!/usr/bin/env python3
"""Run one evidence-bound scheduled off-host backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

INSTALL_ROOT = Path("/usr/local/libexec/boost-gateway/backup")
DEFAULT_TOOL = INSTALL_ROOT / "scripts/tools/manage_backup_recovery.py"
DEFAULT_POLICY = INSTALL_ROOT / "deploy/operations/backup-recovery-policy.example.json"
DEFAULT_PROFILE = INSTALL_ROOT / "env/redis/redis.production-validation.conf"
DEFAULT_CONFIG = Path("/etc/boost-gateway")
DEFAULT_BACKUP_ROOT = Path("/var/backups/boost-gateway")
DEFAULT_EVIDENCE_ROOT = Path("/var/lib/boost-gateway-evidence/recovery")
DEFAULT_LOCK = Path("/var/lib/boost-gateway/deployment-transactions/.lifecycle.lock")
REMOTE_RE = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
Runner = Callable[..., subprocess.CompletedProcess[str]]


class ScheduledBackupError(RuntimeError):
    """Raised when scheduled backup evidence cannot be trusted."""


def now_text(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    return current.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ScheduledBackupError(f"{label} must be a regular non-symlink file")
    return path


def load_json(path: Path, label: str) -> dict[str, Any]:
    regular(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScheduledBackupError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ScheduledBackupError(f"{label} must be a JSON object")
    return value


def write_new(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def remote_host(path: Path) -> str:
    value = regular(path, "backup remote host").read_text(encoding="ascii").strip()
    if REMOTE_RE.fullmatch(value) is None:
        raise ScheduledBackupError("backup remote host is invalid")
    return value


def backup_id(started: datetime, suffix: str) -> str:
    if re.fullmatch(r"[0-9a-f]{8}", suffix) is None:
        raise ScheduledBackupError("backup ID suffix is invalid")
    timestamp = started.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"todo0012-scheduled-{timestamp}-{suffix}"


def retention_classes(started: datetime, weekly_iso_weekday: int) -> list[str]:
    if weekly_iso_weekday not in range(1, 8):
        raise ScheduledBackupError("weekly ISO weekday must be between 1 and 7")
    classes = ["daily"]
    if started.astimezone(UTC).isoweekday() == weekly_iso_weekday:
        classes.append("weekly")
    return classes


def validate_result(
    result: dict[str, Any],
    *,
    identifier: str,
    classes: list[str],
    output_root: Path,
    receipt_root: Path,
    policy_sha256: str,
    redis_profile_sha256: str,
) -> dict[str, Any]:
    manifest = result.get("manifest")
    receipt = result.get("remote_receipt")
    if not isinstance(manifest, dict) or not isinstance(receipt, dict):
        raise ScheduledBackupError("backup result omitted manifest or remote receipt")
    archive_path = Path(str(result.get("archive_path", "")))
    manifest_path = Path(str(result.get("manifest_path", "")))
    receipt_path = Path(str(result.get("remote_receipt_path", "")))
    expected_paths = (
        (archive_path, output_root / f"{identifier}.tar.age", "encrypted archive"),
        (manifest_path, output_root / f"{identifier}.manifest.json", "manifest"),
        (receipt_path, receipt_root / f"{identifier}.json", "remote receipt"),
    )
    for actual, expected, label in expected_paths:
        if actual != expected:
            raise ScheduledBackupError(f"{label} path differs")
        regular(actual, label)
    archive_sha = sha256_file(archive_path)
    manifest_sha = sha256_file(manifest_path)
    receipt_sha = sha256_file(receipt_path)
    source_id = str(manifest.get("source_host", {}).get("host_id_sha256", ""))
    vault_id = str(receipt.get("vault_host_id_sha256", ""))
    if (
        manifest.get("schema_version") != 2
        or manifest.get("backup_id") != identifier
        or manifest.get("retention_classes") != classes
        or manifest.get("backup_policy_sha256") != policy_sha256
        or manifest.get("redis_profile_sha256") != redis_profile_sha256
        or manifest.get("formal_todo0012_claim") is not False
        or manifest.get("secret_material_recorded") is not False
        or manifest.get("archive", {}).get("sha256") != archive_sha
        or manifest.get("archive", {}).get("size_bytes") != archive_path.stat().st_size
    ):
        raise ScheduledBackupError("backup manifest binding differs")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("backup_id") != identifier
        or receipt.get("archive_sha256") != archive_sha
        or receipt.get("archive_size") != archive_path.stat().st_size
        or receipt.get("manifest_sha256") != manifest_sha
        or receipt.get("manifest_size") != manifest_path.stat().st_size
        or receipt.get("remote_readback_sha256") is not True
        or receipt.get("create_only") is not True
        or receipt.get("secret_material_recorded") is not False
        or SHA256_RE.fullmatch(source_id) is None
        or SHA256_RE.fullmatch(vault_id) is None
        or source_id == vault_id
    ):
        raise ScheduledBackupError("remote receipt binding differs")
    if load_json(manifest_path, "persisted manifest") != manifest:
        raise ScheduledBackupError("persisted manifest differs from command result")
    if load_json(receipt_path, "persisted remote receipt") != receipt:
        raise ScheduledBackupError(
            "persisted remote receipt differs from command result"
        )
    return {
        "archive_path": str(archive_path),
        "archive_sha256": archive_sha,
        "archive_size_bytes": archive_path.stat().st_size,
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "receipt_path": str(receipt_path),
        "receipt_sha256": receipt_sha,
        "source_host_id_sha256": source_id,
        "vault_host_id_sha256": vault_id,
        "deployment": manifest.get("deployment", {}),
        "backup_policy_sha256": policy_sha256,
        "redis_profile_sha256": redis_profile_sha256,
    }


def run_scheduled_backup(
    args: argparse.Namespace,
    *,
    started: datetime | None = None,
    suffix: str | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    start = (started or datetime.now(UTC)).astimezone(UTC)
    identifier = backup_id(start, suffix or uuid.uuid4().hex[:8])
    summary_path = args.evidence_root / f"{identifier}-summary.json"
    if summary_path.exists() or summary_path.is_symlink():
        raise ScheduledBackupError("create-only scheduled summary already exists")
    failure = ""
    bindings: dict[str, Any] = {}
    classes: list[str] = []
    try:
        classes = retention_classes(start, args.weekly_iso_weekday)
        tool = regular(args.tool, "backup tool")
        policy = regular(args.policy, "backup policy")
        redis_profile = regular(args.redis_profile, "Redis profile")
        command = [
            sys.executable,
            str(tool),
            "backup",
            "--backup-id",
            identifier,
            "--policy",
            str(policy),
            "--redis-profile",
            str(redis_profile),
            "--deployment-record",
            str(regular(args.deployment_record, "deployment record")),
            "--recipient-file",
            str(regular(args.recipient_file, "age recipient")),
            "--staging-root",
            str(args.staging_root),
            "--output-root",
            str(args.output_root),
            "--lock-path",
            str(args.lock_path),
            "--redis-container",
            args.redis_container,
            "--docker",
            args.docker,
            "--age",
            args.age,
            "--remote-host",
            remote_host(args.remote_host_file),
            "--remote-host-id-attestation",
            str(regular(args.remote_host_id_attestation, "vault identity attestation")),
            "--ssh",
            args.ssh,
            "--ssh-identity-file",
            str(regular(args.ssh_identity_file, "backup SSH identity")),
            "--ssh-known-hosts",
            str(regular(args.ssh_known_hosts, "backup SSH known_hosts")),
            "--receipt-root",
            str(args.receipt_root),
        ]
        for value in classes:
            command.extend(("--retention-class", value))
        policy_sha256 = sha256_file(policy)
        redis_profile_sha256 = sha256_file(redis_profile)
        completed = runner(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=args.timeout_seconds,
        )
        if completed.returncode != 0:
            raise ScheduledBackupError(
                f"backup engine exited {completed.returncode}: {completed.stderr[-1000:].strip()}"
            )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ScheduledBackupError("backup engine output is not JSON") from exc
        if not isinstance(result, dict):
            raise ScheduledBackupError("backup engine output is not an object")
        bindings = validate_result(
            result,
            identifier=identifier,
            classes=classes,
            output_root=args.output_root,
            receipt_root=args.receipt_root,
            policy_sha256=policy_sha256,
            redis_profile_sha256=redis_profile_sha256,
        )
    except (
        OSError,
        UnicodeError,
        subprocess.TimeoutExpired,
        ScheduledBackupError,
    ) as exc:
        failure = str(exc)
    completed_at = now_text()
    success = not failure
    summary = {
        "schema_version": 1,
        "operation": "scheduled-off-host-backup",
        "backup_id": identifier,
        "started_at": now_text(start),
        "completed_at": completed_at,
        "retention_classes": classes,
        "weekly_iso_weekday": args.weekly_iso_weekday,
        "overall_pass": success,
        "status": "passed" if success else "failed",
        "failure": failure,
        "off_host_copy_verified": success,
        "remote_readback_sha256": success,
        "create_only": True,
        "bindings": bindings,
        "restore_known_good": False,
        "formal_todo0012_claim": False,
        "secret_material_recorded": False,
    }
    write_new(summary_path, summary)
    if not success:
        raise ScheduledBackupError(
            f"scheduled backup failed; summary={summary_path}: {failure}"
        )
    return {**summary, "summary_path": str(summary_path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", type=Path, default=DEFAULT_TOOL)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--redis-profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument(
        "--deployment-record",
        type=Path,
        default=Path("/opt/boost-gateway/current/record.json"),
    )
    parser.add_argument(
        "--recipient-file", type=Path, default=DEFAULT_CONFIG / "backup.age-recipient"
    )
    parser.add_argument(
        "--remote-host-file", type=Path, default=DEFAULT_CONFIG / "backup-remote-host"
    )
    parser.add_argument(
        "--remote-host-id-attestation",
        type=Path,
        default=DEFAULT_CONFIG / "backup-remote-host-id.sha256",
    )
    parser.add_argument(
        "--ssh-identity-file",
        type=Path,
        default=DEFAULT_CONFIG / "backup-vault-ed25519",
    )
    parser.add_argument(
        "--ssh-known-hosts",
        type=Path,
        default=DEFAULT_CONFIG / "backup-vault-known-hosts",
    )
    parser.add_argument(
        "--staging-root", type=Path, default=DEFAULT_BACKUP_ROOT / "staging"
    )
    parser.add_argument(
        "--output-root", type=Path, default=DEFAULT_BACKUP_ROOT / "encrypted"
    )
    parser.add_argument(
        "--receipt-root", type=Path, default=DEFAULT_BACKUP_ROOT / "receipts"
    )
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--redis-container", default="boost-redis")
    parser.add_argument("--docker", default="/usr/bin/docker")
    parser.add_argument("--age", default="/usr/local/bin/age")
    parser.add_argument("--ssh", default="/usr/bin/ssh")
    parser.add_argument("--weekly-iso-weekday", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=5400)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if os.geteuid() != 0:
        print("scheduled backup: FAIL: run as root", file=sys.stderr)
        return 1
    try:
        result = run_scheduled_backup(args)
    except ScheduledBackupError as exc:
        print(f"scheduled backup: FAIL: {exc}", file=sys.stderr)
        return 1
    print("scheduled backup: PASS")
    print(f"backup_id={result['backup_id']}")
    print(f"summary={result['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
