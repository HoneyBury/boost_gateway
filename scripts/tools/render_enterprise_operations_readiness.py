#!/usr/bin/env python3
"""Audit TODO-0017 evidence and render the TODO-0018 readiness decision."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE = (
    ROOT / "runtime/validation/todo0017-20260908T0309Z-fixed-end"
)
DEFAULT_REPORT = ROOT / "docs/enterprise-operations-readiness-20261008.md"
DEFAULT_SUMMARY = ROOT / "docs/enterprise-operations-readiness-20261008.json"


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON evidence {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON evidence must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def close(left: Any, right: float) -> bool:
    return isinstance(left, (int, float)) and math.isclose(
        float(left), right, rel_tol=0.0, abs_tol=1e-12
    )


def audit(evidence_dir: Path) -> dict[str, Any]:
    names = {
        "aggregate": "aggregate.raw.json",
        "scope": "scope-adjusted-software-assessment.json",
        "host": "host-window-audit.json",
        "lifecycle": "deployment-verification-summary.json",
        "alert": "alert-delivery-attestation-20261008.json",
        "preflight": "todo0017-final-preflight-20261008.json",
        "backup_receipt": "backup-vault-receipt-20261008.json",
        "retention": "backup-retention-completion-20261008.json",
        "offhost": "final-offhost-verification-receipt.json",
        "completion": "closure-completion.json",
    }
    paths = {key: evidence_dir / name for key, name in names.items()}
    documents = {key: load_object(path) for key, path in paths.items()}
    hashes = {key: sha256_file(path) for key, path in paths.items()}

    aggregate = documents["aggregate"]
    scope = documents["scope"]
    host = documents["host"]
    lifecycle = documents["lifecycle"]
    alert = documents["alert"]
    preflight = documents["preflight"]
    backup = documents["backup_receipt"]
    retention = documents["retention"]
    offhost = documents["offhost"]
    completion = documents["completion"]

    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    expected = int(aggregate.get("expected_samples", -1))
    recorded = int(aggregate.get("recorded_samples", -1))
    successful = int(aggregate.get("successful_samples", -1))
    failed = int(aggregate.get("failed_samples", -1))
    missing = expected - recorded
    coverage = recorded / expected if expected > 0 else -1.0
    availability = successful / expected if expected > 0 else -1.0
    recorded_success = successful / recorded if recorded > 0 else -1.0

    add("raw-count-balance", expected == recorded + missing and recorded == successful + failed,
        f"expected={expected}, recorded={recorded}, successful={successful}, failed={failed}, missing={missing}")
    add("raw-duration", aggregate.get("period", {}).get("duration_seconds") == 2_592_000,
        f"duration_seconds={aggregate.get('period', {}).get('duration_seconds')}")
    add("raw-recomputed-rates",
        close(aggregate.get("coverage_rate"), coverage)
        and close(aggregate.get("availability_including_approved_maintenance"), availability)
        and close(aggregate.get("recorded_success_rate"), recorded_success),
        f"coverage={coverage:.12f}, availability={availability:.12f}, recorded_success={recorded_success:.12f}")
    add("raw-result-preserved", aggregate.get("overall_pass") is False
        and completion.get("raw_governed_result", {}).get("result") == "FAIL_PRESERVED"
        and completion.get("raw_governed_result", {}).get("controller_final_json_created") is False,
        "original finalizer FAIL and absent controller final.json remain explicit")
    add("candidate-consistent", aggregate.get("candidate_consistent") is True,
        f"candidate={aggregate.get('candidate', {})}")

    raw_scope = scope.get("raw_governed_result", {})
    exclusions = scope.get("approved_scope_exclusions", {})
    adjusted = scope.get("scope_adjusted_software_result", {})
    excluded_missing = int(exclusions.get("missing_minutes", -1))
    excluded_failed = int(exclusions.get("recorded_failed_minutes", -1))
    evaluated = expected - excluded_missing - excluded_failed
    add("scope-raw-matches",
        raw_scope.get("recorded_samples") == recorded
        and raw_scope.get("successful_samples") == successful
        and raw_scope.get("failed_samples") == failed
        and raw_scope.get("missing_minutes") == missing,
        "scope assessment repeats the immutable aggregate without rewriting it")
    add("scope-adjusted-recomputed",
        evaluated == adjusted.get("evaluated_minutes")
        and successful == adjusted.get("successful_minutes")
        and adjusted.get("failed_minutes") == 0
        and close(adjusted.get("availability"), successful / evaluated)
        and adjusted.get("result") == "PASS",
        f"evaluated={evaluated}, successful={successful}, excluded_missing={excluded_missing}, excluded_failed={excluded_failed}")

    service = host.get("service_evidence", {})
    hardware = host.get("hardware_evidence", {})
    add("host-window-hard-gates",
        host.get("overall_pass") is True
        and float(host.get("coverage_rate", -1)) >= 0.999
        and int(host.get("max_gap_seconds", 999999)) <= 120
        and float(host.get("host_memory_max_ratio", 1)) < 0.80
        and float(host.get("filesystem_max_used_ratio", 1)) < 0.75
        and host.get("unknown_restarts") == 0
        and host.get("oom_events") == 0
        and host.get("sustained_thermal_throttles") == 0
        and host.get("unexplained_growth") == 0
        and service.get("governed_container_restart_count_max") == 0
        and hardware.get("kernel_storage_io_errors") == 0,
        "coverage, gap, memory, disk, restart, OOM, thermal, growth and storage thresholds pass")
    lifecycle_checks = lifecycle.get("checks", [])
    add("lifecycle-verification", lifecycle.get("overall_pass") is True
        and isinstance(lifecycle_checks, list)
        and len(lifecycle_checks) == 22
        and all(isinstance(item, dict) and item.get("passed") is True for item in lifecycle_checks),
        f"passed={sum(isinstance(item, dict) and item.get('passed') is True for item in lifecycle_checks)}/22")

    counters = alert.get("notification_counters", {})
    add("alert-delivery",
        alert.get("overall_pass") is True
        and alert.get("message_ids_distinct") is True
        and counters.get("email_total_after") == counters.get("email_total_before", -1) + 2
        and counters.get("email_failed_after") == counters.get("email_failed_before"),
        f"email_total={counters.get('email_total_before')}->{counters.get('email_total_after')}, failures={counters.get('email_failed_before')}->{counters.get('email_failed_after')}")
    add("observability-preflight", preflight.get("overall_pass") is True
        and preflight.get("alert_delivery_attestation_sha256") == hashes["alert"],
        "preflight passes and binds the final alert delivery attestation")
    add("backup-vault-copy", backup.get("create_only") is True
        and backup.get("remote_readback_sha256") is True,
        f"backup_id={backup.get('backup_id')}")
    add("backup-retention", retention.get("state") == "deleted"
        and retention.get("delete_only_after_verified_remote_copy") is True
        and retention.get("anchor_backup_id") == backup.get("backup_id"),
        f"retained={len(retention.get('retained_backup_ids', []))}, deleted={len(retention.get('deleted_backup_ids', []))}")

    output = offhost.get("checksums", {}).get("verification_output", [])
    daily_count = sum(isinstance(line, str) and line.startswith("ledger/records/daily/") for line in output)
    weekly_count = sum(isinstance(line, str) and line.startswith("ledger/records/weekly/") for line in output)
    incident_count = sum(isinstance(line, str) and line.startswith("ledger/records/incident/") for line in output)
    add("offhost-final-package", offhost.get("overall_pass") is True
        and offhost.get("create_only") is True
        and offhost.get("off_host_copy_verified") is True
        and offhost.get("manifest", {}).get("entry_count") == 289
        and offhost.get("checksums", {}).get("verified_entry_count") == 290,
        f"manifest_entries={offhost.get('manifest', {}).get('entry_count')}, verified_checksums={offhost.get('checksums', {}).get('verified_entry_count')}")
    add("ledger-coverage", daily_count >= 30 and weekly_count >= 4 and incident_count >= 1,
        f"daily={daily_count}, weekly={weekly_count}, incident={incident_count}")

    protected = completion.get("protected_checks", {})
    final_seal = completion.get("final_seal", {})
    add("evidence-hash-bindings",
        scope.get("inputs", {}).get("raw_aggregate_sha256") == hashes["aggregate"]
        and completion.get("raw_governed_result", {}).get("aggregate_sha256") == hashes["aggregate"]
        and completion.get("scope_adjusted_result", {}).get("assessment_sha256") == hashes["scope"]
        and protected.get("host_window_audit_sha256") == hashes["host"]
        and protected.get("lifecycle_verification_sha256") == hashes["lifecycle"]
        and protected.get("alert_delivery_attestation_sha256") == hashes["alert"]
        and protected.get("observability_preflight_sha256") == hashes["preflight"]
        and protected.get("backup_vault_receipt_sha256") == hashes["backup_receipt"]
        and protected.get("backup_retention_completion_sha256") == hashes["retention"]
        and final_seal.get("receipt_sha256") == hashes["offhost"],
        "completion marker binds all independently loaded closure inputs")
    add("completion-boundary", completion.get("overall_pass") is True
        and completion.get("result") == "COMPLETE_USER_APPROVED_SCOPE"
        and final_seal.get("formal_30_day_claim") is False
        and final_seal.get("readback_verified") is True,
        "scope-adjusted completion is explicit; formal_30_day_claim remains false")

    audit_pass = all(item["passed"] for item in checks)
    return {
        "schema_version": 1,
        "task": "TODO-0018",
        "audit_pass": audit_pass,
        "decision": "READY_FOR_CLOUD_SINGLE_NODE_PILOT" if audit_pass else "NOT_READY",
        "formal_30_day_gate": "FAIL_PRESERVED",
        "formal_30_day_claim": False,
        "unrestricted_production_ready": False,
        "raw_recomputed": {
            "expected_samples": expected,
            "recorded_samples": recorded,
            "successful_samples": successful,
            "failed_samples": failed,
            "missing_samples": missing,
            "coverage_rate": coverage,
            "availability": availability,
            "recorded_success_rate": recorded_success,
        },
        "scope_adjusted_recomputed": {
            "excluded_missing_minutes": excluded_missing,
            "excluded_failed_minutes": excluded_failed,
            "evaluated_minutes": evaluated,
            "successful_minutes": successful,
            "availability": successful / evaluated,
        },
        "host_recomputed": {
            "prometheus_samples": service.get("prometheus_node_exporter_samples"),
            "coverage_rate": host.get("coverage_rate"),
            "max_gap_seconds": host.get("max_gap_seconds"),
            "memory_max_ratio": host.get("host_memory_max_ratio"),
            "filesystem_max_used_ratio": host.get("filesystem_max_used_ratio"),
            "container_count": service.get("governed_container_count"),
            "max_container_restarts": service.get("governed_container_restart_count_max"),
        },
        "ledger_recomputed": {
            "daily_records": daily_count,
            "weekly_records": weekly_count,
            "incident_records": incident_count,
            "verified_checksums": offhost.get("checksums", {}).get("verified_entry_count"),
        },
        "checks": checks,
        "source_sha256": hashes,
    }


def render_report(summary: dict[str, Any], generated_at: str) -> str:
    raw = summary["raw_recomputed"]
    adjusted = summary["scope_adjusted_recomputed"]
    host = summary["host_recomputed"]
    ledger = summary["ledger_recomputed"]
    check_rows = "\n".join(
        f"| `{item['name']}` | {'PASS' if item['passed'] else 'FAIL'} | {item['detail']} |"
        for item in summary["checks"]
    )
    source_rows = "\n".join(
        f"| `{name}` | `{digest}` |" for name, digest in sorted(summary["source_sha256"].items())
    )
    return f"""# Enterprise Operations Readiness Decision

