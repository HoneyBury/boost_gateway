from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.lib import backup_recovery as backup
from scripts.tools import backup_vault_ssh_receiver as receiver
from scripts.tools import run_backup_vault_retention as retention

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "deploy/operations/aliyun-backup-vault-retention-policy.json"
FIXED_RETENTION_TIME = "2026-09-06T04:00:00Z"
FIXED_DELETION_ID = "prune-test-fixed"


class BackupVaultRetentionTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.vault = Path(temporary.name) / "vault"
        self.vault.mkdir(mode=0o750)
        self.vault.chmod(0o750)
        for name in ("backups", ".incoming", "known-good", "deletions", ".trash"):
            directory = self.vault / name
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)
        self.identity = self.vault / ".vault-identity"
        self.identity.write_bytes(b"v" * 32)
        self.identity.chmod(0o640)
        self.lock = self.vault / ".vault.lock"
        self.lock.touch(mode=0o660)
        self.lock.chmod(0o660)
        self.trusted_uid = os.getuid()

    def args(self) -> argparse.Namespace:
        return argparse.Namespace(vault_root=self.vault, policy=POLICY)

    def run_kwargs(self) -> dict[str, object]:
        return {
            "trusted_owner_uid": self.trusted_uid,
            "enforce_policy_vault_root": False,
            "generated_at": FIXED_RETENTION_TIME,
            "deletion_id_factory": lambda: FIXED_DELETION_ID,
        }

    def record(
        self, backup_id: str, created_at: str, *, logical_bytes: int = 100
    ) -> dict[str, object]:
        directory = self.vault / "backups" / backup_id
        directory.mkdir(mode=0o700)
        receipt = {
            "schema_version": 1,
            "backup_id": backup_id,
            "stored_at": created_at,
            "vault_host_id_sha256": retention.sha256_file(self.identity),
            "remote_readback_sha256": True,
            "create_only": True,
            "secret_material_recorded": False,
        }
        receipt_path = directory / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        return {
            "backup_id": backup_id,
            "directory": directory,
            "created_at": created_at,
            "classes": {"daily"},
            "receipt_sha256": retention.sha256_file(receipt_path),
            "logical_bytes": logical_bytes,
        }

    @staticmethod
    def successful_plan(
        anchor: dict[str, object],
        deleted_backup_ids: list[str],
        deleted_logical_bytes: int,
    ) -> dict[str, object]:
        return {
            "anchor_backup_id": anchor["backup_id"],
            "anchor_receipt_sha256": anchor["receipt_sha256"],
            "retained_backup_ids": [anchor["backup_id"]],
            "retained_known_good_backup_ids": [],
            "known_good_attestation_sha256s": {},
            "deleted_backup_ids": deleted_backup_ids,
            "deleted_logical_bytes": deleted_logical_bytes,
        }

    @staticmethod
    def successful_prune(
        anchor: dict[str, object],
        deleted_backup_ids: list[str],
        deleted_logical_bytes: int,
    ) -> dict[str, object]:
        return {
            "state": "deleted",
            "anchor_backup_id": anchor["backup_id"],
            "anchor_receipt_sha256": anchor["receipt_sha256"],
            "delete_only_after_verified_remote_copy": True,
            "secret_material_recorded": False,
            "deleted_backup_ids": deleted_backup_ids,
            "deleted_logical_bytes": deleted_logical_bytes,
            "deletion_id": FIXED_DELETION_ID,
            "recorded_at": FIXED_RETENTION_TIME,
        }

    def test_policy_is_the_only_source_of_bounded_defaults(self) -> None:
        args = retention.build_parser().parse_args([])
        self.assertEqual(retention.DEFAULT_POLICY, args.policy)
        policy = retention.load_retention_policy(
            POLICY, trusted_owner_uid=self.trusted_uid
        )
        self.assertEqual(7, policy["daily_copies"])
        self.assertEqual(4, policy["weekly_copies"])
        self.assertEqual(2, policy["minimum_known_good_copies"])
        self.assertEqual(20_000_000_000, policy["max_vault_bytes"])
        self.assertEqual(5_000_000_000, policy["minimum_free_bytes"])
        self.assertEqual("logical_regular_file_bytes", policy["size_accounting"])

    def test_policy_drift_fails_closed(self) -> None:
        drifted = self.vault.parent / "policy.json"
        value = json.loads(POLICY.read_text(encoding="utf-8"))
        value["retention"]["daily_copies"] = 6
        drifted.write_text(json.dumps(value), encoding="utf-8")
        drifted.chmod(0o644)
        with self.assertRaisesRegex(
            retention.VaultRetentionError, "policy content differs"
        ):
            retention.load_retention_policy(drifted, trusted_owner_uid=self.trusted_uid)

    def test_over_limit_vault_self_heals_using_exact_plan(self) -> None:
        older = self.record("backup-old", "2026-09-05T02:20:00Z")
        newest = self.record("backup-new", "2026-09-06T02:20:00Z")
        records = [older, newest]
        plan = self.successful_plan(newest, ["backup-old"], 10_000_000_000)
        planner = mock.Mock(return_value=plan)
        pruner = mock.Mock(
            return_value=self.successful_prune(newest, ["backup-old"], 10_000_000_000)
        )
        sizes = mock.Mock(side_effect=[25_000_000_000, 15_000_000_000])
        disk = mock.Mock(
            side_effect=[
                SimpleNamespace(free=4_000_000_000),
                SimpleNamespace(free=6_000_000_000),
            ]
        )

        result = retention.run_retention(
            self.args(),
            records_reader=lambda _root, **_kwargs: records,
            size_reader=sizes,
            disk_usage_reader=disk,
            planner=planner,
            pruner=pruner,
            **self.run_kwargs(),
        )

        expected_identity = retention.sha256_file(self.identity)
        planner.assert_called_once_with(
            self.vault.resolve(),
            anchor_backup_id="backup-new",
            anchor_receipt_sha256=newest["receipt_sha256"],
            daily_copies=7,
            weekly_copies=4,
            minimum_known_good=2,
            expected_vault_host_id_sha256=expected_identity,
            records=records,
        )
        pruner.assert_called_once_with(
            self.vault.resolve(),
            anchor_backup_id="backup-new",
            anchor_receipt_sha256=newest["receipt_sha256"],
            daily_copies=7,
            weekly_copies=4,
            minimum_known_good=2,
            deletion_id=FIXED_DELETION_ID,
            recorded_at=FIXED_RETENTION_TIME,
            expected_vault_host_id_sha256=expected_identity,
        )
        self.assertEqual(
            15_000_000_000 + result["capacity"]["planned_deletion_evidence_bytes"],
            result["capacity"]["planned_after_vault_bytes"],
        )
        self.assertEqual(backup.sha256_file(POLICY), result["policy"]["policy_sha256"])
        self.assertTrue(result["overall_pass"])

    def test_missing_anchor_never_plans_or_prunes(self) -> None:
        planner = mock.Mock()
        pruner = mock.Mock()
        with self.assertRaisesRegex(retention.VaultRetentionError, "no complete"):
            retention.run_retention(
                self.args(),
                records_reader=lambda _root, **_kwargs: [],
                size_reader=lambda _root: 1,
                disk_usage_reader=lambda _root: SimpleNamespace(free=10_000_000_000),
                planner=planner,
                pruner=pruner,
                **self.run_kwargs(),
            )
        planner.assert_not_called()
        pruner.assert_not_called()

    def test_invalid_latest_receipt_never_falls_back_or_prunes(self) -> None:
        older = self.record("backup-old", "2026-09-05T02:20:00Z")
        newest = self.record("backup-new", "2026-09-06T02:20:00Z")
        receipt_path = self.vault / "backups/backup-new/receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["vault_host_id_sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        newest["receipt_sha256"] = retention.sha256_file(receipt_path)
        pruner = mock.Mock()

        with self.assertRaisesRegex(retention.VaultRetentionError, "valid readback"):
            retention.run_retention(
                self.args(),
                records_reader=lambda _root, **_kwargs: [older, newest],
                size_reader=lambda _root: 1,
                disk_usage_reader=lambda _root: SimpleNamespace(free=10_000_000_000),
                planner=mock.Mock(),
                pruner=pruner,
                **self.run_kwargs(),
            )
        pruner.assert_not_called()

    def test_mandatory_retained_set_over_limit_performs_zero_deletion(self) -> None:
        newest = self.record("backup-new", "2026-09-06T02:20:00Z")
        pruner = mock.Mock()
        with self.assertRaisesRegex(
            retention.VaultRetentionError, "zero deletion performed"
        ):
            retention.run_retention(
                self.args(),
                records_reader=lambda _root, **_kwargs: [newest],
                size_reader=lambda _root: 25_000_000_000,
                disk_usage_reader=lambda _root: SimpleNamespace(free=4_000_000_000),
                planner=mock.Mock(
                    return_value=self.successful_plan(
                        newest, ["eligible-old"], 4_000_000_000
                    )
                ),
                pruner=pruner,
                **self.run_kwargs(),
            )
        pruner.assert_not_called()

    def test_deletion_evidence_is_reserved_before_any_boundary_deletion(self) -> None:
        newest = self.record("backup-new", "2026-09-06T02:20:00Z")
        pruner = mock.Mock()
        with self.assertRaisesRegex(
            retention.VaultRetentionError, "zero deletion performed"
        ):
            retention.run_retention(
                self.args(),
                records_reader=lambda _root, **_kwargs: [newest],
                size_reader=lambda _root: 21_000_000_000,
                disk_usage_reader=lambda _root: SimpleNamespace(free=6_000_000_000),
                planner=mock.Mock(
                    return_value=self.successful_plan(
                        newest, ["eligible-old"], 1_000_000_000
                    )
                ),
                pruner=pruner,
                **self.run_kwargs(),
            )
        pruner.assert_not_called()

    def test_insecure_root_or_identity_never_calls_pruner(self) -> None:
        pruner = mock.Mock()
        self.vault.chmod(0o700)
        with self.assertRaisesRegex(retention.BackupError, "mode 0750"):
            retention.run_retention(self.args(), pruner=pruner, **self.run_kwargs())
        pruner.assert_not_called()

        self.vault.chmod(0o750)
        self.identity.chmod(0o600)
        with self.assertRaisesRegex(retention.BackupError, "mode 0640"):
            retention.run_retention(self.args(), pruner=pruner, **self.run_kwargs())
        pruner.assert_not_called()

    def test_post_prune_capacity_is_a_hard_failure(self) -> None:
        record = self.record("backup-one", "2026-09-06T02:20:00Z")
        plan = self.successful_plan(record, ["backup-old"], 10_000_000_000)
        pruner = mock.Mock(
            return_value=self.successful_prune(record, ["backup-old"], 10_000_000_000)
        )
        with self.assertRaisesRegex(retention.VaultRetentionError, "configured limit"):
            retention.run_retention(
                self.args(),
                records_reader=lambda _root, **_kwargs: [record],
                size_reader=mock.Mock(side_effect=[25_000_000_000, 21_000_000_000]),
                disk_usage_reader=lambda _root: SimpleNamespace(free=6_000_000_000),
                planner=mock.Mock(return_value=plan),
                pruner=pruner,
                **self.run_kwargs(),
            )
        pruner.assert_called_once()

    def test_post_prune_free_space_is_a_hard_failure(self) -> None:
        record = self.record("backup-one", "2026-09-06T02:20:00Z")
        plan = self.successful_plan(record, [], 0)
        pruner = mock.Mock(return_value=self.successful_prune(record, [], 0))
        with self.assertRaisesRegex(retention.VaultRetentionError, "free space"):
            retention.run_retention(
                self.args(),
                records_reader=lambda _root, **_kwargs: [record],
                size_reader=lambda _root: 10_000_000_000,
                disk_usage_reader=mock.Mock(
                    side_effect=[
                        SimpleNamespace(free=4_000_000_000),
                        SimpleNamespace(free=4_500_000_000),
                    ]
                ),
                planner=mock.Mock(return_value=plan),
                pruner=pruner,
                **self.run_kwargs(),
            )
        pruner.assert_called_once()

    def test_symlink_in_vault_is_rejected_by_shared_byte_measure(self) -> None:
        target = self.vault / "backups" / "safe"
        target.write_text("data", encoding="utf-8")
        os.symlink(target, self.vault / "backups" / "unsafe")
        with self.assertRaisesRegex(retention.VaultRetentionError, "unsafe file"):
            retention.vault_size_bytes(self.vault)

    def test_logical_byte_measure_counts_hardlinks_and_sparse_size(self) -> None:
        inventory = self.vault.parent / "logical-inventory"
        inventory.mkdir()
        original = inventory / "original"
        original.write_bytes(b"four")
        os.link(original, inventory / "hardlink")
        sparse = inventory / "sparse"
        with sparse.open("wb") as stream:
            stream.seek(1_000_000)
            stream.write(b"x")
        self.assertEqual(
            4 + 4 + 1_000_001,
            backup.logical_regular_file_bytes(inventory),
        )

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires a POSIX host")
    def test_logical_byte_measure_rejects_special_files(self) -> None:
        inventory = self.vault.parent / "special-inventory"
        inventory.mkdir()
        os.mkfifo(inventory / "fifo")
        with self.assertRaisesRegex(backup.BackupError, "unsafe file"):
            backup.logical_regular_file_bytes(inventory)

    def test_vault_lock_rejects_hardlink_alias(self) -> None:
        alias = self.vault.parent / "lock-alias"
        os.link(self.lock, alias)
        with self.assertRaisesRegex(backup.BackupError, "mode 0660"):
            with backup.vault_local_lock(
                self.vault, self.lock, trusted_owner_uid=self.trusted_uid
            ):
                self.fail("unsafe multiply-linked lock was acquired")

    def test_receiver_enforces_root_controlled_identity_and_data_boundary(self) -> None:
        with self.assertRaisesRegex(backup.BackupError, "trusted-owner"):
            receiver.require_secure_vault(
                self.vault,
                self.identity,
                self.lock,
                trusted_owner_uid=self.trusted_uid + 1,
            )

        receiver.require_secure_vault(
            self.vault,
            self.identity,
            self.lock,
            trusted_owner_uid=self.trusted_uid,
        )
        self.identity.chmod(0o660)
        with self.assertRaisesRegex(backup.BackupError, "mode 0640"):
            receiver.require_secure_vault(
                self.vault,
                self.identity,
                self.lock,
                trusted_owner_uid=self.trusted_uid,
            )

    def test_receiver_and_retention_lock_are_mutually_exclusive(self) -> None:
        second_entered = threading.Event()
        second_finished = threading.Event()

        def second_holder() -> None:
            with backup.vault_local_lock(
                self.vault, self.lock, trusted_owner_uid=self.trusted_uid
            ):
                second_entered.set()
            second_finished.set()

        with backup.vault_local_lock(
            self.vault, self.lock, trusted_owner_uid=self.trusted_uid
        ):
            thread = threading.Thread(target=second_holder, daemon=True)
            thread.start()
            self.assertFalse(second_entered.wait(0.1))
        self.assertTrue(second_entered.wait(1.0))
        self.assertTrue(second_finished.wait(1.0))
        thread.join(timeout=1.0)

    def test_cli_failure_does_not_echo_untrusted_receipt_content(self) -> None:
        secret = "should-never-appear"
        stderr = StringIO()
        stdout = StringIO()
        with mock.patch.object(
            retention,
            "run_retention",
            side_effect=retention.VaultRetentionError("anchor receipt is invalid"),
        ), redirect_stderr(stderr), redirect_stdout(stdout):
            result = retention.main([])
        self.assertEqual(1, result)
        self.assertNotIn(secret, stderr.getvalue())
        self.assertEqual("", stdout.getvalue())


