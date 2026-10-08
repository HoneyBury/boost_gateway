from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.tools.render_enterprise_operations_readiness import audit


def write_json(root: Path, name: str, value: dict[str, object]) -> str:
    path = root / name
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_evidence(root: Path) -> None:
    aggregate = {
        "expected_samples": 43_200,
        "recorded_samples": 43_156,
        "successful_samples": 43_154,
        "failed_samples": 2,
        "coverage_rate": 43_156 / 43_200,
        "availability_including_approved_maintenance": 43_154 / 43_200,
        "recorded_success_rate": 43_154 / 43_156,
        "overall_pass": False,
        "candidate_consistent": True,
        "candidate": {"tag": "v3.6.7"},
        "period": {"duration_seconds": 2_592_000},
    }
    aggregate_hash = write_json(root, "aggregate.raw.json", aggregate)

    scope = {
        "inputs": {"raw_aggregate_sha256": aggregate_hash},
        "raw_governed_result": {
            "recorded_samples": 43_156,
            "successful_samples": 43_154,
            "failed_samples": 2,
            "missing_minutes": 44,
        },
        "approved_scope_exclusions": {
            "missing_minutes": 44,
            "recorded_failed_minutes": 2,
        },
        "scope_adjusted_software_result": {
            "evaluated_minutes": 43_154,
            "successful_minutes": 43_154,
            "failed_minutes": 0,
            "availability": 1.0,
            "result": "PASS",
        },
    }
    scope_hash = write_json(root, "scope-adjusted-software-assessment.json", scope)

    host = {
        "overall_pass": True,
        "coverage_rate": 1.0,
        "max_gap_seconds": 15,
        "host_memory_max_ratio": 0.13,
        "filesystem_max_used_ratio": 0.31,
        "unknown_restarts": 0,
        "oom_events": 0,
        "sustained_thermal_throttles": 0,
        "unexplained_growth": 0,
        "service_evidence": {
            "prometheus_node_exporter_samples": 172_800,
            "governed_container_count": 13,
            "governed_container_restart_count_max": 0,
        },
        "hardware_evidence": {"kernel_storage_io_errors": 0},
    }
    host_hash = write_json(root, "host-window-audit.json", host)

    lifecycle = {
        "overall_pass": True,
        "checks": [{"name": f"check-{index}", "passed": True} for index in range(22)],
    }
    lifecycle_hash = write_json(root, "deployment-verification-summary.json", lifecycle)

    alert = {
        "overall_pass": True,
        "message_ids_distinct": True,
        "notification_counters": {
            "email_total_before": 37,
            "email_total_after": 39,
            "email_failed_before": 3,
            "email_failed_after": 3,
        },
    }
    alert_hash = write_json(root, "alert-delivery-attestation-20261008.json", alert)
    preflight = {"overall_pass": True, "alert_delivery_attestation_sha256": alert_hash}
    preflight_hash = write_json(root, "todo0017-final-preflight-20261008.json", preflight)

    backup = {
        "backup_id": "backup-1",
        "create_only": True,
        "remote_readback_sha256": True,
    }
    backup_hash = write_json(root, "backup-vault-receipt-20261008.json", backup)
    retention = {
        "state": "deleted",
        "delete_only_after_verified_remote_copy": True,
        "anchor_backup_id": "backup-1",
        "retained_backup_ids": ["backup-1"],
        "deleted_backup_ids": ["backup-0"],
    }
    retention_hash = write_json(root, "backup-retention-completion-20261008.json", retention)

    verification_output = [f"ledger/records/daily/{index}.json: OK" for index in range(30)]
    verification_output += [f"ledger/records/weekly/{index}.json: OK" for index in range(4)]
    verification_output += ["ledger/records/incident/1.json: OK"]
    offhost = {
        "overall_pass": True,
        "create_only": True,
        "off_host_copy_verified": True,
        "manifest": {"entry_count": 289},
        "checksums": {
            "verified_entry_count": 290,
            "verification_output": verification_output,
        },
    }
    offhost_hash = write_json(root, "final-offhost-verification-receipt.json", offhost)

    completion = {
        "overall_pass": True,
        "result": "COMPLETE_USER_APPROVED_SCOPE",
        "raw_governed_result": {
            "result": "FAIL_PRESERVED",
            "aggregate_sha256": aggregate_hash,
            "controller_final_json_created": False,
        },
        "scope_adjusted_result": {"assessment_sha256": scope_hash},
        "protected_checks": {
            "host_window_audit_sha256": host_hash,
            "lifecycle_verification_sha256": lifecycle_hash,
            "alert_delivery_attestation_sha256": alert_hash,
            "observability_preflight_sha256": preflight_hash,
            "backup_vault_receipt_sha256": backup_hash,
            "backup_retention_completion_sha256": retention_hash,
        },
        "final_seal": {
            "formal_30_day_claim": False,
            "readback_verified": True,
            "receipt_sha256": offhost_hash,
        },
    }
    write_json(root, "closure-completion.json", completion)


def test_audit_recomputes_and_accepts_bound_evidence(tmp_path: Path) -> None:
    make_evidence(tmp_path)

    summary = audit(tmp_path)

    assert summary["audit_pass"] is True
    assert summary["decision"] == "READY_FOR_CLOUD_SINGLE_NODE_PILOT"
    assert summary["formal_30_day_gate"] == "FAIL_PRESERVED"
    assert summary["raw_recomputed"]["missing_samples"] == 44
    assert summary["scope_adjusted_recomputed"]["availability"] == 1.0


def test_audit_rejects_mutated_aggregate(tmp_path: Path) -> None:
    make_evidence(tmp_path)
    aggregate_path = tmp_path / "aggregate.raw.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate["successful_samples"] = 43_153
    aggregate_path.write_text(json.dumps(aggregate, sort_keys=True) + "\n", encoding="utf-8")

    summary = audit(tmp_path)

    assert summary["audit_pass"] is False
    failed = {item["name"] for item in summary["checks"] if not item["passed"]}
    assert "raw-count-balance" in failed
    assert "evidence-hash-bindings" in failed
