#!/usr/bin/env python3
"""Aggregate comparable 1/2/4 CPU fixed-runner capacity evidence."""

from __future__ import annotations
if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))


import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


from scripts.lib.cpu_capacity_evidence_contract import *  # noqa: E402,F401,F403

def validate_source(spec: SourceSpec) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    paths = {
        "long_soak": evidence_path(spec.extracted_dir, "validation/long-soak-capacity-summary.json"),
        "capacity": evidence_path(spec.extracted_dir, "perf/fixed-runner-capacity/summary.json"),
        "business": evidence_path(spec.extracted_dir, "perf/fixed-runner-business-capacity/summary.json"),
        "r4": evidence_path(spec.extracted_dir, "validation/fixed-runner-release-capacity-summary.json"),
    }
    summaries = {name: load_json(path) for name, path in paths.items()}
    for name, path in paths.items():
        add_check(checks, f"cpu-{spec.cpu_count}:{name}:summary", bool(summaries[name]), str(path))

    long_soak = summaries["long_soak"]
    capacity = summaries["capacity"]
    business = summaries["business"]
    r4 = summaries["r4"]
    revisions = {
        name: candidate_revision(summary)
        for name, summary in summaries.items()
        if summary
    }
    revisions_valid = (
        len(revisions) == 4
        and len(set(revisions.values())) == 1
        and all(SHA_PATTERN.fullmatch(value) for value in revisions.values())
    )
    add_check(checks, f"cpu-{spec.cpu_count}:same-candidate", revisions_valid, str(revisions))

    provenance = long_soak.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    add_check(
        checks,
        f"cpu-{spec.cpu_count}:run-id",
        str(provenance.get("run_id", "")) == spec.run_id,
        f"expected={spec.run_id} observed={provenance.get('run_id', '')}",
    )

    capacity_service_set = validate_affinity(
        checks, f"cpu-{spec.cpu_count}:capacity-service", capacity, spec.cpu_count,
        "service_resource_constraint",
    )
    business_service_set = validate_affinity(
        checks, f"cpu-{spec.cpu_count}:business-service", business, spec.cpu_count,
        "service_resource_constraint",
    )
    capacity_loadgen_constraint = capacity.get("loadgen_resource_constraint")
    capacity_loadgen_constraint = (
        capacity_loadgen_constraint if isinstance(capacity_loadgen_constraint, dict) else {}
    )
    loadgen_cpu_count = capacity_loadgen_constraint.get("cpu_count")
    loadgen_cpu_count = loadgen_cpu_count if isinstance(loadgen_cpu_count, int) else 0
    capacity_loadgen_set = validate_affinity(
        checks, f"cpu-{spec.cpu_count}:capacity-loadgen", capacity, loadgen_cpu_count,
        "loadgen_resource_constraint",
    )
    business_loadgen_set = validate_affinity(
        checks, f"cpu-{spec.cpu_count}:business-loadgen", business, loadgen_cpu_count,
        "loadgen_resource_constraint",
    )
    isolated = (
        bool(capacity_service_set)
        and capacity_service_set == business_service_set
        and bool(capacity_loadgen_set)
        and capacity_loadgen_set == business_loadgen_set
        and capacity_service_set.isdisjoint(capacity_loadgen_set)
    )
    add_check(
        checks,
        f"cpu-{spec.cpu_count}:service-loadgen-isolation",
        isolated,
        f"service={sorted(capacity_service_set)} loadgen={sorted(capacity_loadgen_set)}",
    )
    validate_resource_deltas(
        checks, f"cpu-{spec.cpu_count}:capacity", capacity, spec.cpu_count, loadgen_cpu_count,
    )
    validate_resource_deltas(
        checks, f"cpu-{spec.cpu_count}:business", business, spec.cpu_count, loadgen_cpu_count,
    )
    validate_business_resource_window(
        checks, f"cpu-{spec.cpu_count}:business", business, spec.cpu_count, loadgen_cpu_count,
    )
    long_cpu_set = parse_cpu_set(long_soak.get("cpu_set"))
    long_loadgen_cpu_set = parse_cpu_set(long_soak.get("loadgen_cpu_set"))
    add_check(
        checks,
        f"cpu-{spec.cpu_count}:orchestrator-affinity",
        len(long_cpu_set) == spec.cpu_count
        and long_cpu_set == capacity_service_set
        and long_loadgen_cpu_set == capacity_loadgen_set,
        f"service_cpu_set={sorted(long_cpu_set)} loadgen_cpu_set={sorted(long_loadgen_cpu_set)}",
    )

    repetitions = capacity.get("repetitions")
    repetitions_valid = (
        isinstance(repetitions, int)
        and repetitions >= 3
        and business.get("repetitions") == repetitions
        and long_soak.get("perf_repetitions") == repetitions
    )
    add_check(
        checks,
        f"cpu-{spec.cpu_count}:repetitions",
        repetitions_valid,
        f"capacity={repetitions} business={business.get('repetitions')} orchestrator={long_soak.get('perf_repetitions')}",
    )

    capacity_cases = case_map(capacity)
    business_cases = case_map(business)
    capacity_manifest = validate_case_lifecycle(
        checks, f"cpu-{spec.cpu_count}:capacity", capacity,
    )
    business_manifest = validate_case_lifecycle(
        checks, f"cpu-{spec.cpu_count}:business", business,
    )
    capacity_cases_valid = set(capacity_cases) == REQUIRED_CAPACITY_CASES and all(
        item.get("runs") == repetitions for item in capacity_cases.values()
    )
    business_cases_valid = set(business_cases) == REQUIRED_BUSINESS_CASES and all(
        item.get("runs") == repetitions for item in business_cases.values()
    )
    add_check(
        checks,
        f"cpu-{spec.cpu_count}:capacity-cases",
        capacity_cases_valid,
        f"cases={sorted(capacity_cases)}",
    )
    add_check(
        checks,
        f"cpu-{spec.cpu_count}:business-cases",
        business_cases_valid,
        f"cases={sorted(business_cases)}",
    )

    orchestration_valid = (
        long_soak.get("run_capacity") is True
        and long_soak.get("run_business_capacity") is True
        and long_soak.get("run_business_operation_perf") is True
    )
    add_check(
        checks,
        f"cpu-{spec.cpu_count}:workload-selection",
        orchestration_valid,
        "capacity, business-capacity, and business-operation profiles are required",
    )
    business_scenarios = business_scenario_map(business)
    business_operations_valid = set(business_scenarios) == {"matchmaking", "leaderboard"} and all(
        item.get("passed") is True
        and item.get("runs") == repetitions
        and item.get("passed_runs") == repetitions
        for item in business_scenarios.values()
    )
    add_check(
        checks,
        f"cpu-{spec.cpu_count}:business-operations",
        business_operations_valid,
        f"scenarios={sorted(business_scenarios)}",
    )
    redis_comparison = business.get("leaderboard_persistence_comparison")
    redis_comparison_valid = (
        not long_soak.get("leaderboard_redis_comparison", False)
        or isinstance(redis_comparison, dict)
        and redis_comparison.get("verified") is True
    )
    add_check(
        checks,
        f"cpu-{spec.cpu_count}:leaderboard-redis-comparison",
        redis_comparison_valid,
        f"requested={bool(long_soak.get('leaderboard_redis_comparison', False))} "
        f"verified={bool(isinstance(redis_comparison, dict) and redis_comparison.get('verified'))}",
    )
    add_check(
        checks,
        f"cpu-{spec.cpu_count}:r4-contract",
        isinstance(r4.get("overall_pass"), bool) and isinstance(r4.get("checks"), list),
        f"overall_pass={r4.get('overall_pass')}",
    )

    capacity_topology = capacity.get("topology")
    business_topology = business.get("topology")
    capacity_topology = capacity_topology if isinstance(capacity_topology, dict) else {}
    business_topology = business_topology if isinstance(business_topology, dict) else {}
    topology_valid = (
        isinstance(long_soak.get("io_cores"), int)
        and long_soak.get("io_cores") > 0
        and capacity_topology.get("io_cores") == long_soak.get("io_cores")
        and business_topology.get("io_cores") == long_soak.get("io_cores")
        and isinstance(long_soak.get("loadgen_io_threads"), int)
        and long_soak.get("loadgen_io_threads") > 0
        and capacity_topology.get("loadgen_io_threads") == long_soak.get("loadgen_io_threads")
        and business_topology.get("loadgen_io_threads") == long_soak.get("loadgen_io_threads")
    )
    add_check(
        checks,
        f"cpu-{spec.cpu_count}:topology-identity",
        topology_valid,
        f"io_cores={long_soak.get('io_cores')} loadgen_io_threads={long_soak.get('loadgen_io_threads')}",
    )

    workload_identity = {
        "repetitions": repetitions,
        "backend_pool_size": long_soak.get("backend_pool_size"),
        "battle_route_workers": long_soak.get("battle_route_workers"),
        "business_flow_clients": long_soak.get("business_flow_clients"),
        "business_operation_clients": long_soak.get("business_operation_clients"),
        "business_operation_iterations": long_soak.get("business_operation_iterations"),
        "leaderboard_redis_comparison": bool(long_soak.get("leaderboard_redis_comparison", False)),
        "loadgen_cpu_count": loadgen_cpu_count,
        "loadgen_io_threads": long_soak.get("loadgen_io_threads"),
        "gateway_io_cores": long_soak.get("io_cores"),
        "capacity_cases": sorted(capacity_cases),
        "business_capacity_cases": sorted(business_cases),
        "capacity_case_identity": [
            normalized_case_identity(capacity_manifest[name])
            for name in sorted(capacity_manifest)
        ],
        "business_capacity_case_identity": [
            normalized_case_identity(business_manifest[name])
            for name in sorted(business_manifest)
        ],
    }
    source = {
        "cpu_count": spec.cpu_count,
        "run_id": spec.run_id,
        "extracted_dir": str(spec.extracted_dir),
        "artifact_name": f"long-soak-capacity-{spec.run_id}",
        "candidate_revision": next(iter(revisions.values()), ""),
        "requested_cpu_set": capacity.get("service_resource_constraint", {}).get("requested", ""),
        "effective_cpu_set": capacity.get("service_resource_constraint", {}).get("effective_cpu_set", ""),
        "loadgen_cpu_set": capacity.get("loadgen_resource_constraint", {}).get("effective_cpu_set", ""),
        "workload_identity": workload_identity,
        "capacity_release_gates_passed": capacity.get("release_gates", {}).get("overall_pass") is True,
        "business_release_gates_passed": business.get("release_gates", {}).get("overall_pass") is True,
        "r4_passed": r4.get("overall_pass") is True,
        "summaries": summaries,
        "artifacts": {
            name: {"path": str(path), "sha256": sha256_file(path) if path.is_file() else ""}
            for name, path in paths.items()
        },
    }
    return source, checks


