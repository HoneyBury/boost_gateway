#!/usr/bin/env python3
"""Export one validated vault Redis snapshot into a create-only restore bundle."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from scripts.lib import backup_recovery as backup  # noqa: E402
    from scripts.lib import backup_vault as vault  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.lib import backup_recovery as backup  # type: ignore[no-redef]  # noqa: E402
    from scripts.lib import backup_vault as vault  # type: ignore[no-redef]  # noqa: E402

StartCommand = Callable[..., Any]


class RestoreExportError(backup.BackupError):
    """Raised when a restore bundle cannot be exported safely."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def require_nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise RestoreExportError(f"{label} is invalid")
    return value


def artifact_digests(
    archive: Path,
    manifest: Path,
    receipt: Path,
    vault_identity: Path,
    validation_summary: Path,
) -> dict[str, str | int]:
    return {
        "archive_sha256": backup.sha256_file(archive),
        "archive_size_bytes": archive.stat().st_size,
        "manifest_sha256": backup.sha256_file(manifest),
        "manifest_size_bytes": manifest.stat().st_size,
        "receipt_sha256": backup.sha256_file(receipt),
        "receipt_size_bytes": receipt.stat().st_size,
        "validation_summary_sha256": backup.sha256_file(validation_summary),
        "validation_summary_size_bytes": validation_summary.stat().st_size,
        "vault_host_id_sha256": backup.sha256_file(vault_identity),
    }


