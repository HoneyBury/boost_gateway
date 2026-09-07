#!/usr/bin/env python3
"""Apply governed, capacity-bounded retention to an off-host backup vault."""

from __future__ import annotations

import argparse
import json
import shutil
import stat
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from scripts.lib.backup_recovery import (  # noqa: E402
        BackupError,
        default_prune_id,
        load_json_object,
        logical_regular_file_bytes,
        plan_remote_retention,
        prune_remote,
        require_regular,
        require_secure_vault_layout,
        retention_evidence_documents,
        sha256_file,
        validate_backup_id,
        valid_utc_timestamp,
        vault_local_lock,
        verified_vault_records,
    )
except ModuleNotFoundError as exc:  # standalone operations-host installation
    if exc.name != "scripts":
        raise
    from backup_recovery import (  # type: ignore[no-redef]  # noqa: E402
        BackupError,
        default_prune_id,
        load_json_object,
        logical_regular_file_bytes,
        plan_remote_retention,
        prune_remote,
        require_regular,
        require_secure_vault_layout,
        retention_evidence_documents,
        sha256_file,
        validate_backup_id,
        valid_utc_timestamp,
        vault_local_lock,
        verified_vault_records,
    )


DEFAULT_VAULT_ROOT = Path("/srv/boost-gateway-vault")
DEFAULT_POLICY = Path(
    "/usr/local/libexec/boost-gateway-vault/deploy/operations/"
    "aliyun-backup-vault-retention-policy.json"
)
EXPECTED_POLICY_ID = "aliyun-offhost-vault-bounded-retention-v1"
EXPECTED_DAILY_COPIES = 7
EXPECTED_WEEKLY_COPIES = 4
EXPECTED_MINIMUM_KNOWN_GOOD = 2
EXPECTED_MAX_VAULT_BYTES = 20_000_000_000
EXPECTED_MINIMUM_FREE_BYTES = 5_000_000_000

RecordsReader = Callable[..., list[dict[str, Any]]]
DiskUsageReader = Callable[[Path], Any]
SizeReader = Callable[[Path], int]
Planner = Callable[..., dict[str, Any]]
Pruner = Callable[..., dict[str, Any]]
DeletionIdFactory = Callable[[], str]


