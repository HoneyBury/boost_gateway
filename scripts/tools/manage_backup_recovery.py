#!/usr/bin/env python3
"""Compatibility CLI for governed backup and recovery operations."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from scripts.lib.backup_recovery import *  # noqa: E402,F403
except ModuleNotFoundError as exc:  # standalone forced-command installation
    if exc.name != "scripts":
        raise
    from backup_recovery import *  # type: ignore[no-redef]  # noqa: E402,F403



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser(
        "backup", help="create, encrypt and optionally upload one backup"
    )
    backup.add_argument("--backup-id", default="")
    backup.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    backup.add_argument("--redis-profile", type=Path, required=True)
    backup.add_argument("--deployment-record", type=Path, required=True)
    backup.add_argument("--recipient-file", type=Path, required=True)
    backup.add_argument(
        "--staging-root", type=Path, default=Path("/var/backups/boost-gateway/staging")
    )
    backup.add_argument(
        "--output-root", type=Path, default=Path("/var/backups/boost-gateway/encrypted")
    )
    backup.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK)
    backup.add_argument("--redis-container", default="boost-redis")
    backup.add_argument("--docker", default="/usr/bin/docker")
    backup.add_argument("--age", default="/usr/bin/age")
    backup.add_argument(
        "--retention-class", action="append", choices=("daily", "weekly"), default=[]
    )
    backup.add_argument("--remote-host")
    backup.add_argument("--remote-command", default="boost-gateway-vault store")
    backup.add_argument("--remote-host-id-attestation", type=Path)
    backup.add_argument("--ssh", default="/usr/bin/ssh")
    backup.add_argument("--ssh-identity-file", type=Path)
    backup.add_argument("--ssh-known-hosts", type=Path)
    backup.add_argument(
        "--receipt-root", type=Path, default=Path("/var/backups/boost-gateway/receipts")
    )

    store = subparsers.add_parser(
        "remote-store", help="receive one framed create-only backup on the vault"
    )
    store.add_argument("--vault-root", type=Path, required=True)
    store.add_argument("--vault-identity-file", type=Path, required=True)

    receipt = subparsers.add_parser(
        "remote-receipt", help="read an existing vault receipt"
    )
    receipt.add_argument("--vault-root", type=Path, required=True)
    receipt.add_argument("--backup-id", required=True)

    attest = subparsers.add_parser(
        "attest-known-good",
        help="create an evidence-bound known-good attestation on the vault",
    )
    attest.add_argument("--vault-root", type=Path, required=True)
    attest.add_argument("--backup-id", required=True)
    attest.add_argument("--vault-validation-summary", type=Path, required=True)
    attest.add_argument("--restore-summary", type=Path, required=True)
    attest.add_argument("--business-summary", type=Path, required=True)

    prune = subparsers.add_parser(
        "remote-prune", help="apply guarded retention on the vault"
    )
    prune.add_argument("--vault-root", type=Path, required=True)
    prune.add_argument("--anchor-backup-id", required=True)
    prune.add_argument("--anchor-receipt-sha256", required=True)
    prune.add_argument("--daily-copies", type=int, default=14)
    prune.add_argument("--weekly-copies", type=int, default=8)
    prune.add_argument("--minimum-known-good", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "remote-store":
            result = remote_store(
                args.vault_root, args.vault_identity_file, sys.stdin.buffer
            )
        elif args.command == "remote-receipt":
            result = remote_receipt(args.vault_root, args.backup_id)
        elif args.command == "attest-known-good":
            result = create_known_good_attestation(
                args.vault_root,
                backup_id=args.backup_id,
                vault_validation_summary=args.vault_validation_summary,
                restore_summary=args.restore_summary,
                business_summary=args.business_summary,
            )
        elif args.command == "remote-prune":
            result = prune_remote(
                args.vault_root,
                anchor_backup_id=args.anchor_backup_id,
                anchor_receipt_sha256=args.anchor_receipt_sha256,
                daily_copies=args.daily_copies,
                weekly_copies=args.weekly_copies,
                minimum_known_good=args.minimum_known_good,
            )
        else:
            backup_id = args.backup_id or default_backup_id()
            classes = args.retention_class or ["daily"]
            archive, manifest_path, manifest = create_encrypted_backup(
                backup_id=backup_id,
                policy_path=args.policy,
                redis_profile=args.redis_profile,
                deployment_record=args.deployment_record,
                recipient_file=args.recipient_file,
                staging_root=args.staging_root,
                output_root=args.output_root,
                lock_path=args.lock_path,
                redis_container=args.redis_container,
                docker=args.docker,
                age=args.age,
                retention_classes=classes,
            )
            result = {
                "manifest": manifest,
                "archive_path": str(archive),
                "manifest_path": str(manifest_path),
            }
            remote_values = [
                args.remote_host,
                args.remote_host_id_attestation,
                args.ssh_identity_file,
                args.ssh_known_hosts,
            ]
            if any(remote_values) and not all(remote_values):
                raise BackupError("all remote upload arguments are required together")
            if all(remote_values):
                source_host_id = str(manifest["source_host"].get("host_id_sha256", ""))
                expected_remote_id = parse_expected_digest(
                    args.remote_host_id_attestation
                )
                receipt = upload_remote(
                    backup_id=backup_id,
                    archive=archive,
                    manifest=manifest_path,
                    remote_host=args.remote_host,
                    remote_command=args.remote_command,
                    ssh=args.ssh,
                    ssh_identity_file=args.ssh_identity_file,
                    ssh_known_hosts=args.ssh_known_hosts,
                    source_host_id_sha256=source_host_id,
                    expected_remote_host_id_sha256=expected_remote_id,
                )
                receipt_path = args.receipt_root / f"{backup_id}.json"
                write_new(receipt_path, canonical_json(receipt), 0o640)
                result["remote_receipt"] = receipt
                result["remote_receipt_path"] = str(receipt_path)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (BackupError, OSError, ValueError) as exc:
        print(f"backup recovery: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