def validate_vault_summary(
    path: Path,
    *,
    backup_id: str,
    expected_artifacts: dict[str, str | int],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    summary_path = backup.require_regular(path, "vault validation summary")
    summary = backup.load_json_object(summary_path, "vault validation summary")
    checks = summary.get("checks")
    artifacts = summary.get("artifacts")
    required_checks = {
        "metadata_binding",
        "distinct_host_identity",
        "age_decryption",
        "safe_archive_members",
        "redis_manifest_binding",
        "redis_check_rdb",
    }
    if (
        summary.get("schema_version") != 1
        or summary.get("backup_id") != backup_id
        or summary.get("overall_pass") is not True
        or summary.get("formal_todo0012_claim") is not False
        or summary.get("restore_known_good") is not False
        or summary.get("secret_material_recorded") is not False
        or not isinstance(checks, dict)
        or not isinstance(artifacts, dict)
    ):
        raise RestoreExportError("vault validation summary is not an eligible pass")
    if any(checks.get(name) is not True for name in required_checks):
        raise RestoreExportError("vault validation summary lacks a required pass")

    for field in (
        "archive_sha256",
        "manifest_sha256",
        "receipt_sha256",
        "vault_host_id_sha256",
    ):
        if artifacts.get(field) != expected_artifacts[field]:
            raise RestoreExportError(
                f"vault validation summary artifact differs: {field}"
            )

    reference = vault.redis_reference(manifest)
    if (
        artifacts.get("plaintext_sha256")
        != manifest.get("archive", {}).get("plaintext_sha256")
        or artifacts.get("redis_sha256") != reference["sha256"]
        or artifacts.get("redis_size_bytes") != reference["size_bytes"]
        or not isinstance(artifacts.get("plaintext_size_bytes"), int)
        or isinstance(artifacts.get("plaintext_size_bytes"), bool)
        or artifacts["plaintext_size_bytes"] <= 0
        or not isinstance(artifacts.get("member_count"), int)
        or isinstance(artifacts.get("member_count"), bool)
        or artifacts["member_count"] <= 0
    ):
        raise RestoreExportError(
            "vault validation summary plaintext or Redis binding differs"
        )
    return summary


def copy_evidence_new(source: Path, destination: Path, label: str) -> None:
    source_path = backup.require_regular(source, label)
    try:
        content = source_path.read_bytes()
    except OSError as exc:
        raise RestoreExportError(f"cannot read {label}: {exc}") from exc
    backup.write_new(destination, content, 0o600)
    os.chmod(destination, 0o600)


def verify_copied_evidence(
    bundle: Path, expected_artifacts: dict[str, str | int]
) -> None:
    for name, prefix in (
        ("manifest.json", "manifest"),
        ("receipt.json", "receipt"),
        ("vault-validation.json", "validation_summary"),
    ):
        copied = backup.require_regular(bundle / name, f"copied {name}")
        if (
            backup.sha256_file(copied) != expected_artifacts[f"{prefix}_sha256"]
            or copied.stat().st_size != expected_artifacts[f"{prefix}_size_bytes"]
        ):
            raise RestoreExportError(f"copied restore evidence differs: {name}")


def deployment_identity(manifest: dict[str, Any]) -> dict[str, str]:
    deployment = manifest.get("deployment")
    source_host = manifest.get("source_host")
    if not isinstance(deployment, dict) or not isinstance(source_host, dict):
        raise RestoreExportError("backup deployment identity is incomplete")
    host = deployment.get("host")
    if not isinstance(host, dict):
        raise RestoreExportError("backup deployment host identity is incomplete")
    source_host_id = backup.validate_sha256(
        source_host.get("host_id_sha256"), "source host ID"
    )
    if host.get("host_id_sha256") != source_host_id:
        raise RestoreExportError("deployment and source host identities differ")
    commit = require_nonempty_string(deployment.get("commit"), "deployment commit")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise RestoreExportError("deployment commit is invalid")
    return {
        "deployment_id": require_nonempty_string(
            deployment.get("deployment_id"), "deployment ID"
        ),
        "tag": require_nonempty_string(deployment.get("tag"), "deployment tag"),
        "commit": commit,
        "runtime_asset_sha256": backup.validate_sha256(
            deployment.get("runtime_asset_sha256"), "runtime asset digest"
        ),
    }


def decrypt_redis(
    *,
    encrypted: Path,
    identity: Path,
    manifest: dict[str, Any],
    destination: Path,
    age: str,
    starter: StartCommand,
) -> dict[str, Any]:
    try:
        process = starter(
            [age, "--decrypt", "--identity", str(identity), str(encrypted)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise RestoreExportError(f"age could not start: {exc}") from exc
    if process.stdout is None:
        vault.stop_failed_decryptor(process)
        raise RestoreExportError("age stdout is unavailable")
    try:
        result = vault.extract_and_verify_stream(
            process.stdout,
            manifest=manifest,
            redis_destination=destination,
        )
    except Exception:
        process.stdout.close()
        vault.stop_failed_decryptor(process)
        raise
    process.stdout.close()
    try:
        return_code = process.wait(timeout=300)
    except subprocess.TimeoutExpired as exc:
        vault.stop_failed_decryptor(process)
        raise RestoreExportError("age decryption timed out") from exc
    stderr = process.stderr.read() if process.stderr is not None else b""
    if return_code != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:200]
        raise RestoreExportError(f"age decryption failed: {detail}")
    os.chmod(destination, 0o600)
    return result


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def export_restore_bundle(
    *,
    vault_root: Path,
    backup_id: str,
    validation_summary: Path,
    age_identity: Path,
    bundle_dir: Path,
    age: str = "age",
    starter: StartCommand = subprocess.Popen,
    generated_at: str | None = None,
) -> dict[str, Any]:
    backup.validate_backup_id(backup_id)
    (
        encrypted,
        manifest_path,
        receipt_path,
        vault_identity,
        manifest,
        _receipt,
    ) = vault.validate_metadata(vault_root, backup_id)
    identity = vault.require_private_identity(age_identity)
    summary_path = backup.require_regular(
        validation_summary, "vault validation summary"
    )
    initial_artifacts = artifact_digests(
        encrypted,
        manifest_path,
        receipt_path,
        vault_identity,
        summary_path,
    )
    validate_vault_summary(
        summary_path,
        backup_id=backup_id,
        expected_artifacts=initial_artifacts,
        manifest=manifest,
    )
    deployment = deployment_identity(manifest)
    policy_sha256 = backup.validate_sha256(
        manifest.get("backup_policy_sha256"), "backup policy digest"
    )
    redis_profile_sha256 = backup.validate_sha256(
        manifest.get("redis_profile_sha256"), "Redis profile digest"
    )
    source_host_id = backup.validate_sha256(
        manifest["source_host"].get("host_id_sha256"), "source host ID"
    )

    parent = bundle_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    parent = backup.require_directory(parent, "restore bundle parent")
    target = parent / bundle_dir.name
    try:
        target.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise RestoreExportError(
            f"create-only restore bundle already exists: {target}"
        ) from exc
    incomplete = target / ".incomplete"
    try:
        os.chmod(target, 0o700)
        backup.write_new(incomplete, b"restore export in progress\n", 0o600)
        os.chmod(incomplete, 0o600)
        copy_evidence_new(manifest_path, target / "manifest.json", "backup manifest")
        copy_evidence_new(receipt_path, target / "receipt.json", "remote receipt")
        copy_evidence_new(
            summary_path,
            target / "vault-validation.json",
            "vault validation summary",
        )
        rdb = target / "dump.rdb"
        archive_result = decrypt_redis(
            encrypted=encrypted,
            identity=identity,
            manifest=manifest,
            destination=rdb,
            age=age,
            starter=starter,
        )
        if (
            artifact_digests(
                encrypted,
                manifest_path,
                receipt_path,
                vault_identity,
                summary_path,
            )
            != initial_artifacts
        ):
            raise RestoreExportError("vault evidence changed during restore export")
        verify_copied_evidence(target, initial_artifacts)

        result = {
            "schema_version": 1,
            "generated_at": generated_at or utc_now(),
            "backup_id": backup_id,
            "overall_pass": True,
            "identities": {
                "source_host_id_sha256": source_host_id,
                "vault_host_id_sha256": initial_artifacts["vault_host_id_sha256"],
                "deployment": deployment,
            },
            "policy": {
                "backup_policy_sha256": policy_sha256,
                "redis_profile_sha256": redis_profile_sha256,
            },
            "artifacts": {
                **initial_artifacts,
                "plaintext_archive_sha256": archive_result["plaintext_sha256"],
                "redis_sha256": archive_result["redis_sha256"],
                "redis_size_bytes": archive_result["redis_size_bytes"],
            },
            "restore_payload": {
                "path": "dump.rdb",
                "sha256": archive_result["redis_sha256"],
                "size_bytes": archive_result["redis_size_bytes"],
                "header": "REDIS",
            },
            "create_only": True,
            "formal_todo0012_claim": False,
            "restore_known_good": False,
            "secret_material_recorded": False,
        }
        bundle_manifest = target / "bundle.json"
        backup.write_new(bundle_manifest, backup.canonical_json(result), 0o600)
        os.chmod(bundle_manifest, 0o600)
        fsync_directory(target)
        incomplete.unlink()
        fsync_directory(target)
        return result
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--backup-id", required=True)
    parser.add_argument(
        "--vault-validation-summary",
        "--validation-summary",
        dest="validation_summary",
        type=Path,
        required=True,
    )
    parser.add_argument("--age-identity", type=Path, required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--age", default="age")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = export_restore_bundle(
            vault_root=args.vault_root,
            backup_id=args.backup_id,
            validation_summary=args.validation_summary,
            age_identity=args.age_identity,
            bundle_dir=args.bundle_dir,
            age=args.age,
        )
    except (backup.BackupError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"restore bundle export: FAIL: {exc}", file=sys.stderr)
        return 1
    print("restore bundle export: PASS")
    print(f"backup_id: {result['backup_id']}")
    print(f"bundle: {args.bundle_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
