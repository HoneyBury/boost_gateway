from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.tools import manage_backup_recovery as backup
from scripts.tools import verify_backup_vault as verify


class FakeAgeProcess:
    def __init__(self, plaintext: bytes, returncode: int = 0) -> None:
        self.stdout = io.BytesIO(plaintext)
        self.stderr = io.BytesIO(b"decryption rejected" if returncode else b"")
        self.returncode = returncode

    def wait(self, timeout: int) -> int:
        return self.returncode


class BackupVaultVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / "vault"
        self.backup_id = "backup-test"
        self.backup = self.vault / "backups" / self.backup_id
        self.backup.mkdir(parents=True)
        self.identity = self.vault / ".vault-identity"
        self.identity.write_bytes(b"mac-vault-identity-material")
        self.age_identity = self.root / "age-identity.txt"
        self.age_identity.write_text("AGE-SECRET-KEY-TEST\n", encoding="ascii")
        self.age_identity.chmod(0o600)
        self.plaintext = self._tar([("redis/dump.rdb", b"REDIS0009payload", "file")])
        self._write_artifacts(self.plaintext)
        self.commands: list[list[str]] = []

    def _tar(self, members: list[tuple[str, bytes, str]]) -> bytes:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, content, kind in members:
                item = tarfile.TarInfo(name)
                if kind == "file":
                    item.size = len(content)
                    archive.addfile(item, io.BytesIO(content))
                elif kind == "symlink":
                    item.type = tarfile.SYMTYPE
                    item.linkname = content.decode("utf-8")
                    archive.addfile(item)
                elif kind == "hardlink":
                    item.type = tarfile.LNKTYPE
                    item.linkname = content.decode("utf-8")
                    archive.addfile(item)
                elif kind == "fifo":
                    item.type = tarfile.FIFOTYPE
                    archive.addfile(item)
        return stream.getvalue()

    def _write_artifacts(self, plaintext: bytes) -> None:
        encrypted = self.backup / "payload.tar.age"
        encrypted.write_bytes(b"encrypted-fixture")
        rdb = b"REDIS0009payload"
        manifest = {
            "schema_version": 2,
            "backup_id": self.backup_id,
            "archive": {
                "sha256": backup.sha256_file(encrypted),
                "size_bytes": encrypted.stat().st_size,
                "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
            },
            "deployment": {"host": {"host_id_sha256": "1" * 64}},
            "source_host": {"host_id_sha256": "1" * 64},
            "backup_policy_sha256": "2" * 64,
            "sources": [
                {
                    "id": "redis_snapshot",
                    "archive_path": "redis/dump.rdb",
                    "sha256": hashlib.sha256(rdb).hexdigest(),
                    "size_bytes": len(rdb),
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
        manifest_path = self.backup / "manifest.json"
        manifest_path.write_bytes(backup.canonical_json(manifest))
        receipt = {
            "schema_version": 1,
            "backup_id": self.backup_id,
            "archive_sha256": backup.sha256_file(encrypted),
            "archive_size": encrypted.stat().st_size,
            "manifest_sha256": backup.sha256_file(manifest_path),
            "manifest_size": manifest_path.stat().st_size,
            "vault_host_id_sha256": backup.sha256_file(self.identity),
            "remote_readback_sha256": True,
            "create_only": True,
            "secret_material_recorded": False,
        }
        (self.backup / "receipt.json").write_bytes(backup.canonical_json(receipt))

    def _runner(
        self, command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"RDB looks OK", b"")

    def _verify(self, plaintext: bytes | None = None) -> dict[str, object]:
        content = self.plaintext if plaintext is None else plaintext
        return verify.verify_backup(
            vault_root=self.vault,
            backup_id=self.backup_id,
            age_identity=self.age_identity,
            age="age-test",
            docker="docker-test",
            redis_image="redis@sha256:" + "a" * 64,
            runner=self._runner,
            starter=lambda *args, **kwargs: FakeAgeProcess(content),
            generated_at="2026-07-26T00:00:00Z",
        )

    def test_success_binds_metadata_and_runs_isolated_rdb_check(self) -> None:
        summary = self._verify()

        self.assertTrue(summary["overall_pass"])
        self.assertFalse(summary["formal_todo0012_claim"])
        self.assertFalse(summary["restore_known_good"])
        self.assertFalse(summary["secret_material_recorded"])
        command = self.commands[0]
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertIn("ALL", command)
        self.assertIn("no-new-privileges", command)

    def test_rejects_archive_and_receipt_digest_drift(self) -> None:
        (self.backup / "payload.tar.age").write_bytes(b"changed")
        with self.assertRaisesRegex(verify.VaultVerificationError, "receipt field"):
            self._verify()

    def test_rejects_schema_one_or_invalid_link_contract(self) -> None:
        manifest = json.loads((self.backup / "manifest.json").read_text())
        manifest["schema_version"] = 1
        (self.backup / "manifest.json").write_bytes(backup.canonical_json(manifest))
        self._refresh_receipt_manifest_binding()
        with self.assertRaisesRegex(verify.VaultVerificationError, "incomplete"):
            self._verify()

        manifest["schema_version"] = 2
        manifest["archive_contract"]["symbolic_link_entries"] = 1
        (self.backup / "manifest.json").write_bytes(backup.canonical_json(manifest))
        self._refresh_receipt_manifest_binding()
        with self.assertRaisesRegex(backup.BackupError, "link-free"):
            self._verify()

    def test_rejects_same_source_and_vault_identity(self) -> None:
        manifest = json.loads((self.backup / "manifest.json").read_text())
        host_id = backup.sha256_file(self.identity)
        manifest["source_host"]["host_id_sha256"] = host_id
        manifest["deployment"]["host"]["host_id_sha256"] = host_id
        (self.backup / "manifest.json").write_bytes(backup.canonical_json(manifest))
        self._refresh_receipt_manifest_binding()
        with self.assertRaisesRegex(verify.VaultVerificationError, "not distinct"):
            self._verify()

    def test_rejects_plaintext_digest_drift(self) -> None:
        changed = self._tar(
            [
                ("redis/dump.rdb", b"REDIS0009payload", "file"),
                ("sources/config/new", b"changed", "file"),
            ]
        )
        with self.assertRaisesRegex(
            verify.VaultVerificationError, "plaintext archive digest differs"
        ):
            self._verify(changed)

    def test_rejects_unsafe_and_duplicate_member_names(self) -> None:
        cases = {
            "absolute": [("/escape", b"x", "file")],
            "parent": [("../escape", b"x", "file")],
            "duplicate": [
                ("safe", b"x", "file"),
                ("safe", b"y", "file"),
            ],
        }
        for label, extra in cases.items():
            with self.subTest(label=label):
                plaintext = self._tar(
                    [("redis/dump.rdb", b"REDIS0009payload", "file"), *extra]
                )
                self._set_plaintext_digest(plaintext)
                pattern = "duplicate" if label == "duplicate" else "unsafe"
                with self.assertRaisesRegex(verify.VaultVerificationError, pattern):
                    self._verify(plaintext)

    def test_rejects_links_and_special_files(self) -> None:
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                plaintext = self._tar(
                    [
                        ("redis/dump.rdb", b"REDIS0009payload", "file"),
                        ("unsafe", b"redis/dump.rdb", kind),
                    ]
                )
                self._set_plaintext_digest(plaintext)
                with self.assertRaisesRegex(verify.VaultVerificationError, "forbidden"):
                    self._verify(plaintext)

    def test_rejects_redis_header_and_manifest_binding_drift(self) -> None:
        plaintext = self._tar([("redis/dump.rdb", b"WRONG0009payload", "file")])
        manifest = json.loads((self.backup / "manifest.json").read_text())
        manifest["archive"]["plaintext_sha256"] = hashlib.sha256(plaintext).hexdigest()
        manifest["sources"][0]["sha256"] = hashlib.sha256(
            b"WRONG0009payload"
        ).hexdigest()
        manifest["sources"][0]["size_bytes"] = len(b"WRONG0009payload")
        (self.backup / "manifest.json").write_bytes(backup.canonical_json(manifest))
        self._refresh_receipt_manifest_binding()
        with self.assertRaisesRegex(verify.VaultVerificationError, "header"):
            self._verify(plaintext)

    def test_rejects_failed_age_or_redis_validation(self) -> None:
        with self.assertRaisesRegex(verify.VaultVerificationError, "age decryption"):
            verify.verify_backup(
                vault_root=self.vault,
                backup_id=self.backup_id,
                age_identity=self.age_identity,
                age="age-test",
                docker="docker-test",
                redis_image="redis-test",
                runner=self._runner,
                starter=lambda *args, **kwargs: FakeAgeProcess(
                    self.plaintext, returncode=1
                ),
            )

        def failed_runner(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(command, 1, b"", b"invalid")

        with self.assertRaisesRegex(verify.VaultVerificationError, "rejected"):
            verify.verify_backup(
                vault_root=self.vault,
                backup_id=self.backup_id,
                age_identity=self.age_identity,
                age="age-test",
                docker="docker-test",
                redis_image="sha256:" + "b" * 64,
                runner=failed_runner,
                starter=lambda *args, **kwargs: FakeAgeProcess(self.plaintext),
            )

        with self.assertRaisesRegex(verify.VaultVerificationError, "immutable digest"):
            verify.verify_backup(
                vault_root=self.vault,
                backup_id=self.backup_id,
                age_identity=self.age_identity,
                age="age-test",
                docker="docker-test",
                redis_image="redis:7-alpine",
                runner=self._runner,
                starter=lambda *args, **kwargs: FakeAgeProcess(self.plaintext),
            )

    def test_summary_write_is_create_only(self) -> None:
        summary = self.root / "summary.json"
        result = self._verify()
        backup.write_new(summary, backup.canonical_json(result))
        with self.assertRaises(FileExistsError):
            backup.write_new(summary, backup.canonical_json(result))

    def _set_plaintext_digest(self, plaintext: bytes) -> None:
        manifest = json.loads((self.backup / "manifest.json").read_text())
        manifest["archive"]["plaintext_sha256"] = hashlib.sha256(plaintext).hexdigest()
        (self.backup / "manifest.json").write_bytes(backup.canonical_json(manifest))
        self._refresh_receipt_manifest_binding()

    def _refresh_receipt_manifest_binding(self) -> None:
        manifest_path = self.backup / "manifest.json"
        receipt_path = self.backup / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["manifest_sha256"] = backup.sha256_file(manifest_path)
        receipt["manifest_size"] = manifest_path.stat().st_size
        receipt_path.write_bytes(backup.canonical_json(receipt))


if __name__ == "__main__":
    unittest.main()
