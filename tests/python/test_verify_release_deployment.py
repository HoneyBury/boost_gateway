"""Unit tests for deployed release topology and full-flow verification helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock

from scripts.tools import verify_release_deployment as module


class VerifyReleaseDeploymentTest(unittest.TestCase):
    def test_legacy_redis_bridge_accepts_only_exact_rdb_contract(self) -> None:
        document = {
            "services": {
                "redis": {
                    "image": "redis:7-alpine",
                    "command": ["redis-server", "--appendonly", "no"],
                    "cap_add": ["CHOWN", "SETGID", "SETUID"],
                    "cap_drop": ["ALL"],
                }
            }
        }
        failures = sorted(module.LEGACY_REDIS_CONTRACT_FAILURES)

        self.assertTrue(
            module.validate_legacy_redis_hardening_bridge(document, failures)
        )
        self.assertFalse(
            module.validate_legacy_redis_hardening_bridge(
                document, [*failures, "redis: unexpected drift"]
            )
        )
        document["services"]["redis"]["command"] = [
            "redis-server",
            "--appendonly",
            "yes",
        ]
        self.assertFalse(
            module.validate_legacy_redis_hardening_bridge(document, failures)
        )

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

    def test_prometheus_metric_inventory_requires_every_signal_family(self) -> None:
        metrics = sorted(
            module.REQUIRED_PROMETHEUS_METRICS
            | {
                "node_hwmon_temp_celsius",
                "gateway_backend_login_requests_total",
                "gateway_backend_login_errors_total",
                "gateway_backend_login_p99_latency_us",
            }
        )
        document = {"status": "success", "data": metrics}
        self.assertEqual(module.validate_prometheus_metric_inventory(document), [])
        document["data"].remove("node_hwmon_temp_celsius")
        self.assertIn(
            "Prometheus has no host thermal samples",
            module.validate_prometheus_metric_inventory(document),
        )

    def test_aof_metric_inventory_is_an_explicit_additional_gate(self) -> None:
        metrics = sorted(
            module.REQUIRED_PROMETHEUS_METRICS
            | module.AOF_REQUIRED_PROMETHEUS_METRICS
            | {
                "node_hwmon_temp_celsius",
                "gateway_backend_login_requests_total",
                "gateway_backend_login_errors_total",
                "gateway_backend_login_p99_latency_us",
            }
        )
        document = {"status": "success", "data": metrics}

        self.assertEqual(
            module.validate_prometheus_metric_inventory(
                document,
                module.REQUIRED_PROMETHEUS_METRICS
                | module.AOF_REQUIRED_PROMETHEUS_METRICS,
            ),
            [],
        )
        document["data"].remove("boost_gateway_redis_aof_enabled")
        self.assertTrue(
            module.validate_prometheus_metric_inventory(
                document,
                module.REQUIRED_PROMETHEUS_METRICS
                | module.AOF_REQUIRED_PROMETHEUS_METRICS,
            )
        )

    def test_prometheus_flags_require_45_day_retention(self) -> None:
        self.assertEqual(
            module.validate_prometheus_flags(
                {
                    "status": "success",
                    "data": {"storage.tsdb.retention.time": "45d"},
                }
            ),
            [],
        )
        self.assertTrue(
            module.validate_prometheus_flags(
                {
                    "status": "success",
                    "data": {"storage.tsdb.retention.time": "30d"},
                }
            )
        )

    def test_prometheus_rules_require_complete_healthy_inventory(self) -> None:
        document = {
            "status": "success",
            "data": {
                "groups": [
                    {
                        "rules": [
                            {"name": name, "health": "ok", "lastError": ""}
                            for name in module.REQUIRED_ALERT_RULES
                        ]
                    }
                ]
            },
        }

        self.assertEqual(module.validate_prometheus_rules(document), [])
        document["data"]["groups"][0]["rules"][0]["health"] = "err"
        document["data"]["groups"][0]["rules"][0]["lastError"] = "parse error"
        self.assertTrue(module.validate_prometheus_rules(document))

    @mock.patch.object(module, "run")
    def test_aof_runtime_requires_exact_config_info_and_manifest(
        self, run: mock.Mock
    ) -> None:
        expected = {
            "appendonly": "yes",
            "appendfsync": "everysec",
            "no-appendfsync-on-rewrite": "no",
            "aof-load-truncated": "no",
            "aof-use-rdb-preamble": "yes",
            "maxmemory-policy": "noeviction",
            "dir": "/data",
            "save": "300 100 60 10000",
            "stop-writes-on-bgsave-error": "yes",
        }
        run.side_effect = [
            mock.Mock(
                returncode=0,
                stdout="\n".join(item for pair in expected.items() for item in pair)
                + "\n",
                stderr="",
            ),
            mock.Mock(
                returncode=0,
                stdout=(
                    "aof_enabled:1\n"
                    "aof_delayed_fsync:0\n"
                    "aof_last_write_status:ok\n"
                    "aof_last_bgrewrite_status:ok\n"
                    "rdb_last_bgsave_status:ok\n"
                ),
                stderr="",
            ),
            mock.Mock(returncode=0, stdout="", stderr=""),
        ]

        passed, detail = module.validate_redis_aof_runtime(
            ["docker", "compose", "-f", "/release/compose.yml"]
        )

        self.assertTrue(passed, detail)
        self.assertIn('"aof_manifest_present": true', detail)
        manifest_command = run.call_args_list[2].args[0]
        self.assertEqual(
            manifest_command[
                manifest_command.index("exec") : manifest_command.index("sh")
            ],
            ["exec", "-T", "--user", "redis", "redis"],
        )

    @mock.patch.object(module, "run")
    def test_aof_runtime_preserves_manifest_check_failure_detail(
        self, run: mock.Mock
    ) -> None:
        expected = {
            "appendonly": "yes",
            "appendfsync": "everysec",
            "no-appendfsync-on-rewrite": "no",
            "aof-load-truncated": "no",
            "aof-use-rdb-preamble": "yes",
            "maxmemory-policy": "noeviction",
            "dir": "/data",
            "save": "300 100 60 10000",
            "stop-writes-on-bgsave-error": "yes",
        }
        run.side_effect = [
            mock.Mock(
                returncode=0,
                stdout="\n".join(item for pair in expected.items() for item in pair)
                + "\n",
                stderr="",
            ),
            mock.Mock(
                returncode=0,
                stdout=(
                    "aof_enabled:1\n"
                    "aof_delayed_fsync:0\n"
                    "aof_last_write_status:ok\n"
                    "aof_last_bgrewrite_status:ok\n"
                    "rdb_last_bgsave_status:ok\n"
                ),
                stderr="",
            ),
            mock.Mock(
                returncode=1,
                stdout="",
                stderr="test: appendonly.aof.manifest: Permission denied\n",
            ),
        ]

        passed, detail = module.validate_redis_aof_runtime(["docker", "compose"])

        self.assertFalse(passed)
        parsed = json.loads(detail)
        self.assertFalse(parsed["aof_manifest_present"])
        self.assertEqual(parsed["aof_manifest_check"]["exit_code"], 1)
        self.assertIn("Permission denied", parsed["aof_manifest_check"]["stderr_tail"])

    @mock.patch.object(module, "run")
    def test_aof_runtime_rejects_delayed_fsync(self, run: mock.Mock) -> None:
        expected = {
            "appendonly": "yes",
            "appendfsync": "everysec",
            "no-appendfsync-on-rewrite": "no",
            "aof-load-truncated": "no",
            "aof-use-rdb-preamble": "yes",
            "maxmemory-policy": "noeviction",
            "dir": "/data",
            "save": "300 100 60 10000",
            "stop-writes-on-bgsave-error": "yes",
        }
        run.side_effect = [
            mock.Mock(
                returncode=0,
                stdout="\n".join(item for pair in expected.items() for item in pair)
                + "\n",
                stderr="",
            ),
            mock.Mock(
                returncode=0,
                stdout=(
                    "aof_enabled:1\n"
                    "aof_delayed_fsync:1\n"
                    "aof_last_write_status:ok\n"
                    "aof_last_bgrewrite_status:ok\n"
                    "rdb_last_bgsave_status:ok\n"
                ),
                stderr="",
            ),
            mock.Mock(returncode=0, stdout="", stderr=""),
        ]

        passed, detail = module.validate_redis_aof_runtime(["docker", "compose"])

        self.assertFalse(passed)
        self.assertIn("aof_delayed_fsync", detail)

    def test_governed_container_query_requires_exact_inventory(self) -> None:
        document = {
            "status": "success",
            "data": {
                "result": [
                    {"metric": {"container": name}, "value": [1, "1"]}
                    for name in module.REQUIRED_CONTAINER_NAMES
                ]
            },
        }

        self.assertEqual(module.validate_governed_container_query(document), [])
        document["data"]["result"].pop()
        self.assertTrue(module.validate_governed_container_query(document))

    def test_prometheus_nonempty_query_rejects_empty_vector(self) -> None:
        self.assertEqual(
            module.validate_prometheus_nonempty_query(
                {"status": "success", "data": {"result": [{"value": [1, "1"]}]}}
            ),
            [],
        )
        self.assertTrue(
            module.validate_prometheus_nonempty_query(
                {"status": "success", "data": {"result": []}}
            )
        )

    def test_parse_compose_ps_accepts_array_and_json_lines(self) -> None:
        items = [{"Service": "gateway", "State": "running", "Health": "healthy"}]
        self.assertEqual(module.parse_compose_ps(json.dumps(items)), items)
        self.assertEqual(
            module.parse_compose_ps("\n".join(map(json.dumps, items))), items
        )

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
