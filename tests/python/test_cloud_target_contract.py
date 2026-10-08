from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/gates/infrastructure/check_cloud_target_contract.py"
SPEC = importlib.util.spec_from_file_location("check_cloud_target_contract", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CloudTargetContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(
            (ROOT / "deploy/cloud/cloud-target-contract.json").read_text(
                encoding="utf-8"
            )
        )
        cls.policy = json.loads(
            (ROOT / "deploy/operations/operations-host-policy.json").read_text(
                encoding="utf-8"
            )
        )

    def test_repository_draft_is_structurally_valid_but_not_ready(self) -> None:
        checks = MODULE.validate_structure(self.contract, self.policy, ROOT)
        selection = MODULE.validate_selection(self.contract)
        admission = MODULE.validate_admission_evidence(self.contract)

        self.assertTrue(all(check.passed for check in checks))
        self.assertFalse(all(check.passed for check in selection))
        self.assertFalse(all(check.passed for check in admission))

    def test_rejects_capacity_and_public_exposure_regressions(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["compute"]["vcpu_count"] = 2
        contract["storage"]["system_disk"]["size_bytes"] = 40000000000
        contract["network"]["management_cidrs"] = ["0.0.0.0/0"]
        contract["network"]["public_ingress_ports"] = [22, 6379]
        contract["network"]["public_redis"] = True

        checks = {
            check.name: check
            for check in MODULE.validate_structure(contract, self.policy, ROOT)
        }

        for name in (
            "capacity:vcpu",
            "capacity:root-disk",
            "network:management",
            "network:no-public-ingress",
            "network:forbidden-services",
        ):
            self.assertFalse(checks[name].passed, name)

        self.assertFalse(MODULE.private_ipv4_cidrs(["100.64.0.0/10"]))
        self.assertFalse(MODULE.private_ipv4_cidrs(["203.0.113.0/24"]))
        self.assertTrue(MODULE.private_ipv4_cidrs(["10.42.0.0/16"]))

    def test_selected_contract_requires_cost_and_deviation_evidence(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["status"] = "selected"
        contract["provider"].update(
            {"region_id": "cn-example", "zone_id": "cn-example-a"}
        )
        contract["compute"].update(
            {"instance_type": "ecs.example.3xlarge", "image_id": "ubuntu-image-id"}
        )
        contract["dns"]["hostname"] = "gateway.internal.example"
        contract["secrets"]["source"] = "alicloud-kms-secret-manager"
        contract["terraform_state"]["backend"] = "encrypted-remote-backend"
        contract["cost"].update(
            {
                "expected_monthly_amount": 1000.0,
                "quote_reference": "quote-20261008",
                "quoted_at": "2026-10-08T00:00:00Z",
            }
        )
        contract["recovery"]["backup_failure_domain"] = "separate-region-account"
        self.assertTrue(all(check.passed for check in MODULE.validate_selection(contract)))
        self.assertFalse(
            all(check.passed for check in MODULE.validate_admission_evidence(contract))
        )

        for row in contract["cloud_deviations"]:
            row["evidence_status"] = "accepted"
            row["evidence_reference"] = f"/evidence/{row['host_check']}"
            row["evidence_sha256"] = "a" * 64

        self.assertTrue(
            all(check.passed for check in MODULE.validate_admission_evidence(contract))
        )

    def test_cli_fail_closes_when_selected_contract_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary) / "summary.json"
            result = MODULE.main(
                [
                    "--contract",
                    str(ROOT / "deploy/cloud/cloud-target-contract.json"),
                    "--summary-path",
                    str(summary),
                    "--require-selected",
                ]
            )

            self.assertEqual(1, result)
            document = json.loads(summary.read_text(encoding="utf-8"))
            self.assertFalse(document["overall_pass"])
            self.assertFalse(document["provisioning_ready"])


if __name__ == "__main__":
    unittest.main()
