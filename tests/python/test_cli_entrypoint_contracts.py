from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]

# These commands predate the direct-test requirement. Keep each path explicit so
# tooling metrics can distinguish executable coverage from a passive allowlist.
CLI_HELP_CONTRACTS = (
    "scripts/gates/governance/check_conan_lockfile_workflows.py",
    "scripts/gates/governance/check_config_governance.py",
    "scripts/gates/governance/check_config_source_layout.py",
    "scripts/gates/governance/check_current_docs_install.py",
    "scripts/gates/governance/check_evidence_provenance_contract.py",
    "scripts/gates/governance/check_legacy_helper_inventory.py",
    "scripts/gates/governance/check_r5_docker_image_policy_contract.py",
    "scripts/gates/governance/check_reliability_matrix.py",
    "scripts/gates/governance/check_repository_governance.py",
    "scripts/gates/governance/check_v3_grpc_poc_decision.py",
    "scripts/gates/governance/check_v3_proto_schema.py",
    "scripts/gates/governance/check_validation_summary_contract.py",
    "scripts/gates/governance/check_workflow_catalog.py",
    "scripts/gates/governance/check_workflow_python_cli_contracts.py",
    "scripts/gates/governance/verify_release_source_authorization.py",
    "scripts/gates/infrastructure/check_fixed_runner_evidence_plan.py",
    "scripts/gates/k8s/check_operator_manifests.py",
    "scripts/gates/k8s/verify_k8s_full_flow.py",
    "scripts/gates/production/check_deploy_operability.py",
    "scripts/gates/production/check_monitoring_operability.py",
    "scripts/gates/production/check_production_candidate_audit.py",
    "scripts/gates/production/check_production_hardening_gate.py",
    "scripts/gates/production/check_production_recovery_gate.py",
    "scripts/gates/production/verify_data_recovery_gate.py",
    "scripts/gates/production/verify_gateway_observability_runtime.py",
    "scripts/gates/production/verify_observability_gate.py",
    "scripts/gates/production/verify_production_evidence_gate.py",
    "scripts/gates/release/check_p3_p4_release_readiness.py",
    "scripts/gates/release/check_security_release_gate.py",
    "scripts/gates/release/verify_p5_p8_business_closure.py",
    "scripts/gates/release/verify_r4_contract.py",
    "scripts/gates/release/verify_release_candidate.py",
    "scripts/gates/sdk/check_sdk_distribution.py",
    "scripts/gates/sdk/verify_sdk_business_flow.py",
    "scripts/gates/sdk/verify_sdk_enterprise_delivery.py",
    "scripts/gates/sdk/verify_sdk_package_consumer.py",
    "scripts/gates/transport/check_tls_profile.py",
    "scripts/gates/transport/check_transport_config_governance.py",
    "scripts/gates/transport/verify_tls_preprod_multi_run.py",
    "scripts/gates/transport/verify_tls_production_readiness.py",
    "scripts/producers/collect_docker_production_perf_snapshot.py",
    "scripts/producers/collect_release_baseline.py",
    "scripts/producers/run_cloud_production_closure.py",
    "scripts/tools/build_docker.py",
    "scripts/tools/check_backup_recovery_policy.py",
    "scripts/tools/collect_container_restart_metrics.py",
    "scripts/tools/collect_redis_persistence_metrics.py",
    "scripts/tools/create_debug_symbol_package.py",
    "scripts/tools/deploy_k8s.py",
    "scripts/tools/gen_certs.py",
    "scripts/tools/generate_conan_lock.py",
    "scripts/tools/generate_proto_cpp.py",
    "scripts/tools/inspect_dependency_layout.py",
    "scripts/tools/manage_observability_evidence.py",
    "scripts/tools/render_validation_summary.py",
    "scripts/tools/restore_bundle_ssh_receiver.py",
    "scripts/tools/run_scheduled_backup.py",
    "scripts/tools/schedule_observability_evidence.py",
    "scripts/tools/send_restore_bundle.py",
)

POSIX_ONLY = {"scripts/tools/schedule_observability_evidence.py"}


class CliEntrypointContractsTest(unittest.TestCase):
    def test_historical_cli_help_contracts_are_import_safe(self) -> None:
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        for relative in CLI_HELP_CONTRACTS:
            with self.subTest(command=relative):
                if os.name == "nt" and relative in POSIX_ONLY:
                    continue
                result = subprocess.run(
                    [sys.executable, relative, "--help"],
                    cwd=ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"{relative} --help failed:\n{result.stdout}\n{result.stderr}",
                )
                self.assertIn("usage:", result.stdout.lower(), relative)


if __name__ == "__main__":
    unittest.main()
