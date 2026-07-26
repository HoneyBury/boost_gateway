from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.tools import restore_bundle_ssh_receiver as receiver
from scripts.tools import send_restore_bundle as sender


class RestoreBundleTransportTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir(mode=0o700)
        self.restore_id = "restore-one"
        self.backup_id = "backup-one"
        self.rdb = b"REDIS0011restore-transport-fixture"
        self.receiver_identity = self.root / "machine-id"
        self.receiver_identity.write_bytes(b"ubuntu-machine-id\n")
        self.source_host_id = receiver.sha256_file(self.receiver_identity)
        self._write_bundle()
        self.staging = self.root / "staging"
        self.identity = self.root / "restore-ed25519"
        self.identity.write_text("private-key-fixture\n", encoding="ascii")
        self.identity.chmod(0o600)
        self.known_hosts = self.root / "known_hosts"
        self.known_hosts.write_text("host ssh-ed25519 fixture\n", encoding="ascii")

    def _write_json(self, name: str, value: object) -> None:
        (self.bundle / name).write_bytes(receiver.canonical_json(value))

    def _write_bundle(self) -> None:
        (self.bundle / "dump.rdb").write_bytes(self.rdb)
        manifest = {
            "schema_version": 2,
            "backup_id": self.backup_id,
            "archive": {
                "sha256": "a" * 64,
                "size_bytes": 100,
                "plaintext_sha256": "c" * 64,
            },
            "deployment": {
                "deployment_id": "v3.6.2-test",
                "tag": "v3.6.2",
                "commit": "d" * 40,
                "runtime_asset_sha256": "e" * 64,
                "host": {"host_id_sha256": self.source_host_id},
            },
            "source_host": {"host_id_sha256": self.source_host_id},
            "backup_policy_sha256": "f" * 64,
            "redis_profile_sha256": "9" * 64,
            "sources": [
                {
                    "id": "redis_snapshot",
                    "archive_path": "redis/dump.rdb",
                    "sha256": receiver.sha256_file(self.bundle / "dump.rdb"),
                    "size_bytes": len(self.rdb),
                }
            ],
            "source_links": [],
            "archive_contract": {
                "format": "link_free_tar_v1",
                "symbolic_link_entries": 0,
                "hard_link_entries": 0,
                "symbolic_links_recorded": 0,
            },
            "consistent_redis_snapshot": True,
            "encrypted_before_transfer": True,
            "formal_todo0012_claim": False,
            "secret_material_recorded": False,
        }
        self._write_json("manifest.json", manifest)
        receipt = {
            "schema_version": 1,
            "backup_id": self.backup_id,
            "archive_sha256": "a" * 64,
            "archive_size": 100,
            "manifest_sha256": receiver.sha256_file(self.bundle / "manifest.json"),
            "manifest_size": (self.bundle / "manifest.json").stat().st_size,
            "vault_host_id_sha256": "b" * 64,
            "remote_readback_sha256": True,
            "create_only": True,
            "secret_material_recorded": False,
        }
        self._write_json("receipt.json", receipt)
        validation = {
            "schema_version": 1,
            "backup_id": self.backup_id,
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
                "archive_sha256": "a" * 64,
                "manifest_sha256": receiver.sha256_file(self.bundle / "manifest.json"),
                "receipt_sha256": receiver.sha256_file(self.bundle / "receipt.json"),
                "vault_host_id_sha256": "b" * 64,
                "plaintext_sha256": "c" * 64,
                "redis_sha256": receiver.sha256_file(self.bundle / "dump.rdb"),
                "redis_size_bytes": len(self.rdb),
            },
            "formal_todo0012_claim": False,
            "restore_known_good": False,
            "secret_material_recorded": False,
        }
        self._write_json("vault-validation.json", validation)
        bundle = {
            "schema_version": 1,
            "backup_id": self.backup_id,
            "overall_pass": True,
            "identities": {
                "source_host_id_sha256": self.source_host_id,
                "vault_host_id_sha256": "b" * 64,
                "deployment": {
                    "deployment_id": "v3.6.2-test",
                    "tag": "v3.6.2",
                    "commit": "d" * 40,
                    "runtime_asset_sha256": "e" * 64,
                },
            },
            "policy": {
                "backup_policy_sha256": "f" * 64,
                "redis_profile_sha256": "9" * 64,
            },
            "artifacts": {
                "archive_sha256": "a" * 64,
                "archive_size_bytes": 100,
                "manifest_sha256": receiver.sha256_file(self.bundle / "manifest.json"),
                "manifest_size_bytes": (self.bundle / "manifest.json").stat().st_size,
                "receipt_sha256": receiver.sha256_file(self.bundle / "receipt.json"),
                "receipt_size_bytes": (self.bundle / "receipt.json").stat().st_size,
                "validation_summary_sha256": receiver.sha256_file(
                    self.bundle / "vault-validation.json"
                ),
                "validation_summary_size_bytes": (self.bundle / "vault-validation.json")
                .stat()
                .st_size,
                "redis_sha256": receiver.sha256_file(self.bundle / "dump.rdb"),
                "redis_size_bytes": len(self.rdb),
                "plaintext_archive_sha256": "c" * 64,
                "vault_host_id_sha256": "b" * 64,
            },
            "restore_payload": {
                "path": "dump.rdb",
                "sha256": receiver.sha256_file(self.bundle / "dump.rdb"),
                "size_bytes": len(self.rdb),
                "header": "REDIS",
            },
            "create_only": True,
            "formal_todo0012_claim": False,
            "restore_known_good": False,
            "secret_material_recorded": False,
        }
        self._write_json("bundle.json", bundle)

    def _frame(self) -> bytes:
        stream = io.BytesIO()
        receiver.write_frame(stream, self.restore_id, self.bundle)
        return stream.getvalue()

    def test_stream_store_is_create_only_and_receipt_is_readback_bound(self) -> None:
        receipt = receiver.store_bundle(
            self.staging,
            self.receiver_identity,
            io.BytesIO(self._frame()),
            received_at="2026-07-27T00:00:00Z",
        )

        target = self.staging / self.restore_id
        self.assertEqual(0o700, target.stat().st_mode & 0o777)
        self.assertEqual(self.rdb, (target / "dump.rdb").read_bytes())
        self.assertEqual(self.backup_id, receipt["backup_id"])
        self.assertTrue(receipt["remote_readback_sha256"])
        self.assertTrue(receipt["create_only"])
        self.assertEqual(
            receipt,
            receiver.read_receipt(
                self.staging, self.receiver_identity, self.restore_id
            ),
        )
        with self.assertRaisesRegex(receiver.RestoreTransportError, "already exists"):
            receiver.store_bundle(
                self.staging, self.receiver_identity, io.BytesIO(self._frame())
            )

    def test_rejects_extra_missing_symlink_and_binding_drift(self) -> None:
        extra = self.bundle / "unexpected"
        extra.write_text("x", encoding="ascii")
        with self.assertRaisesRegex(
            receiver.RestoreTransportError, "inventory differs"
        ):
            receiver.validate_bundle_binding(self.bundle)
        extra.unlink()

        manifest = self.bundle / "manifest.json"
        original = manifest.read_bytes()
        manifest.unlink()
        with self.assertRaisesRegex(receiver.RestoreTransportError, "missing"):
            receiver.validate_bundle_binding(self.bundle)
        manifest.write_bytes(original)

        manifest.unlink()
        manifest.symlink_to(self.bundle / "receipt.json")
        with self.assertRaisesRegex(receiver.RestoreTransportError, "non-symlink"):
            receiver.validate_bundle_binding(self.bundle)
        manifest.unlink()
        manifest.write_bytes(original)

        value = json.loads((self.bundle / "bundle.json").read_text())
        value["artifacts"]["redis_sha256"] = "0" * 64
        self._write_json("bundle.json", value)
        with self.assertRaisesRegex(receiver.RestoreTransportError, "redis_sha256"):
            receiver.validate_bundle_binding(self.bundle)

    def test_rejects_identity_policy_and_link_contract_drift(self) -> None:
        bundle_path = self.bundle / "bundle.json"
        original_bundle = bundle_path.read_bytes()
        value = json.loads(original_bundle)
        value["identities"]["source_host_id_sha256"] = "0" * 64
        self._write_json("bundle.json", value)
        with self.assertRaisesRegex(
            receiver.RestoreTransportError, "identity or policy"
        ):
            receiver.validate_bundle_binding(self.bundle)

        value = json.loads(original_bundle)
        value["policy"]["redis_profile_sha256"] = "0" * 64
        self._write_json("bundle.json", value)
        with self.assertRaisesRegex(
            receiver.RestoreTransportError, "identity or policy"
        ):
            receiver.validate_bundle_binding(self.bundle)
        bundle_path.write_bytes(original_bundle)

        manifest = json.loads((self.bundle / "manifest.json").read_text())
        manifest["archive_contract"]["hard_link_entries"] = 1
        with self.assertRaisesRegex(receiver.RestoreTransportError, "link-free"):
            receiver.validate_manifest_archive_contract(manifest)
        manifest["archive_contract"]["hard_link_entries"] = 0
        manifest["source_links"] = [
            {
                "archive_path": "sources/configuration/unsafe",
                "original_link_text": "/etc/passwd",
                "target_source_id": "redis_snapshot",
                "target_relative_path": "../../etc/passwd",
                "target_type": "file",
            }
        ]
        manifest["archive_contract"]["symbolic_links_recorded"] = 1
        with self.assertRaisesRegex(receiver.RestoreTransportError, "link metadata"):
            receiver.validate_manifest_archive_contract(manifest)

    def test_receiver_rejects_digest_drift_truncation_and_trailing_data(self) -> None:
        frame = bytearray(self._frame())
        frame[-1] ^= 1
        with self.assertRaisesRegex(receiver.RestoreTransportError, "readback digest"):
            receiver.store_bundle(
                self.staging, self.receiver_identity, io.BytesIO(frame)
            )
        self.assertFalse((self.staging / self.restore_id).exists())

        with self.assertRaisesRegex(receiver.RestoreTransportError, "truncated"):
            receiver.store_bundle(
                self.staging,
                self.receiver_identity,
                io.BytesIO(self._frame()[:-1]),
            )
        with self.assertRaisesRegex(receiver.RestoreTransportError, "trailing"):
            receiver.store_bundle(
                self.staging,
                self.receiver_identity,
                io.BytesIO(self._frame() + b"x"),
            )

    def test_receiver_rejects_a_host_other_than_the_backup_source(self) -> None:
        wrong_identity = self.root / "other-machine-id"
        wrong_identity.write_bytes(b"different-ubuntu-host\n")
        with self.assertRaisesRegex(
            receiver.RestoreTransportError, "not the backup source"
        ):
            receiver.store_bundle(
                self.staging, wrong_identity, io.BytesIO(self._frame())
            )
        self.assertFalse((self.staging / self.restore_id).exists())

    def test_forced_command_surface_rejects_shell_paths_and_deletion(self) -> None:
        self.assertEqual(
            ("store", []),
            receiver.parse_original_command("boost-gateway-restore store"),
        )
        self.assertEqual(
            ("receipt", [self.restore_id]),
            receiver.parse_original_command(
                f"boost-gateway-restore receipt {self.restore_id}"
            ),
        )
        rejected = (
            "",
            "bash",
            "boost-gateway-vault store",
            "boost-gateway-restore store /tmp/x",
            "boost-gateway-restore receipt ../escape",
            "boost-gateway-restore delete restore-one",
            "boost-gateway-restore receipt restore-one; id",
        )
        for command in rejected:
            with self.subTest(command=command), self.assertRaisesRegex(
                receiver.RestoreTransportError, "outside"
            ):
                receiver.parse_original_command(command)

    def test_sender_pins_ssh_and_fetches_persisted_receipt(self) -> None:
        commands: list[list[str]] = []

        def runner(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            commands.append(command)
            original = command[-1]
            if original == "boost-gateway-restore store":
                stream = kwargs["stdin"]
                assert hasattr(stream, "read")
                receipt = receiver.store_bundle(
                    self.staging,
                    self.receiver_identity,
                    stream,  # type: ignore[arg-type]
                    received_at="2026-07-27T00:00:00Z",
                )
            else:
                receipt = receiver.read_receipt(
                    self.staging, self.receiver_identity, self.restore_id
                )
            return subprocess.CompletedProcess(
                command, 0, receiver.canonical_json(receipt), b""
            )

        local_receipt = self.root / "receipts" / "transport.json"
        receipt = sender.send_restore_bundle(
            restore_id=self.restore_id,
            bundle_dir=self.bundle,
            remote_host="restore@ubuntu.test",
            ssh_identity_file=self.identity,
            ssh_known_hosts=self.known_hosts,
            ssh="ssh-test",
            receipt_path=local_receipt,
            runner=runner,
        )

        self.assertEqual(self.restore_id, receipt["restore_id"])
        self.assertEqual(2, len(commands))
        for command in commands:
            joined = " ".join(command)
            self.assertIn("BatchMode=yes", joined)
            self.assertIn("StrictHostKeyChecking=yes", joined)
            self.assertIn("ClearAllForwardings=yes", joined)
            self.assertIn("IdentitiesOnly=yes", joined)
        self.assertNotIn("accept-new", joined)
        self.assertEqual(receipt, json.loads(local_receipt.read_text()))
        with self.assertRaises(FileExistsError):
            sender.write_new_receipt(local_receipt, receipt)

    def test_sender_rejects_weak_identity_and_receipt_drift(self) -> None:
        self.identity.chmod(0o644)
        with self.assertRaisesRegex(receiver.RestoreTransportError, "group/world"):
            sender.send_restore_bundle(
                restore_id=self.restore_id,
                bundle_dir=self.bundle,
                remote_host="restore@ubuntu.test",
                ssh_identity_file=self.identity,
                ssh_known_hosts=self.known_hosts,
            )
        self.identity.chmod(0o600)

        def drifted(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            binding = receiver.validate_bundle_binding(self.bundle)
            receipt = {
                "schema_version": 1,
                "restore_id": self.restore_id,
                "backup_id": self.backup_id,
                "received_at": "2026-07-27T00:00:00Z",
                "files": binding["files"],
                "bundle_sha256": "0" * 64,
                "receiver_host_id_sha256": self.source_host_id,
                "remote_readback_sha256": True,
                "create_only": True,
                "secret_material_recorded": False,
            }
            return subprocess.CompletedProcess(
                command, 0, receiver.canonical_json(receipt), b""
            )

        with self.assertRaisesRegex(receiver.RestoreTransportError, "bundle_sha256"):
            sender.send_restore_bundle(
                restore_id=self.restore_id,
                bundle_dir=self.bundle,
                remote_host="restore@ubuntu.test",
                ssh_identity_file=self.identity,
                ssh_known_hosts=self.known_hosts,
                runner=drifted,
            )


if __name__ == "__main__":
    unittest.main()
