from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.tools import check_release_compose


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy/operations/docker-compose.production.yml"
IMAGE_ID = "sha256:" + ("a" * 64)


def service(*, ports: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "image": IMAGE_ID,
        "pull_policy": "never",
        "restart": "unless-stopped",
        "cpus": 1.0,
        "mem_limit": 512 * 1024 * 1024,
        "pids_limit": 256,
        "healthcheck": {
            "test": ["CMD", "true"],
            "interval": 10_000_000_000,
            "timeout": 3_000_000_000,
            "retries": 3,
        },
        "logging": {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "5"},
        },
        "volumes": [
            {"type": "volume", "source": "logs", "target": "/app/logs"}
        ],
        "ports": ports or [],
    }


def valid_document() -> dict[str, object]:
    services = {
        name: service()
        for name in sorted(check_release_compose.REQUIRED_RUNTIME_SERVICES)
    }
    services["gateway"] = service(
        ports=[
            {"host_ip": "0.0.0.0", "published": "9201", "target": 9201},
            {"host_ip": "127.0.0.1", "published": "9080", "target": 9080},
        ]
    )
    services["node-exporter"].update(
        {
            "image": check_release_compose.NODE_EXPORTER_IMAGE,
            "pid": "host",
            "command": [
                "--path.rootfs=/host",
                "--collector.hwmon",
                "--collector.thermal_zone",
                "--collector.systemd",
                "--collector.textfile.directory=/var/lib/node_exporter/textfile_collector",
            ],
            "volumes": [
                {
                    "type": "bind",
                    "source": "/",
                    "target": "/host",
                    "read_only": True,
                },
                {
                    "type": "bind",
                    "source": "/run/dbus/system_bus_socket",
                    "target": "/var/run/dbus/system_bus_socket",
                    "read_only": True,
                },
                {
                    "type": "bind",
                    "source": "/var/lib/boost-gateway-evidence/metrics",
                    "target": "/var/lib/node_exporter/textfile_collector",
                    "read_only": True,
                },
            ],
        }
    )
    services["cadvisor"].update(
        {
            "image": check_release_compose.CADVISOR_IMAGE,
            "privileged": True,
            "read_only": True,
            "devices": [
                {
                    "source": "/dev/kmsg",
                    "target": "/dev/kmsg",
                    "permissions": "rwm",
                }
            ],
            "tmpfs": ["/tmp"],
            "volumes": [
                {
                    "type": "bind",
                    "source": source,
                    "target": target,
                    "read_only": True,
                }
                for source, target in sorted(check_release_compose.CADVISOR_BINDS)
            ],
        }
    )
    services["prometheus"].update(
        {
            "command": [
                "--config.file=/etc/prometheus/prometheus.yml",
                "--storage.tsdb.path=/prometheus",
                "--storage.tsdb.retention.time=45d",
            ],
            "volumes": [
                {
                    "type": "volume",
                    "source": "prometheus-data",
                    "target": "/prometheus",
                }
            ],
        }
    )
    services["grafana"]["environment"] = {
        "GF_SECURITY_ADMIN_USER": "unit-test-operator",
        "GF_SECURITY_ADMIN_PASSWORD": "unit-test-only-secret-value",
    }
    services["alertmanager"]["volumes"] = [
        {
            "type": "bind",
            "source": "/etc/boost-gateway/alertmanager.yml",
            "target": "/etc/alertmanager/alertmanager.yml",
            "read_only": True,
        }
    ]
    return {
        "name": "test",
        "services": services,
        "volumes": {"logs": {}, "prometheus-data": {}},
    }


