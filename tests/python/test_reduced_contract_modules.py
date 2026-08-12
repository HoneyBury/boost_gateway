"""Direct contracts for CLI-free modules extracted during tooling reduction."""

from pathlib import Path

from scripts.lib import conan_workflow_contract
from scripts.lib import deploy_operability_contract
from scripts.lib import evidence_provenance_cases
from scripts.lib import long_soak_contract
from scripts.lib import monitoring_operability_contract
from scripts.lib import raft_mixed_binary_runtime
from scripts.lib import sdk_distribution_contract


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_reduced_contract_modules_bind_the_repository_root() -> None:
    modules = {
        "scripts/lib/conan_workflow_contract.py": conan_workflow_contract.ROOT,
        "scripts/lib/deploy_operability_contract.py": deploy_operability_contract.REPO_ROOT,
        "scripts/lib/evidence_provenance_cases.py": evidence_provenance_cases.ROOT,
        "scripts/lib/long_soak_contract.py": long_soak_contract.ROOT,
        "scripts/lib/monitoring_operability_contract.py": monitoring_operability_contract.REPO_ROOT,
        "scripts/lib/raft_mixed_binary_runtime.py": raft_mixed_binary_runtime.ROOT,
        "scripts/lib/sdk_distribution_contract.py": sdk_distribution_contract.REPO_ROOT,
    }
    for path, resolved in modules.items():
        assert path.endswith(".py")
        assert resolved == REPO_ROOT


def test_reduced_contract_modules_keep_expected_entry_contracts() -> None:
    assert conan_workflow_contract.bootstrap_uses_resolved_home(
        'python scripts/bootstrap_conan.py --conan-home "$CONAN_HOME"'
    )
    assert deploy_operability_contract.PROJECT_VERSION == "3.6.6"
    assert evidence_provenance_cases.provenance()["candidate_revision"] == "a" * 40
    assert "2h" in long_soak_contract.LONG_SOAK_PRESETS
    assert "BoostGatewayScrapeDown" in monitoring_operability_contract.REQUIRED_ALERTS
    assert callable(raft_mixed_binary_runtime.reserve_ports)
    assert sdk_distribution_contract.SDK_VERSION == "4.2.1"
