from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
