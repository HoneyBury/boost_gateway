from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.tools import export_backup_restore_bundle as export
from scripts.tools import manage_backup_recovery as backup


class FakeAgeProcess:
    def __init__(self, plaintext: bytes, returncode: int = 0) -> None:
        self.stdout = io.BytesIO(plaintext)
        self.stderr = io.BytesIO(b"age failed" if returncode else b"")
        self.returncode = returncode
        self.command: list[str] = []

    def wait(self, timeout: int) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class RestoreBundleExportTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = self.root / "vault"
        self.backup_id = "todo0012-linkfree-test"
        self.backup_dir = self.vault / "backups" / self.backup_id
        self.backup_dir.mkdir(parents=True)
        self.vault_identity = self.vault / ".vault-identity"
        self.vault_identity.write_bytes(b"independent-mac-vault-identity")
        self.age_identity = self.root / "age-identity.txt"
        self.age_identity.write_text("AGE-SECRET-KEY-TEST\n", encoding="ascii")
        self.age_identity.chmod(0o600)
        self.rdb = b"REDIS0011restore-payload"
        self.plaintext = self._tar(
            [
                ("redis/dump.rdb", self.rdb, "file"),
                ("sources/config/public.txt", b"not exported", "file"),
            ]
        )
        self._write_vault_artifacts()
        self.validation = self.root / "vault-validation.json"
        self._write_validation()
        self.bundle = self.root / "restore" / self.backup_id
        self.started_commands: list[list[str]] = []

    def _tar(self, members: list[tuple[str, bytes, str]]) -> bytes:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, content, kind in members:
                member = tarfile.TarInfo(name)
                if kind == "file":
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
                elif kind == "symlink":
                    member.type = tarfile.SYMTYPE
                    member.linkname = content.decode("ascii")
                    archive.addfile(member)
        return stream.getvalue()

    def _write_vault_artifacts(self) -> None:
        encrypted = self.backup_dir / "payload.tar.age"
        encrypted.write_bytes(b"encrypted-fixture")
        manifest = {
            "schema_version": 2,
            "backup_id": self.backup_id,
            "archive": {
                "sha256": backup.sha256_file(encrypted),
                "size_bytes": encrypted.stat().st_size,
                "plaintext_sha256": hashlib.sha256(self.plaintext).hexdigest(),
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
            "redis_profile_sha256": "3" * 64,
            "sources": [
                {
                    "id": "redis_snapshot",
                    "archive_path": "redis/dump.rdb",
                    "sha256": hashlib.sha256(self.rdb).hexdigest(),
                    "size_bytes": len(self.rdb),
                },
                {"id": "config", "archive_path": "sources/config"},
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
        manifest_path = self.backup_dir / "manifest.json"
        manifest_path.write_bytes(backup.canonical_json(manifest))
        receipt = {
            "schema_version": 1,
            "backup_id": self.backup_id,
            "archive_sha256": backup.sha256_file(encrypted),
            "archive_size": encrypted.stat().st_size,
            "manifest_sha256": backup.sha256_file(manifest_path),
            "manifest_size": manifest_path.stat().st_size,
            "vault_host_id_sha256": backup.sha256_file(self.vault_identity),
            "remote_readback_sha256": True,
            "create_only": True,
            "secret_material_recorded": False,
        }
        (self.backup_dir / "receipt.json").write_bytes(backup.canonical_json(receipt))

    def _validation_data(self) -> dict[str, object]:
        encrypted = self.backup_dir / "payload.tar.age"
        manifest = self.backup_dir / "manifest.json"
        receipt = self.backup_dir / "receipt.json"
        return {
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
                "archive_sha256": backup.sha256_file(encrypted),
                "manifest_sha256": backup.sha256_file(manifest),
                "receipt_sha256": backup.sha256_file(receipt),
                "vault_host_id_sha256": backup.sha256_file(self.vault_identity),
                "plaintext_sha256": hashlib.sha256(self.plaintext).hexdigest(),
                "plaintext_size_bytes": len(self.plaintext),
                "member_count": 2,
                "redis_sha256": hashlib.sha256(self.rdb).hexdigest(),
                "redis_size_bytes": len(self.rdb),
            },
            "formal_todo0012_claim": False,
            "restore_known_good": False,
            "secret_material_recorded": False,
        }

    def _write_validation(self, data: dict[str, object] | None = None) -> None:
        self.validation.write_bytes(
            backup.canonical_json(data or self._validation_data())
        )

    def _starter(self, command: list[str], **kwargs: object) -> FakeAgeProcess:
        self.started_commands.append(command)
        process = FakeAgeProcess(self.plaintext)
        process.command = command
        return process

    def _export(self, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "vault_root": self.vault,
            "backup_id": self.backup_id,
            "validation_summary": self.validation,
            "age_identity": self.age_identity,
            "bundle_dir": self.bundle,
            "age": "age-test",
            "starter": self._starter,
            "generated_at": "2026-07-27T00:00:00Z",
        }
        arguments.update(overrides)
        return export.export_restore_bundle(**arguments)  # type: ignore[arg-type]

    def test_exports_only_bound_redis_payload_and_bundle_metadata(self) -> None:
        result = self._export()

        self.assertEqual(
            {
                "bundle.json",
                "dump.rdb",
                "manifest.json",
                "receipt.json",
                "vault-validation.json",
            },
            {p.name for p in self.bundle.iterdir()},
        )
        self.assertEqual(self.rdb, (self.bundle / "dump.rdb").read_bytes())
        self.assertEqual(
            (self.backup_dir / "manifest.json").read_bytes(),
            (self.bundle / "manifest.json").read_bytes(),
        )
        self.assertEqual(
            (self.backup_dir / "receipt.json").read_bytes(),
            (self.bundle / "receipt.json").read_bytes(),
        )
        self.assertEqual(
            self.validation.read_bytes(),
            (self.bundle / "vault-validation.json").read_bytes(),
        )
        self.assertEqual(0o700, self.bundle.stat().st_mode & 0o777)
        self.assertEqual(0o600, (self.bundle / "dump.rdb").stat().st_mode & 0o777)
        for name in (
            "bundle.json",
            "manifest.json",
            "receipt.json",
            "vault-validation.json",
        ):
            self.assertEqual(0o600, (self.bundle / name).stat().st_mode & 0o777)
        self.assertFalse(result["formal_todo0012_claim"])
        self.assertFalse(result["restore_known_good"])
        self.assertFalse(result["secret_material_recorded"])
        self.assertEqual(
            "v3.6.2-test",
            result["identities"]["deployment"]["deployment_id"],
        )
        self.assertEqual("1" * 64, result["identities"]["source_host_id_sha256"])
        self.assertEqual(
            backup.sha256_file(self.vault_identity),
            result["identities"]["vault_host_id_sha256"],
        )
        self.assertEqual("2" * 64, result["policy"]["backup_policy_sha256"])
        self.assertEqual("3" * 64, result["policy"]["redis_profile_sha256"])
        self.assertEqual(
            backup.sha256_file(self.validation),
            result["artifacts"]["validation_summary_sha256"],
        )
        self.assertEqual(
            self.validation.stat().st_size,
            result["artifacts"]["validation_summary_size_bytes"],
        )
        self.assertEqual("REDIS", result["restore_payload"]["header"])
        self.assertEqual(
            [
                "age-test",
                "--decrypt",
                "--identity",
                str(self.age_identity.resolve()),
                str((self.backup_dir / "payload.tar.age").resolve()),
            ],
            self.started_commands[0],
        )

    def test_rejects_failed_or_drifted_validation_summary(self) -> None:
        cases = {
            "overall": ("overall_pass", False, "eligible pass"),
            "formal": ("formal_todo0012_claim", True, "eligible pass"),
            "check": ("checks.safe_archive_members", False, "required pass"),
            "archive": ("artifacts.archive_sha256", "f" * 64, "artifact differs"),
            "redis": ("artifacts.redis_sha256", "e" * 64, "Redis binding"),
        }
        for label, (field, value, pattern) in cases.items():
            with self.subTest(label=label):
                data = self._validation_data()
                target: dict[str, object] = data
                parts = field.split(".")
                for part in parts[:-1]:
                    target = target[part]  # type: ignore[assignment]
                target[parts[-1]] = value
                self.validation.unlink()
                self._write_validation(data)
                with self.assertRaisesRegex(export.RestoreExportError, pattern):
                    self._export()
                self.assertFalse(self.bundle.exists())

    def test_rejects_unsafe_archive_and_removes_incomplete_bundle(self) -> None:
        self.plaintext = self._tar(
            [
                ("redis/dump.rdb", self.rdb, "file"),
                ("../escape", b"secret", "file"),
            ]
        )
        self._refresh_plaintext_bindings(member_count=2)
        with self.assertRaisesRegex(export.vault.VaultVerificationError, "unsafe"):
            self._export()
        self.assertFalse(self.bundle.exists())

    def test_detects_vault_mutation_during_export_and_cleans_bundle(self) -> None:
        encrypted = self.backup_dir / "payload.tar.age"

        def mutating_starter(command: list[str], **kwargs: object) -> FakeAgeProcess:
            encrypted.write_bytes(b"mutated-after-admission")
            return FakeAgeProcess(self.plaintext)

        with self.assertRaisesRegex(export.RestoreExportError, "changed during"):
            self._export(starter=mutating_starter)
        self.assertFalse(self.bundle.exists())

    def test_rejects_bad_rdb_or_failed_age_without_completed_bundle(self) -> None:
        self.rdb = b"WRONG0011restore-payload"
        self.plaintext = self._tar([("redis/dump.rdb", self.rdb, "file")])
        self._refresh_plaintext_bindings(member_count=1)
        with self.assertRaisesRegex(export.vault.VaultVerificationError, "header"):
            self._export()
        self.assertFalse(self.bundle.exists())

        def failed_starter(command: list[str], **kwargs: object) -> FakeAgeProcess:
            return FakeAgeProcess(self.plaintext, returncode=1)

        self.rdb = b"REDIS0011restore-payload"
        self.plaintext = self._tar([("redis/dump.rdb", self.rdb, "file")])
        self._refresh_plaintext_bindings(member_count=1)
        with self.assertRaisesRegex(export.RestoreExportError, "age decryption failed"):
            self._export(starter=failed_starter)
        self.assertFalse(self.bundle.exists())

    def test_rejects_rdb_digest_drift_without_completed_bundle(self) -> None:
        self._replace_redis_reference("sha256", "f" * 64)
        with self.assertRaisesRegex(
            export.vault.VaultVerificationError, "digest differs"
        ):
            self._export()
        self.assertFalse(self.bundle.exists())

    def test_rejects_rdb_size_drift_without_completed_bundle(self) -> None:
        self._replace_redis_reference("size_bytes", len(self.rdb) + 1)
        with self.assertRaisesRegex(
            export.vault.VaultVerificationError, "size differs"
        ):
            self._export()
        self.assertFalse(self.bundle.exists())

    def test_create_only_rejects_existing_bundle_without_mutation(self) -> None:
        self.bundle.mkdir(parents=True)
        marker = self.bundle / "owned"
        marker.write_text("keep", encoding="ascii")

        with self.assertRaisesRegex(export.RestoreExportError, "already exists"):
            self._export()
        self.assertEqual("keep", marker.read_text(encoding="ascii"))

    def test_rejects_group_readable_age_identity_before_creating_bundle(self) -> None:
        self.age_identity.chmod(0o640)
        with self.assertRaisesRegex(export.vault.VaultVerificationError, "accessible"):
            self._export()
        self.assertFalse(self.bundle.exists())

    def _refresh_plaintext_bindings(self, *, member_count: int) -> None:
        manifest_path = self.backup_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["archive"]["plaintext_sha256"] = hashlib.sha256(
            self.plaintext
        ).hexdigest()
        manifest["sources"][0]["sha256"] = hashlib.sha256(self.rdb).hexdigest()
        manifest["sources"][0]["size_bytes"] = len(self.rdb)
        manifest_path.write_bytes(backup.canonical_json(manifest))
        receipt_path = self.backup_dir / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["manifest_sha256"] = backup.sha256_file(manifest_path)
        receipt["manifest_size"] = manifest_path.stat().st_size
        receipt_path.write_bytes(backup.canonical_json(receipt))
        validation = self._validation_data()
        validation["artifacts"]["member_count"] = member_count
        self.validation.unlink()
        self._write_validation(validation)

    def _replace_redis_reference(self, field: str, value: object) -> None:
        manifest_path = self.backup_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["sources"][0][field] = value
        manifest_path.write_bytes(backup.canonical_json(manifest))
        receipt_path = self.backup_dir / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["manifest_sha256"] = backup.sha256_file(manifest_path)
        receipt["manifest_size"] = manifest_path.stat().st_size
        receipt_path.write_bytes(backup.canonical_json(receipt))
        validation = self._validation_data()
        if field == "sha256":
            validation["artifacts"]["redis_sha256"] = value
        else:
            validation["artifacts"]["redis_size_bytes"] = value
        self.validation.unlink()
        self._write_validation(validation)


if __name__ == "__main__":
    unittest.main()
