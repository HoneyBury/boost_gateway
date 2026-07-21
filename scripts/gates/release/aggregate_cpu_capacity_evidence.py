#!/usr/bin/env python3
"""Aggregate comparable 1/2/4 CPU fixed-runner capacity evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_CPU_COUNTS = {1, 2, 4}
REQUIRED_CAPACITY_CASES = {
    "echo-1000-30s",
    "echo-5000-30s",
    "echo-10000-30s",
    "battle-100-30s",
    "battle-500-30s",
}
REQUIRED_BUSINESS_CASES = {
    "echo-1000-30s",
    "battle-100-30s",
    "battle-500-30s",
}
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class SourceSpec:
    cpu_count: int
    run_id: str
    extracted_dir: Path


def parse_source(value: str) -> SourceSpec:
    parts = value.split(":", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("source must be CPU_COUNT:RUN_ID:EXTRACTED_DIR")
    try:
        cpu_count = int(parts[0])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("source CPU_COUNT must be an integer") from exc
    if cpu_count <= 0:
        raise argparse.ArgumentTypeError("source CPU_COUNT must be positive")
    if not parts[1].isdigit():
        raise argparse.ArgumentTypeError("source RUN_ID must be numeric")
    if not parts[2]:
        raise argparse.ArgumentTypeError("source EXTRACTED_DIR must not be empty")
    return SourceSpec(cpu_count, parts[1], Path(parts[2]).expanduser().resolve())


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_path(root: Path, relative: str) -> Path:
    direct = root / relative
    if direct.is_file():
        return direct
    return root / "runtime" / relative


def parse_cpu_set(value: object) -> set[int]:
    if not isinstance(value, str) or not value:
        return set()
    cpus: set[int] = set()
    for item in value.split(","):
        if not item:
            return set()
        if "-" in item:
            bounds = item.split("-")
            if len(bounds) != 2 or not all(bound.isdigit() for bound in bounds):
                return set()
            start, end = (int(bound) for bound in bounds)
            if end < start:
                return set()
            cpus.update(range(start, end + 1))
        elif item.isdigit():
            cpus.add(int(item))
        else:
            return set()
    return cpus


def candidate_revision(summary: dict[str, Any]) -> str:
    provenance = summary.get("provenance")
    if isinstance(provenance, dict):
        revision = provenance.get("candidate_revision") or provenance.get("git_commit")
        if isinstance(revision, str):
            return revision
    revision = summary.get("git_commit")
    return revision if isinstance(revision, str) else ""


def case_map(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    aggregates = summary.get("case_aggregates")
    if not isinstance(aggregates, list):
        return {}
    return {
        str(item.get("case_name")): item
        for item in aggregates
        if isinstance(item, dict) and isinstance(item.get("case_name"), str)
    }


def business_scenario_map(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    business_perf = summary.get("business_operation_perf")
    aggregates = business_perf.get("scenario_aggregates") if isinstance(business_perf, dict) else None
    if not isinstance(aggregates, list):
        return {}
    return {
        str(item.get("scenario")): item
        for item in aggregates
        if isinstance(item, dict) and isinstance(item.get("scenario"), str)
    }


def metric(aggregate: dict[str, Any], name: str, stat: str) -> float | int | None:
    value = aggregate.get(name)
    if not isinstance(value, dict):
        return None
    observed = value.get(stat)
    return observed if isinstance(observed, (int, float)) and not isinstance(observed, bool) else None


def distribution_int(aggregate: dict[str, Any], name: str, stat: str, default: int = -1) -> int:
    observed = metric(aggregate, name, stat)
    return int(observed) if observed is not None else default


def normalized_case_identity(entry: object) -> dict[str, Any]:
    """Return comparison identity with the two experimental axes removed."""
    if not isinstance(entry, dict):
        return {}
    ignored = {
        "service_cpu_set",
        "service_cpu_count",
        "io_cores",
        "comparison_identity",
    }
    return {key: value for key, value in entry.items() if key not in ignored}


def case_manifest_map(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    manifest = summary.get("case_manifest")
    if not isinstance(manifest, list):
        return {}
    mapped: dict[str, dict[str, Any]] = {}
    for entry in manifest:
        if not isinstance(entry, dict):
            return {}
        name = entry.get("case_name") or entry.get("case_id")
        if not isinstance(name, str) or not name or name in mapped:
            return {}
        mapped[name] = entry
    return mapped


def validate_case_lifecycle(
    checks: list[dict[str, Any]],
    label: str,
    summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Require the post-fix manifest and real connection lifecycle for every aggregate."""
    manifest = case_manifest_map(summary)
    aggregates = case_map(summary)
    manifest_valid = (
        summary.get("case_manifest_version") == 1
        and bool(manifest)
        and set(manifest) == set(aggregates)
        and all(
            aggregate.get("case_identity") == manifest[name]
            for name, aggregate in aggregates.items()
        )
    )
    add_check(
        checks,
        f"{label}:case-manifest",
        manifest_valid,
        f"version={summary.get('case_manifest_version')} manifest={sorted(manifest)} aggregates={sorted(aggregates)}",
    )

    invalid: list[str] = []
    for name, aggregate in aggregates.items():
        target_min = distribution_int(aggregate, "target_clients", "min")
        target_max = distribution_int(aggregate, "target_clients", "max")
        valid = (
            target_min > 0
            and target_min == target_max
            and distribution_int(aggregate, "started_clients", "min") == target_min
            and distribution_int(aggregate, "tcp_connected_clients", "min") == target_min
            and distribution_int(aggregate, "authenticated_clients", "min") == target_min
            and distribution_int(aggregate, "peak_active_clients", "min") == target_min
            and distribution_int(aggregate, "cancelled_clients", "max") == 0
            and distribution_int(aggregate, "cancelled_before_connect", "max") == 0
            and aggregate.get("ramp_completed") is True
            and aggregate.get("measurement_started") is True
            and aggregate.get("steady_state_completed") is True
            and aggregate.get("bench_exit_code") == 0
            and aggregate.get("forced_timeout") is False
        )
        if not valid:
            invalid.append(name)
    lifecycle_valid = bool(aggregates) and not invalid
    add_check(
        checks,
        f"{label}:real-client-lifecycle",
        lifecycle_valid,
        f"aggregates={len(aggregates)} invalid={invalid}",
    )
    return manifest


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def validate_affinity(
    checks: list[dict[str, Any]],
    label: str,
    summary: dict[str, Any],
    expected_cpu_count: int,
    constraint_key: str,
) -> set[int]:
    constraint = summary.get(constraint_key)
    constraint = constraint if isinstance(constraint, dict) else {}
    requested = parse_cpu_set(constraint.get("requested"))
    effective = parse_cpu_set(constraint.get("effective_cpu_set"))
    processes = constraint.get("processes")
    process_evidence_valid = (
        isinstance(processes, list)
        and bool(processes)
        and all(
            isinstance(item, dict)
            and item.get("verified") is True
            and parse_cpu_set(item.get("requested_cpu_set")) == requested
            and parse_cpu_set(item.get("effective_cpu_set")) == effective
            for item in processes
        )
    )
    valid = (
        constraint.get("type") == "linux_cpu_affinity"
        and constraint.get("applied") is True
        and requested == effective
        and len(effective) == expected_cpu_count
        and constraint.get("cpu_count") == expected_cpu_count
        and process_evidence_valid
    )
    add_check(
        checks,
        f"{label}:cpu-affinity",
        valid,
        f"constraint={constraint_key} requested={sorted(requested)} effective={sorted(effective)} "
        f"expected_count={expected_cpu_count} processes={len(processes) if isinstance(processes, list) else 0}",
    )
    return effective