class VaultRetentionError(BackupError):
    """Raised before an unsafe or unverifiable retention operation."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_retention_policy(
    policy_path: Path, *, trusted_owner_uid: int = 0
) -> dict[str, Any]:
    path = require_regular(policy_path, "Aliyun vault retention policy")
    metadata = path.stat()
    if metadata.st_uid != trusted_owner_uid or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise VaultRetentionError(
            "Aliyun vault retention policy must be trusted-owner-owned and not writable by group/world"
        )
    document = load_json_object(path, "Aliyun vault retention policy")
    scope = document.get("scope")
    retention = document.get("retention")
    bootstrap = document.get("bootstrap")
    expected_retention = {
        "daily_copies": EXPECTED_DAILY_COPIES,
        "weekly_copies": EXPECTED_WEEKLY_COPIES,
        "minimum_known_good_copies": EXPECTED_MINIMUM_KNOWN_GOOD,
        "max_vault_bytes": EXPECTED_MAX_VAULT_BYTES,
        "minimum_free_bytes": EXPECTED_MINIMUM_FREE_BYTES,
        "size_accounting": "logical_regular_file_bytes",
        "current_vault_identity_only": True,
        "pre_upload_reservation_required": True,
        "delete_only_after_verified_remote_copy": True,
        "deletion_plan_required": True,
        "deletion_record_required": True,
    }
    if (
        document.get("schema_version") != 1
        or document.get("policy_id") != EXPECTED_POLICY_ID
        or not isinstance(scope, dict)
        or scope.get("host_role") != "off_host_backup_vault"
        or scope.get("host_alias") != "aliyunserver"
        or scope.get("vault_root") != str(DEFAULT_VAULT_ROOT)
        or scope.get("source_backup_policy")
        != "deploy/operations/backup-recovery-policy.example.json"
        or scope.get("source_retention_override") is not True
        or retention != expected_retention
        or not isinstance(bootstrap, dict)
        or bootstrap.get("new_vault_identity_required") is not True
        or bootstrap.get("mixed_identity_records_rejected") is not True
        or bootstrap.get("retention_disabled_until_two_known_good_copies") is not True
        or document.get("secret_material_recorded") is not False
    ):
        raise VaultRetentionError("Aliyun vault retention policy content differs")
    return {
        **expected_retention,
        "policy_id": EXPECTED_POLICY_ID,
        "policy_sha256": sha256_file(path),
        "vault_root": str(DEFAULT_VAULT_ROOT),
    }


def require_secure_vault(
    vault_root: Path, *, trusted_owner_uid: int = 0
) -> tuple[Path, Path, Path]:
    return require_secure_vault_layout(
        vault_root,
        vault_root / ".vault-identity",
        vault_root / ".vault.lock",
        trusted_owner_uid=trusted_owner_uid,
    )


def vault_size_bytes(vault_root: Path) -> int:
    """Compatibility name for the governed logical regular-file byte measure."""

    try:
        return logical_regular_file_bytes(vault_root)
    except BackupError as exc:
        raise VaultRetentionError(str(exc)) from exc


def capacity_snapshot(
    vault_root: Path,
    *,
    phase: str,
    max_vault_bytes: int,
    minimum_free_bytes: int,
    size_reader: SizeReader = vault_size_bytes,
    disk_usage_reader: DiskUsageReader = shutil.disk_usage,
    enforce_limits: bool,
) -> dict[str, int]:
    if max_vault_bytes <= 0 or minimum_free_bytes < 0:
        raise VaultRetentionError("capacity limits are invalid")
    vault_bytes = size_reader(vault_root)
    free_bytes = int(disk_usage_reader(vault_root).free)
    if vault_bytes < 0 or free_bytes < 0:
        raise VaultRetentionError("capacity observation is invalid")
    if enforce_limits:
        if vault_bytes > max_vault_bytes:
            raise VaultRetentionError(
                "vault exceeds the configured limit during "
                f"{phase}: observed={vault_bytes}; maximum={max_vault_bytes}"
            )
        if free_bytes < minimum_free_bytes:
            raise VaultRetentionError(
                "filesystem free space is below the configured floor during "
                f"{phase}: observed={free_bytes}; minimum={minimum_free_bytes}"
            )
    return {"vault_bytes": vault_bytes, "filesystem_free_bytes": free_bytes}


def latest_verified_anchor(
    vault_root: Path,
    vault_identity: Path,
    records: list[dict[str, Any]],
) -> dict[str, str]:
    if not records:
        raise VaultRetentionError("no complete readback-verified backup is available")
    newest = max(
        records,
        key=lambda item: (
            str(item.get("created_at", "")),
            str(item.get("backup_id", "")),
        ),
    )
    backup_id = validate_backup_id(str(newest.get("backup_id", "")))
    directory = require_secure_anchor_directory(vault_root, backup_id)
    receipt_path = require_regular(
        directory / "receipt.json", "retention anchor receipt"
    )
    receipt = load_json_object(receipt_path, "retention anchor receipt")
    receipt_sha256 = sha256_file(receipt_path)
    expected = {
        "schema_version": 1,
        "backup_id": backup_id,
        "vault_host_id_sha256": sha256_file(vault_identity),
        "remote_readback_sha256": True,
        "create_only": True,
        "secret_material_recorded": False,
    }
    if (
        any(receipt.get(field) != value for field, value in expected.items())
        or receipt_sha256 != newest.get("receipt_sha256")
        or not valid_utc_timestamp(receipt.get("stored_at"))
    ):
        raise VaultRetentionError(
            "latest complete backup does not have a valid readback receipt"
        )
    return {
        "backup_id": backup_id,
        "created_at": str(newest["created_at"]),
        "receipt_sha256": receipt_sha256,
    }


def require_secure_anchor_directory(vault_root: Path, backup_id: str) -> Path:
    backups = (vault_root / "backups").resolve(strict=True)
    directory = (backups / backup_id).resolve(strict=True)
    if (
        (backups / backup_id).is_symlink()
        or not directory.is_dir()
        or directory.parent != backups
    ):
        raise VaultRetentionError("retention anchor is outside the backup root")
    return directory


def run_retention(
    args: argparse.Namespace,
    *,
    records_reader: RecordsReader = verified_vault_records,
    size_reader: SizeReader = vault_size_bytes,
    disk_usage_reader: DiskUsageReader = shutil.disk_usage,
    planner: Planner = plan_remote_retention,
    pruner: Pruner = prune_remote,
    generated_at: str | None = None,
    deletion_id_factory: DeletionIdFactory = default_prune_id,
    trusted_owner_uid: int = 0,
    enforce_policy_vault_root: bool = True,
) -> dict[str, Any]:
    root, identity, lock = require_secure_vault(
        args.vault_root, trusted_owner_uid=trusted_owner_uid
    )
    operation_timestamp = generated_at or utc_now()
    deletion_id = deletion_id_factory()
    with vault_local_lock(root, lock, trusted_owner_uid=trusted_owner_uid):
        policy = load_retention_policy(args.policy, trusted_owner_uid=trusted_owner_uid)
        if enforce_policy_vault_root and root != Path(policy["vault_root"]):
            raise VaultRetentionError("vault root differs from the governed policy")
        expected_identity = sha256_file(identity)
        before = capacity_snapshot(
            root,
            phase="preflight",
            max_vault_bytes=policy["max_vault_bytes"],
            minimum_free_bytes=policy["minimum_free_bytes"],
            size_reader=size_reader,
            disk_usage_reader=disk_usage_reader,
            enforce_limits=False,
        )
        records = records_reader(root, expected_vault_host_id_sha256=expected_identity)
        anchor = latest_verified_anchor(root, identity, records)
        plan = planner(
            root,
            anchor_backup_id=anchor["backup_id"],
            anchor_receipt_sha256=anchor["receipt_sha256"],
            daily_copies=policy["daily_copies"],
            weekly_copies=policy["weekly_copies"],
            minimum_known_good=policy["minimum_known_good_copies"],
            expected_vault_host_id_sha256=expected_identity,
            records=records,
        )
        deleted_bytes = plan.get("deleted_logical_bytes")
        if (
            not isinstance(deleted_bytes, int)
            or isinstance(deleted_bytes, bool)
            or deleted_bytes < 0
            or deleted_bytes > before["vault_bytes"]
        ):
            raise VaultRetentionError("retention deletion plan byte count is invalid")
        evidence = retention_evidence_documents(
            plan,
            deletion_id=deletion_id,
            recorded_at=operation_timestamp,
            daily_copies=policy["daily_copies"],
            weekly_copies=policy["weekly_copies"],
            minimum_known_good=policy["minimum_known_good_copies"],
        )
        planned_after_bytes = (
            before["vault_bytes"] - deleted_bytes + evidence["logical_bytes"]
        )
        if planned_after_bytes > policy["max_vault_bytes"]:
            raise VaultRetentionError(
                "mandatory retained vault set exceeds the configured limit; zero deletion performed"
            )
        retained = pruner(
            root,
            anchor_backup_id=anchor["backup_id"],
            anchor_receipt_sha256=anchor["receipt_sha256"],
            daily_copies=policy["daily_copies"],
            weekly_copies=policy["weekly_copies"],
            minimum_known_good=policy["minimum_known_good_copies"],
            deletion_id=deletion_id,
            recorded_at=operation_timestamp,
            expected_vault_host_id_sha256=expected_identity,
        )
        if (
            retained.get("state") != "deleted"
            or retained.get("anchor_backup_id") != anchor["backup_id"]
            or retained.get("anchor_receipt_sha256") != anchor["receipt_sha256"]
            or retained.get("deleted_backup_ids") != plan.get("deleted_backup_ids")
            or retained.get("deleted_logical_bytes") != deleted_bytes
            or retained.get("deletion_id") != deletion_id
            or retained.get("recorded_at") != operation_timestamp
            or retained.get("delete_only_after_verified_remote_copy") is not True
            or retained.get("secret_material_recorded") is not False
        ):
            raise VaultRetentionError(
                "retention result does not satisfy the planned deletion contract"
            )
        after = capacity_snapshot(
            root,
            phase="completion",
            max_vault_bytes=policy["max_vault_bytes"],
            minimum_free_bytes=policy["minimum_free_bytes"],
            size_reader=size_reader,
            disk_usage_reader=disk_usage_reader,
            enforce_limits=True,
        )
    return {
        "schema_version": 1,
        "generated_at": operation_timestamp,
        "overall_pass": True,
        "anchor": anchor,
        "policy": {
            "policy_id": policy["policy_id"],
            "policy_sha256": policy["policy_sha256"],
            "daily_copies": policy["daily_copies"],
            "weekly_copies": policy["weekly_copies"],
            "minimum_known_good_copies": policy["minimum_known_good_copies"],
            "max_vault_bytes": policy["max_vault_bytes"],
            "minimum_free_bytes": policy["minimum_free_bytes"],
            "size_accounting": policy["size_accounting"],
        },
        "capacity": {
            "before": before,
            "planned_deletion_evidence_bytes": evidence["logical_bytes"],
            "planned_after_vault_bytes": planned_after_bytes,
            "after": after,
        },
        "retention": retained,
        "secret_material_recorded": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_retention(args)
    except (BackupError, OSError, TypeError, ValueError) as exc:
        print(f"backup vault retention: FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
