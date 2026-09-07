from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.tools import backup_vault_ssh_receiver as receiver
from scripts.tools import manage_backup_recovery as manager
from scripts.lib import backup_recovery as backup


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class BackupRecoveryToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.vault = self.root / "vault"
        self.vault.mkdir(mode=0o700)
        self.lock = self.vault / ".vault.lock"
        self.identity = self.root / "vault-identity"
        self.identity.write_bytes(b"mac-vault-identity-material")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _artifacts(
        self,
        backup_id: str,
        *,
        created_at: str = "2026-07-26T00:00:00Z",
        classes: list[str] | None = None,
    ) -> tuple[Path, Path]:
        archive = self.root / f"{backup_id}.age"
        archive.write_bytes(b"age-encrypted:" + backup_id.encode("ascii"))
        manifest = self.root / f"{backup_id}.json"
        manifest.write_bytes(
            backup.canonical_json(
                {
                    "schema_version": 2,
                    "backup_id": backup_id,
                    "created_at": created_at,
                    "archive": {
                        "sha256": backup.sha256_file(archive),
                        "size_bytes": archive.stat().st_size,
                    },
                    "backup_policy_sha256": "a" * 64,
                    "sources": [
                        {"id": "redis_snapshot", "archive_path": "redis/dump.rdb"}
                    ],
                    "source_links": [],
                    "archive_contract": {
                        "format": "link_free_tar_v1",
                        "symbolic_link_entries": 0,
                        "hard_link_entries": 0,
                        "symbolic_links_recorded": 0,
                    },
                    "retention_classes": classes or ["daily"],
                    "secret_material_recorded": False,
                }
            )
        )
        return archive, manifest

    def _store(
        self,
        backup_id: str,
        *,
        created_at: str = "2026-07-26T00:00:00Z",
        classes: list[str] | None = None,
    ) -> dict[str, object]:
        archive, manifest = self._artifacts(
            backup_id, created_at=created_at, classes=classes
        )
        framed = self._framed(backup_id, archive, manifest)
        return self._remote_store(
            self.vault,
            self.identity,
            io.BytesIO(framed),
            recorded_at=created_at,
        )

    def _framed(self, backup_id: str, archive: Path, manifest: Path) -> bytes:
        stream = io.BytesIO()
        backup.write_upload_frame(stream, backup_id, archive, manifest)
        return stream.getvalue()

    def _remote_store(self, *args: object, **kwargs: object) -> dict[str, object]:
        kwargs.setdefault("trusted_lock_owner_uid", os.getuid())
        return backup.remote_store(*args, **kwargs)

    def _enable_secure_vault_layout(self) -> None:
        self.vault.chmod(0o750)
        for name in ("backups", ".incoming", "known-good", "deletions", ".trash"):
            directory = self.vault / name
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
        self.identity = self.vault / ".vault-identity"
        self.identity.write_bytes(b"v" * 32)
        self.identity.chmod(0o640)
        self.lock.touch(mode=0o660)
        self.lock.chmod(0o660)

    def test_remote_store_is_create_only_and_readback_bound(self) -> None:
        archive, manifest = self._artifacts("backup-one")
        framed = self._framed("backup-one", archive, manifest)

        receipt = self._remote_store(self.vault, self.identity, io.BytesIO(framed))

        stored = self.vault / "backups/backup-one"
        self.assertEqual(backup.sha256_file(archive), receipt["archive_sha256"])
        self.assertEqual(backup.sha256_file(manifest), receipt["manifest_sha256"])
        self.assertTrue(receipt["remote_readback_sha256"])
        self.assertTrue(receipt["create_only"])
        self.assertEqual(
            archive.read_bytes(), (stored / "payload.tar.age").read_bytes()
        )
        self.assertEqual(receipt, json.loads((stored / "receipt.json").read_text()))
        with self.assertRaisesRegex(backup.BackupError, "already exists"):
            self._remote_store(self.vault, self.identity, io.BytesIO(framed))

    def test_remote_store_rejects_digest_drift_and_trailing_bytes(self) -> None:
        archive, manifest = self._artifacts("backup-drift")
        framed = bytearray(self._framed("backup-drift", archive, manifest))
        framed[-1] ^= 1
        with self.assertRaisesRegex(backup.BackupError, "manifest readback digest"):
            self._remote_store(self.vault, self.identity, io.BytesIO(framed))
        self.assertFalse((self.vault / "backups/backup-drift").exists())

        archive, manifest = self._artifacts("backup-trailing")
        with self.assertRaisesRegex(backup.BackupError, "trailing bytes"):
            self._remote_store(
                self.vault,
                self.identity,
                io.BytesIO(self._framed("backup-trailing", archive, manifest) + b"x"),
            )

    def test_legacy_remote_store_does_not_apply_secure_capacity_gate(self) -> None:
        archive, manifest = self._artifacts("backup-legacy-capacity")
        receipt = self._remote_store(
            self.vault,
            self.identity,
            io.BytesIO(self._framed("backup-legacy-capacity", archive, manifest)),
            size_reader=mock.Mock(
                side_effect=AssertionError("legacy vault used secure size gate")
            ),
            disk_usage_reader=mock.Mock(
                side_effect=AssertionError("legacy vault used secure free-space gate")
            ),
        )

        self.assertEqual("backup-legacy-capacity", receipt["backup_id"])

    def test_remote_store_dangling_fixed_lock_never_falls_back(self) -> None:
        archive, manifest = self._artifacts("backup-unsafe-lock")
        self.lock.symlink_to(self.root / "missing-lock-target")

        with (
            mock.patch.object(backup, "_remote_store_locked") as store,
            self.assertRaises(OSError),
        ):
            self._remote_store(
                self.vault,
                self.identity,
                io.BytesIO(self._framed("backup-unsafe-lock", archive, manifest)),
            )

        store.assert_not_called()

    def test_remote_store_unsafe_fixed_lock_entry_never_falls_back(self) -> None:
        archive, manifest = self._artifacts("backup-lock-directory")
        self.lock.mkdir(mode=0o700)

        with (
            mock.patch.object(backup, "_remote_store_locked") as store,
            self.assertRaisesRegex(backup.BackupError, "regular non-symlink"),
        ):
            self._remote_store(
                self.vault,
                self.identity,
                io.BytesIO(self._framed("backup-lock-directory", archive, manifest)),
            )

        store.assert_not_called()

    def test_secure_remote_store_cli_uses_fixed_vault_lock(self) -> None:
        self._enable_secure_vault_layout()
        archive, manifest = self._artifacts("backup-cli-lock")

        class Input:
            buffer = io.BytesIO(self._framed("backup-cli-lock", archive, manifest))

        stderr = io.StringIO()
        stdout = io.StringIO()
        original_lock = backup.vault_local_lock
        original_layout = backup.require_secure_vault_layout

        def test_lock(
            vault_root: Path, lock_path: Path, *, trusted_owner_uid: int
        ) -> object:
            self.assertEqual(0, trusted_owner_uid)
            return original_lock(vault_root, lock_path, trusted_owner_uid=os.getuid())

        def test_layout(
            vault_root: Path,
            identity_file: Path,
            lock_path: Path,
            *,
            trusted_owner_uid: int,
        ) -> object:
            self.assertEqual(0, trusted_owner_uid)
            return original_layout(
                vault_root,
                identity_file,
                lock_path,
                trusted_owner_uid=os.getuid(),
            )

        with (
            mock.patch.object(manager.sys, "stdin", Input()),
            mock.patch.object(manager.sys, "stderr", stderr),
            mock.patch.object(manager.sys, "stdout", stdout),
            mock.patch.object(
                backup, "vault_local_lock", side_effect=test_lock
            ) as lock_mock,
            mock.patch.object(
                backup, "require_secure_vault_layout", side_effect=test_layout
            ),
        ):
            result = manager.main(
                [
                    "remote-store",
                    "--vault-root",
                    str(self.vault),
                    "--vault-identity-file",
                    str(self.identity),
                ]
            )
        self.assertEqual(0, result, stderr.getvalue())
        resolved_vault = self.vault.resolve()
        lock_mock.assert_called_once_with(
            resolved_vault,
            resolved_vault / ".vault.lock",
            trusted_owner_uid=0,
        )
        self.assertTrue((self.vault / "backups/backup-cli-lock").is_dir())

    def test_remote_store_reserves_candidate_before_reading_payload(self) -> None:
        self._enable_secure_vault_layout()
        archive, manifest = self._artifacts("backup-capacity")
        framed = self._framed("backup-capacity", archive, manifest)
        header_size = backup.FRAME.unpack(framed[: backup.FRAME.size])[0]
        header_end = backup.FRAME.size + header_size
        stream = io.BytesIO(framed)
        candidate = (
            archive.stat().st_size
            + manifest.stat().st_size
            + backup.REMOTE_RECEIPT_RESERVATION_BYTES
        )
        with self.assertRaisesRegex(backup.BackupError, "reservation exceeds"):
            self._remote_store(
                self.vault,
                self.identity,
                stream,
                size_reader=lambda _root: 20_000_000_001 - candidate,
                disk_usage_reader=lambda _root: SimpleNamespace(free=30_000_000_000),
            )
        self.assertEqual(header_end, stream.tell())
        self.assertFalse((self.vault / "backups/backup-capacity").exists())

    def test_remote_store_records_completion_time_and_checks_actual_free_space(
        self,
    ) -> None:
        self._enable_secure_vault_layout()
        archive, manifest = self._artifacts("backup-completion-time")
        framed = self._framed("backup-completion-time", archive, manifest)
        stream = io.BytesIO(framed)

        def completion_time() -> str:
            self.assertEqual(len(framed), stream.tell())
            return "2026-09-07T04:01:02Z"

        with mock.patch.object(backup, "now", side_effect=completion_time):
            receipt = self._remote_store(
                self.vault,
                self.identity,
                stream,
                disk_usage_reader=mock.Mock(
                    side_effect=[
                        SimpleNamespace(free=30_000_000_000),
                        SimpleNamespace(free=6_000_000_000),
                    ]
                ),
            )
        self.assertEqual("2026-09-07T04:01:02Z", receipt["stored_at"])

        archive, manifest = self._artifacts("backup-low-free-after-write")
        with self.assertRaisesRegex(backup.BackupError, "free-space floor"):
            self._remote_store(
                self.vault,
                self.identity,
                io.BytesIO(
                    self._framed("backup-low-free-after-write", archive, manifest)
                ),
                disk_usage_reader=mock.Mock(
                    side_effect=[
                        SimpleNamespace(free=30_000_000_000),
                        SimpleNamespace(free=4_999_999_999),
                    ]
                ),
            )
        self.assertFalse((self.vault / "backups/backup-low-free-after-write").exists())

    def test_receipt_rejects_same_or_unattested_host(self) -> None:
        archive, manifest = self._artifacts("backup-identity")
        receipt = self._store("backup-identity")
        remote_id = backup.vault_host_id(self.identity)
        with self.assertRaisesRegex(backup.BackupError, "not distinct"):
            backup.verify_remote_receipt(
                receipt,
                backup_id="backup-identity",
                archive=archive,
                manifest=manifest,
                source_host_id_sha256=remote_id,
                expected_remote_host_id_sha256=remote_id,
            )
        with self.assertRaisesRegex(backup.BackupError, "vault_host_id_sha256"):
            backup.verify_remote_receipt(
                receipt,
                backup_id="backup-identity",
                archive=archive,
                manifest=manifest,
                source_host_id_sha256="1" * 64,
                expected_remote_host_id_sha256="2" * 64,
            )

    def test_retention_requires_verified_anchor_and_preserves_policy_sets(self) -> None:
        for index in range(10):
            classes = ["daily", "weekly"] if index % 3 == 0 else ["daily"]
            self._store(
                f"backup-{index:02d}",
                created_at=f"2026-07-{index + 1:02d}T00:00:00Z",
                classes=classes,
            )
        anchor = self.vault / "backups/backup-09/receipt.json"
        with self.assertRaisesRegex(backup.BackupError, "not a verified"):
            backup.prune_remote(
                self.vault,
                anchor_backup_id="backup-09",
                anchor_receipt_sha256="0" * 64,
                daily_copies=3,
                weekly_copies=2,
                minimum_known_good=2,
                expected_vault_host_id_sha256=backup.vault_host_id(self.identity),
            )

        with mock.patch.object(
            backup,
            "load_known_good_attestation",
            side_effect=lambda _root, backup_id: {
                "attestation_sha256": digest(backup_id.encode("ascii")),
                "restore_id": f"restore-{backup_id}",
                "identities": {
                    "target_volume_identity_sha256": digest(
                        f"target-{backup_id}".encode("ascii")
                    )
                },
            },
        ):
            record = backup.prune_remote(
                self.vault,
                anchor_backup_id="backup-09",
                anchor_receipt_sha256=backup.sha256_file(anchor),
                daily_copies=3,
                weekly_copies=2,
                minimum_known_good=2,
                deletion_id="prune-test",
                expected_vault_host_id_sha256=backup.vault_host_id(self.identity),
            )
        self.assertEqual(
            {"backup-09", "backup-08", "backup-07", "backup-06"},
            set(record["retained_backup_ids"]),
        )
        self.assertEqual(6, len(record["deleted_backup_ids"]))
        self.assertTrue(record["delete_only_after_verified_remote_copy"])
        self.assertTrue((self.vault / "deletions/prune-test.json").is_file())
        self.assertTrue((self.vault / "deletions/prune-test.intent.json").is_file())
        self.assertFalse((self.vault / ".trash/prune-test").exists())

    def test_secure_vault_compatibility_cli_cannot_bypass_retention_policy(
        self,
    ) -> None:
        self._enable_secure_vault_layout()
        pruner = mock.Mock()
        stderr = io.StringIO()
        stdout = io.StringIO()

        with (
            mock.patch.object(manager, "prune_remote", pruner),
            mock.patch.object(manager.sys, "stderr", stderr),
            mock.patch.object(manager.sys, "stdout", stdout),
        ):
            result = manager.main(
                [
                    "remote-prune",
                    "--vault-root",
                    str(self.vault),
                    "--anchor-backup-id",
                    "backup-anchor",
                    "--anchor-receipt-sha256",
                    "a" * 64,
                    "--daily-copies",
                    "1",
                    "--weekly-copies",
                    "1",
                ]
            )

        self.assertEqual(1, result)
        self.assertIn("policy-bound retention service", stderr.getvalue())
        self.assertEqual("", stdout.getvalue())
        pruner.assert_not_called()

    def test_legacy_vault_compatibility_cli_retains_local_prune(self) -> None:
        pruner = mock.Mock(return_value={"state": "deleted"})
        stderr = io.StringIO()
        stdout = io.StringIO()

        with (
            mock.patch.object(manager, "prune_remote", pruner),
            mock.patch.object(manager.sys, "stderr", stderr),
            mock.patch.object(manager.sys, "stdout", stdout),
        ):
            result = manager.main(
                [
                    "remote-prune",
                    "--vault-root",
                    str(self.vault),
                    "--anchor-backup-id",
                    "backup-anchor",
                    "--anchor-receipt-sha256",
                    "a" * 64,
                    "--daily-copies",
                    "14",
                    "--weekly-copies",
                    "8",
                ]
            )

        self.assertEqual(0, result, stderr.getvalue())
        self.assertIn('"state": "deleted"', stdout.getvalue())
        pruner.assert_called_once_with(
            self.vault.resolve(),
            anchor_backup_id="backup-anchor",
            anchor_receipt_sha256="a" * 64,
            daily_copies=14,
            weekly_copies=8,
            minimum_known_good=2,
        )

    def test_real_seven_four_two_plan_ignores_mixed_vault_identity(self) -> None:
        for index in range(10):
            classes = ["daily", "weekly"] if index % 3 == 0 else ["daily"]
            self._store(
                f"current-{index:02d}",
                created_at=f"2026-08-{index + 1:02d}T00:00:00Z",
                classes=classes,
            )
        old_identity = self.root / "old-vault-identity"
        old_identity.write_bytes(b"old-vault-identity-material")
        archive, manifest = self._artifacts(
            "mixed-old", created_at="2026-07-01T00:00:00Z"
        )
        self._remote_store(
            self.vault,
            old_identity,
            io.BytesIO(self._framed("mixed-old", archive, manifest)),
            recorded_at="2026-07-01T00:00:00Z",
        )
        current_identity = backup.vault_host_id(self.identity)
        records = backup.verified_vault_records(
            self.vault, expected_vault_host_id_sha256=current_identity
        )
        self.assertNotIn("mixed-old", {item["backup_id"] for item in records})
        newest = self.vault / "backups/current-09/receipt.json"

        with mock.patch.object(
            backup,
            "load_known_good_attestation",
            side_effect=lambda _root, backup_id: {
                "attestation_sha256": digest(backup_id.encode("ascii")),
                "restore_id": f"restore-{backup_id}",
                "identities": {
                    "target_volume_identity_sha256": digest(
                        f"target-{backup_id}".encode("ascii")
                    )
                },
            },
        ):
            plan = backup.plan_remote_retention(
                self.vault,
                anchor_backup_id="current-09",
                anchor_receipt_sha256=backup.sha256_file(newest),
                daily_copies=7,
                weekly_copies=4,
                minimum_known_good=2,
                expected_vault_host_id_sha256=current_identity,
            )
            result = backup.prune_remote(
                self.vault,
                anchor_backup_id="current-09",
                anchor_receipt_sha256=backup.sha256_file(newest),
                daily_copies=7,
                weekly_copies=4,
                minimum_known_good=2,
                deletion_id="prune-seven-four-two",
                expected_vault_host_id_sha256=current_identity,
            )

        self.assertEqual(["current-01", "current-02"], plan["deleted_backup_ids"])
        self.assertEqual(plan["deleted_backup_ids"], result["deleted_backup_ids"])
        self.assertTrue((self.vault / "backups/mixed-old").is_dir())
        self.assertNotIn("mixed-old", result["retained_backup_ids"])

    def test_retention_failure_keeps_truthful_quarantine_without_completion(
        self,
    ) -> None:
        for index in range(4):
            self._store(
                f"failure-{index}",
                created_at=f"2026-07-{index + 1:02d}T00:00:00Z",
            )
        anchor = self.vault / "backups/failure-3/receipt.json"
        original_rmtree = shutil.rmtree

        def fail_trash(path: object, *args: object, **kwargs: object) -> None:
            if Path(path).name == "prune-failure":
                raise OSError("injected removal failure")
            original_rmtree(path, *args, **kwargs)

        with (
            mock.patch.object(shutil, "rmtree", side_effect=fail_trash),
            mock.patch.object(
                backup,
                "load_known_good_attestation",
                side_effect=lambda _root, backup_id: {
                    "attestation_sha256": digest(backup_id.encode("ascii")),
                    "restore_id": f"restore-{backup_id}",
                    "identities": {
                        "target_volume_identity_sha256": digest(
                            f"target-{backup_id}".encode("ascii")
                        )
                    },
                },
            ),
        ):
            with self.assertRaisesRegex(backup.BackupError, "quarantine remains"):
                backup.prune_remote(
                    self.vault,
                    anchor_backup_id="failure-3",
                    anchor_receipt_sha256=backup.sha256_file(anchor),
                    daily_copies=1,
                    weekly_copies=1,
                    minimum_known_good=2,
                    deletion_id="prune-failure",
                    expected_vault_host_id_sha256=backup.vault_host_id(self.identity),
                )
        self.assertTrue((self.vault / "deletions/prune-failure.intent.json").is_file())
        self.assertFalse((self.vault / "deletions/prune-failure.json").exists())
        self.assertTrue((self.vault / ".trash/prune-failure").is_dir())

    def test_remote_store_rejects_invalid_retention_timestamp(self) -> None:
        archive, manifest = self._artifacts(
            "backup-bad-time", created_at="not-a-timestamp"
        )
        with self.assertRaisesRegex(backup.BackupError, "created_at"):
            self._remote_store(
                self.vault,
                self.identity,
                io.BytesIO(self._framed("backup-bad-time", archive, manifest)),
            )

    def test_remote_store_rejects_unsafe_link_metadata(self) -> None:
        archive, manifest = self._artifacts("backup-unsafe-link")
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["source_links"] = [
            {
                "archive_path": "sources/configuration/current",
                "original_link_text": "/etc/passwd",
                "target_source_id": "redis_snapshot",
                "target_relative_path": "../../etc/passwd",
                "target_type": "file",
            }
        ]
        value["archive_contract"]["symbolic_links_recorded"] = 1
        manifest.write_bytes(backup.canonical_json(value))

        with self.assertRaisesRegex(backup.BackupError, "link metadata"):
            self._remote_store(
                self.vault,
                self.identity,
                io.BytesIO(self._framed("backup-unsafe-link", archive, manifest)),
            )

    def test_remote_store_rejects_symlinked_internal_vault_root(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (self.vault / "backups").symlink_to(outside, target_is_directory=True)
        archive, manifest = self._artifacts("backup-symlink")
        with self.assertRaisesRegex(
            backup.BackupError, "unsafe directory|non-symlink directory"
        ):
            self._remote_store(
                self.vault,
                self.identity,
                io.BytesIO(self._framed("backup-symlink", archive, manifest)),
            )
        self.assertEqual([], list(outside.iterdir()))

    def test_forced_receiver_only_allows_store_and_receipt(self) -> None:
        self.assertEqual(
            ("store", []),
            receiver.parse_original_command("boost-gateway-vault store"),
        )
        self.assertEqual(
            ("receipt", ["backup-01"]),
            receiver.parse_original_command("boost-gateway-vault receipt backup-01"),
        )
        rejected = (
            "",
            "bash -c id",
            "boost-gateway-vault store extra",
            "boost-gateway-vault receipt ../secret",
            "boost-gateway-vault prune backup-01 " + "0" * 64 + " 14 8 2",
        )
        for command in rejected:
            with self.subTest(command=command):
                with self.assertRaises(backup.BackupError):
                    receiver.parse_original_command(command)

    def test_forced_receiver_bounds_store_lifetime(self) -> None:
        with (
            mock.patch.object(receiver.signal, "signal") as signal_mock,
            mock.patch.object(receiver.signal, "alarm") as alarm_mock,
        ):
            with receiver.bounded_store(90):
                pass

        alarm_mock.assert_has_calls([mock.call(90), mock.call(0)])
        self.assertEqual(2, signal_mock.call_count)
        with self.assertRaisesRegex(backup.BackupError, "timeout must be positive"):
            with receiver.bounded_store(0):
                pass

    def test_forced_receiver_runs_from_standalone_install_directory(self) -> None:
        install_root = self.root / "receiver-install"
        install_root.mkdir()
        tools_root = Path(__file__).resolve().parents[2] / "scripts/tools"
        shutil.copy2(
            Path(__file__).resolve().parents[2] / "scripts/lib/backup_recovery.py",
            install_root / "backup_recovery.py",
        )
        for name in ("manage_backup_recovery.py", "backup_vault_ssh_receiver.py"):
            shutil.copy2(tools_root / name, install_root / name)

        completed = subprocess.run(
            [
                sys.executable,
                str(install_root / "backup_vault_ssh_receiver.py"),
                "--help",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("--vault-root", completed.stdout)

    def test_legacy_forced_receiver_supports_store_and_receipt_without_lock(
        self,
    ) -> None:
        archive, manifest = self._artifacts("backup-legacy-receiver")

        class Input:
            def __init__(self, content: bytes) -> None:
                self.buffer = io.BytesIO(content)

        class Output:
            def __init__(self) -> None:
                self.buffer = io.BytesIO()

        store_output = Output()
        stderr = io.StringIO()
        with (
            mock.patch.dict(
                receiver.os.environ,
                {"SSH_ORIGINAL_COMMAND": "boost-gateway-vault store"},
            ),
            mock.patch.object(
                receiver.sys,
                "stdin",
                Input(self._framed("backup-legacy-receiver", archive, manifest)),
            ),
            mock.patch.object(receiver.sys, "stdout", store_output),
            mock.patch.object(receiver.sys, "stderr", stderr),
        ):
            result = receiver.main(
                [
                    "--vault-root",
                    str(self.vault),
                    "--vault-identity-file",
                    str(self.identity),
                ]
            )
        self.assertEqual(0, result, stderr.getvalue())
        stored_receipt = json.loads(store_output.buffer.getvalue())
        self.assertEqual("backup-legacy-receiver", stored_receipt["backup_id"])

        receipt_output = Output()
        with (
            mock.patch.dict(
                receiver.os.environ,
                {
                    "SSH_ORIGINAL_COMMAND": (
                        "boost-gateway-vault receipt backup-legacy-receiver"
                    )
                },
            ),
            mock.patch.object(receiver.sys, "stdin", Input(b"")),
            mock.patch.object(receiver.sys, "stdout", receipt_output),
            mock.patch.object(receiver.sys, "stderr", stderr),
        ):
            result = receiver.main(
                [
                    "--vault-root",
                    str(self.vault),
                    "--vault-identity-file",
                    str(self.identity),
                ]
            )
        self.assertEqual(0, result, stderr.getvalue())
        self.assertEqual(stored_receipt, json.loads(receipt_output.buffer.getvalue()))

    def test_forced_receiver_has_no_lock_or_capacity_override(self) -> None:
        required = [
            "--vault-root",
            str(self.vault),
            "--vault-identity-file",
            str(self.identity),
        ]
        for forbidden in ("--lock-file", "--max-vault-bytes", "--minimum-free-bytes"):
            with self.subTest(forbidden=forbidden), self.assertRaises(SystemExit):
                receiver.build_parser().parse_args([*required, forbidden, "1"])

    def test_upload_uses_only_fixed_forced_command_surface(self) -> None:
        archive, manifest = self._artifacts("backup-upload")
        remote_id = backup.vault_host_id(self.identity)
        ssh_identity = self.root / "ssh-identity"
        ssh_identity.write_text("test private key\n", encoding="ascii")
        known_hosts = self.root / "known-hosts"
        known_hosts.write_text("host ssh-ed25519 test\n", encoding="ascii")
        expected_receipt = {
            "schema_version": 1,
            "backup_id": "backup-upload",
            "archive_sha256": backup.sha256_file(archive),
            "archive_size": archive.stat().st_size,
            "manifest_sha256": backup.sha256_file(manifest),
            "manifest_size": manifest.stat().st_size,
            "vault_host_id_sha256": remote_id,
            "remote_readback_sha256": True,
            "create_only": True,
            "secret_material_recorded": False,
        }

        def runner(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(
                [
                    "/usr/bin/ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=yes",
                    "-o",
                    "ClearAllForwardings=yes",
                    "-o",
                    "IdentitiesOnly=yes",
                    "-o",
                    "ServerAliveInterval=30",
                    "-o",
                    "ServerAliveCountMax=3",
                    "-o",
                    f"IdentityFile={ssh_identity.resolve()}",
                    "-o",
                    f"UserKnownHostsFile={known_hosts.resolve()}",
                    "--",
                    "vault@100.64.0.2",
                    "boost-gateway-vault store",
                ],
                command,
            )
            framed = kwargs["stdin"]
            self.assertTrue(hasattr(framed, "read"))
            self.assertEqual(backup.FRAME.size, len(framed.read(backup.FRAME.size)))
            return subprocess.CompletedProcess(
                command, 0, backup.canonical_json(expected_receipt), b""
            )

        with mock.patch.object(
            Path, "read_bytes", side_effect=AssertionError("unbounded read")
        ):
            receipt = backup.upload_remote(
                backup_id="backup-upload",
                archive=archive,
                manifest=manifest,
                remote_host="vault@100.64.0.2",
                remote_command="boost-gateway-vault store",
                ssh="/usr/bin/ssh",
                ssh_identity_file=ssh_identity,
                ssh_known_hosts=known_hosts,
                source_host_id_sha256="1" * 64,
                expected_remote_host_id_sha256=remote_id,
                runner=runner,
            )
        self.assertEqual(expected_receipt, receipt)
        with self.assertRaisesRegex(backup.BackupError, "fixed vault receiver"):
            backup.upload_remote(
                backup_id="backup-upload",
                archive=archive,
                manifest=manifest,
                remote_host="vault@100.64.0.2",
                remote_command="rm -rf /",
                ssh="/usr/bin/ssh",
                ssh_identity_file=ssh_identity,
                ssh_known_hosts=known_hosts,
                source_host_id_sha256="1" * 64,
                expected_remote_host_id_sha256=remote_id,
                runner=runner,
            )

    def test_snapshot_uses_redis_rdb_stream_and_always_cleans_container_temp(
        self,
    ) -> None:
        destination = self.root / "dump.rdb"
        commands: list[list[str]] = []

        def runner(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            commands.append(command)
            if command[1] == "cp":
                Path(command[-1]).write_bytes(b"REDIS0011\xfa\x00\x00\xff")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        backup.stage_redis_snapshot(
            destination,
            container="boost-redis",
            docker="/usr/bin/docker",
            runner=runner,
        )
        self.assertIn("--rdb", commands[0])
        self.assertEqual("cp", commands[1][1])
        self.assertEqual("rm", commands[-1][3])

    def test_create_backup_binds_identity_and_removes_plaintext_staging(self) -> None:
        source = self.root / "configuration"
        source.mkdir()
        (source / "service.conf").write_text("secret=value\n", encoding="utf-8")
        policy = self.root / "policy.json"
        policy.write_text(
            json.dumps(
                {
                    "activation": {"state": "candidate_only"},
                    "backup": {
                        "source_contracts": [
                            {
                                "id": "redis_snapshot",
                                "kind": "generated_redis_snapshot",
                                "path": str(self.root / "unused-redis-path"),
                                "required": True,
                            },
                            {
                                "id": "host_configuration",
                                "kind": "directory",
                                "path": str(source),
                                "required": True,
                            },
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        profile = self.root / "redis.conf"
        profile.write_text("appendonly yes\n", encoding="ascii")
        recipient = self.root / "recipient.txt"
        recipient.write_text("age1testrecipient\n", encoding="ascii")
        deployment = self.root / "deployment.json"
        deployment.write_text(
            json.dumps(
                {
                    "deployment_id": "deployment-test",
                    "tag": "v3.6.2",
                    "commit": "a" * 40,
                    "runtime_asset_sha256": "b" * 64,
                    "host": {"host_id_sha256": "1" * 64},
                }
            ),
            encoding="utf-8",
        )
        staging = self.root / "staging"
        output = self.root / "encrypted"

        def runner(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            if command[0] == "/usr/bin/docker" and command[1] == "cp":
                Path(command[-1]).write_bytes(b"REDIS0011\xfa\x00\x00\xff")
            if command[0] == "/usr/bin/age":
                encrypted = Path(command[command.index("--output") + 1])
                plaintext = Path(command[-1])
                encrypted.write_bytes(b"AGE-TEST:" + plaintext.read_bytes())
            return subprocess.CompletedProcess(command, 0, b"", b"")

        archive, manifest_path, manifest = backup.create_encrypted_backup(
            backup_id="backup-full-create",
            policy_path=policy,
            redis_profile=profile,
            deployment_record=deployment,
            recipient_file=recipient,
            staging_root=staging,
            output_root=output,
            lock_path=self.root / "lifecycle.lock",
            redis_container="boost-redis",
            docker="/usr/bin/docker",
            age="/usr/bin/age",
            retention_classes=["daily"],
            runner=runner,
            identity={
                "host": {"host_id_sha256": "1" * 64},
                "operator": {"name": "test-operator", "uid": 501},
            },
        )

        self.assertTrue(archive.is_file())
        self.assertEqual(manifest, json.loads(manifest_path.read_text()))
        self.assertEqual("candidate_only", manifest["policy_activation_state"])
        self.assertFalse(manifest["formal_todo0012_claim"])
        self.assertFalse(manifest["secret_material_recorded"])
        self.assertEqual(backup.sha256_file(policy), manifest["backup_policy_sha256"])
        self.assertEqual("link_free_tar_v1", manifest["archive_contract"]["format"])
        self.assertEqual([], manifest["source_links"])
        self.assertEqual(
            {"redis_snapshot", "host_configuration"},
            {reference["id"] for reference in manifest["sources"]},
        )
        self.assertEqual([], list(staging.iterdir()))

    def test_archive_excludes_links_and_records_validated_source_mapping(self) -> None:
        source_a = self.root / "source-a"
        source_b = self.root / "source-b"
        source_a.mkdir()
        source_b.mkdir()
        target = source_b / "release/config.env"
        target.parent.mkdir()
        target.write_text("IMAGE=sha256:test\n", encoding="utf-8")
        (source_a / "absolute-current").symlink_to(target)
        (source_a / "relative-current").symlink_to(
            Path("../source-b/release/config.env")
        )
        (source_a / "release-directory").symlink_to(target.parent)
        hardlink = source_b / "release/config-hardlink.env"
        hardlink.hardlink_to(target)
        redis = self.root / "dump.rdb"
        redis.write_bytes(b"REDIS0011\xfa\x00\x00\xff")
        archive = self.root / "link-free.tar"

        references, links = backup.build_plain_archive(
            archive,
            redis,
            [("source_a", source_a.resolve()), ("source_b", source_b.resolve())],
        )

        self.assertEqual(3, len(links))
        self.assertEqual(
            {"source_a", "source_b"},
            {
                reference["id"]
                for reference in references
                if reference["id"] != "redis_snapshot"
            },
        )
        file_links = [link for link in links if link["target_type"] == "file"]
        self.assertEqual(2, len(file_links))
        for link in file_links:
            self.assertEqual("source_b", link["target_source_id"])
            self.assertEqual("release/config.env", link["target_relative_path"])
            self.assertEqual("file", link["target_type"])
        directory_link = next(
            link for link in links if link["target_type"] == "directory"
        )
        self.assertEqual("source_b", directory_link["target_source_id"])
        self.assertEqual("release", directory_link["target_relative_path"])
        with backup.tarfile.open(archive, mode="r:") as bundle:
            members = {member.name: member for member in bundle}
        self.assertNotIn("sources/source_a/absolute-current", members)
        self.assertNotIn("sources/source_a/relative-current", members)
        self.assertNotIn("sources/source_a/release-directory", members)
        self.assertTrue(members["sources/source_b/release/config.env"].isreg())
        self.assertTrue(members["sources/source_b/release/config-hardlink.env"].isreg())
        self.assertFalse(
            any(member.issym() or member.islnk() for member in members.values())
        )

    def test_archive_rejects_broken_and_escaping_symbolic_links(self) -> None:
        source = self.root / "source"
        source.mkdir()
        redis = self.root / "dump.rdb"
        redis.write_bytes(b"REDIS0011\xfa\x00\x00\xff")
        outside = self.root / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")

        (source / "escape").symlink_to(outside)
        with self.assertRaisesRegex(backup.BackupError, "escapes declared"):
            backup.build_plain_archive(
                self.root / "escape.tar",
                redis,
                [("source", source.resolve())],
            )

        (source / "escape").unlink()
        (source / "broken").symlink_to(source / "missing")
        with self.assertRaisesRegex(backup.BackupError, "broken or invalid"):
            backup.build_plain_archive(
                self.root / "broken.tar",
                redis,
                [("source", source.resolve())],
            )

    def test_age_encryption_is_create_only(self) -> None:
        plaintext = self.root / "payload.tar"
        plaintext.write_bytes(b"secret configuration")
        recipient = self.root / "recipient.txt"
        recipient.write_text("age1testrecipient\n", encoding="ascii")
        encrypted = self.root / "payload.tar.age"

        def runner(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            output = Path(command[command.index("--output") + 1])
            source = Path(command[-1])
            output.write_bytes(b"AGE-TEST:" + source.read_bytes())
            return subprocess.CompletedProcess(command, 0, b"", b"")

        backup.encrypt_archive(
            plaintext,
            encrypted,
            recipient_file=recipient,
            age="/usr/bin/age",
            runner=runner,
        )
        self.assertTrue(encrypted.read_bytes().startswith(b"AGE-TEST:"))
        with self.assertRaisesRegex(backup.BackupError, "already exists"):
            backup.encrypt_archive(
                plaintext,
                encrypted,
                recipient_file=recipient,
                age="/usr/bin/age",
                runner=runner,
            )


if __name__ == "__main__":
    unittest.main()
