"""Unit tests for deployed release topology and full-flow verification helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock

from scripts.tools import verify_release_deployment as module


class VerifyReleaseDeploymentTest(unittest.TestCase):
    @mock.patch.object(module, "load_http_json")
    def test_json_validation_retries_until_semantics_pass(
        self, load_http_json: mock.Mock
    ) -> None:
        load_http_json.side_effect = [
            {"ready": False},
            {"status": "pass", "ready": True, "checks": [{"status": "pass"}]},
        ]
        passed, detail = module.wait_valid_json(
            "http://127.0.0.1/ready",
            1,
            module.validate_gateway_ready,
            retry_seconds=0,
        )
        self.assertTrue(passed)
        self.assertEqual(detail, "validated")
        self.assertEqual(load_http_json.call_count, 2)

    def test_gateway_readiness_requires_pass_and_ready(self) -> None:
        document = {"status": "pass", "ready": True, "checks": [{"status": "pass"}]}
        self.assertEqual(module.validate_gateway_ready(document), [])
        document["ready"] = False
        self.assertTrue(module.validate_gateway_ready(document))

    def test_prometheus_targets_require_all_jobs_up(self) -> None:
        document = {
            "status": "success",
            "data": {
                "activeTargets": [
                    {"labels": {"job": job}, "health": "up", "lastError": ""}
                    for job in module.REQUIRED_PROMETHEUS_JOBS
                ]
            },
        }
        self.assertEqual(module.validate_prometheus_targets(document), [])
        document["data"]["activeTargets"][0]["health"] = "down"
        self.assertTrue(module.validate_prometheus_targets(document))

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

    def test_load_expected_images_maps_immutable_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = module.Path(temporary) / "images.env"
            path.write_text(
                "".join(
                    f"{variable}=sha256:{index:064x}\n"
                    for index, variable in enumerate(
                        module.IMAGE_ENV_BY_SERVICE.values(), start=1
                    )
                ),
                encoding="utf-8",
            )
            expected = module.load_expected_images(path)
        self.assertEqual(set(expected), set(module.IMAGE_ENV_BY_SERVICE))
        self.assertTrue(all(value.startswith("sha256:") for value in expected.values()))


if __name__ == "__main__":
    unittest.main()
