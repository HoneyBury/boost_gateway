#!/usr/bin/env python3
"""Validate the versioned cloud target contract before provisioning."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import re
import ipaddress
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from scripts.lib.release_lifecycle_io import atomic_write_json  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTRACT = ROOT / "deploy/cloud/cloud-target-contract.json"
DEFAULT_POLICY = ROOT / "deploy/operations/operations-host-policy.json"
DEFAULT_SUMMARY = ROOT / "runtime/validation/cloud-target-contract-summary.json"
EXPECTED_DEVIATIONS = {
    "storage:smart-health",
    "thermal:temperature",
    "power:restart-on-power-loss",
}
PENDING_PREFIXES = ("PENDING", "REPLACE_WITH")


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def nested(document: dict[str, Any], *keys: str) -> Any:
    value: Any = document
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def concrete(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not value.strip().upper().startswith(PENDING_PREFIXES)
    )


def private_ipv4_cidrs(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    try:
        networks = [ipaddress.ip_network(item, strict=True) for item in value]
    except (TypeError, ValueError):
        return False
    private_ranges = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    ]
    return all(
        network.version == 4
        and any(network.subnet_of(private_range) for private_range in private_ranges)
        for network in networks
    )


def add(checks: list[Check], name: str, passed: bool, detail: str) -> None:
    checks.append(Check(name, passed, detail))


def validate_structure(
    contract: dict[str, Any], policy: dict[str, Any], root: Path
) -> list[Check]:
    checks: list[Check] = []
    add(checks, "contract:schema", contract.get("schema_version") == 1, "schema_version must be 1")
    add(checks, "contract:status", contract.get("status") in {"draft", "selected"}, "status is draft or selected")
    add(checks, "contract:target-id", concrete(contract.get("target_id")), "target_id is concrete")
    add(checks, "provider:name", nested(contract, "provider", "name") == "alicloud", "provider is alicloud")
    add(checks, "platform:os", nested(contract, "compute", "os_id") == "ubuntu" and nested(contract, "compute", "os_version") == "24.04", "Ubuntu 24.04 is fixed")
    add(checks, "platform:architecture", nested(contract, "compute", "architecture") == "x86_64", "architecture is x86_64")

    target = policy.get("target", {})
    add(
        checks,
        "capacity:vcpu",
        isinstance(nested(contract, "compute", "vcpu_count"), int)
        and nested(contract, "compute", "vcpu_count") >= int(target.get("min_logical_cpus", 0)),
        "vCPU count preserves the operations-host logical CPU floor",
    )
    add(
        checks,
        "capacity:memory",
        isinstance(nested(contract, "compute", "memory_bytes"), int)
        and nested(contract, "compute", "memory_bytes") >= int(target.get("min_memory_bytes", 0)),
        "memory preserves the operations-host floor",
    )
    add(
        checks,
        "capacity:root-disk",
        isinstance(nested(contract, "storage", "system_disk", "size_bytes"), int)
        and nested(contract, "storage", "system_disk", "size_bytes")
        >= int(target.get("min_root_filesystem_bytes", 0)),
        "system disk preserves the root filesystem floor",
    )
    add(
        checks,
        "storage:encryption",
        nested(contract, "storage", "system_disk", "encrypted") is True
        and nested(contract, "storage", "data_disk", "encrypted") is True,
        "system and persistent data disks are encrypted",
    )
    add(
        checks,
        "storage:persistence",
        nested(contract, "storage", "data_disk", "delete_with_instance") is False
        and int(nested(contract, "storage", "data_disk", "snapshot_retention_days") or 0) >= 30,
        "data disk survives instance replacement and keeps daily snapshots",
    )

    network = contract.get("network", {})
    network = network if isinstance(network, dict) else {}
    add(checks, "network:management", network.get("management_path") == "private-vpc" and private_ipv4_cidrs(network.get("management_cidrs")), "management is restricted to explicit private CIDRs")
    add(checks, "network:application", network.get("service_ingress_mode") == "private" and private_ipv4_cidrs(network.get("application_cidrs")), "pilot application ingress is private")
    add(checks, "network:no-public-ingress", network.get("public_ingress_ports") == [], "no public ingress port is declared")
    add(checks, "network:forbidden-services", all(network.get(key) is False for key in ("public_docker_api", "public_redis", "public_monitoring")), "Docker API, Redis and monitoring stay non-public")

    add(checks, "secrets:repository", nested(contract, "secrets", "repository_stored") is False, "secrets are excluded from the repository")
    add(checks, "secrets:terraform-state", nested(contract, "secrets", "terraform_state_contains_secret_values") is False, "secret values are excluded from Terraform state")
    add(checks, "deployment:no-source-build", nested(contract, "deployment", "source_compilation_on_target") is False, "target source compilation is disabled")
    add(checks, "deployment:artifact-mode", nested(contract, "deployment", "artifact_mode") == "verified-immutable-release", "deployment uses verified immutable release assets")
    add(checks, "deployment:clock", nested(contract, "deployment", "time_synchronization") == "chrony", "chrony is the time synchronization contract")

    cloud_policy_path = nested(contract, "deployment", "host_policy_path")
    cloud_policy_file = root / str(cloud_policy_path) if concrete(cloud_policy_path) else Path("/")
    try:
        cloud_policy = load_object(cloud_policy_file)
    except (OSError, ValueError, json.JSONDecodeError):
        cloud_policy = {}
    non_network_sections = {
        key for key in policy if key not in {"network"}
    }
    add(
        checks,
        "host-policy:base-contract",
        concrete(cloud_policy_path)
        and cloud_policy_file.is_file()
        and all(cloud_policy.get(key) == policy.get(key) for key in non_network_sections),
        "cloud host policy preserves every non-network operations-host requirement",
    )
    base_network = policy.get("network", {})
    cloud_network = cloud_policy.get("network", {}) if isinstance(cloud_policy, dict) else {}
    add(
        checks,
        "host-policy:private-gateway",
        isinstance(cloud_network, dict)
        and cloud_network.get("public_tcp_ports") == []
        and set(cloud_network.get("restricted_tcp_ports", []))
        == set(base_network.get("restricted_tcp_ports", []))
        and set(cloud_network.get("trusted_cidrs", []))
        == set(base_network.get("trusted_cidrs", []))
        and set(cloud_network.get("firewall_protected_tcp_ports", []))
        == set(base_network.get("firewall_protected_tcp_ports", [])) | {9201}
        and set(cloud_network.get("required_trusted_tcp_ports", []))
        == set(base_network.get("required_trusted_tcp_ports", [])) | {9201}
        and cloud_network.get("required_public_listener") == 9201,
        "cloud policy changes TCP 9201 from public ingress to firewall-protected trusted ingress only",
    )

    deviations = contract.get("cloud_deviations")
    deviation_rows = deviations if isinstance(deviations, list) else []
    deviation_names = {
        row.get("host_check")
        for row in deviation_rows
        if isinstance(row, dict) and isinstance(row.get("host_check"), str)
    }
    add(checks, "deviations:exact-set", deviation_names == EXPECTED_DEVIATIONS and len(deviation_rows) == len(EXPECTED_DEVIATIONS), "cloud-only hardware deviations are explicit and unique")
    add(
        checks,
        "deviations:blocking-substitutes",
        all(
            isinstance(row, dict)
            and concrete(row.get("rationale"))
            and concrete(row.get("blocking_substitute"))
            and row.get("evidence_status") in {"pending", "accepted"}
            for row in deviation_rows
        ),
        "every deviation has a rationale, blocking substitute and evidence state",
    )

    iac_path = nested(contract, "deployment", "iac_path")
    iac_root = root / str(iac_path) if concrete(iac_path) else Path("/")
    required_iac = {
        "versions.tf",
        "variables.tf",
        "main.tf",
        "outputs.tf",
        "cloud-init.yaml.tftpl",
        ".terraform.lock.hcl",
        "terraform.tfvars.example",
    }
    add(checks, "iac:path", concrete(iac_path) and iac_root.is_dir(), "IaC directory exists")
    add(checks, "iac:files", iac_root.is_dir() and required_iac <= {path.name for path in iac_root.iterdir() if path.is_file()}, "IaC and bootstrap files are complete")
    if iac_root.is_dir() and (iac_root / "main.tf").is_file():
        terraform_text = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(iac_root.glob("*.tf"))
        )
        add(checks, "iac:resources", all(token in terraform_text for token in ("alicloud_instance", "alicloud_vpc", "alicloud_vswitch", "alicloud_security_group", "alicloud_ecs_disk", "alicloud_ecs_auto_snapshot_policy")), "IaC declares compute, network, firewall, persistent disk and snapshots")
        add(checks, "iac:no-world-ingress", 'cidr_ip = "0.0.0.0/0"' not in terraform_text, "IaC has no world-open ingress rule")
    else:
        add(checks, "iac:resources", False, "main.tf is unavailable")
        add(checks, "iac:no-world-ingress", False, "main.tf is unavailable")
    return checks


def validate_selection(contract: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    add(checks, "selection:status", contract.get("status") == "selected", "status is selected")
    for label, keys in (
        ("region", ("provider", "region_id")),
        ("zone", ("provider", "zone_id")),
        ("instance-type", ("compute", "instance_type")),
        ("image-id", ("compute", "image_id")),
        ("dns-hostname", ("dns", "hostname")),
        ("secret-source", ("secrets", "source")),
        ("state-backend", ("terraform_state", "backend")),
        ("cost-quote", ("cost", "quote_reference")),
        ("cost-quoted-at", ("cost", "quoted_at")),
        ("backup-failure-domain", ("recovery", "backup_failure_domain")),
    ):
        add(checks, f"selection:{label}", concrete(nested(contract, *keys)), f"{'.'.join(keys)} is frozen")
    amount = nested(contract, "cost", "expected_monthly_amount")
    add(checks, "selection:monthly-cost", isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0, "expected monthly cost is recorded")
    return checks


def validate_admission_evidence(contract: dict[str, Any]) -> list[Check]:
    deviations = contract.get("cloud_deviations", [])
    return [
        Check(
            "admission:deviation-evidence",
            isinstance(deviations, list)
            and len(deviations) == len(EXPECTED_DEVIATIONS)
            and all(
                isinstance(row, dict)
                and row.get("evidence_status") == "accepted"
                and concrete(row.get("evidence_reference"))
                and Path(str(row.get("evidence_reference"))).is_absolute()
                and re.fullmatch(r"[0-9a-f]{64}", str(row.get("evidence_sha256", "")))
                is not None
                for row in deviations
            ),
            "all cloud deviation substitutes have accepted evidence references",
        )
    ]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--require-selected", action="store_true")
    parser.add_argument("--require-admission-evidence", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    contract_path = args.contract if args.contract.is_absolute() else ROOT / args.contract
    policy_path = args.policy if args.policy.is_absolute() else ROOT / args.policy
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    checks: list[Check] = []
    contract: dict[str, Any] = {}
    try:
        contract = load_object(contract_path)
        policy = load_object(policy_path)
        checks.extend(validate_structure(contract, policy, ROOT))
        selection_checks = validate_selection(contract)
        admission_checks = validate_admission_evidence(contract)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        add(checks, "contract:initialization", False, f"cannot load contract: {exc}")
        selection_checks = [Check("selection:initialization", False, "contract could not be loaded")]
        admission_checks = [Check("admission:initialization", False, "contract could not be loaded")]

    structure_valid = all(check.passed for check in checks)
    provisioning_ready = structure_valid and all(check.passed for check in selection_checks)
    admission_ready = provisioning_ready and all(check.passed for check in admission_checks)
    required_checks = list(checks)
    if args.require_selected or args.require_admission_evidence:
        required_checks.extend(selection_checks)
    if args.require_admission_evidence:
        required_checks.extend(admission_checks)
    overall_pass = all(check.passed for check in required_checks)
    summary = {
        "summary_version": 1,
        "generated_at": now(),
        "contract_path": str(contract_path),
        "contract_status": contract.get("status", ""),
        "structure_valid": structure_valid,
        "provisioning_ready": provisioning_ready,
        "admission_ready": admission_ready,
        "require_selected": args.require_selected,
        "overall_pass": overall_pass,
        "checks": [check.as_dict() for check in required_checks],
        "pending_selection_checks": [
            check.as_dict() for check in selection_checks if not check.passed
        ],
        "pending_admission_checks": [
            check.as_dict() for check in admission_checks if not check.passed
        ],
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(summary_path, summary)
    print(f"cloud target contract: {'PASS' if overall_pass else 'FAIL'}")
    print(f"provisioning ready: {'yes' if provisioning_ready else 'no'}")
    print(f"admission ready: {'yes' if admission_ready else 'no'}")
    print(f"summary: {summary_path}")
    if not overall_pass:
        for check in required_checks:
            if not check.passed:
                print(f"- {check.name}: {check.detail}", file=sys.stderr)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