Generated at: `{generated_at}`

## Decision

**READY FOR A CONTROLLED CLOUD SINGLE-NODE PILOT.** This decision accepts the
software-stability result under the user-approved home-network boundary. It does
not claim that the original 30-day automated gate passed, and it does not approve
unrestricted public production, multi-node HA, cross-zone disaster recovery, or
an arbitrary capacity envelope.

- Audit result: **{'PASS' if summary['audit_pass'] else 'FAIL'}**
- Original governed finalizer: **FAIL PRESERVED**
- Supplemental formal 30-day claim: **false**
- Unrestricted production ready: **false**

## Independently Recomputed Gates

| Metric | Recomputed value | Required interpretation |
| --- | ---: | --- |
| Window duration | 2,592,000 seconds | complete fixed wall-clock interval |
| Expected canary samples | {raw['expected_samples']:,} | immutable denominator |
| Recorded / successful / failed / missing | {raw['recorded_samples']:,} / {raw['successful_samples']:,} / {raw['failed_samples']} / {raw['missing_samples']} | original evidence retained |
| Raw coverage | {raw['coverage_rate']:.6%} | original gate below 99.9% |
| Raw inclusive availability | {raw['availability']:.6%} | original gate below 99.9% |
| Recorded-sample success | {raw['recorded_success_rate']:.6%} | diagnostic only |
| Scope-adjusted successful minutes | {adjusted['successful_minutes']:,} / {adjusted['evaluated_minutes']:,} | 100%, approved software scope |
| Prometheus coverage / samples | {host['coverage_rate']:.2%} / {host['prometheus_samples']:,} | passes 99.9% coverage |
| Maximum Prometheus gap | {host['max_gap_seconds']} seconds | passes 120-second limit |
| Peak host memory | {host['memory_max_ratio']:.2%} | passes 80% limit |
| Peak filesystem use | {host['filesystem_max_used_ratio']:.2%} | passes 75% limit |
| Governed containers / maximum restarts | {host['container_count']} / {host['max_container_restarts']} | zero unknown restarts |
| Ledger daily / weekly / incident records | {ledger['daily_records']} / {ledger['weekly_records']} / {ledger['incident_records']} | traceability present |
| Off-host verified checksums | {ledger['verified_checksums']} | 289 entries plus manifest |

