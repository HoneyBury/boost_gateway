from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts/gates/infrastructure/apply_operations_host_baseline.py"
)
SPEC = importlib.util.spec_from_file_location("apply_operations_host_baseline", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class OperationsHostBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[2]
        cls.policy = json.loads(
            (cls.root / "deploy/operations/operations-host-policy.json").read_text(
                encoding="utf-8"
            )
        )
        cls.cloud_policy = json.loads(
            (cls.root / "deploy/cloud/cloud-operations-host-policy.json").read_text(
                encoding="utf-8"
            )
        )

    def test_docker_merge_preserves_unrelated_configuration(self) -> None:
        source = {
            "features": {"containerd-snapshotter": True},
            "proxies": {"http-proxy": "http://127.0.0.1:7890"},
            "log-opts": {"labels": "service"},
        }
        merged = MODULE.merge_docker_config(source)

        self.assertEqual(source["features"], merged["features"])
        self.assertEqual(source["proxies"], merged["proxies"])
        self.assertEqual("service", merged["log-opts"]["labels"])
        self.assertEqual("10m", merged["log-opts"]["max-size"])
        self.assertEqual("5", merged["log-opts"]["max-file"])
        self.assertEqual("json-file", merged["log-driver"])

    def test_ufw_plan_allows_gateway_and_tailscale_ssh_only(self) -> None:
        commands = MODULE.ufw_commands(self.policy)
        rendered = [" ".join(command) for command in commands]

        self.assertIn("ufw default deny incoming", rendered)
        self.assertIn("ufw allow 9201/tcp", rendered)
        self.assertIn("ufw allow from 100.64.0.0/10 to any port 22 proto tcp", rendered)
        self.assertIn(
            "ufw allow from fd7a:115c:a1e0::/48 to any port 22 proto tcp",
            rendered,
        )
        self.assertNotIn("ufw allow 22/tcp", rendered)

    def test_plan_does_not_include_application_deployment(self) -> None:
        actions = MODULE.plan(self.policy, restart_docker=True)
        serialized = json.dumps(actions)

        self.assertNotIn("docker compose up", serialized)
        self.assertNotIn("conan", serialized.lower())
        self.assertNotIn("cmake", serialized.lower())

    def test_cloud_plan_never_opens_gateway_to_everywhere(self) -> None:
        rendered = [" ".join(command) for command in MODULE.ufw_commands(self.cloud_policy)]

        self.assertNotIn("ufw allow 9201/tcp", rendered)
        self.assertNotIn("ufw allow 22/tcp", rendered)

    def test_apply_installs_the_selected_policy(self) -> None:
        selected = self.root / "deploy/cloud/cloud-operations-host-policy.json"
        actions: list[dict[str, object]] = []
        with (
            mock.patch.object(MODULE.sys, "platform", "linux"),
            mock.patch.object(MODULE.os, "geteuid", return_value=0),
            mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/tool"),
            mock.patch.object(MODULE, "ensure_service_identity"),
            mock.patch.object(MODULE, "ensure_directories"),
            mock.patch.object(MODULE, "install_governed_file") as install_file,
            mock.patch.object(MODULE, "atomic_write"),
            mock.patch.object(MODULE, "run"),
            mock.patch.object(MODULE, "configure_docker_logging", return_value=False),
        ):
            MODULE.apply(self.policy, selected, False, actions)

        self.assertEqual(selected, install_file.call_args_list[0].args[0])
        self.assertEqual(
            Path("/etc/boost-gateway/operations-host-policy.json"),
            install_file.call_args_list[0].args[1],
        )


if __name__ == "__main__":
    unittest.main()
