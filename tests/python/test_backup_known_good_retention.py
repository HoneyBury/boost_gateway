from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.lib import backup_recovery as recovery
from scripts.tools import manage_backup_recovery as backup


class KnownGoodRetentionTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / "vault"
        self.vault.mkdir(mode=0o750)
        self.vault.chmod(0o750)
        for name in ("backups", ".incoming", "known-good", "deletions", ".trash"):
            directory = self.vault / name
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)
        self.identity = self.vault / ".vault-identity"
        self.identity.write_bytes(b"v" * 32)
        self.identity.chmod(0o640)
        self.lock = self.vault / recovery.DEFAULT_VAULT_LOCK_NAME
        self.lock.touch(mode=0o660)
        self.lock.chmod(0o660)
        self.trusted_uid = os.getuid()

    def _create_backup(
        self,
        backup_id: str,
        created_at: str,
        classes: list[str] | None = None,
    ) -> dict[str, Path]:
        directory = self.vault / "backups" / backup_id
        directory.mkdir()
        archive = directory / "payload.tar.age"
        archive.write_bytes(f"encrypted-{backup_id}".encode("ascii"))
        redis_sha = hashlib.sha256(f"redis-{backup_id}".encode("ascii")).hexdigest()
        manifest = {
            "schema_version": 2,
            "backup_id": backup_id,
            "created_at": created_at,
            "archive": {
                "name": f"{backup_id}.tar.age",
                "sha256": backup.sha256_file(archive),
                "size_bytes": archive.stat().st_size,
                "plaintext_sha256": "3" * 64,
            },
            "deployment": {
                "deployment_id": "v3.6.2-test",
                "tag": "v3.6.2",
                "commit": "a" * 40,
                "runtime_asset_sha256": "b" * 64,
                "host": {"host_id_sha256": "1" * 64},
            },
            "source_host": {"host_id_sha256": "1" * 64},
            "backup_policy_sha256": "2" * 64,
            "redis_profile_sha256": "5" * 64,
            "sources": [
                {
                    "id": "redis_snapshot",
                    "archive_path": "redis/dump.rdb",
                    "sha256": redis_sha,
                    "size_bytes": 128,
                }
            ],
            "source_links": [],
            "archive_contract": {
                "format": "link_free_tar_v1",
                "symbolic_link_entries": 0,
                "hard_link_entries": 0,
                "symbolic_links_recorded": 0,
            },
            "retention_classes": classes or ["daily"],
            "consistent_redis_snapshot": True,
            "encrypted_before_transfer": True,
            "formal_todo0012_claim": False,
            "secret_material_recorded": False,
        }
        manifest_path = directory / "manifest.json"
        manifest_path.write_bytes(backup.canonical_json(manifest))
        receipt = {
            "schema_version": 1,
            "backup_id": backup_id,
            "stored_at": created_at,
            "archive_sha256": backup.sha256_file(archive),
            "archive_size": archive.stat().st_size,
            "manifest_sha256": backup.sha256_file(manifest_path),
            "manifest_size": manifest_path.stat().st_size,
            "vault_host_id_sha256": backup.sha256_file(self.identity),
            "remote_readback_sha256": True,
            "create_only": True,
            "secret_material_recorded": False,
        }
        receipt_path = directory / "receipt.json"
        receipt_path.write_bytes(backup.canonical_json(receipt))

        evidence = self.root / "evidence" / backup_id
        evidence.mkdir(parents=True)
        validation = {
            "schema_version": 1,
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
                "archive_sha256": backup.sha256_file(archive),
                "manifest_sha256": backup.sha256_file(manifest_path),
                "receipt_sha256": backup.sha256_file(receipt_path),
                "vault_host_id_sha256": backup.sha256_file(self.identity),
                "plaintext_sha256": "3" * 64,
                "plaintext_size_bytes": 10240,
                "member_count": 5,
                "redis_sha256": redis_sha,
                "redis_size_bytes": 128,
            },
            "restore_known_good": False,
            "formal_todo0012_claim": False,
            "secret_material_recorded": False,
        }
        validation_path = evidence / "vault-validation.json"
        validation_path.write_bytes(backup.canonical_json(validation))
        deployment = {
            field: manifest["deployment"][field]
            for field in ("deployment_id", "tag", "commit", "runtime_asset_sha256")
        }
        restore_id = f"restore-{backup_id}"
        target_volume = f"target-{backup_id}"
        target_identity = hashlib.sha256(target_volume.encode("ascii")).hexdigest()
        active_identity = "6" * 64
        restored_seed = hashlib.sha256(f"seed-{backup_id}".encode("ascii")).hexdigest()
        restore = {
            "schema_version": 1,
            "restore_id": restore_id,
            "backup_id": backup_id,
            "deployment": deployment,
            "elapsed_seconds": 30.0,
            "rto_budget_seconds": 600.0,
            "rto_pass": True,
            "overall_pass": True,
            "status": "passed",
            "cleanup_failures": [],
            "redis_snapshot_sha256": redis_sha,
            "backup_manifest_sha256": backup.sha256_file(manifest_path),
            "remote_receipt_sha256": backup.sha256_file(receipt_path),
            "vault_validation_sha256": backup.sha256_file(validation_path),
            "transport_receipt_sha256": "7" * 64,
            "transport_remote_readback_bound": True,
            "backup_policy_sha256": "2" * 64,
            "redis_profile_sha256": "5" * 64,
            "source_host_id_sha256": "1" * 64,
            "vault_host_id_sha256": backup.sha256_file(self.identity),
            "target_volume": target_volume,
            "target_volume_identity_sha256": target_identity,
            "active_volume": "boost-gateway-production-redis-data",
            "active_volume_identity_sha256": active_identity,
            "redis_image": "sha256:" + "c" * 64,
            "canonical_seed_restored_sha256": restored_seed,
            "leaderboard_seed_exact": True,
            "redis_ping": True,
            "offline_redis_check_rdb": True,
            "restore_payload_copy_verified": True,
            "vault_link_free_validation_bound": True,
            "active_volume_preserved": True,
            "target_volume_retained": True,
            "production_switched": False,
            "active_volume_mounted_by_drill": False,
            "restore_known_good": False,
            "formal_todo0012_claim": False,
            "secret_material_recorded": False,
        }
        restore_path = evidence / "restore-summary.json"
        restore_path.write_bytes(backup.canonical_json(restore))
        business = {
            "schema_version": 1,
            "business_validation_id": f"business-{backup_id}",
            "restore_id": restore_id,
            "backup_id": backup_id,
            "deployment": deployment,
            "elapsed_seconds": 45.0,
            "rto_budget_seconds": 300.0,
            "rto_pass": True,
            "overall_pass": True,
            "status": "passed",
            "cleanup_failures": [],
            "deployment_record_sha256": "8" * 64,
            "release_manifest_sha256": "9" * 64,
            "release_sdk_full_flow_sha256": "a" * 64,
            "restore_summary_sha256": backup.sha256_file(restore_path),
            "active_volume": "boost-gateway-production-redis-data",
            "active_volume_identity_after_sha256": active_identity,
            "active_volume_unchanged": True,
            "retained_volume": target_volume,
            "retained_volume_identity_sha256": target_identity,
            "retained_volume_identity_after_sha256": target_identity,
            "retained_volume_mounted_readonly": True,
            "retained_seed_before_sha256": restored_seed,
            "retained_seed_after_sha256": restored_seed,
            "retained_seed_unchanged": True,
            "leaderboard_submit": True,
            "leaderboard_top": True,
            "leaderboard_rank": True,
            "sdk_full_flow_checked": True,
            "source_build_performed": False,
            "public_conan_access_performed": False,
            "work_seed_mutated_by_business_checks": True,
            "work_volume_created": True,
            "work_volume_snapshot_sha256": "b" * 64,
            "work_volume_removed": True,
            "isolated_network_created": True,
            "isolated_network_internal": True,
            "isolated_network_removed": True,
            "gateway_runtime_binding_verified": True,
            "restore_volume_identity_binding_verified": True,
            "restore_snapshot_binding_verified": True,
            "restore_redis_image_binding_verified": True,
            "redis_image": "sha256:" + "c" * 64,
            "production_switched": False,
            "active_volume_identity_sha256": active_identity,
            "restore_known_good": False,
            "formal_todo0012_claim": False,
            "secret_material_recorded": False,
        }
        business_path = evidence / "business-summary.json"
        business_path.write_bytes(backup.canonical_json(business))
        return {
            "manifest": manifest_path,
            "receipt": receipt_path,
            "validation": validation_path,
            "restore": restore_path,
            "business": business_path,
        }

    def _attest(self, backup_id: str, paths: dict[str, Path]) -> dict[str, object]:
        return backup.create_known_good_attestation(
            self.vault,
            backup_id=backup_id,
            vault_validation_summary=paths["validation"],
            restore_summary=paths["restore"],
            business_summary=paths["business"],
            attested_at="2026-07-27T00:00:00Z",
            trusted_owner_uid=self.trusted_uid,
        )

    def test_attestation_is_create_only_and_binds_complete_evidence(self) -> None:
        paths = self._create_backup("backup-one", "2026-07-27T00:00:00Z")
        result = self._attest("backup-one", paths)

        directory = self.vault / "known-good" / "backup-one"
        self.assertEqual(
            {
                "attestation.json",
                "vault-validation.json",
                "restore-summary.json",
                "business-summary.json",
            },
            {path.name for path in directory.iterdir()},
        )
        self.assertTrue(result["restore_known_good"])
        self.assertFalse(result["formal_todo0012_claim"])
        self.assertFalse(result["secret_material_recorded"])
        self.assertEqual(
            backup.sha256_file(paths["manifest"]),
            result["evidence"]["backup_manifest"]["sha256"],
        )
        self.assertEqual(
            backup.sha256_file(paths["business"]),
            result["evidence"]["recovery_aggregate"]["sha256"],
        )
        loaded = backup.load_known_good_attestation(self.vault, "backup-one")
        self.assertRegex(loaded["attestation_sha256"], r"^[0-9a-f]{64}$")
        with self.assertRaisesRegex(backup.BackupError, "already exists"):
            self._attest("backup-one", paths)

    def test_rejects_drift_or_formal_source_without_partial_attestation(self) -> None:
        paths = self._create_backup("backup-bad", "2026-07-27T00:00:00Z")
        business = json.loads(paths["business"].read_text())
        business["restore_summary_sha256"] = "f" * 64
        business["formal_todo0012_claim"] = True
        paths["business"].write_bytes(backup.canonical_json(business))

        with self.assertRaisesRegex(backup.BackupError, "business summary"):
            self._attest("backup-bad", paths)
        self.assertFalse((self.vault / "known-good" / "backup-bad").exists())

    def test_rejects_post_business_volume_identity_drift(self) -> None:
        paths = self._create_backup("backup-drift", "2026-07-27T00:00:00Z")
        business = json.loads(paths["business"].read_text())
        business["retained_volume_identity_after_sha256"] = "f" * 64
        paths["business"].write_bytes(backup.canonical_json(business))

        with self.assertRaisesRegex(backup.BackupError, "business summary"):
            self._attest("backup-drift", paths)
        self.assertFalse((self.vault / "known-good" / "backup-drift").exists())

    def test_prune_fails_closed_with_fewer_than_two_valid_attestations(self) -> None:
        one = self._create_backup("backup-one", "2026-07-27T00:00:00Z")
        two = self._create_backup("backup-two", "2026-07-27T01:00:00Z")
        self._attest("backup-one", one)

        with self.assertRaisesRegex(backup.BackupError, "2 valid known-good"):
            backup.prune_remote(
                self.vault,
                anchor_backup_id="backup-two",
                anchor_receipt_sha256=backup.sha256_file(two["receipt"]),
                daily_copies=1,
                weekly_copies=1,
                minimum_known_good=2,
            )
        self.assertTrue((self.vault / "backups" / "backup-one").is_dir())
        self.assertTrue((self.vault / "backups" / "backup-two").is_dir())
        self.assertEqual([], list((self.vault / "deletions").iterdir()))

    def test_attestation_capacity_preflight_is_fail_closed(self) -> None:
        paths = self._create_backup("backup-capacity", "2026-07-27T00:00:00Z")

        with mock.patch.object(
            recovery,
            "logical_regular_file_bytes",
            return_value=recovery.DEFAULT_MAX_VAULT_BYTES,
        ), mock.patch.object(
            recovery.shutil,
            "disk_usage",
            return_value=SimpleNamespace(
                free=recovery.DEFAULT_MINIMUM_FREE_BYTES + 1_000_000
            ),
        ), self.assertRaisesRegex(
            backup.BackupError, "configured vault limit"
        ):
            self._attest("backup-capacity", paths)

        self.assertFalse((self.vault / "known-good" / "backup-capacity").exists())

    def test_attestation_free_space_reservation_is_fail_closed(self) -> None:
        paths = self._create_backup("backup-free", "2026-07-27T00:00:00Z")

        with mock.patch.object(
            recovery, "logical_regular_file_bytes", return_value=0
        ), mock.patch.object(
            recovery.shutil,
            "disk_usage",
            return_value=SimpleNamespace(free=recovery.DEFAULT_MINIMUM_FREE_BYTES),
        ), self.assertRaisesRegex(
            backup.BackupError, "free-space floor"
        ):
            self._attest("backup-free", paths)

        self.assertFalse((self.vault / "known-good" / "backup-free").exists())

    def test_attestation_postcheck_rolls_back_only_its_target(self) -> None:
        paths = self._create_backup("backup-postcheck", "2026-07-27T00:00:00Z")
        sentinel = self.vault / "known-good" / "existing-attestation"
        sentinel.mkdir(mode=0o700)
        sentinel_file = sentinel / "sentinel"
        sentinel_file.write_text("preserve", encoding="utf-8")

        with mock.patch.object(
            recovery,
            "logical_regular_file_bytes",
            side_effect=[0, recovery.DEFAULT_MAX_VAULT_BYTES + 1],
        ), mock.patch.object(
            recovery.shutil,
            "disk_usage",
            return_value=SimpleNamespace(
                free=recovery.DEFAULT_MINIMUM_FREE_BYTES + 1_000_000
            ),
        ), self.assertRaisesRegex(
            backup.BackupError, "configured vault limit"
        ):
            self._attest("backup-postcheck", paths)

        self.assertFalse((self.vault / "known-good" / "backup-postcheck").exists())
        self.assertEqual("preserve", sentinel_file.read_text(encoding="utf-8"))

    def test_attestation_rejects_source_growth_after_reservation(self) -> None:
        paths = self._create_backup("backup-growth", "2026-07-27T00:00:00Z")
        original_snapshot = recovery._known_good_capacity_snapshot
        mutated = False

        def mutate_after_preflight(*args: object, **kwargs: object) -> dict[str, int]:
            nonlocal mutated
            result = original_snapshot(*args, **kwargs)
            if kwargs.get("phase") == "preflight":
                with paths["validation"].open("ab") as stream:
                    stream.write(b"unexpected-growth")
                mutated = True
            return result

        with mock.patch.object(
            recovery,
            "_known_good_capacity_snapshot",
            side_effect=mutate_after_preflight,
        ), self.assertRaisesRegex(backup.BackupError, "capacity reservation"):
            self._attest("backup-growth", paths)

        self.assertTrue(mutated)
        self.assertFalse((self.vault / "known-good" / "backup-growth").exists())

    def test_attestation_uses_the_fixed_vault_local_lock(self) -> None:
        paths = self._create_backup("backup-locked", "2026-07-27T00:00:00Z")
        finished = threading.Event()
        failures: list[BaseException] = []

        def attest() -> None:
            try:
                self._attest("backup-locked", paths)
            except BaseException as exc:  # pragma: no cover - asserted below
                failures.append(exc)
            finally:
                finished.set()

        with recovery.vault_local_lock(
            self.vault, self.lock, trusted_owner_uid=self.trusted_uid
        ):
            thread = threading.Thread(target=attest, daemon=True)
            thread.start()
            self.assertFalse(finished.wait(0.1))
            self.assertFalse((self.vault / "known-good" / "backup-locked").exists())
        self.assertTrue(finished.wait(2.0))
        thread.join(timeout=2.0)
        self.assertEqual([], failures)
        self.assertTrue((self.vault / "known-good" / "backup-locked").is_dir())

    def test_attestation_preserves_legacy_vault_without_lock(self) -> None:
        self.lock.unlink()
        self.identity.write_bytes(b"independent-vault-host-identity")
        paths = self._create_backup("backup-legacy", "2026-07-27T00:00:00Z")

        with mock.patch.object(
            recovery,
            "_known_good_capacity_snapshot",
            side_effect=AssertionError("legacy attestation used secure capacity gate"),
        ) as capacity_snapshot:
            result = self._attest("backup-legacy", paths)

        capacity_snapshot.assert_not_called()
        self.assertTrue(result["restore_known_good"])
        self.assertTrue((self.vault / "known-good" / "backup-legacy").is_dir())

    def test_attestation_dangling_lock_symlink_cannot_downgrade(self) -> None:
        paths = self._create_backup("backup-symlink", "2026-07-27T00:00:00Z")
        self.lock.unlink()
        self.lock.symlink_to(self.root / "missing-lock-target")

        with mock.patch.object(
            recovery, "_create_known_good_attestation"
        ) as create_attestation, self.assertRaises(OSError):
            self._attest("backup-symlink", paths)

        create_attestation.assert_not_called()
        self.assertFalse((self.vault / "known-good" / "backup-symlink").exists())

    def test_attestation_unsafe_lock_entry_cannot_downgrade(self) -> None:
        paths = self._create_backup("backup-unsafe-lock", "2026-07-27T00:00:00Z")
        self.lock.unlink()
        self.lock.mkdir(mode=0o700)

        with mock.patch.object(
            recovery, "_create_known_good_attestation"
        ) as create_attestation, self.assertRaisesRegex(
            backup.BackupError, "regular non-symlink"
        ):
            self._attest("backup-unsafe-lock", paths)

        create_attestation.assert_not_called()
        self.assertFalse((self.vault / "known-good" / "backup-unsafe-lock").exists())

    def test_attestation_cli_has_no_capacity_or_lock_override(self) -> None:
        required = [
            "attest-known-good",
            "--vault-root",
            str(self.vault),
            "--backup-id",
            "backup-one",
            "--vault-validation-summary",
            "validation.json",
            "--restore-summary",
            "restore.json",
            "--business-summary",
            "business.json",
        ]
        for forbidden in ("--max-vault-bytes", "--minimum-free-bytes", "--lock-file"):
            with self.subTest(forbidden=forbidden), self.assertRaises(SystemExit):
                backup.build_parser().parse_args([*required, forbidden, "1"])

    def test_prune_counts_only_valid_attestations_and_preserves_them(self) -> None:
        self._create_backup("backup-old", "2026-07-26T00:00:00Z")
        one = self._create_backup("backup-one", "2026-07-27T00:00:00Z")
        two = self._create_backup("backup-two", "2026-07-27T01:00:00Z", ["weekly"])
        newest = self._create_backup("backup-new", "2026-07-27T02:00:00Z")
        first = self._attest("backup-one", one)
        second = self._attest("backup-two", two)

        result = backup.prune_remote(
            self.vault,
            anchor_backup_id="backup-new",
            anchor_receipt_sha256=backup.sha256_file(newest["receipt"]),
            daily_copies=1,
            weekly_copies=1,
            minimum_known_good=2,
            deletion_id="prune-known-good",
        )

        self.assertEqual(["backup-old"], result["deleted_backup_ids"])
        self.assertEqual(
            ["backup-one", "backup-two"],
            result["retained_known_good_backup_ids"],
        )
        self.assertEqual(
            {
                "backup-one": backup.sha256_file(
                    self.vault / "known-good/backup-one/attestation.json"
                ),
                "backup-two": backup.sha256_file(
                    self.vault / "known-good/backup-two/attestation.json"
                ),
            },
            result["known_good_attestation_sha256s"],
        )
        self.assertTrue(first["restore_known_good"])
        self.assertTrue(second["restore_known_good"])
        self.assertFalse((self.vault / "backups" / "backup-old").exists())
        for backup_id in ("backup-one", "backup-two", "backup-new"):
            self.assertTrue((self.vault / "backups" / backup_id).is_dir())

    def test_tampered_attestation_does_not_count_for_retention(self) -> None:
        one = self._create_backup("backup-one", "2026-07-27T00:00:00Z")
        two = self._create_backup("backup-two", "2026-07-27T01:00:00Z")
        self._attest("backup-one", one)
        self._attest("backup-two", two)
        attestation = self.vault / "known-good/backup-two/attestation.json"
        document = json.loads(attestation.read_text())
        document["restore_known_good"] = False
        attestation.write_bytes(backup.canonical_json(document))

        with self.assertRaisesRegex(backup.BackupError, "2 valid known-good"):
            backup.prune_remote(
                self.vault,
                anchor_backup_id="backup-two",
                anchor_receipt_sha256=backup.sha256_file(two["receipt"]),
                daily_copies=1,
                weekly_copies=1,
                minimum_known_good=2,
            )

    def test_same_restore_target_does_not_count_as_two_known_good_copies(self) -> None:
        one = self._create_backup("backup-one", "2026-07-27T00:00:00Z")
        two = self._create_backup("backup-two", "2026-07-27T01:00:00Z")
        first_restore = json.loads(one["restore"].read_text())
        second_restore = json.loads(two["restore"].read_text())
        second_restore["target_volume"] = first_restore["target_volume"]
        second_restore["target_volume_identity_sha256"] = first_restore[
            "target_volume_identity_sha256"
        ]
        two["restore"].write_bytes(backup.canonical_json(second_restore))
        second_business = json.loads(two["business"].read_text())
        second_business["restore_summary_sha256"] = backup.sha256_file(two["restore"])
        second_business["retained_volume"] = first_restore["target_volume"]
        second_business["retained_volume_identity_sha256"] = first_restore[
            "target_volume_identity_sha256"
        ]
        second_business["retained_volume_identity_after_sha256"] = first_restore[
            "target_volume_identity_sha256"
        ]
        two["business"].write_bytes(backup.canonical_json(second_business))
        self._attest("backup-one", one)
        self._attest("backup-two", two)

        with self.assertRaisesRegex(backup.BackupError, "2 valid known-good"):
            backup.prune_remote(
                self.vault,
                anchor_backup_id="backup-two",
                anchor_receipt_sha256=backup.sha256_file(two["receipt"]),
                daily_copies=1,
                weekly_copies=1,
                minimum_known_good=2,
            )


if __name__ == "__main__":
    unittest.main()