## Audit Checks

| Check | Result | Evidence |
| --- | --- | --- |
{check_rows}

## Proven Scope

- Immutable Linux x64 release consumption and a single-node Ubuntu 24.04 deployment without a server-side source build.
- Idempotent install, upgrade, verification, rollback, and reboot recovery with retained deployment identity.
- External full-flow canary, Prometheus host/container/application coverage, Alertmanager delivery, and an auditable ledger.
- Redis AOF/RDB policy, encrypted off-host backup, retention, isolated restore, and business-flow recovery.
- A 72-hour immutable shakedown with 4,320/4,320 successful samples and the scope-adjusted 30-day software result with 43,154/43,154 successful applicable minutes.
- Zero governed container restarts, OOM events, sustained thermal throttling, storage errors, or unexplained resource growth during the final window.

## Unsupported Or Unproven Scope

- The original controller finalizer did not pass and did not create `final.json`; its immutable result remains authoritative.
- No multi-node failover, quorum service, cross-availability-zone continuity, or regional disaster recovery claim is supported.
- Historical capacity measurements are candidate- and runner-specific; the selected cloud instance has no production capacity envelope yet.
- Default production traffic is plain TCP. A public cloud endpoint requires TLS/mTLS or a private network boundary before exposure.
- Linux ARM64 and macOS ARM64 evidence remains platform-specific and cannot substitute for the Linux x64 cloud target. Linux ARM64 SDK Issue #63 remains open.
- The home-router exclusion applies only to the completed home validation. Cloud network, disk, and instance failures are production incidents.