def validate_resource_deltas(
    checks: list[dict[str, Any]],
    label: str,
    summary: dict[str, Any],
    service_cpu_count: int,
    loadgen_cpu_count: int,
) -> None:
    resource_analysis = summary.get("resource_analysis")
    per_run = resource_analysis.get("per_run") if isinstance(resource_analysis, dict) else None
    process_snapshots = summary.get("process_snapshots")
    process_snapshots = process_snapshots if isinstance(process_snapshots, dict) else {}
    cases = summary.get("cases")
    expected_runs = len(cases) if isinstance(cases, list) else 0
    valid = isinstance(per_run, list) and len(per_run) == expected_runs and expected_runs > 0
    violations: list[str] = []
    if valid:
        for run in per_run:
            if not isinstance(run, dict) or float(run.get("elapsed_seconds", 0.0)) <= 0:
                valid = False
                violations.append("invalid run or elapsed time")
                continue
            services = run.get("services")
            loadgen = run.get("loadgen")
            raw = process_snapshots.get(run.get("case_name"))
            raw_valid = (
                isinstance(raw, dict)
                and isinstance(raw.get("before"), list)
                and bool(raw.get("before"))
                and isinstance(raw.get("after"), list)
                and bool(raw.get("after"))
                and isinstance(raw.get("loadgen"), dict)
                and isinstance(raw["loadgen"].get("before"), dict)
                and isinstance(raw["loadgen"].get("after"), dict)
                and float(raw.get("elapsed_seconds", 0.0)) > 0
                and isinstance(raw.get("quiescence"), dict)
                and raw["quiescence"].get("quiesced") is True
            )
            if (
                not isinstance(services, dict)
                or not services
                or not isinstance(loadgen, dict)
                or not raw_valid
            ):
                valid = False
                violations.append(f"{run.get('case_name')}: missing raw service/loadgen snapshots or deltas")
                continue
            cpu_values = [
                item.get("cpu_percent_from_cpu_seconds")
                for item in services.values()
                if isinstance(item, dict)
            ]
            loadgen_cpu = loadgen.get("cpu_percent_from_cpu_seconds")
            if (
                any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or value < 0
                    or value > service_cpu_count * 100 + 5
                    for value in cpu_values
                )
                or len(cpu_values) != len(services)
                or sum(float(value) for value in cpu_values if isinstance(value, (int, float)))
                > service_cpu_count * 100 + 5
                or not isinstance(loadgen_cpu, (int, float))
                or isinstance(loadgen_cpu, bool)
                or loadgen_cpu < 0
                or loadgen_cpu > loadgen_cpu_count * 100 + 5
            ):
                valid = False
                violations.append(f"{run.get('case_name')}: CPU delta outside physical constraint")
    add_check(
        checks,
        f"{label}:per-run-resource-deltas",
        valid,
        f"runs={len(per_run) if isinstance(per_run, list) else 0}/{expected_runs} violations={violations[:3]}",
    )


