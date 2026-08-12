from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scripts.tools import run_scheduled_backup as scheduled


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ScheduledBackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "encrypted"
        self.receipts = self.root / "receipts"
        self.evidence = self.root / "evidence"
        self.output.mkdir()
        self.receipts.mkdir()
        self.evidence.mkdir()
        self.files: dict[str, Path] = {}
        for name in (
            "tool",
            "policy",
            "profile",
            "record",
            "recipient",
            "remote-id",
            "identity",
            "known-hosts",
        ):
            path = self.root / name
            path.write_text(f"{name}\n", encoding="utf-8")
            self.files[name] = path
        self.remote_host_file = self.root / "remote-host"
        self.remote_host_file.write_text("backup@vault.example\n", encoding="ascii")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def args(self, weekly_iso_weekday: int = 1) -> argparse.Namespace:
        return argparse.Namespace(
            tool=self.files["tool"],
            policy=self.files["policy"],
            redis_profile=self.files["profile"],
            deployment_record=self.files["record"],
            recipient_file=self.files["recipient"],
            remote_host_file=self.remote_host_file,
            remote_host_id_attestation=self.files["remote-id"],
            ssh_identity_file=self.files["identity"],
            ssh_known_hosts=self.files["known-hosts"],
            staging_root=self.root / "staging",
            output_root=self.output,
            receipt_root=self.receipts,
            evidence_root=self.evidence,
            lock_path=self.root / "lifecycle.lock",
            redis_container="redis-test",
            docker="docker-test",
            age="age-test",
            ssh="ssh-test",
            weekly_iso_weekday=weekly_iso_weekday,
            timeout_seconds=30,
        )

    def successful_runner(
        self, expected_classes: list[str]
    ) -> tuple[scheduled.Runner, list[list[str]]]:
        commands: list[list[str]] = []

        def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            identifier = command[command.index("--backup-id") + 1]
            classes = [
                command[index + 1]
                for index, value in enumerate(command)
                if value == "--retention-class"
            ]
            self.assertEqual(expected_classes, classes)
            archive = self.output / f"{identifier}.tar.age"
            archive.write_bytes(b"encrypted archive")
            manifest_path = self.output / f"{identifier}.manifest.json"
            manifest = {
                "schema_version": 2,
                "backup_id": identifier,
                "created_at": "2026-07-27T02:15:00Z",
                "archive": {
                    "name": archive.name,
                    "sha256": digest(archive),
                    "size_bytes": archive.stat().st_size,
                },
                "backup_policy_sha256": digest(self.files["policy"]),
                "redis_profile_sha256": digest(self.files["profile"]),
                "source_host": {"host_id_sha256": "1" * 64},
                "deployment": {"deployment_id": "release-one"},
                "retention_classes": classes,
                "formal_todo0012_claim": False,
                "secret_material_recorded": False,
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            receipt_path = self.receipts / f"{identifier}.json"
            receipt = {
                "schema_version": 1,
                "backup_id": identifier,
                "archive_sha256": digest(archive),
                "archive_size": archive.stat().st_size,
                "manifest_sha256": digest(manifest_path),
                "manifest_size": manifest_path.stat().st_size,
                "remote_readback_sha256": True,
                "create_only": True,
                "secret_material_recorded": False,
                "vault_host_id_sha256": "2" * 64,
            }
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            result = {
                "archive_path": str(archive),
                "manifest_path": str(manifest_path),
                "manifest": manifest,
                "remote_receipt_path": str(receipt_path),
                "remote_receipt": receipt,
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        return run, commands

    def test_monday_backup_binds_daily_weekly_and_remote_readback(self) -> None:
        runner, commands = self.successful_runner(["daily", "weekly"])
        result = scheduled.run_scheduled_backup(
            self.args(),
            started=datetime(2026, 7, 27, 2, 15, tzinfo=UTC),
            suffix="1234abcd",
            runner=runner,
        )

        self.assertTrue(result["overall_pass"])
        self.assertTrue(result["off_host_copy_verified"])
        self.assertEqual(["daily", "weekly"], result["retention_classes"])
        self.assertFalse(result["restore_known_good"])
        self.assertFalse(result["formal_todo0012_claim"])
        self.assertEqual(1, len(commands))
        self.assertIn("backup@vault.example", commands[0])
        persisted = json.loads(Path(result["summary_path"]).read_text())
        self.assertEqual(
            {key: value for key, value in result.items() if key != "summary_path"},
            persisted,
        )

    def test_non_monday_backup_is_daily_only(self) -> None:
        runner, _ = self.successful_runner(["daily"])
        result = scheduled.run_scheduled_backup(
            self.args(),
            started=datetime(2026, 7, 28, 2, 15, tzinfo=UTC),
            suffix="abcdef12",
            runner=runner,
        )
        self.assertEqual(["daily"], result["retention_classes"])

    def test_engine_failure_writes_fail_closed_create_only_summary(self) -> None:
        def fail(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, "", "injected failure")

        with self.assertRaisesRegex(scheduled.ScheduledBackupError, "summary="):
            scheduled.run_scheduled_backup(
                self.args(),
                started=datetime(2026, 7, 28, 2, 15, tzinfo=UTC),
                suffix="deadbeef",
                runner=fail,
            )
        summaries = list(self.evidence.glob("*-summary.json"))
        self.assertEqual(1, len(summaries))
        summary = json.loads(summaries[0].read_text())
        self.assertFalse(summary["overall_pass"])
        self.assertFalse(summary["off_host_copy_verified"])
        self.assertIn("injected failure", summary["failure"])
        self.assertFalse(summary["formal_todo0012_claim"])
        with self.assertRaisesRegex(
            scheduled.ScheduledBackupError, "create-only scheduled summary"
        ):
            scheduled.run_scheduled_backup(
                self.args(),
                started=datetime(2026, 7, 28, 2, 15, tzinfo=UTC),
                suffix="deadbeef",
                runner=fail,
            )

    def test_rejects_unsafe_remote_and_invalid_weekday(self) -> None:
        self.remote_host_file.write_text("backup@vault;id\n", encoding="ascii")
        runner, _ = self.successful_runner(["daily"])
        with self.assertRaisesRegex(scheduled.ScheduledBackupError, "remote host"):
            scheduled.run_scheduled_backup(
                self.args(),
                started=datetime(2026, 7, 28, tzinfo=UTC),
                suffix="11223344",
                runner=runner,
            )
        with self.assertRaisesRegex(scheduled.ScheduledBackupError, "ISO weekday"):
            scheduled.retention_classes(datetime.now(UTC), 0)

    def test_systemd_and_installer_keep_secrets_out_of_units(self) -> None:
        root = Path(__file__).resolve().parents[2]
        service = (root / "deploy/systemd/boost-gateway-backup.service").read_text()
        timer = (root / "deploy/systemd/boost-gateway-backup.timer").read_text()
        installer = (
            root / "deploy/operations/install_backup_host_units.sh"
        ).read_text()
        self.assertIn("User=root", service)
        self.assertIn("NoNewPrivileges=yes", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("CapabilityBoundingSet=\n", service)
        self.assertIn("TimeoutStartSec=90min", service)
        self.assertNotIn("PRIVATE KEY", service)
        self.assertNotIn("EnvironmentFile", service)
        self.assertIn("OnCalendar=*-*-* 02:15:00 UTC", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("RandomizedDelaySec=15min", timer)
        self.assertIn("systemctl enable --now boost-gateway-backup.timer", installer)
        self.assertIn("chmod 0600", installer)
        self.assertIn("--run-now", installer)
        for runtime_dependency in (
            '"${ROOT}/scripts/__init__.py"',
            '"${ROOT}/scripts/lib/__init__.py"',
            '"${ROOT}/scripts/lib/operations_host.py"',
            '"${ROOT}/scripts/tools/__init__.py"',
        ):
            self.assertIn(runtime_dependency, installer)
        self.assertIn("/usr/local/libexec/boost-gateway/backup/scripts/lib", installer)


if __name__ == "__main__":
    unittest.main()
