from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.tools import manage_backup_recovery as backup
from scripts.tools import restore_backup_isolated as restore


class FakeDocker:
    def __init__(self, *, drift_target: bool = False) -> None:
        self.commands: list[list[str]] = []
        self.drift_target = drift_target
        self.target_created = False
        self.baseline_staging_modes: tuple[int, int] | None = None

    def __call__(
        self, command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[object]:
        self.commands.append(command)
        text = bool(kwargs.get("text"))
        stdout: str | bytes = "" if text else b""
        returncode = 0
        if command[1:3] == ["volume", "inspect"]:
            if command[-1] == "boost-gateway-production-redis-data":
                value = [
                    {
                        "Name": command[-1],
                        "Driver": "local",
                        "Mountpoint": "/var/lib/docker/volumes/active/_data",
                        "Scope": "local",
                        "Labels": {"com.docker.compose.project": "production"},
                    }
                ]
                stdout = json.dumps(value) if text else json.dumps(value).encode()
            elif self.target_created:
                value = [
                    {
                        "Name": command[-1],
                        "Driver": "local",
                        "Mountpoint": "/var/lib/docker/volumes/recovery/_data",
                        "Scope": "local",
                        "Labels": {
                            "boost-gateway.todo": "TODO-0012",
                            "boost-gateway.restore-id": "drill-one",
                        },
                    }
                ]
                stdout = json.dumps(value) if text else json.dumps(value).encode()
            else:
                returncode = 1
        elif command[1:3] == ["volume", "ls"]:
            value = "boost-gateway-recovery-drill-one\n" if self.target_created else ""
            stdout = value if text else value.encode()
        elif command[1:3] == ["volume", "create"]:
            self.target_created = True
        elif command[1:3] == ["volume", "rm"]:
            self.target_created = False
        elif command[1:3] == ["run", "-d"] and "restore-baseline" in command:
            mount = next(
                value
                for value in command
                if value.startswith("type=bind,src=")
                and value.endswith(",dst=/data,readonly")
            )
            source = Path(mount.split(",", 2)[1].removeprefix("src="))
            self.baseline_staging_modes = (
                source.stat().st_mode & 0o777,
                (source / "dump.rdb").stat().st_mode & 0o777,
            )
        elif command[1:3] == ["ps", "-aq"]:
            stdout = "" if text else b""
        elif "redis-cli" in command:
            container = command[2]
            arguments = command[command.index("redis-cli") + 1 :]
            if arguments == ["--raw", "PING"]:
                stdout = "PONG\n" if text else b"PONG\n"
            elif arguments[:2] == ["--json", "SCAN"]:
                stdout = '["0",["lb:global:names","lb:global"]]' if text else b""
            elif arguments[:2] == ["--json", "TYPE"]:
                kind = "zset" if arguments[-1] == "lb:global" else "hash"
                stdout = json.dumps(kind) if text else b""
            elif arguments[:2] == ["--raw", "DUMP"]:
                key = arguments[-1]
                value = (
                    b"\x00\xc3\x28dump-zset\xff\n"
                    if key == "lb:global"
                    else b"\x00\x80dump-hash\xfe\n"
                )
                if (
                    self.drift_target
                    and container == "restore-target"
                    and key == "lb:global"
                ):
                    value = b"\x00\xc3\x28changed\xff\n"
                stdout = value if not text else ""
        return subprocess.CompletedProcess(command, returncode, stdout, b"")


class IsolatedRestoreTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir(mode=0o700)
        self.policy = self.root / "policy.json"
        self.policy.write_text('{"schema_version":1}\n', encoding="ascii")
        self.profile = self.root / "redis.conf"
        self.profile.write_text("appendonly yes\n", encoding="ascii")
        self.backup_id = "todo0012-linkfree-test"
        self.rdb = b"REDIS0011isolated-restore"
        (self.bundle / "dump.rdb").write_bytes(self.rdb)
        self._write_evidence()
        self.summary = self.root / "evidence" / "restore.json"
        self.lock = self.root / "lifecycle.lock"
        self.image = "redis@sha256:" + "a" * 64

    def _write_evidence(self) -> None:
        encrypted_sha = "a" * 64
        plaintext_sha = "b" * 64
        source_id = "1" * 64
        vault_id = "2" * 64
        deployment = {
            "deployment_id": "v3.6.2-test",
            "tag": "v3.6.2",
            "commit": "c" * 40,
            "runtime_asset_sha256": "d" * 64,
            "host": {"host_id_sha256": source_id},
        }
        manifest = {
            "schema_version": 2,
            "backup_id": self.backup_id,
            "archive": {
                "sha256": encrypted_sha,
                "size_bytes": 1234,
                "plaintext_sha256": plaintext_sha,
            },
            "deployment": deployment,
            "source_host": {"host_id_sha256": source_id},
            "backup_policy_sha256": backup.sha256_file(self.policy),
            "redis_profile_sha256": backup.sha256_file(self.profile),
            "sources": [
                {
                    "id": "redis_snapshot",
                    "archive_path": "redis/dump.rdb",
                    "sha256": hashlib.sha256(self.rdb).hexdigest(),
                    "size_bytes": len(self.rdb),
                },
                {
                    "id": "host_configuration",
                    "archive_path": "sources/host_configuration",
                    "symbolic_link_count": 0,
                },
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
        manifest_path = self.bundle / "manifest.json"
        manifest_path.write_bytes(backup.canonical_json(manifest))
        receipt = {
            "schema_version": 1,
            "backup_id": self.backup_id,
            "archive_sha256": encrypted_sha,
            "archive_size": 1234,
            "manifest_sha256": backup.sha256_file(manifest_path),
            "manifest_size": manifest_path.stat().st_size,
            "vault_host_id_sha256": vault_id,
            "remote_readback_sha256": True,
            "create_only": True,
            "secret_material_recorded": False,
        }
        receipt_path = self.bundle / "receipt.json"
        receipt_path.write_bytes(backup.canonical_json(receipt))
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
                "archive_sha256": encrypted_sha,
                "manifest_sha256": backup.sha256_file(manifest_path),
                "receipt_sha256": backup.sha256_file(receipt_path),
                "vault_host_id_sha256": vault_id,
                "plaintext_sha256": plaintext_sha,
                "redis_sha256": hashlib.sha256(self.rdb).hexdigest(),
                "redis_size_bytes": len(self.rdb),
                "plaintext_size_bytes": 10240,
                "member_count": 2,
            },
            "formal_todo0012_claim": False,
            "restore_known_good": False,
            "secret_material_recorded": False,
        }
        validation_path = self.bundle / "vault-validation.json"
        validation_path.write_bytes(backup.canonical_json(validation))
        artifacts = {
            "archive_sha256": encrypted_sha,
            "manifest_sha256": backup.sha256_file(manifest_path),
            "manifest_size_bytes": manifest_path.stat().st_size,
            "receipt_sha256": backup.sha256_file(receipt_path),
            "receipt_size_bytes": receipt_path.stat().st_size,
            "validation_summary_sha256": backup.sha256_file(validation_path),
            "validation_summary_size_bytes": validation_path.stat().st_size,
            "vault_host_id_sha256": vault_id,
            "plaintext_archive_sha256": plaintext_sha,
            "redis_sha256": hashlib.sha256(self.rdb).hexdigest(),
            "redis_size_bytes": len(self.rdb),
        }
        bundle = {
            "schema_version": 1,
            "generated_at": "2026-07-27T00:00:00Z",
            "backup_id": self.backup_id,
            "overall_pass": True,
            "identities": {
                "source_host_id_sha256": source_id,
                "vault_host_id_sha256": vault_id,
                "deployment": {
                    key: deployment[key]
                    for key in (
                        "deployment_id",
                        "tag",
                        "commit",
                        "runtime_asset_sha256",
                    )
                },
            },
            "policy": {
                "backup_policy_sha256": backup.sha256_file(self.policy),
                "redis_profile_sha256": backup.sha256_file(self.profile),
            },
            "artifacts": artifacts,
            "restore_payload": {
                "path": "dump.rdb",
                "sha256": hashlib.sha256(self.rdb).hexdigest(),
                "size_bytes": len(self.rdb),
                "header": "REDIS",
            },
            "create_only": True,
            "formal_todo0012_claim": False,
            "restore_known_good": False,
            "secret_material_recorded": False,
        }
        bundle_path = self.bundle / "bundle.json"
        bundle_path.write_bytes(backup.canonical_json(bundle))
        files = []
        for name in (
            "dump.rdb",
            "bundle.json",
            "manifest.json",
            "receipt.json",
            "vault-validation.json",
        ):
            path = self.bundle / name
            files.append(
                {
                    "name": name,
                    "size_bytes": path.stat().st_size,
                    "sha256": backup.sha256_file(path),
                }
            )
        transport_receipt = {
            "schema_version": 1,
            "restore_id": "drill-one",
            "backup_id": self.backup_id,
            "received_at": "2026-07-27T00:01:00Z",
            "files": files,
            "bundle_sha256": backup.sha256_file(bundle_path),
            "receiver_host_id_sha256": source_id,
            "remote_readback_sha256": True,
            "create_only": True,
            "secret_material_recorded": False,
        }
        (self.bundle / "transport-receipt.json").write_bytes(
            backup.canonical_json(transport_receipt)
        )

    def _run(self, runner: FakeDocker | None = None) -> dict[str, object]:
        return restore.run_isolated_restore(
            restore_id="drill-one",
            bundle_dir=self.bundle,
            policy_path=self.policy,
            redis_profile_path=self.profile,
            target_volume="boost-gateway-recovery-drill-one",
            baseline_container="restore-baseline",
            target_container="restore-target",
            active_volume="boost-gateway-production-redis-data",
            redis_image=self.image,
            summary_path=self.summary,
            required_seed_keys=["lb:global", "lb:global:names"],
            lock_path=self.lock,
            docker="docker-test",
            runner=runner or FakeDocker(),
            monotonic=lambda: 10.0,
        )

    def test_passes_with_fresh_volume_and_exact_canonical_seed(self) -> None:
        runner = FakeDocker()
        result = self._run(runner)

        self.assertTrue(result["overall_pass"])
        self.assertTrue(result["leaderboard_seed_exact"])
        self.assertEqual(
            result["canonical_seed_baseline_sha256"],
            result["canonical_seed_restored_sha256"],
        )
        self.assertEqual(2, result["canonical_seed_key_count"])
        self.assertEqual(self.image, result["redis_image"])
        self.assertEqual(["lb:global", "lb:global:names"], result["required_seed_keys"])
        self.assertTrue(result["target_volume_retained"])
        self.assertFalse(result["active_volume_mounted_by_drill"])
        self.assertFalse(result["production_switched"])
        self.assertFalse(result["formal_todo0012_claim"])
        self.assertFalse(result["restore_known_good"])
        self.assertEqual((0o700, 0o600), runner.baseline_staging_modes)
        flattened = [item for command in runner.commands for item in command]
        self.assertNotIn("KEYS", flattened)
        self.assertIn("SCAN", flattened)
        self.assertIn(
            ["--raw", "DUMP", "lb:global"],
            [
                command[command.index("redis-cli") + 1 :]
                for command in runner.commands
                if "redis-cli" in command
            ],
        )
        self.assertNotIn(
            ["--json", "DUMP", "lb:global"],
            [
                command[command.index("redis-cli") + 1 :]
                for command in runner.commands
                if "redis-cli" in command
            ],
        )
        self.assertEqual(
            "base64(redis-cli --raw stdout)",
            result["canonical_seed_dump_encoding"],
        )
        mounts = [item for item in flattened if item.startswith("type=")]
        self.assertFalse(any("production-redis-data" in item for item in mounts))
        run_commands = [
            command for command in runner.commands if command[1:2] == ["run"]
        ]
        self.assertTrue(all("none" in command for command in run_commands))

    def test_rejects_copied_evidence_drift_before_docker_mutation(self) -> None:
        manifest = self.bundle / "manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        runner = FakeDocker()

        with self.assertRaisesRegex(restore.RestoreError, "copied manifest"):
            self._run(runner)

        self.assertEqual([], runner.commands)
        summary = json.loads(self.summary.read_text())
        self.assertFalse(summary["overall_pass"])
        self.assertFalse(summary["formal_todo0012_claim"])

    def test_rejects_extra_bundle_file_and_link_contract_drift(self) -> None:
        (self.bundle / "unexpected").write_text("x", encoding="ascii")
        with self.assertRaisesRegex(restore.RestoreError, "inventory"):
            restore.validate_bundle(
                self.bundle,
                self.policy,
                self.profile,
                expected_restore_id="drill-one",
            )
        (self.bundle / "unexpected").unlink()

        manifest_path = self.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["archive_contract"]["symbolic_link_entries"] = 1
        manifest_path.write_bytes(backup.canonical_json(manifest))
        with self.assertRaisesRegex(restore.RestoreError, "copied manifest|link-free"):
            restore.validate_bundle(
                self.bundle,
                self.policy,
                self.profile,
                expected_restore_id="drill-one",
            )

    def test_seed_mismatch_removes_only_new_target(self) -> None:
        runner = FakeDocker(drift_target=True)
        with self.assertRaisesRegex(restore.RestoreError, "seed differs"):
            self._run(runner)

        self.assertIn(
            ["docker-test", "volume", "rm", "boost-gateway-recovery-drill-one"],
            runner.commands,
        )
        self.assertNotIn(
            ["docker-test", "volume", "rm", "boost-gateway-production-redis-data"],
            runner.commands,
        )
        summary = json.loads(self.summary.read_text())
        self.assertTrue(summary["target_removed_on_failure"])
        self.assertFalse(summary["target_volume_retained"])
        self.assertFalse(summary["restore_known_good"])

    def test_rejects_transport_restore_or_receiver_identity_drift(self) -> None:
        path = self.bundle / "transport-receipt.json"
        original = json.loads(path.read_text())
        for field, changed in (
            ("restore_id", "another-drill"),
            ("receiver_host_id_sha256", "f" * 64),
        ):
            with self.subTest(field=field):
                receipt = dict(original)
                receipt[field] = changed
                path.write_bytes(backup.canonical_json(receipt))
                with self.assertRaisesRegex(
                    restore.RestoreError, "transport receipt binding"
                ):
                    restore.validate_bundle(
                        self.bundle,
                        self.policy,
                        self.profile,
                        expected_restore_id="drill-one",
                    )
        path.write_bytes(backup.canonical_json(original))

    def test_refuses_active_or_existing_target_and_create_only_summary(self) -> None:
        with self.assertRaisesRegex(restore.RestoreError, "active volume"):
            restore.ensure_target_absent(
                FakeDocker(),
                "docker-test",
                "boost-gateway-production-redis-data",
                "boost-gateway-production-redis-data",
            )
        existing = FakeDocker()
        existing.target_created = True
        with self.assertRaisesRegex(restore.RestoreError, "already exists"):
            restore.ensure_target_absent(
                existing,
                "docker-test",
                "boost-gateway-recovery-drill-one",
                "boost-gateway-production-redis-data",
            )
        self.summary.parent.mkdir(parents=True)
        self.summary.write_text("owned\n", encoding="ascii")
        with self.assertRaisesRegex(restore.RestoreError, "already exists"):
            self._run()
        self.assertEqual("owned\n", self.summary.read_text())


if __name__ == "__main__":
    unittest.main()