def validate_business_resource_window(
    checks: list[dict[str, Any]],
    label: str,
    summary: dict[str, Any],
    service_cpu_count: int,
    loadgen_cpu_count: int,
) -> None:
    business = summary.get("business_operation_perf")
    evidence = business.get("resource_evidence") if isinstance(business, dict) else None
    services = evidence.get("services") if isinstance(evidence, dict) else None
    loadgen = evidence.get("loadgen") if isinstance(evidence, dict) else None
    raw = evidence.get("raw") if isinstance(evidence, dict) else None
    service_cpu_values = [
        item.get("cpu_percent_from_cpu_seconds")
        for item in services.values()
        if isinstance(item, dict)
    ] if isinstance(services, dict) else []
    loadgen_cpu = loadgen.get("cpu_percent_from_cpu_seconds") if isinstance(loadgen, dict) else None
    valid = (
        isinstance(evidence, dict)
        and float(evidence.get("elapsed_seconds", 0.0)) > 0
        and isinstance(evidence.get("quiescence"), dict)
        and evidence["quiescence"].get("quiesced") is True
        and isinstance(services, dict)
        and bool(services)
        and isinstance(raw, dict)
        and all(isinstance(raw.get(key), (list, dict)) for key in (
            "service_before", "service_after", "loadgen_before", "loadgen_after"
        ))
        and len(service_cpu_values) == len(services)
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0 <= value <= service_cpu_count * 100 + 5
            for value in service_cpu_values
        )
        and sum(float(value) for value in service_cpu_values) <= service_cpu_count * 100 + 5
        and isinstance(loadgen_cpu, (int, float))
        and not isinstance(loadgen_cpu, bool)
        and 0 <= loadgen_cpu <= loadgen_cpu_count * 100 + 5
    )
    add_check(
        checks,
        f"{label}:business-operation-resource-window",
        valid,
        f"services={len(services) if isinstance(services, dict) else 0} loadgen_cpu={loadgen_cpu}",
    )


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
