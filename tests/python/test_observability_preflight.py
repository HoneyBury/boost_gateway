from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.tools import check_observability_preflight as preflight


NOW = datetime(2026, 7, 26, 3, 0, tzinfo=UTC)
HOST_ID = "a" * 64


class ObservabilityPreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "alertmanager.yml"
        self.env = self.root / "compose.env"
        self.attestation = self.root / "attestation.json"
        self.config.write_text(
            """global:
  resolve_timeout: 5m
route:
  receiver: operations-webhook
receivers:
  - name: operations-webhook
    webhook_configs:
      - url: https://alerts.internal.invalid/boost-gateway
        send_resolved: true
""",
            encoding="utf-8",
        )
        self.env.write_text(
            "GRAFANA_ADMIN_USER=operations-user\n"
            "GRAFANA_ADMIN_PASSWORD=unit-test-secret-value-1234\n",
            encoding="utf-8",
        )
        self._write_attestation()

    def _write_attestation(self, **overrides: object) -> None:
        value: dict[str, object] = {
            "schema_version": 1,
            "overall_pass": True,
            "receiver": "operations-webhook",
            "alertmanager_config_sha256": hashlib.sha256(
                self.config.read_bytes()
            ).hexdigest(),
            "host_id_sha256": HOST_ID,
            "tested_at": "2026-07-26T02:30:00Z",
            "firing_delivery": {
                "id": "delivery-firing-1",
                "observed_at": "2026-07-26T02:20:00Z",
            },
            "resolved_delivery": {
                "id": "delivery-resolved-1",
                "observed_at": "2026-07-26T02:25:00Z",
            },
        }
        value.update(overrides)
        self.attestation.write_text(json.dumps(value), encoding="utf-8")

    def _validate(self) -> dict[str, object]:
        return preflight.validate_preflight(
            self.config,
            self.env,
            self.attestation,
            current_time=NOW,
            enforce_ownership=False,
            config_validator=lambda _: None,
            identity_provider=lambda: {
                "host": {"host_id_sha256": HOST_ID},
                "operator": {"username": "tester", "uid": 1000},
            },
        )

    def test_accepts_bound_recent_firing_and_resolved_deliveries(self) -> None:
        summary = self._validate()

        self.assertTrue(summary["overall_pass"])
        self.assertEqual(summary["receiver"], "operations-webhook")
        self.assertFalse(summary["secret_material_recorded"])
        rendered = json.dumps(summary)
        self.assertNotIn("unit-test-secret", rendered)
        self.assertNotIn("alerts.internal", rendered)

    def test_rejects_placeholder_receiver(self) -> None:
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "operations-webhook", "default"
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(preflight.PreflightError, "placeholder receiver"):
            self._validate()

    def test_rejects_route_receiver_without_its_own_integration(self) -> None:
        self.config.write_text(
            """route:
  receiver: operations-empty
receivers:
  - name: operations-empty
  - name: unused-webhook
    webhook_configs:
      - url: https://alerts.internal.invalid/unused
""",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(preflight.PreflightError, "route receiver has no"):
            self._validate()

    def test_rejects_default_grafana_credentials(self) -> None:
        self.env.write_text(
            "GRAFANA_ADMIN_USER=admin\n"
            "GRAFANA_ADMIN_PASSWORD=boost-gateway-change-me\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(preflight.PreflightError, "username"):
            self._validate()

    def test_rejects_config_digest_drift(self) -> None:
        self._write_attestation(alertmanager_config_sha256="b" * 64)

        with self.assertRaisesRegex(preflight.PreflightError, "active config"):
            self._validate()

    def test_rejects_attestation_from_another_host(self) -> None:
        self._write_attestation(host_id_sha256="b" * 64)

        with self.assertRaisesRegex(preflight.PreflightError, "another host"):
            self._validate()

    def test_rejects_stale_delivery_attestation(self) -> None:
        stale = NOW - preflight.MAX_ATTESTATION_AGE - timedelta(seconds=1)
        self._write_attestation(
            tested_at=stale.isoformat().replace("+00:00", "Z"),
            firing_delivery={
                "id": "old-firing",
                "observed_at": (stale - timedelta(minutes=2)).isoformat().replace(
                    "+00:00", "Z"
                ),
            },
            resolved_delivery={
                "id": "old-resolved",
                "observed_at": (stale - timedelta(minutes=1)).isoformat().replace(
                    "+00:00", "Z"
                ),
            },
        )

        with self.assertRaisesRegex(preflight.PreflightError, "older than 7 days"):
            self._validate()

    def test_rejects_missing_resolved_delivery(self) -> None:
        self._write_attestation(resolved_delivery={})

        with self.assertRaisesRegex(preflight.PreflightError, "resolved notification"):
            self._validate()


if __name__ == "__main__":
    unittest.main()
