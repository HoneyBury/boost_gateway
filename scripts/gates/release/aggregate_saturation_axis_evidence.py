#!/usr/bin/env python3
"""Aggregate trustworthy fixed-case saturation evidence along one runtime axis."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.gates.release.aggregate_cpu_capacity_evidence import (
    REPO_ROOT,
    add_check,
    candidate_revision,
    case_map,
    evidence_path,
    load_json,
    metric,
    normalized_case_identity,
    sha256_file,
    validate_affinity,
    validate_case_lifecycle,
    validate_resource_deltas,
)
from scripts.lib.evidence_provenance import validate_evidence_provenance


SUPPORTED_AXES = {"service_cpu_count", "io_cores"}
REQUIRED_AXIS_VALUES = {1, 2, 4}


@dataclass(frozen=True)
class AxisSourceSpec:
    axis: str
    value: int
    run_id: str
    extracted_dir: Path


@dataclass(frozen=True)
class SelectionSourceSpec:
    run_id: str
    extracted_dir: Path


def parse_source(value: str) -> AxisSourceSpec:
    parts = value.split(":", 3)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "source must be AXIS:VALUE:RUN_ID:EXTRACTED_DIR"
        )
    axis = parts[0]
    if axis not in SUPPORTED_AXES:
        raise argparse.ArgumentTypeError(
            f"source AXIS must be one of {sorted(SUPPORTED_AXES)}"
        )
    try:
        axis_value = int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("source VALUE must be an integer") from exc
    if axis_value <= 0:
        raise argparse.ArgumentTypeError("source VALUE must be positive")
    if not parts[2].isdigit():
        raise argparse.ArgumentTypeError("source RUN_ID must be numeric")
    if not parts[3]:
        raise argparse.ArgumentTypeError("source EXTRACTED_DIR must not be empty")
    return AxisSourceSpec(
        axis=axis,
        value=axis_value,
        run_id=parts[2],
        extracted_dir=Path(parts[3]).expanduser().resolve(),
    )


def parse_selection_source(value: str) -> SelectionSourceSpec:
    parts = value.split(":", 1)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            "selection source must be RUN_ID:EXTRACTED_DIR"
        )
    if not parts[0].isdigit():
        raise argparse.ArgumentTypeError("selection source RUN_ID must be numeric")
    if not parts[1]:
        raise argparse.ArgumentTypeError(
            "selection source EXTRACTED_DIR must not be empty"
        )
    return SelectionSourceSpec(
        run_id=parts[0],
        extracted_dir=Path(parts[1]).expanduser().resolve(),
    )


def find_perf_summary(root: Path) -> Path:
    candidates = [
        evidence_path(root, "perf/fixed-runner-saturation/summary.json"),
        root / "summary.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    matches = [
        path
        for path in root.glob("**/summary.json")
        if load_json(path).get("preset") == "saturation"
    ]
    return matches[0] if len(matches) == 1 else candidates[0]


def find_provenance_summary(root: Path) -> Path:
    candidates = [
        evidence_path(root, "validation/saturation-baseline-summary.json"),
        evidence_path(root, "validation/long-soak-capacity-summary.json"),
        evidence_path(root, "validation/perf-regression-summary.json"),
    ]
    return next((path for path in candidates if path.is_file()), candidates[0])


def gateway_cpu_percent(summary: dict[str, Any], case_name: str) -> float | None:
    resource = summary.get("resource_analysis")
    aggregates = resource.get("case_aggregates") if isinstance(resource, dict) else None
    if not isinstance(aggregates, list):
        return None
    for aggregate in aggregates:
        if not isinstance(aggregate, dict) or aggregate.get("case_name") != case_name:
            continue
        services = aggregate.get("services")
        gateway = services.get("v2_gateway_demo") if isinstance(services, dict) else None
        value = gateway.get("cpu_percent_from_cpu_seconds") if isinstance(gateway, dict) else None
        observed = value.get("median") if isinstance(value, dict) else None
        if isinstance(observed, (int, float)) and not isinstance(observed, bool):
            return float(observed)
    return None


def provenance_identity(provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        key: provenance.get(key)
        for key in (
            "runner", "runner_os", "runner_arch", "workflow", "build_configuration",
            "conan_lockfile", "conan_lockfile_sha256",
        )
    }


def validate_source(
    spec: AxisSourceSpec,
    fixed_case: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    perf_path = find_perf_summary(spec.extracted_dir)
    provenance_path = find_provenance_summary(spec.extracted_dir)
    perf = load_json(perf_path)
    envelope = load_json(provenance_path)
    add_check(checks, f"{spec.axis}-{spec.value}:perf-summary", bool(perf), str(perf_path))
    add_check(
        checks,
        f"{spec.axis}-{spec.value}:provenance-summary",
        bool(envelope),
        str(provenance_path),
    )

    provenance = envelope.get("provenance") if isinstance(envelope, dict) else None
    provenance_errors = validate_evidence_provenance(provenance, require_lockfile=True)
    add_check(
        checks,
        f"{spec.axis}-{spec.value}:provenance",
        not provenance_errors,
        f"errors={provenance_errors}",
    )
    provenance = provenance if isinstance(provenance, dict) else {}
    revision = candidate_revision(envelope)
    perf_revision = candidate_revision(perf)
    add_check(
        checks,
        f"{spec.axis}-{spec.value}:candidate-binding",
        bool(revision) and revision == perf_revision,
        f"envelope={revision} perf={perf_revision}",
    )
    add_check(
        checks,
        f"{spec.axis}-{spec.value}:run-id",
        str(provenance.get("run_id", "")) == spec.run_id,
        f"expected={spec.run_id} observed={provenance.get('run_id', '')}",
    )

    service_constraint = perf.get("service_resource_constraint")
    service_constraint = service_constraint if isinstance(service_constraint, dict) else {}
    loadgen_constraint = perf.get("loadgen_resource_constraint")
    loadgen_constraint = loadgen_constraint if isinstance(loadgen_constraint, dict) else {}
    expected_service_cpu_count = spec.value if spec.axis == "service_cpu_count" else 1
    service_set = validate_affinity(
        checks,
        f"{spec.axis}-{spec.value}:service",
        perf,
        expected_service_cpu_count,
        "service_resource_constraint",
    )
    loadgen_cpu_count = loadgen_constraint.get("cpu_count")
    loadgen_cpu_count = loadgen_cpu_count if isinstance(loadgen_cpu_count, int) else 0
    loadgen_set = validate_affinity(
        checks,
        f"{spec.axis}-{spec.value}:loadgen",
        perf,
        loadgen_cpu_count,
        "loadgen_resource_constraint",
    )
    add_check(
        checks,
        f"{spec.axis}-{spec.value}:service-loadgen-isolation",
        bool(service_set) and bool(loadgen_set) and service_set.isdisjoint(loadgen_set),
        f"service={sorted(service_set)} loadgen={sorted(loadgen_set)}",
    )

    topology = perf.get("topology")
    topology = topology if isinstance(topology, dict) else {}
    io_cores = topology.get("io_cores")
    topology_valid = (
        io_cores == spec.value if spec.axis == "io_cores"
        else isinstance(io_cores, int) and io_cores > 0
    )
    add_check(
        checks,
        f"{spec.axis}-{spec.value}:axis-binding",
        topology_valid
        and service_constraint.get("cpu_count") == expected_service_cpu_count
        and envelope.get("backend_pool_size") == topology.get("backend_connection_pool_size")
        and envelope.get("battle_route_workers") == topology.get("battle_route_workers"),
        f"service_cpu_count={service_constraint.get('cpu_count')} io_cores={io_cores}",
    )

    repetitions = perf.get("repetitions")
    add_check(
        checks,
        f"{spec.axis}-{spec.value}:three-repetitions",
        repetitions == 3,
        f"repetitions={repetitions}",
    )
    manifest = validate_case_lifecycle(checks, f"{spec.axis}-{spec.value}", perf)
    validate_resource_deltas(
        checks,
        f"{spec.axis}-{spec.value}",
        perf,
        expected_service_cpu_count,
        loadgen_cpu_count,
    )

    analysis = perf.get("saturation_analysis")
    analysis = analysis if isinstance(analysis, dict) else {}
    saturation_valid = (
        perf.get("preset") == "saturation"
        and analysis.get("collection_pass") is True
        and analysis.get("evidence_valid") is True
        and analysis.get("saturation_found") is False
        and analysis.get("analysis_mode") == "comparison_point"
        and analysis.get("curve_complete", False) is False
        and isinstance(analysis.get("points"), list)
        and len(analysis["points"]) == 1
        and analysis["points"][0].get("evidence_valid") is True
        and analysis["points"][0].get("resource_window_accepted") is True
        and len(manifest) == 1
    )
    add_check(
        checks,
        f"{spec.axis}-{spec.value}:saturation-collection",
        saturation_valid,
        f"conclusion={analysis.get('conclusion')} failures={analysis.get('failures')}",
    )

    selected_case = fixed_case
    if not selected_case and len(manifest) == 1:
        selected_case = next(iter(manifest))
    selected_identity = manifest.get(selected_case, {})
    aggregate = case_map(perf).get(selected_case, {})
    point_identity = (
        analysis["points"][0].get("case_identity")
        if isinstance(analysis.get("points"), list) and len(analysis["points"]) == 1
        else None
    )
    add_check(
        checks,
        f"{spec.axis}-{spec.value}:fixed-case",
        bool(selected_case)
        and bool(selected_identity)
        and bool(aggregate)
        and point_identity == selected_identity,
        f"case={selected_case}",
    )
    cpu_percent = gateway_cpu_percent(perf, selected_case)
    add_check(
        checks,
        f"{spec.axis}-{spec.value}:gateway-cpu",
        cpu_percent is not None and 0 <= cpu_percent <= expected_service_cpu_count * 100 + 5,
        f"cpu_percent={cpu_percent}",
    )

    return {
        "axis": spec.axis,
        "axis_value": spec.value,
        "run_id": spec.run_id,
        "extracted_dir": str(spec.extracted_dir),
        "candidate_revision": revision,
        "provenance_identity": provenance_identity(provenance),
        "service_cpu_count": expected_service_cpu_count,
        "service_cpu_set": service_constraint.get("effective_cpu_set", ""),
        "loadgen_cpu_count": loadgen_cpu_count,
        "loadgen_cpu_set": loadgen_constraint.get("effective_cpu_set", ""),
        "loadgen_io_threads": topology.get("loadgen_io_threads"),
        "io_cores": io_cores,
        "runtime_identity": {
            "backend_connection_pool_size": topology.get("backend_connection_pool_size"),
            "battle_route_workers": topology.get("battle_route_workers"),
            "battle_frame_push_every": topology.get("battle_frame_push_every"),
        },
        "fixed_case": selected_case,
        "fixed_case_identity": normalized_case_identity(selected_identity),
        "normalized_manifest": [
            normalized_case_identity(manifest[name]) for name in sorted(manifest)
        ],
        "metrics": {
            "throughput_msg_per_sec": metric(aggregate, "throughput_msg_per_sec", "median"),
            "latency_p99_ms": metric(aggregate, "latency_p99_ms", "median"),
            "gateway_cpu_percent": cpu_percent,
            "gateway_cpu_quota_percent": round(
                cpu_percent / (expected_service_cpu_count * 100.0) * 100.0, 4
            ) if cpu_percent is not None and expected_service_cpu_count else None,
            "achieved_send_rate_ops_per_sec": metric(
                aggregate, "achieved_send_rate_ops_per_sec", "median"
            ),
            "achieved_response_rate_ops_per_sec": metric(
                aggregate, "achieved_response_rate_ops_per_sec", "median"
            ),
        },
        "artifacts": {
            "perf_summary": {"path": str(perf_path), "sha256": sha256_file(perf_path) if perf_path.is_file() else ""},
            "provenance_summary": {
                "path": str(provenance_path),
                "sha256": sha256_file(provenance_path) if provenance_path.is_file() else "",
            },
        },
    }, checks


def validate_selection_source(
    spec: SelectionSourceSpec | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    root = spec.extracted_dir if spec is not None else Path()
    perf_path = find_perf_summary(root) if spec is not None else Path()
    provenance_path = find_provenance_summary(root) if spec is not None else Path()
    summary = load_json(perf_path) if spec is not None else {}
    envelope = load_json(provenance_path) if spec is not None else {}
    add_check(
        checks,
        "selection:summary",
        bool(summary),
        str(perf_path) if spec is not None else "selection source is required",
    )
    add_check(
        checks,
        "selection:provenance-summary",
        bool(envelope),
        str(provenance_path) if spec is not None else "selection source is required",
    )
    provenance = envelope.get("provenance") if isinstance(envelope, dict) else None
    provenance_errors = validate_evidence_provenance(provenance, require_lockfile=True)
    provenance = provenance if isinstance(provenance, dict) else {}
    fixed_runner_valid = (
        not provenance_errors
        and provenance.get("workflow") != "local"
        and provenance.get("runner") != "local"
        and provenance.get("runner_os") == "Linux"
    )
    add_check(
        checks,
        "selection:provenance",
        fixed_runner_valid,
        f"errors={provenance_errors} workflow={provenance.get('workflow')} runner={provenance.get('runner')}",
    )
    selection_revision = candidate_revision(envelope)
    perf_revision = candidate_revision(summary)
    add_check(
        checks,
        "selection:candidate-binding",
        bool(selection_revision) and selection_revision == perf_revision,
        f"envelope={selection_revision} perf={perf_revision}",
    )
    add_check(
        checks,
        "selection:run-id",
        spec is not None and str(provenance.get("run_id", "")) == spec.run_id,
        f"expected={spec.run_id if spec is not None else ''} observed={provenance.get('run_id', '')}",
    )
    analysis = summary.get("saturation_analysis")
    analysis = analysis if isinstance(analysis, dict) else {}
    points = analysis.get("points")
    point_entries = points if isinstance(points, list) else []
    curve_valid = (
        summary.get("preset") == "saturation"
        and summary.get("repetitions") == 3
        and analysis.get("collection_pass") is True
        and analysis.get("evidence_valid") is True
        and analysis.get("saturation_found") is True
        and analysis.get("analysis_mode") == "curve"
        and analysis.get("curve_complete", True) is True
        and isinstance(points, list)
        and len(points) >= 3
        and all(
            isinstance(point, dict)
            and point.get("evidence_valid") is True
            and point.get("resource_window_accepted") is True
            for point in points
        )
    )
    add_check(
        checks,
        "selection:complete-saturation-curve",
        curve_valid,
        f"mode={analysis.get('analysis_mode')} points={len(points) if isinstance(points, list) else 0} conclusion={analysis.get('conclusion')}",
    )
    manifest = validate_case_lifecycle(checks, "selection", summary)
    candidate = analysis.get("fixed_case_candidate") or analysis.get("cpu_saturation_case")
    candidate_name = ""
    if isinstance(candidate, dict):
        observed_name = candidate.get("case_name") or candidate.get("case_id")
        candidate_name = observed_name if isinstance(observed_name, str) else ""
    candidate_valid = (
        bool(candidate_name)
        and candidate_name in manifest
        and candidate == manifest[candidate_name]
        and any(
            isinstance(point, dict) and point.get("case_identity") == candidate
            for point in point_entries
        )
    )
    add_check(
        checks,
        "selection:fixed-case-candidate",
        candidate_valid,
        f"case={candidate_name}",
    )
    service_constraint = summary.get("service_resource_constraint")
    service_constraint = service_constraint if isinstance(service_constraint, dict) else {}
    loadgen_constraint = summary.get("loadgen_resource_constraint")
    loadgen_constraint = loadgen_constraint if isinstance(loadgen_constraint, dict) else {}
    service_cpu_count = service_constraint.get("cpu_count")
    loadgen_cpu_count = loadgen_constraint.get("cpu_count")
    service_cpu_count = service_cpu_count if isinstance(service_cpu_count, int) else 0
    loadgen_cpu_count = loadgen_cpu_count if isinstance(loadgen_cpu_count, int) else 0
    service_set = validate_affinity(
        checks,
        "selection:service",
        summary,
        service_cpu_count,
        "service_resource_constraint",
    )
    loadgen_set = validate_affinity(
        checks,
        "selection:loadgen",
        summary,
        loadgen_cpu_count,
        "loadgen_resource_constraint",
    )
    add_check(
        checks,
        "selection:service-loadgen-isolation",
        bool(service_set) and bool(loadgen_set) and service_set.isdisjoint(loadgen_set),
        f"service={sorted(service_set)} loadgen={sorted(loadgen_set)}",
    )
    validate_resource_deltas(
        checks,
        "selection",
        summary,
        service_cpu_count,
        loadgen_cpu_count,
    )
    topology = summary.get("topology")
    topology = topology if isinstance(topology, dict) else {}
    add_check(
        checks,
        "selection:runtime-binding",
        envelope.get("backend_pool_size") == topology.get("backend_connection_pool_size")
        and envelope.get("battle_route_workers") == topology.get("battle_route_workers"),
        f"backend_pool_size={envelope.get('backend_pool_size')} battle_route_workers={envelope.get('battle_route_workers')}",
    )
    return {
        "run_id": spec.run_id if spec is not None else "",
        "extracted_dir": str(spec.extracted_dir) if spec is not None else "",
        "candidate_revision": selection_revision,
        "provenance_identity": provenance_identity(provenance),
        "service_cpu_count": service_cpu_count,
        "service_cpu_set": service_constraint.get("effective_cpu_set", ""),
        "loadgen_cpu_count": loadgen_cpu_count,
        "loadgen_cpu_set": loadgen_constraint.get("effective_cpu_set", ""),
        "loadgen_io_threads": topology.get("loadgen_io_threads"),
        "fixed_case": candidate_name,
        "fixed_case_identity": normalized_case_identity(candidate),
        "point_count": len(points) if isinstance(points, list) else 0,
        "runtime_identity": {
            "backend_connection_pool_size": topology.get("backend_connection_pool_size"),
            "battle_route_workers": topology.get("battle_route_workers"),
            "battle_frame_push_every": topology.get("battle_frame_push_every"),
        },
        "artifacts": {
            "perf_summary": {
                "path": str(perf_path),
                "sha256": sha256_file(perf_path) if perf_path.is_file() else "",
            },
            "provenance_summary": {
                "path": str(provenance_path),
                "sha256": sha256_file(provenance_path) if provenance_path.is_file() else "",
            },
        },
    }, checks


def ratio(observed: object, baseline: object) -> float | None:
    if (
        not isinstance(observed, (int, float))
        or isinstance(observed, bool)
        or not isinstance(baseline, (int, float))
        or isinstance(baseline, bool)
        or baseline <= 0
    ):
        return None
    return round(float(observed) / float(baseline), 4)


def build_comparison(sources: list[dict[str, Any]]) -> dict[str, Any]:
    by_value = {str(source["axis_value"]): source["metrics"] for source in sources}
    baseline = by_value.get("1", {})
    speedup: dict[str, dict[str, float | None]] = {}
    efficiency: dict[str, dict[str, float | None]] = {}
    for value in (1, 2, 4):
        key = str(value)
        speedup[key] = {}
        efficiency[key] = {}
        for metric_name in (
            "throughput_msg_per_sec",
            "achieved_send_rate_ops_per_sec",
            "achieved_response_rate_ops_per_sec",
        ):
            observed_ratio = ratio(
                by_value.get(key, {}).get(metric_name), baseline.get(metric_name)
            )
            speedup[key][metric_name] = observed_ratio
            efficiency[key][metric_name] = (
                round(observed_ratio / value, 4) if observed_ratio is not None else None
            )
    return {
        "by_axis_value": by_value,
        "speedup_vs_value_1": speedup,
        "scaling_efficiency": efficiency,
    }


def build_decision(axis: str, comparison: dict[str, Any]) -> dict[str, Any]:
    speedup = comparison["speedup_vs_value_1"].get("4", {}).get("throughput_msg_per_sec")
    efficiency = comparison["scaling_efficiency"].get("4", {}).get("throughput_msg_per_sec")
    if speedup is None or efficiency is None:
        observation = "insufficient_metrics"
    elif axis == "service_cpu_count" and speedup >= 3.2 and efficiency >= 0.8:
        observation = "near_linear_cpu_scaling"
    elif axis == "service_cpu_count" and speedup < 1.2:
        observation = "offered_load_limited_or_shared_bottleneck"
    elif axis == "io_cores" and speedup >= 1.5:
        observation = "material_io_core_gain"
    elif axis == "io_cores":
        observation = "no_material_io_core_gain"
    else:
        observation = "partial_cpu_scaling"
    return {
        "policy_version": 1,
        "observation": observation,
        "automatic_default_change": False,
        "recommended_action": "retain_current_default_pending_manual_review",
        "note": "This aggregate is decision evidence and never edits runtime defaults.",
    }


def aggregate_axis_sources(
    specs: list[AxisSourceSpec],
    *,
    fixed_case: str = "",
    selection_source: SelectionSourceSpec | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    selection, selection_checks = validate_selection_source(selection_source)
    checks.extend(selection_checks)
    selected_case = fixed_case or selection["fixed_case"]
    axes = {spec.axis for spec in specs}
    values = [spec.value for spec in specs]
    source_shape_valid = (
        len(axes) == 1
        and len(values) == len(set(values))
        and set(values) == REQUIRED_AXIS_VALUES
    )
    add_check(
        checks,
        "axis-source-shape",
        source_shape_valid,
        f"axes={sorted(axes)} required_values={sorted(REQUIRED_AXIS_VALUES)} observed={sorted(values)}",
    )
    axis = next(iter(axes), "")
    sources: list[dict[str, Any]] = []
    for spec in sorted(specs, key=lambda item: item.value):
        source, source_checks = validate_source(spec, selected_case)
        sources.append(source)
        checks.extend(source_checks)

    revisions = {source["candidate_revision"] for source in sources if source["candidate_revision"]}
    add_check(checks, "axis:same-candidate", len(revisions) == 1, f"revisions={sorted(revisions)}")
    add_check(
        checks,
        "axis:selection-candidate-binding",
        len(revisions) == 1
        and selection["candidate_revision"] in revisions
        and all(source["fixed_case"] == selection["fixed_case"] for source in sources)
        and all(source["fixed_case_identity"] == selection["fixed_case_identity"] for source in sources),
        f"selection_revision={selection['candidate_revision']} axis_revisions={sorted(revisions)} case={selection['fixed_case']}",
    )
    provenance_identities = {
        json.dumps(source["provenance_identity"], sort_keys=True, separators=(",", ":"))
        for source in sources
    }
    add_check(
        checks,
        "axis:same-fixed-runner-provenance",
        len(provenance_identities) == 1
        and bool(sources)
        and sources[0]["provenance_identity"] == selection["provenance_identity"],
        f"axis_identities={len(provenance_identities)} selection={selection['provenance_identity']}",
    )
    loadgen_identities = {
        (source["loadgen_cpu_count"], source["loadgen_cpu_set"], source["loadgen_io_threads"])
        for source in sources
    }
    add_check(
        checks,
        "axis:same-loadgen",
        len(loadgen_identities) == 1
        and (
            selection["loadgen_cpu_count"],
            selection["loadgen_cpu_set"],
            selection["loadgen_io_threads"],
        ) in loadgen_identities,
        f"axis={sorted(loadgen_identities, key=str)} selection={(selection['loadgen_cpu_count'], selection['loadgen_cpu_set'], selection['loadgen_io_threads'])}",
    )
    runtime_identities = {
        json.dumps(source["runtime_identity"], sort_keys=True, separators=(",", ":"))
        for source in sources
    }
    add_check(
        checks,
        "axis:same-runtime-configuration",
        len(runtime_identities) == 1
        and bool(sources)
        and sources[0]["runtime_identity"] == selection["runtime_identity"],
        f"axis_identities={len(runtime_identities)} selection={selection['runtime_identity']}",
    )
    fixed_identities = {
        json.dumps(source["fixed_case_identity"], sort_keys=True, separators=(",", ":"))
        for source in sources
    }
    add_check(checks, "axis:same-fixed-case", len(fixed_identities) == 1, f"identities={len(fixed_identities)}")
    manifest_identities = {
        json.dumps(source["normalized_manifest"], sort_keys=True, separators=(",", ":"))
        for source in sources
    }
    add_check(checks, "axis:same-case-manifest", len(manifest_identities) == 1, f"identities={len(manifest_identities)}")

    if axis == "service_cpu_count":
        fixed_non_axis = {(source["io_cores"], source["service_cpu_set"] == "") for source in sources}
        non_axis_valid = len({source["io_cores"] for source in sources}) == 1
    else:
        fixed_non_axis = {(source["service_cpu_count"], source["service_cpu_set"]) for source in sources}
        non_axis_valid = len(fixed_non_axis) == 1 and all(
            source["service_cpu_count"] == 1 for source in sources
        )
    add_check(
        checks,
        "axis:single-variable",
        non_axis_valid,
        f"non_axis_identity={sorted(fixed_non_axis, key=str)}",
    )

    evidence_complete = all(check["passed"] for check in checks)
    comparison = build_comparison(sources) if evidence_complete else {}
    return {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "axis": axis,
        "required_axis_values": sorted(REQUIRED_AXIS_VALUES),
        "fixed_case": sources[0]["fixed_case"] if evidence_complete and sources else "",
        "candidate_revision": next(iter(sorted(revisions)), ""),
        "evidence_complete": evidence_complete,
        "overall_pass": evidence_complete,
        "passed": evidence_complete,
        "failed_category": "" if evidence_complete else "saturation_axis_evidence",
        "failed_step": next((check["name"] for check in checks if not check["passed"]), ""),
        "sources": sources,
        "selection_source": selection,
        "comparison": comparison,
        "decision": build_decision(axis, comparison) if evidence_complete else {},
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
        help="AXIS:VALUE:RUN_ID:EXTRACTED_DIR; provide values 1, 2, and 4 for one axis.",
    )
    parser.add_argument(
        "--fixed-case",
        default="",
        help="Explicit saturation case to compare; must still match the selection source candidate.",
    )
    parser.add_argument(
        "--selection-source",
        type=parse_selection_source,
        default=None,
        help="Full saturation-curve artifact in RUN_ID:EXTRACTED_DIR form.",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/saturation-axis-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path
    summary = aggregate_axis_sources(
        args.source,
        fixed_case=args.fixed_case,
        selection_source=args.selection_source,
    )
    summary["artifacts"]["summary_path"] = str(summary_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"saturation {summary['axis'] or 'axis'} evidence: "
        f"{'COMPLETE' if summary['evidence_complete'] else 'INVALID'}"
    )
    print(f"summary: {summary_path}")
    return 0 if summary["evidence_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