class BackupVaultHostInstallContractTest(unittest.TestCase):
    def test_installer_uses_root_controlled_forced_command_boundaries(self) -> None:
        installer = (
            ROOT / "deploy/operations/install_backup_vault_host_units.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("VAULT_USER=boost-gateway-vault", installer)
        self.assertIn('usermod --lock "${VAULT_USER}"', installer)
        self.assertIn("[[ ${key_type} == ssh-ed25519 ]]", installer)
        self.assertIn('from="%s",restrict,command="%s"', installer)
        self.assertIn('chown "root:${VAULT_GROUP}" "${authorized_keys_tmp}"', installer)
        self.assertIn('chown "root:${VAULT_GROUP}" "${VAULT_IDENTITY}"', installer)
        self.assertIn('chmod 0640 "${VAULT_IDENTITY}"', installer)
        self.assertIn('chmod 0660 "${VAULT_LOCK}"', installer)
        self.assertNotIn("--lock-file", installer)
        self.assertNotIn("--max-vault-bytes", installer)
        self.assertNotIn("--minimum-free-bytes", installer)
        self.assertIn("100.64.0.0/10", installer)
        self.assertIn("fd7a:115c:a1e0::/48", installer)
        self.assertIn("managed directory must not be a symlink", installer)
        self.assertIn("--allow-existing-vault-layout-migration", installer)
        self.assertIn("after quiescing all upload/prune callers", installer)
        self.assertIn("flock -x 9", installer)
        self.assertIn("retention_policy_sha256=", installer)
        self.assertIn("load_retention_policy", installer)
        self.assertIn("systemctl disable --now", installer)
        self.assertNotIn("systemctl enable --now", installer)
        self.assertNotIn("--vault-identity-source", installer)
        self.assertNotIn("NOPASSWD", installer)

    def test_retention_unit_uses_policy_specific_paths_and_late_timer(self) -> None:
        service = (
            ROOT / "deploy/systemd/boost-gateway-backup-vault-retention.service"
        ).read_text(encoding="utf-8")
        timer = (
            ROOT / "deploy/systemd/boost-gateway-backup-vault-retention.timer"
        ).read_text(encoding="utf-8")

        self.assertIn("User=boost-gateway-vault", service)
        self.assertNotIn("ConditionPath", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("PrivateNetwork=yes", service)
        self.assertIn("ReadWritePaths=/srv/boost-gateway-vault\n", service)
        self.assertIn("ReadOnlyPaths=/srv/boost-gateway-vault/.vault-identity", service)
        self.assertNotIn("ReadWritePaths=/srv/boost-gateway-vault/backups", service)
        self.assertIn("--policy /usr/local/libexec/boost-gateway-vault/", service)
        self.assertNotIn("--daily-copies", service)
        self.assertIn("OnCalendar=*-*-* 04:00:00 UTC", timer)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()
