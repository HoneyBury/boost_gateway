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
        self.assertIn("FreeBind=true", text)
        self.assertIn("Wants=network-online.target docker.service mihomo.service", text)
        self.assertIn("After=network-online.target docker.service mihomo.service", text)
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
        self.assertIn("DynamicUser=yes", text)
        self.assertNotIn("User=nobody", text)
        self.assertNotIn("IPAddressDeny", text)
        self.assertNotIn("IPAddressAllow", text)
        self.assertNotIn("EnvironmentFile=-", text)

    def test_installer_discovers_and_limits_the_production_bridge(self) -> None:
        text = (ROOT / "deploy/operations/install_smtp_proxy_host_units.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("docker inspect", text)
        self.assertIn("docker network inspect", text)
        self.assertIn("value.is_private", text)
        self.assertIn("value not in network", text)
        self.assertIn("BOOST_GATEWAY_SMTP_RELAY_ADDITIONAL_CLIENTS", text)
        self.assertIn("CLIENT_CONTAINERS", text)
        self.assertIn("FreeBind=true", text)
        self.assertIn("printf 'ListenStream=%s:%s", text)
        self.assertIn('ufw allow in on "${BRIDGE_NAMES[index]}"', text)
        self.assertIn('from "${NETWORK_SUBNETS[index]}"', text)
        self.assertIn('to "${RELAY_HOSTS[index]}"', text)
        self.assertIn("openssl s_client", text)
        self.assertIn('protocol": "http-connect', text)
        self.assertIn('"clients"', text)
        self.assertIn("boost-gateway-smtp-proxy-health.timer", text)
        self.assertNotIn("set -x", text)

    def test_health_timer_rechecks_the_primary_relay(self) -> None:
        service = (
            ROOT / "deploy/systemd/boost-gateway-smtp-proxy-health.service"
        ).read_text(encoding="utf-8")
        timer = (
            ROOT / "deploy/systemd/boost-gateway-smtp-proxy-health.timer"
        ).read_text(encoding="utf-8")

        self.assertIn("Requires=boost-gateway-smtp-proxy.socket", service)
        self.assertIn("EnvironmentFile=/etc/boost-gateway/smtp-proxy.env", service)
        self.assertIn("openssl s_client -starttls smtp", service)
        self.assertIn("-servername ${SMTP_HOST}", service)
        self.assertIn("OnUnitActiveSec=5min", timer)
        self.assertIn("Persistent=true", timer)

    def test_activation_reuses_the_existing_secret_and_recreates_only_alertmanager(
        self,
    ) -> None:
        text = (ROOT / "deploy/operations/switch_alertmanager_smtp_relay.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("smtp_smarthost: smtp.gmail.com:587", text)
        self.assertIn("SMTP_HOST=$(read_env_value SMTP_HOST)", text)
        self.assertIn("server_name: {smtp_host}", text)
        self.assertIn("smtp_tls_server_name", text)
        self.assertIn("--no-deps --force-recreate", text)
        self.assertIn("alertmanager-secrets:/etc/alertmanager/secrets:ro", text)
        self.assertNotIn("gmail-app-password", text)
        self.assertNotIn("set -x", text)


if __name__ == "__main__":
    unittest.main()