def comparison_metrics(aggregate: dict[str, Any]) -> dict[str, float | int | None]:
    return {
        "throughput_median": metric(aggregate, "throughput_msg_per_sec", "median"),
        "latency_p99_median_ms": metric(aggregate, "latency_p99_ms", "median"),
        "failed_clients_max": metric(aggregate, "failed_clients", "max"),
        "rejected_clients_max": metric(aggregate, "rejected_clients", "max"),
    }


def build_case_comparisons(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for profile, summary_key, required_cases in (
        ("capacity", "capacity", REQUIRED_CAPACITY_CASES),
        ("business-capacity", "business", REQUIRED_BUSINESS_CASES),
    ):
        for case_name in sorted(required_cases):
            by_cpu: dict[str, dict[str, float | int | None]] = {}
            for source in sources:
                aggregate = case_map(source["summaries"][summary_key]).get(case_name, {})
                by_cpu[str(source["cpu_count"])] = comparison_metrics(aggregate)
            baseline = by_cpu.get("1", {}).get("throughput_median")
            speedup: dict[str, float | None] = {}
            efficiency: dict[str, float | None] = {}
            for cpu_count in (2, 4):
                observed = by_cpu.get(str(cpu_count), {}).get("throughput_median")
                ratio = round(float(observed) / float(baseline), 4) if observed is not None and baseline else None
                speedup[str(cpu_count)] = ratio
                efficiency[str(cpu_count)] = round(ratio / cpu_count, 4) if ratio is not None else None
            comparisons.append({
                "profile": profile,
                "case": case_name,
                "by_cpu": by_cpu,
                "throughput_speedup_vs_1_cpu": speedup,
                "throughput_scaling_efficiency": efficiency,
            })
    return comparisons


def operation_map(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    operations = scenario.get("operations")
    if not isinstance(operations, list):
        return {}
    return {
        str(item.get("operation")): item
        for item in operations
        if isinstance(item, dict) and isinstance(item.get("operation"), str)
    }


def business_summary_for_mode(source: dict[str, Any], mode: str) -> dict[str, Any]:
    business = source["summaries"]["business"]
    if mode != "redis_primary_with_memory_shadow":
        return business
    comparison = business.get("leaderboard_persistence_comparison")
    modes = comparison.get("modes") if isinstance(comparison, dict) else None
    entry = modes.get(mode) if isinstance(modes, dict) else None
    summary = entry.get("summary") if isinstance(entry, dict) else None
    return summary if isinstance(summary, dict) else {}


def build_business_operation_comparisons(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    matchmaking_by_cpu = {
        str(source["cpu_count"]): business_scenario_map(source["summaries"]["business"])
        .get("matchmaking", {})
        .get("time_to_match_p99_ms")
        for source in sources
    }
    comparisons.append({
        "scenario": "matchmaking",
        "metric": "time_to_match_p99_ms",
        "by_cpu": matchmaking_by_cpu,
    })

    modes = ["in_memory_only"]
    if all(source["workload_identity"].get("leaderboard_redis_comparison") for source in sources):
        modes.append("redis_primary_with_memory_shadow")
    for mode in modes:
        scenario_by_source = {
            source["cpu_count"]: business_scenario_map(business_summary_for_mode(source, mode)).get("leaderboard", {})
            for source in sources
        }
        operation_names = sorted({
            name
            for scenario in scenario_by_source.values()
            for name in operation_map(scenario)
        })
        for operation_name in operation_names:
            by_cpu: dict[str, dict[str, float | int | None]] = {}
            for cpu_count, scenario in scenario_by_source.items():
                operation = operation_map(scenario).get(operation_name, {})
                by_cpu[str(cpu_count)] = {
                    "throughput_median": metric(operation, "throughput_ops_per_sec", "median"),
                    "latency_p99_median_ms": metric(operation, "latency_p99_ms", "median"),
                    "failed": operation.get("failed") if isinstance(operation.get("failed"), int) else None,
                }
            comparisons.append({
                "scenario": "leaderboard",
                "persistence_mode": mode,
                "operation": operation_name,
                "by_cpu": by_cpu,
            })
    return comparisons


def aggregate_sources(specs: list[SourceSpec]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    observed_counts = [spec.cpu_count for spec in specs]
    add_check(
        checks,
        "source-cpu-counts",
        len(observed_counts) == len(set(observed_counts)) and set(observed_counts) == REQUIRED_CPU_COUNTS,
        f"required={sorted(REQUIRED_CPU_COUNTS)} observed={sorted(observed_counts)}",
    )

    sources: list[dict[str, Any]] = []
    for spec in sorted(specs, key=lambda item: item.cpu_count):
        source, source_checks = validate_source(spec)
        sources.append(source)
        checks.extend(source_checks)

    revisions = {source["candidate_revision"] for source in sources if source["candidate_revision"]}
    add_check(checks, "matrix:same-candidate", len(revisions) == 1, f"revisions={sorted(revisions)}")
    identities = {
        json.dumps(source["workload_identity"], sort_keys=True, separators=(",", ":"))
        for source in sources
    }
    add_check(checks, "matrix:same-workload", len(identities) == 1, f"identities={len(identities)}")
    manifests = {
        json.dumps(
            {
                "capacity": source["workload_identity"].get("capacity_case_identity"),
                "business": source["workload_identity"].get("business_capacity_case_identity"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        for source in sources
    }
    add_check(
        checks,
        "matrix:same-case-manifest",
        len(manifests) == 1,
        f"normalized_manifests={len(manifests)}",
    )

    evidence_complete = all(check["passed"] for check in checks)
    all_workload_gates_passed = (
        evidence_complete
        and all(
            source["capacity_release_gates_passed"]
            and source["business_release_gates_passed"]
            and source["r4_passed"]
            for source in sources
        )
    )
    public_sources = [
        {key: value for key, value in source.items() if key != "summaries"}
        for source in sources
    ]
    return {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "candidate_revision": next(iter(sorted(revisions)), ""),
        "required_cpu_counts": sorted(REQUIRED_CPU_COUNTS),
        "evidence_complete": evidence_complete,
        "all_workload_gates_passed": all_workload_gates_passed,
        "overall_pass": evidence_complete,
        "passed": evidence_complete,
        "failed_category": "" if evidence_complete else "cpu_capacity_evidence",
        "failed_step": next((check["name"] for check in checks if not check["passed"]), ""),
        "workload_identity": sources[0]["workload_identity"] if evidence_complete and sources else {},
        "sources": public_sources,
        "case_comparisons": build_case_comparisons(sources) if evidence_complete else [],
        "business_operation_comparisons": build_business_operation_comparisons(sources) if evidence_complete else [],
        "validation_checks": checks,
        "artifacts": {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        action="append",
        type=parse_source,
        required=True,
        help="Evidence source in CPU_COUNT:RUN_ID:EXTRACTED_DIR form; provide 1, 2, and 4 CPU sources.",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/cpu-capacity-matrix-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path
    summary = aggregate_sources(args.source)
    summary["artifacts"]["summary_path"] = str(summary_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        "cpu capacity evidence: "
        f"{'COMPLETE' if summary['evidence_complete'] else 'INVALID'}; "
        f"all workload gates passed={summary['all_workload_gates_passed']}"
    )
    print(f"summary: {summary_path}")
    return 0 if summary["evidence_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
