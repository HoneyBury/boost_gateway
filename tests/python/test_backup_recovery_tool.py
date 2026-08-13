from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.tools import backup_vault_ssh_receiver as receiver
from scripts.lib import backup_recovery as backup


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class BackupRecoveryToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.vault = self.root / "vault"
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
        return backup.remote_store(
            self.vault,
            self.identity,
            io.BytesIO(framed),
            recorded_at=created_at,
        )

    def _framed(self, backup_id: str, archive: Path, manifest: Path) -> bytes:
        stream = io.BytesIO()
        backup.write_upload_frame(stream, backup_id, archive, manifest)
        return stream.getvalue()

    def test_remote_store_is_create_only_and_readback_bound(self) -> None:
        archive, manifest = self._artifacts("backup-one")
        framed = self._framed("backup-one", archive, manifest)

        receipt = backup.remote_store(self.vault, self.identity, io.BytesIO(framed))

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
            backup.remote_store(self.vault, self.identity, io.BytesIO(framed))

    def test_remote_store_rejects_digest_drift_and_trailing_bytes(self) -> None:
        archive, manifest = self._artifacts("backup-drift")
        framed = bytearray(self._framed("backup-drift", archive, manifest))
        framed[-1] ^= 1
        with self.assertRaisesRegex(backup.BackupError, "manifest readback digest"):
            backup.remote_store(self.vault, self.identity, io.BytesIO(framed))
        self.assertFalse((self.vault / "backups/backup-drift").exists())

        archive, manifest = self._artifacts("backup-trailing")
        with self.assertRaisesRegex(backup.BackupError, "trailing bytes"):
            backup.remote_store(
                self.vault,
                self.identity,
                io.BytesIO(self._framed("backup-trailing", archive, manifest) + b"x"),
            )

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
                )
        self.assertTrue((self.vault / "deletions/prune-failure.intent.json").is_file())
        self.assertFalse((self.vault / "deletions/prune-failure.json").exists())
        self.assertTrue((self.vault / ".trash/prune-failure").is_dir())

    def test_remote_store_rejects_invalid_retention_timestamp(self) -> None:
        archive, manifest = self._artifacts(
            "backup-bad-time", created_at="not-a-timestamp"
        )
        with self.assertRaisesRegex(backup.BackupError, "created_at"):
            backup.remote_store(
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
            backup.remote_store(
                self.vault,
                self.identity,
                io.BytesIO(self._framed("backup-unsafe-link", archive, manifest)),
            )

    def test_remote_store_rejects_symlinked_internal_vault_root(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        self.vault.mkdir()
        (self.vault / "backups").symlink_to(outside, target_is_directory=True)
        archive, manifest = self._artifacts("backup-symlink")
        with self.assertRaisesRegex(backup.BackupError, "non-symlink directory"):
            backup.remote_store(
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
