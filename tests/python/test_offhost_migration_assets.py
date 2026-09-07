from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class OffhostMigrationAssetsTest(unittest.TestCase):
    def test_bounded_vault_policy_is_scoped_and_fail_closed(self) -> None:
        policy = json.loads(
            (
                ROOT / "deploy/operations/aliyun-backup-vault-retention-policy.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(1, policy["schema_version"])
        self.assertEqual("aliyunserver", policy["scope"]["host_alias"])
        self.assertEqual("/srv/boost-gateway-vault", policy["scope"]["vault_root"])
        retention = policy["retention"]
        self.assertEqual(7, retention["daily_copies"])
        self.assertEqual(4, retention["weekly_copies"])
        self.assertEqual(2, retention["minimum_known_good_copies"])
        self.assertEqual(20_000_000_000, retention["max_vault_bytes"])
        self.assertEqual(5_000_000_000, retention["minimum_free_bytes"])
        self.assertEqual("logical_regular_file_bytes", retention["size_accounting"])
        self.assertTrue(retention["current_vault_identity_only"])
        self.assertTrue(retention["pre_upload_reservation_required"])
        self.assertTrue(retention["delete_only_after_verified_remote_copy"])
        self.assertFalse(policy["secret_material_recorded"])

    def test_sshd_fragment_and_installer_are_key_only_and_verified(self) -> None:
        fragment = (ROOT / "deploy/operations/offhost-sshd-hardening.conf").read_text(
            encoding="utf-8"
        )
        installer = (
            ROOT / "deploy/operations/install_offhost_ssh_hardening.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("PasswordAuthentication no", fragment)
        self.assertIn("KbdInteractiveAuthentication no", fragment)
        self.assertIn("PermitRootLogin prohibit-password", fragment)
        self.assertIn("/etc/ssh/sshd_config.d/00-boost-gateway-offhost.conf", installer)
        self.assertIn("/usr/sbin/sshd -t", installer)
        self.assertIn("/usr/sbin/sshd -T", installer)
        self.assertIn("systemctl reload ssh.service", installer)
        self.assertNotIn("PasswordAuthentication yes", fragment)
        self.assertNotIn("NOPASSWD", installer)


if __name__ == "__main__":
    unittest.main()