## Performance And Resilience Change Audit

| Work item | RCA and before evidence | After evidence and regression | Rollback decision |
| --- | --- | --- | --- |
| TODO-0005 / Issue #19 | Linux ARM64 tail attributed to Gateway route queue/topology under the recorded runner layout. | Isolated runs kept the 250 ms gate; battle-100 P99 was 10/10/10 ms with zero failures. | Shipped only in immutable v3.6.2; revert the isolated runtime change with a new release. |
| Issue #65 / PR #67 | Battle/Tank plugin state ownership leaked after instance teardown. | Mainline and ASan/UBSan/LSan passed with leak detection enabled and no suppressions. | Single runtime commit revert; do not disable leak detection. |
| Issue #72 / v3.6.5 | Backend session threads and stale pooled routes caused five observed transport timeouts and backend memory growth. | Corrected release passed full regression and a 4,320/4,320 external window. | Roll back through the governed immutable release lifecycle. |
| Issue #78 / PR #79 | Completed Battle runtime, replay, and per-battle state caused 0.48-0.50 MiB/hour linear RSS growth. | 2,048 lifecycle cycles, full ServiceBusIntegrity, CI, and ASan/UBSan/LSan passed; the final host audit found no unexplained growth. | Revert PR #79 only through a new release; the leaking candidates remain rejected. |
| Issue #83 / PR #82 | Redis RDB stale alert contradicted the active `save` thresholds while AOF remained healthy. | PromQL, 142 monitoring checks, governance, hosted CI, and fixed-runner CI passed without weakening persistence gates. | Revert the rule change if it suppresses a real checkpoint failure; retain separate AOF/BGSAVE critical alerts. |
| Issue #86 | Login session and token maps were not cleared on disconnect; unchanged resource gate measured 0.713714 MiB/window. | Same eight-window workload and thresholds measured 0.0 MiB/window, zero FD/thread growth, and full-flow PASS. | Single commit revert; the original failing run remains the comparison baseline. |

