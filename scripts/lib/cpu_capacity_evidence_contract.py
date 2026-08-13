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


REPO_ROOT = Path(__file__).resolve().parents[2]
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