class ReleaseComposeContractTest(unittest.TestCase):
    def test_accepts_complete_immutable_contract(self) -> None:
        self.assertEqual(
            [], check_release_compose.validate_compose_document(valid_document())
        )

    def test_rejects_source_build_and_mutable_image(self) -> None:
        document = valid_document()
        gateway = document["services"]["gateway"]
        gateway["build"] = {"context": "."}
        gateway["image"] = "boost-gateway:latest"

        failures = check_release_compose.validate_compose_document(document)

        self.assertTrue(any("source build is forbidden" in item for item in failures))
        self.assertTrue(any("immutable sha256" in item for item in failures))

    def test_rejects_empty_build_key_and_unstructured_port(self) -> None:
        document = valid_document()
        document["services"]["redis"] = service()
        document["services"]["redis"]["build"] = {}
        document["services"]["redis"]["ports"] = ["6379:6379"]

        failures = check_release_compose.validate_compose_document(document)

        self.assertTrue(
            any("redis: source build is forbidden" in item for item in failures)
        )
        self.assertTrue(
            any("port mapping is not a structured object" in item for item in failures)
        )

    def test_rejects_public_internal_port(self) -> None:
        document = valid_document()
        document["services"]["login-backend"]["ports"] = [
            {"host_ip": "0.0.0.0", "published": "9202", "target": 9202}
        ]

        failures = check_release_compose.validate_compose_document(document)

        self.assertTrue(
            any("9202:9202 must bind to loopback" in item for item in failures)
        )

    def test_rejects_privileged_non_cadvisor_service(self) -> None:
        document = valid_document()
        document["services"]["redis"]["privileged"] = True

        failures = check_release_compose.validate_compose_document(document)

        self.assertIn("redis: privileged containers are forbidden", failures)

    def test_rejects_cadvisor_privilege_scope_drift(self) -> None:
        document = valid_document()
        document["services"]["cadvisor"]["volumes"].append(
            {
                "type": "bind",
                "source": "/etc",
                "target": "/host-etc",
                "read_only": True,
            }
        )

        failures = check_release_compose.validate_compose_document(document)

        self.assertTrue(any("exact read-only host metric binds" in item for item in failures))

    def test_rejects_short_prometheus_retention(self) -> None:
        document = valid_document()
        document["services"]["prometheus"]["command"][-1] = (
            "--storage.tsdb.retention.time=30d"
        )

        failures = check_release_compose.validate_compose_document(document)

        self.assertIn("prometheus: TSDB retention must be at least 45 days", failures)

    def test_rejects_default_grafana_credentials(self) -> None:
        document = valid_document()
        document["services"]["grafana"]["environment"] = {
            "GF_SECURITY_ADMIN_USER": "admin",
            "GF_SECURITY_ADMIN_PASSWORD": "boost-gateway-change-me",
        }

        failures = check_release_compose.validate_compose_document(document)

        self.assertTrue(any("default or empty admin username" in item for item in failures))
        self.assertTrue(any("default, empty, or short admin password" in item for item in failures))

    def test_rejects_missing_observability_collector(self) -> None:
        document = valid_document()
        del document["services"]["node-exporter"]

        failures = check_release_compose.validate_compose_document(document)

        self.assertTrue(any("missing project services: node-exporter" in item for item in failures))

    def test_rejects_missing_operational_limits(self) -> None:
        for field, expected in (
            ("cpus", "positive cpus"),
            ("mem_limit", "positive mem_limit"),
            ("pids_limit", "positive pids_limit"),
            ("healthcheck", "bounded healthcheck"),
            ("logging", "bounded json-file"),
            ("volumes", "named /app/logs"),
        ):
            with self.subTest(field=field):
                document = valid_document()
                del document["services"]["room-backend"][field]
                failures = check_release_compose.validate_compose_document(document)
                self.assertTrue(any(expected in item for item in failures), failures)

    def test_rejects_loopback_only_gateway_business_port(self) -> None:
        document = valid_document()
        document["services"]["gateway"]["ports"][0]["host_ip"] = "127.0.0.1"

        failures = check_release_compose.validate_compose_document(document)

        self.assertTrue(any("externally reachable" in item for item in failures))

    def test_runtime_dockerfiles_are_pinned_and_network_free(self) -> None:
        digest = (
            "ubuntu@sha256:"
            "4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90"
        )
        for name in ("Dockerfile.gateway", "Dockerfile.backend"):
            with self.subTest(name=name):
                text = (ROOT / "deploy/runtime" / name).read_text(encoding="utf-8")
                self.assertIn(f"FROM {digest}", text)
                self.assertNotIn("RUN ", text)
                self.assertNotIn("apt-get", text)
                self.assertNotIn("cmake", text.lower())
                self.assertNotIn("conan", text.lower())
                self.assertIn("org.opencontainers.image.revision", text)
                self.assertIn("io.boost-gateway.release.asset.sha256", text)
                self.assertIn("COPY manifest.json /app/release-manifest.json", text)

    def test_production_compose_requires_all_image_ids(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8")
        for variable in (
            "GATEWAY_IMAGE_ID",
            "LOGIN_IMAGE_ID",
            "ROOM_IMAGE_ID",
            "BATTLE_IMAGE_ID",
            "MATCHMAKING_IMAGE_ID",
            "LEADERBOARD_IMAGE_ID",
        ):
            self.assertIn("${" + variable + ":?", text)
        self.assertNotIn("build:", text)

    def test_redis_has_only_the_volume_initialization_capabilities(self) -> None:
        environment = os.environ.copy()
        for variable in (
            "GATEWAY_IMAGE_ID",
            "LOGIN_IMAGE_ID",
            "ROOM_IMAGE_ID",
            "BATTLE_IMAGE_ID",
            "MATCHMAKING_IMAGE_ID",
            "LEADERBOARD_IMAGE_ID",
        ):
            environment[variable] = IMAGE_ID
        environment["GRAFANA_ADMIN_PASSWORD"] = "unit-test-only-secret-value"
        environment["GRAFANA_ADMIN_USER"] = "unit-test-operator"
        document = check_release_compose.load_compose_document(
            COMPOSE, environment=environment
        )
        redis = document["services"]["redis"]
        self.assertEqual(set(redis.get("cap_add", [])), {"CHOWN", "SETGID", "SETUID"})
        self.assertEqual(redis.get("cap_drop"), ["ALL"])

    def test_real_compose_resolution_satisfies_contract(self) -> None:
        if shutil.which("docker") is None:
            self.skipTest("Docker CLI is unavailable")
        version = subprocess.run(
            ["docker", "compose", "version"],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if version.returncode:
            self.skipTest("Docker Compose is unavailable")
        environment = os.environ.copy()
        for variable in (
            "GATEWAY_IMAGE_ID",
            "LOGIN_IMAGE_ID",
            "ROOM_IMAGE_ID",
            "BATTLE_IMAGE_ID",
            "MATCHMAKING_IMAGE_ID",
            "LEADERBOARD_IMAGE_ID",
        ):
            environment[variable] = IMAGE_ID
        environment["GRAFANA_ADMIN_PASSWORD"] = "unit-test-only-secret-value"
        environment["GRAFANA_ADMIN_USER"] = "unit-test-operator"

        document = check_release_compose.load_compose_document(
            COMPOSE, environment=environment
        )

        self.assertEqual(
            [], check_release_compose.validate_compose_document(document)
        )


if __name__ == "__main__":
    unittest.main()