The SMTP relay startup, TLS hostname, backup-vault locking/capacity, dead-man,
and fixed-end controller corrections are retained as operational incident and
deployment evidence. They changed no performance threshold and were verified
through their respective recovery, delivery, or governance gates.

## Next Release Inputs

1. `TODO-0020`: admit a reproducible Ubuntu 24.04 x64 cloud target with IaC, private management access, durable storage, time synchronization, firewall rules, and secrets outside the repository.
2. `TODO-0021`: deploy the immutable release through the governed lifecycle, provide TLS/mTLS or private-only business ingress, and preserve the current host as rollback until cutover acceptance.
3. `TODO-0022`: move backup and evidence copies to a separate cloud failure domain and execute real restore and host-replacement drills.
4. `TODO-0023`: establish a capacity envelope on the selected cloud SKU, run a seven-day low-traffic pilot, and use cloud incidents without the historical home-network exclusion.
5. Keep multi-node HA and cross-zone disaster recovery as separate architecture work; they are not prerequisites for the controlled single-node pilot.

## Evidence Digests

| Input | SHA-256 |
| --- | --- |
{source_rows}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--generated-at", default="")
    args = parser.parse_args()

    evidence_dir = args.evidence_dir.resolve()
    report_path = args.report.resolve()
    summary_path = args.summary.resolve()
    generated_at = args.generated_at or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        summary = audit(evidence_dir)
    except ValueError as exc:
        print(f"enterprise operations readiness: ERROR: {exc}")
        return 2
    summary["generated_at"] = generated_at
    summary["evidence_directory"] = display_path(evidence_dir)
    summary["report_path"] = display_path(report_path)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(summary, generated_at), encoding="utf-8")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"enterprise operations readiness: {'PASS' if summary['audit_pass'] else 'FAIL'}")
    print(f"decision: {summary['decision']}")
    print(f"report: {report_path}")
    print(f"summary: {summary_path}")
    return 0 if summary["audit_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
