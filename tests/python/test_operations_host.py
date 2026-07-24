from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts/gates/infrastructure/check_operations_host.py"
)
SPEC = importlib.util.spec_from_file_location("check_operations_host", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


NETWORK_POLICY = {
    "public_tcp_ports": [9201],
    "restricted_tcp_ports": [22, 9080, 9090, 6379],
    "trusted_cidrs": [
        "10.0.0.0/8",
        "100.64.0.0/10",
        "192.168.0.0/16",
        "fd7a:115c:a1e0::/48",
    ],
    "firewall_protected_tcp_ports": [22],
    "required_trusted_tcp_ports": [22],
    "required_public_listener": 9201,
}


class OperationsHostParserTests(unittest.TestCase):
    def test_parses_host_facts(self) -> None:
        self.assertEqual(
            {"ID": "ubuntu", "VERSION_ID": "24.04"},
            MODULE.parse_os_release('ID=ubuntu\nVERSION_ID="24.04"\n'),
        )
        self.assertEqual(16 * 1024, MODULE.parse_meminfo("MemTotal:       16 kB\n"))
        self.assertEqual(28, MODULE.parse_version_major("28.3.2"))
        self.assertEqual(
            2, MODULE.parse_version_major("Docker Compose version v2.27.0")
        )

    def test_parses_ipv4_and_ipv6_listeners(self) -> None:
        listeners = MODULE.parse_ss_listeners(
            "LISTEN 0 4096 0.0.0.0:9201 0.0.0.0:*\n" "LISTEN 0 4096 [::1]:9080 [::]:*\n"
        )
        self.assertEqual(
            [{"host": "0.0.0.0", "port": 9201}, {"host": "::1", "port": 9080}],
            listeners,
        )


class OperationsHostNetworkPolicyTests(unittest.TestCase):
    def test_accepts_only_gateway_public_and_management_trusted(self) -> None:
        passed, evaluated, errors = MODULE.evaluate_listener_boundary(
            [
                {"host": "0.0.0.0", "port": 9201},
                {"host": "127.0.0.1", "port": 9080},
                {"host": "10.4.5.6", "port": 22},
            ],
            NETWORK_POLICY,
        )
        self.assertTrue(passed)
        self.assertEqual([], errors)
        self.assertEqual(
            ["wildcard", "loopback", "trusted"], [row["scope"] for row in evaluated]
        )

    def test_accepts_wildcard_ssh_when_firewall_is_the_trust_boundary(self) -> None:
        passed, _, errors = MODULE.evaluate_listener_boundary(
            [
                {"host": "0.0.0.0", "port": 9201},
                {"host": "0.0.0.0", "port": 22},
                {"host": "fd7a:115c:a1e0::bb3a:4776", "port": 46239},
            ],
            NETWORK_POLICY,
        )
        self.assertTrue(passed)
        self.assertEqual([], errors)

    def test_rejects_management_wildcard_and_unknown_listener(self) -> None:
        passed, _, errors = MODULE.evaluate_listener_boundary(
            [
                {"host": "0.0.0.0", "port": 9201},
                {"host": "0.0.0.0", "port": 9080},
                {"host": "public.example", "port": 9443},
            ],
            NETWORK_POLICY,
        )
        self.assertFalse(passed)
        self.assertGreaterEqual(len(errors), 3)

    def test_rejects_missing_gateway_listener(self) -> None:
        passed, _, errors = MODULE.evaluate_listener_boundary(
            [{"host": "127.0.0.1", "port": 9080}], NETWORK_POLICY
        )
        self.assertFalse(passed)
        self.assertIn("required public gateway listener TCP 9201 is absent", errors)

    def test_initial_admission_does_not_require_deployed_gateway(self) -> None:
        passed, _, errors = MODULE.evaluate_listener_boundary(
            [{"host": "127.0.0.1", "port": 9080}],
            NETWORK_POLICY,
            require_public_listener=False,
        )
        self.assertTrue(passed)
        self.assertEqual([], errors)

    def test_accepts_default_deny_gateway_and_trusted_ssh(self) -> None:
        passed, errors = MODULE.evaluate_ufw_policy(
            "Status: active\nDefault: deny (incoming), allow (outgoing)\n"
            "9201/tcp ALLOW IN Anywhere\n"
            "22/tcp ALLOW IN 10.0.0.0/8\n"
            "22/tcp ALLOW IN fd7a:115c:a1e0::/48\n"
            "9201/tcp (v6) ALLOW IN Anywhere (v6)\n",
            NETWORK_POLICY,
        )
        self.assertTrue(passed)
        self.assertEqual([], errors)

    def test_rejects_world_access_to_management(self) -> None:
        passed, errors = MODULE.evaluate_ufw_policy(
            "Status: active\nDefault: deny (incoming), allow (outgoing)\n"
            "9201/tcp ALLOW IN Anywhere\n"
            "22/tcp ALLOW IN Anywhere\n",
            NETWORK_POLICY,
        )
        self.assertFalse(passed)
        self.assertIn("UFW allows non-gateway TCP 22 from Anywhere", errors)

    def test_rejects_firewall_without_trusted_ssh_rule(self) -> None:
        passed, errors = MODULE.evaluate_ufw_policy(
            "Status: active\nDefault: deny (incoming), allow (outgoing)\n"
            "9201/tcp ALLOW IN Anywhere\n",
            NETWORK_POLICY,
        )
        self.assertFalse(passed)
        self.assertIn("UFW lacks trusted-network allow rules for TCP ports 22", errors)


class OperationsHostRebootEvidenceTests(unittest.TestCase):
    def test_machine_id_hash_matches_sha256sum_file_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "machine-id"
            path.write_bytes(b"host-machine-id\n")
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                MODULE.machine_id_sha256(path),
            )

    def test_requires_same_host_and_changed_boot_id(self) -> None:
        marker = {
            "schema_version": 1,
            "host_id_sha256": "host-a",
            "boot_id_before": "boot-a",
        }
        self.assertTrue(MODULE.evaluate_reboot_marker(marker, "host-a", "boot-b"))
        self.assertFalse(MODULE.evaluate_reboot_marker(marker, "host-b", "boot-b"))
        self.assertFalse(MODULE.evaluate_reboot_marker(marker, "host-a", "boot-a"))
        marker["boot_id_after"] = "boot-b"
        self.assertTrue(MODULE.evaluate_reboot_marker(marker, "host-a", "boot-b"))
        self.assertFalse(MODULE.evaluate_reboot_marker(marker, "host-a", "boot-c"))

    def test_atomic_summary_replaces_stale_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "summary.json"
            path.write_text("stale", encoding="utf-8")
            MODULE.atomic_write_json(path, {"overall_pass": False})
            self.assertEqual(
                {"overall_pass": False},
                MODULE.load_json(path),
            )

    def test_failed_initialization_summary_tolerates_missing_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = root / "missing-policy.json"
            summary_path = root / "failed-summary.json"
            report = MODULE.Report()
            report.add("admission:initialization", False, "missing policy")
            summary = MODULE.write_summary(
                summary_path,
                "admit",
                policy,
                report,
                "",
                "",
            )
            self.assertFalse(summary["overall_pass"])
            self.assertEqual("", summary["policy"]["sha256"])

    def test_smartctl_scan_arguments_keep_device_last(self) -> None:
        self.assertEqual(
            ["smartctl", "-H", "-j", "-d", "sat", "/dev/sda"],
            MODULE.smartctl_health_command(["/dev/sda", "-d", "sat"]),
        )


