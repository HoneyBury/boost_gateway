"""Tests for secret-free operations identity collection."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.lib.operations_host import (
    OperationsIdentityError,
    collect_operations_identity,
)


class OperationsIdentityTest(unittest.TestCase):
    def test_collects_host_and_sudo_operator_without_environment_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            machine_id = root / "machine-id"
            boot_id = root / "boot-id"
            os_release = root / "os-release"
            machine_id.write_bytes(b"host-machine-id\n")
            boot_id.write_text("boot-123\n", encoding="utf-8")
            os_release.write_text(
                'ID="ubuntu"\nVERSION_ID="24.04"\nSECRET_TOKEN=do-not-record\n',
                encoding="utf-8",
            )

            identity = collect_operations_identity(
                environment={
                    "SUDO_USER": "operator",
                    "SUDO_UID": "1001",
                    "GRAFANA_ADMIN_PASSWORD": "do-not-record",
                },
                machine_id_path=machine_id,
                boot_id_path=boot_id,
                os_release_path=os_release,
            )

        self.assertEqual(
            identity["host"]["host_id_sha256"],
            hashlib.sha256(b"host-machine-id\n").hexdigest(),
        )
        self.assertEqual(identity["host"]["boot_id"], "boot-123")
        self.assertEqual(identity["host"]["os"]["id"], "ubuntu")
        self.assertEqual(
            identity["operator"],
            {"name": "operator", "uid": 1001, "source": "sudo"},
        )
        self.assertNotIn("do-not-record", repr(identity))

    def test_rejects_partial_sudo_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "machine-id").write_text("host", encoding="utf-8")
            (root / "boot-id").write_text("boot", encoding="utf-8")
            (root / "os-release").write_text(
                "ID=ubuntu\nVERSION_ID=24.04\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(OperationsIdentityError, "SUDO_USER"):
                collect_operations_identity(
                    environment={"SUDO_USER": "operator"},
                    machine_id_path=root / "machine-id",
                    boot_id_path=root / "boot-id",
                    os_release_path=root / "os-release",
                )


if __name__ == "__main__":
    unittest.main()
