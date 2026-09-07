"""Tests for the one-deployment production network compatibility bridge."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from scripts.lib import release_deployment_runtime as bridge


def completed(
    command: list[str], stdout: str, returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, "")


class LegacyProductionNetworkBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_identity_layout(self) -> tuple[Path, Path, dict[str, str]]:
        release = self.root / "release"
        deployment = self.root / "deployment"
        compose = release / "deploy/operations/docker-compose.production.yml"
        compose.parent.mkdir(parents=True)
        compose.write_text("networks: {}\n", encoding="utf-8")
        manifest = {
            "repository": "HoneyBury/boost_gateway",
            "platform": "linux-x64",
            "tag": bridge.LEGACY_TAG,
            "commit": bridge.LEGACY_COMMIT,
            "source_build_performed": False,
            "dependency_resolution_performed": False,
            "configuration": {"sha256": bridge.LEGACY_CONFIGURATION_SHA256},
            "deployment_controller": {"compose_sha256": "pending"},
        }
        compose_sha = hashlib.sha256(compose.read_bytes()).hexdigest()
        manifest["deployment_controller"]["compose_sha256"] = compose_sha
        manifest_path = release / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        deployment.mkdir()
        (deployment / "release").symlink_to(release)
        (deployment / "deploy").symlink_to("release/deploy")
        (deployment / "manifest.json").symlink_to("release/manifest.json")
        image_environment = deployment / "compose-images.env"
        image_environment.write_text("test-image-environment\n", encoding="utf-8")
        image_environment_sha = hashlib.sha256(
            image_environment.read_bytes()
        ).hexdigest()
        record = {
            "deployment_id": deployment.name,
            "deployment_path": str(deployment.resolve()),
            "release_path": str(release.resolve()),
            "tag": bridge.LEGACY_TAG,
            "commit": bridge.LEGACY_COMMIT,
            "configuration_sha256": bridge.LEGACY_CONFIGURATION_SHA256,
            "runtime_asset_sha256": bridge.LEGACY_RUNTIME_ASSET_SHA256,
            "image_environment_sha256": image_environment_sha,
            "manifest_sha256": manifest_sha,
            "status": "verified",
        }
        (deployment / "record.json").write_text(json.dumps(record), encoding="utf-8")
        return (
            deployment,
            compose.resolve(),
            {
                "LEGACY_DEPLOYMENT_ID": deployment.name,
                "LEGACY_COMPOSE_SHA256": compose_sha,
                "LEGACY_MANIFEST_SHA256": manifest_sha,
                "LEGACY_IMAGE_ENVIRONMENT_SHA256": image_environment_sha,
                "LEGACY_DEPLOYMENT_PATH": str(deployment.resolve()),
                "LEGACY_RELEASE_PATH": str(release.resolve()),
            },
        )

    def identity_patch(self, constants: dict[str, str]) -> Any:
        record_identity = {
            "deployment_id": constants["LEGACY_DEPLOYMENT_ID"],
            "deployment_path": constants["LEGACY_DEPLOYMENT_PATH"],
            "release_path": constants["LEGACY_RELEASE_PATH"],
            "tag": bridge.LEGACY_TAG,
            "commit": bridge.LEGACY_COMMIT,
            "configuration_sha256": bridge.LEGACY_CONFIGURATION_SHA256,
            "runtime_asset_sha256": bridge.LEGACY_RUNTIME_ASSET_SHA256,
            "image_environment_sha256": constants["LEGACY_IMAGE_ENVIRONMENT_SHA256"],
            "manifest_sha256": constants["LEGACY_MANIFEST_SHA256"],
        }
        return mock.patch.multiple(
            bridge,
            LEGACY_DEPLOYMENT_ID=constants["LEGACY_DEPLOYMENT_ID"],
            LEGACY_COMPOSE_SHA256=constants["LEGACY_COMPOSE_SHA256"],
            LEGACY_MANIFEST_SHA256=constants["LEGACY_MANIFEST_SHA256"],
            LEGACY_IMAGE_ENVIRONMENT_SHA256=constants[
                "LEGACY_IMAGE_ENVIRONMENT_SHA256"
            ],
            LEGACY_DEPLOYMENT_PATH=Path(constants["LEGACY_DEPLOYMENT_PATH"]),
            LEGACY_RELEASE_PATH=Path(constants["LEGACY_RELEASE_PATH"]),
            LEGACY_RECORD_IDENTITY=record_identity,
        )

    def test_identity_accepts_the_governed_manifest_symlink(self) -> None:
        deployment, compose, constants = self.make_identity_layout()
        with self.identity_patch(constants):
            identity = bridge._validate_identity(deployment.resolve(), compose)
        self.assertEqual(identity["deployment_id"], deployment.name)

    def test_identity_rejects_a_manifest_symlink_outside_the_release(self) -> None:
        deployment, compose, constants = self.make_identity_layout()
        replacement = self.root / "replacement.json"
        replacement.write_text("{}\n", encoding="utf-8")
        (deployment / "manifest.json").unlink()
        (deployment / "manifest.json").symlink_to(replacement)
        with (
            self.identity_patch(constants),
            self.assertRaisesRegex(RuntimeError, "symlink target drift"),
        ):
            bridge._validate_identity(deployment.resolve(), compose)

    def test_identity_rejects_image_environment_drift(self) -> None:
        deployment, compose, constants = self.make_identity_layout()
        (deployment / "compose-images.env").write_text("drift\n", encoding="utf-8")
        with (
            self.identity_patch(constants),
            self.assertRaisesRegex(RuntimeError, "image environment digest drift"),
        ):
            bridge._validate_identity(deployment.resolve(), compose)

    @staticmethod
    def runtime_documents() -> (
        tuple[list[str], list[dict[str, object]], dict[str, object]]
    ):
        names = sorted(bridge.REQUIRED_CONTAINER_NAMES)
        container_ids = [f"{index:064x}" for index in range(1, len(names) + 1)]
        network_id = "a" * 64
        containers = []
        network_containers = {}
        for index, (container_id, name) in enumerate(
            zip(container_ids, names), start=2
        ):
            containers.append(
                {
                    "Id": container_id,
                    "Name": f"/{name}",
                    "Config": {
                        "Labels": {
                            "com.docker.compose.project": "boost-gateway-production",
                            "com.docker.compose.service": bridge.REQUIRED_CONTAINER_SERVICES[
                                name
                            ],
                            "com.docker.compose.version": bridge.LEGACY_COMPOSE_VERSION,
                        }
                    },
                    "NetworkSettings": {
                        "Networks": {
                            bridge.NETWORK_NAME: {
                                "NetworkID": network_id,
                                "IPAddress": f"172.18.0.{index}",
                                "IPPrefixLen": 16,
                                "Gateway": bridge.NETWORK_GATEWAY,
                            }
                        }
                    },
                }
            )
            network_containers[container_id] = {
                "Name": name,
                "IPv4Address": f"172.18.0.{index}/16",
                "IPv6Address": "",
            }
        network = {
            "Name": bridge.NETWORK_NAME,
            "Id": network_id,
            "Driver": "bridge",
            "Scope": "local",
            "EnableIPv6": False,
            "Internal": False,
            "Attachable": False,
            "Ingress": False,
            "ConfigOnly": False,
            "Options": {},
            "Labels": {
                "com.docker.compose.config-hash": bridge.LEGACY_NETWORK_CONFIG_HASH,
                "com.docker.compose.network": "boost-net",
                "com.docker.compose.project": "boost-gateway-production",
                "com.docker.compose.version": bridge.LEGACY_COMPOSE_VERSION,
            },
            "IPAM": {
                "Driver": "default",
                "Options": None,
                "Config": [
                    {
                        "Subnet": bridge.NETWORK_SUBNET,
                        "IPRange": "",
                        "Gateway": bridge.NETWORK_GATEWAY,
                    }
                ],
            },
            "Containers": network_containers,
        }
        return container_ids, containers, network

    def test_bridge_binds_compose_container_ids_to_the_inspected_network(self) -> None:
        container_ids, containers, network = self.runtime_documents()

        def run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
            self.assertEqual(timeout, 30)
            if command[:2] == ["docker", "compose"]:
                return completed(command, "\n".join(container_ids) + "\n")
            if command[:2] == ["docker", "inspect"]:
                self.assertEqual(command[2:], container_ids)
                return completed(command, json.dumps(containers))
            self.assertEqual(command, ["docker", "network", "inspect", "a" * 64])
            return completed(command, json.dumps([network]))

        document = {
            "networks": {
                "boost-net": {
                    "name": bridge.NETWORK_NAME,
                    "driver": "bridge",
                    "ipam": {},
                }
            }
        }
        current = self.root / "current"
        current.symlink_to(self.root)
        with (
            mock.patch.object(bridge, "CURRENT_DEPLOYMENT_LINK", current),
            mock.patch.object(
                bridge, "_validate_identity", return_value={"tag": "v3.6.7"}
            ),
        ):
            evidence = bridge.validate_legacy_production_network_bridge(
                self.root.resolve(),
                self.root / "compose.yml",
                document,
                [bridge.NETWORK_CONTRACT_FAILURE],
                run,
            )
        self.assertEqual(evidence["runtime_network"]["container_count"], 13)
        self.assertEqual(evidence["runtime_network"]["network_id"], "a" * 64)

    def test_bridge_requires_the_canonical_current_symlink(self) -> None:
        current = self.root / "current"
        current.write_text("not-a-symlink\n", encoding="utf-8")
        with (
            mock.patch.object(bridge, "CURRENT_DEPLOYMENT_LINK", current),
            self.assertRaisesRegex(RuntimeError, "not the current deployment"),
        ):
            bridge.validate_legacy_production_network_bridge(
                self.root,
                self.root / "compose.yml",
                {},
                [bridge.NETWORK_CONTRACT_FAILURE],
                mock.Mock(),
            )

    def test_bridge_rejects_any_additional_contract_failure(self) -> None:
        current = self.root / "current"
        current.symlink_to(self.root)
        with (
            mock.patch.object(bridge, "CURRENT_DEPLOYMENT_LINK", current),
            self.assertRaisesRegex(RuntimeError, "contract failures"),
        ):
            bridge.validate_legacy_production_network_bridge(
                self.root.resolve(),
                self.root / "compose.yml",
                {},
                [bridge.NETWORK_CONTRACT_FAILURE, "redis: drift"],
                mock.Mock(),
            )

    def test_bridge_rejects_a_container_attached_to_another_network(self) -> None:
        container_ids, containers, _ = self.runtime_documents()
        containers[0]["NetworkSettings"]["Networks"][bridge.NETWORK_NAME][
            "NetworkID"
        ] = ("b" * 64)

        def run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
            if command[:2] == ["docker", "compose"]:
                return completed(command, "\n".join(container_ids) + "\n")
            return completed(command, json.dumps(containers))

        with self.assertRaisesRegex(RuntimeError, "network ID drift"):
            bridge._load_runtime_containers(run, self.root / "compose.yml")

    def test_bridge_rejects_container_service_label_drift(self) -> None:
        container_ids, containers, _ = self.runtime_documents()
        containers[0]["Config"]["Labels"]["com.docker.compose.service"] = "gateway"

        def run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
            if command[:2] == ["docker", "compose"]:
                return completed(command, "\n".join(container_ids) + "\n")
            return completed(command, json.dumps(containers))

        with self.assertRaisesRegex(RuntimeError, "container labels drift"):
            bridge._load_runtime_containers(run, self.root / "compose.yml")

    def test_bridge_rejects_a_container_using_the_gateway_address(self) -> None:
        container_ids, containers, _ = self.runtime_documents()
        containers[0]["NetworkSettings"]["Networks"][bridge.NETWORK_NAME][
            "IPAddress"
        ] = bridge.NETWORK_GATEWAY

        def run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
            if command[:2] == ["docker", "compose"]:
                return completed(command, "\n".join(container_ids) + "\n")
            return completed(command, json.dumps(containers))

        with self.assertRaisesRegex(RuntimeError, "container address drift"):
            bridge._load_runtime_containers(run, self.root / "compose.yml")

    def test_bridge_rejects_network_attachment_address_drift(self) -> None:
        container_ids, containers, network = self.runtime_documents()
        expected_attachments = {
            container["Id"]: {
                "Name": container["Name"].removeprefix("/"),
                "IPv4Address": (
                    f"{container['NetworkSettings']['Networks'][bridge.NETWORK_NAME]['IPAddress']}"
                    "/16"
                ),
                "IPv6Address": "",
            }
            for container in containers
        }
        first = container_ids[0]
        network["Containers"][first]["IPv4Address"] = "172.18.0.250/16"

        with self.assertRaisesRegex(RuntimeError, "runtime drift"):
            bridge._validate_runtime_network(
                network, "a" * 64, set(container_ids), expected_attachments
            )

    def test_bridge_evidence_rejects_ipv4_or_shape_drift(self) -> None:
        containers = [
            {
                "container_id": f"{index:064x}",
                "name": name,
                "ipv4_address": f"172.18.0.{index + 1}/16",
            }
            for index, name in enumerate(
                sorted(bridge.REQUIRED_CONTAINER_NAMES), start=1
            )
        ]
        evidence = {
            "accepted_contract_failures": [bridge.NETWORK_CONTRACT_FAILURE],
            "identity": {
                **bridge.LEGACY_RECORD_IDENTITY,
                "compose_sha256": bridge.LEGACY_COMPOSE_SHA256,
            },
            "resolved_network": bridge.LEGACY_RESOLVED_NETWORK,
            "runtime_network": {
                **bridge.LEGACY_RUNTIME_NETWORK_IDENTITY,
                "network_id": "a" * 64,
                "containers": containers,
            },
        }
        self.assertTrue(bridge.legacy_production_network_evidence_is_complete(evidence))
        containers[0]["ipv4_address"] = bridge.NETWORK_GATEWAY + "/16"
        self.assertFalse(
            bridge.legacy_production_network_evidence_is_complete(evidence)
        )
        containers[0]["unexpected"] = True
        self.assertFalse(
            bridge.legacy_production_network_evidence_is_complete(evidence)
        )


if __name__ == "__main__":
    unittest.main()
