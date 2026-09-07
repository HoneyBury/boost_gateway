from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "deploy/operations/install_alertmanager_forward_target.sh"


class AlertmanagerForwardTargetInstallerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = INSTALLER.read_text(encoding="utf-8")

    def test_script_has_valid_bash_syntax_and_rejects_unmanaged_arguments(self) -> None:
        subprocess.run(["bash", "-n", str(INSTALLER)], check=True)
        result = subprocess.run(
            ["bash", str(INSTALLER), "--listen-port", "9093"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("unknown argument: --listen-port", result.stderr)

    def test_source_is_one_ed25519_key_from_one_tailscale_address(self) -> None:
        self.assertIn("100.64.0.0/10", self.text)
        self.assertIn("fd7a:115c:a1e0::/48", self.text)
        self.assertIn("address = ipaddress.ip_address(raw)", self.text)
        self.assertIn("source public key file must contain exactly one key", self.text)
        self.assertIn("[[ ${key_type} == ssh-ed25519 ]]", self.text)
        self.assertIn("ssh-keygen -l -f", self.text)
        self.assertNotIn("SOURCE_PORT", self.text)
        self.assertNotIn("TARGET_PORT", self.text)

    def test_authorized_key_is_root_managed_and_fixed_to_alertmanager(self) -> None:
        required_options = (
            'from="%s",command="/bin/false",restrict,port-forwarding,'
            'permitopen="127.0.0.1:9093"'
        )

        self.assertIn(required_options, self.text)
        self.assertIn(
            'chown root:"${FORWARD_GROUP}" "${AUTHORIZED_KEYS_TEMP}"', self.text
        )
        self.assertIn('chmod 0640 "${AUTHORIZED_KEYS_TEMP}"', self.text)
        self.assertIn('install -d -o root -g root -m 0755 "${FORWARD_HOME}"', self.text)
        self.assertIn(
            'install -d -o root -g "${FORWARD_GROUP}" -m 0750 "${SSH_DIRECTORY}"',
            self.text,
        )
        self.assertIn("managed file must not be a symlink", self.text)
        self.assertIn("must have exactly one hard link", self.text)

    def test_service_identity_is_dedicated_locked_and_has_no_shell(self) -> None:
        user_assignment = "FORWARD_USER=boost-gateway-alert-forward"
        self.assertIn(user_assignment, self.text)
        self.assertIn("FORWARD_GROUP=boost-gateway-alert-forward", self.text)
        self.assertLessEqual(len(user_assignment.partition("=")[2]), 32)
        self.assertIn("--no-create-home --shell /usr/sbin/nologin", self.text)
        self.assertIn('usermod --lock "${FORWARD_USER}"', self.text)
        self.assertIn("account_uid} != 0", self.text)
        self.assertIn("must not belong to supplementary groups", self.text)

    def test_match_policy_allows_only_the_fixed_local_forward(self) -> None:
        required_directives = (
            "Match User ${FORWARD_USER}",
            "AuthenticationMethods publickey",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "AuthorizedKeysCommand none",
            "ForceCommand /bin/false",
            "MaxSessions 0",
            "DisableForwarding no",
            "AllowTcpForwarding local",
            "PermitOpen 127.0.0.1:9093",
            "PermitListen none",
            "AllowStreamLocalForwarding no",
            "AllowAgentForwarding no",
            "X11Forwarding no",
            "PermitTTY no",
            "PermitTunnel no",
            "PermitUserRC no",
            "Match all",
        )
        for directive in required_directives:
            with self.subTest(directive=directive):
                self.assertIn(directive, self.text)

    def test_sshd_policy_is_validated_before_write_after_write_and_before_reload(
        self,
    ) -> None:
        first_validation = self.text.index(
            "\"${SSHD_BIN}\" -t || fail 'existing sshd configuration is invalid'"
        )
        account_mutation = self.text.index('groupadd --system "${FORWARD_GROUP}"')
        atomic_install = self.text.index(
            'mv -f -- "${SSHD_DROP_IN_TEMP}" "${SSHD_DROP_IN}"'
        )
        installed_validation = self.text.index(
            "\"${SSHD_BIN}\" -t || fail 'installed sshd drop-in failed syntax validation'"
        )
        pre_reload_validation = self.text.index(
            "\"${SSHD_BIN}\" -t || fail 'sshd configuration changed before reload'"
        )
        reload = self.text.index(
            'systemctl reload "${SSH_SERVICE}"', pre_reload_validation
        )

        self.assertLess(first_validation, account_mutation)
        self.assertLess(atomic_install, installed_validation)
        self.assertLess(installed_validation, pre_reload_validation)
        self.assertLess(pre_reload_validation, reload)
        self.assertGreaterEqual(self.text.count('"${SSHD_BIN}" -T'), 2)
        self.assertIn(
            '-C "user=${FORWARD_USER},host=localhost,addr=${SOURCE_ADDRESS}"',
            self.text,
        )
        self.assertIn("effective sshd policy is not", self.text)

    def test_failed_install_restores_keys_and_sshd_configuration(self) -> None:
        self.assertIn("INSTALL_COMMITTED=false", self.text)
        self.assertIn("SSHD_DROP_IN_REPLACED=false", self.text)
        self.assertIn("AUTHORIZED_KEYS_REPLACED=false", self.text)
        self.assertIn('mv -f -- "${SSHD_DROP_IN_BACKUP}" "${SSHD_DROP_IN}"', self.text)
        self.assertIn(
            'mv -f -- "${AUTHORIZED_KEYS_BACKUP}" "${AUTHORIZED_KEYS}"', self.text
        )
        self.assertIn("restored sshd configuration did not pass sshd -t", self.text)
        self.assertIn("could not reload the restored sshd configuration", self.text)
        self.assertIn("refusing to overwrite an unmanaged file", self.text)
        self.assertNotIn("set -x", self.text)


if __name__ == "__main__":
    unittest.main()