class OperationsHostRepositoryContractTests(unittest.TestCase):
    def test_policy_matches_compose_and_systemd_boundaries(self) -> None:
        root = Path(__file__).resolve().parents[2]
        policy = MODULE.load_json(
            root / "deploy/operations/operations-host-policy.json"
        )
        compose = (root / "env/docker/docker-compose.yml").read_text(encoding="utf-8")
        unit = (root / "deploy/systemd/boost-gateway-compose.service").read_text(
            encoding="utf-8"
        )

        self.assertEqual([9201], policy["network"]["public_tcp_ports"])
        self.assertIn("${GATEWAY_HOST_BIND:-0.0.0.0}:9201:9201", compose)
        for token in [
            "${MANAGEMENT_HOST_BIND:-127.0.0.1}:9080:9080",
            "${BACKEND_HOST_BIND:-127.0.0.1}:9202:9202",
            "${REDIS_HOST_BIND:-127.0.0.1}:${REDIS_HOST_PORT:-6380}:6379",
            "${PROMETHEUS_HOST_BIND:-127.0.0.1}:9090:9090",
            "${ALERTMANAGER_HOST_BIND:-127.0.0.1}:9093:9093",
            "${REDIS_EXPORTER_HOST_BIND:-127.0.0.1}:9121:9121",
        ]:
            self.assertIn(token, compose)
        self.assertIn("WantedBy=multi-user.target", unit)
        self.assertIn("--no-build", unit)
        self.assertIn("--wait --wait-timeout 240", unit)
        self.assertNotIn("User=boost-gateway", unit)
        self.assertEqual(
            hashlib.sha256(unit.encode("utf-8")).hexdigest(),
            policy["systemd"]["unit_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
