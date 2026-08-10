from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class SmtpProxyHostUnitsTest(unittest.TestCase):
    def test_socket_is_safe_until_installer_binds_the_production_bridge(self) -> None:
        text = (ROOT / "deploy/systemd/boost-gateway-smtp-proxy.socket").read_text(
            encoding="utf-8"
        )

        self.assertIn("ListenStream=127.0.0.1:1587", text)
        self.assertIn("Accept=yes", text)
        self.assertIn("MaxConnections=32", text)

    def test_connection_service_only_reaches_the_loopback_connect_proxy(self) -> None:
        text = (ROOT / "deploy/systemd/boost-gateway-smtp-proxy@.service").read_text(
            encoding="utf-8"
        )

        self.assertIn("EnvironmentFile=/etc/boost-gateway/smtp-proxy.env", text)
        self.assertIn("-X connect", text)
        self.assertIn("StandardInput=socket", text)
        self.assertIn("StandardOutput=socket", text)
        self.assertIn("IPAddressDeny=any", text)
        self.assertIn("IPAddressAllow=localhost", text)
        self.assertIn("DynamicUser=yes", text)
        self.assertNotIn("User=nobody", text)
        self.assertNotIn("EnvironmentFile=-", text)

    def test_installer_discovers_and_limits_the_production_bridge(self) -> None:
        text = (ROOT / "deploy/operations/install_smtp_proxy_host_units.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("docker inspect", text)
        self.assertIn("value.is_private", text)
        self.assertIn("printf 'ListenStream=%s:%s", text)
        self.assertIn("openssl s_client", text)
        self.assertIn('protocol": "http-connect', text)
        self.assertNotIn("set -x", text)

    def test_activation_reuses_the_existing_secret_and_recreates_only_alertmanager(
        self,
    ) -> None:
        text = (ROOT / "deploy/operations/switch_alertmanager_smtp_relay.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("smtp_smarthost: smtp.gmail.com:587", text)
        self.assertIn("--no-deps --force-recreate", text)
        self.assertIn("alertmanager-secrets:/etc/alertmanager/secrets:ro", text)
        self.assertNotIn("gmail-app-password", text)
        self.assertNotIn("set -x", text)


if __name__ == "__main__":
    unittest.main()
