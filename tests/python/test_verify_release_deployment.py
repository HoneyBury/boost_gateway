"""Unit tests for deployed release topology and full-flow verification helpers."""

from __future__ import annotations

import json
import unittest

from scripts.tools import verify_release_deployment as module


class VerifyReleaseDeploymentTest(unittest.TestCase):
    def test_parse_compose_ps_accepts_array_and_json_lines(self) -> None:
        items = [{"Service": "gateway", "State": "running", "Health": "healthy"}]
        self.assertEqual(module.parse_compose_ps(json.dumps(items)), items)
        self.assertEqual(module.parse_compose_ps("\n".join(map(json.dumps, items))), items)

    def test_service_state_accepts_complete_healthy_topology(self) -> None:
        items = [
            {"Service": name, "State": "running", "Health": "healthy"}
            for name in module.REQUIRED_SERVICES
        ]
        self.assertEqual(module.verify_service_state(items), [])

    def test_service_state_rejects_missing_unhealthy_or_stopped(self) -> None:
        items = [
            {"Service": name, "State": "running", "Health": "healthy"}
            for name in module.REQUIRED_SERVICES
            if name != "grafana"
        ]
        items[0]["State"] = "exited"
        items[1]["Health"] = "starting"
        failures = module.verify_service_state(items)
        self.assertTrue(any("missing" in item for item in failures))
        self.assertTrue(any("not running" in item for item in failures))
        self.assertTrue(any("not healthy" in item for item in failures))


if __name__ == "__main__":
    unittest.main()
