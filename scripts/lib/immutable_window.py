"""Immutable TODO-0017 declaration and fixed-end evidence contracts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

try:
    from scripts.lib import deadman_evidence as io
except ModuleNotFoundError:
    import deadman_evidence as io

DURATION = 2592000
SAMPLES = 43200
ADMISSION_ROLES = {"lifecycle", "observability", "backup", "retention", "governance", "deadman"}
FINAL_ROLES = ADMISSION_ROLES | {"host_window", "ledger", "offhost"}


def record_identity(record: dict) -> dict:
    candidate = {key: record.get(key) for key in ("tag", "commit", "deployment_id")}
    runtime = record.get("image_ids", {}).get("GATEWAY_IMAGE_ID") or record.get("runtime_asset_sha256")
    io.require(isinstance(runtime, str), "missing runtime identity")
    candidate["runtime_digest"] = runtime if runtime.startswith("sha256:") else "sha256:" + runtime
    io.require(io.HEX.fullmatch(candidate["runtime_digest"].removeprefix("sha256:")) is not None,
               "invalid runtime identity")
    io.require(candidate["tag"] == "v3.6.7"
               and candidate["commit"] == "db0f905d0421b2052b9de7f49d9bf71787915e23"
               and candidate["deployment_id"] == "v3.6.7-fb5f6bfb2626-fa8b69b36dec",
               "unexpected frozen production candidate")
    return candidate


def admission(paths: dict[str, Path], *, now: datetime, final: bool = False) -> dict:
    """Require fresh independently produced reports; callers cannot substitute booleans."""
    io.require(set(paths) == (FINAL_ROLES if final else ADMISSION_ROLES), "missing admission report roles")
    result = {}
    for role, path in paths.items():
        report = io.read_json(path)
        io.require(report.get("overall_pass") is True, "admission report is not passing")
        timestamp = report.get("created_at") or report.get("generated_at") or report.get("checked_at")
        io.require(timestamp is not None, "admission report has no timestamp")
        age = (now - io.instant(timestamp)).total_seconds()
        io.require(0 <= age <= 1800, "admission report is stale or future-dated")
        result[role] = {"sha256": io.digest(path), "basename": path.name, "observed_at": timestamp}
    io.require(len({item["sha256"] for item in result.values()}) == len(result),
               "the same report cannot satisfy multiple admission roles")
    return result


def declare(*, start: datetime, now: datetime, record: dict, record_sha: str,
            host_id: str, endpoint: str, admission_reports: dict, attestation: dict,
            attestation_sha: str, sdk_version: str) -> dict:
    io.require(start.second == 0 and start.microsecond == 0, "start must be a natural UTC minute")
    io.require(120 <= (start - now).total_seconds() <= 3600, "start must be 2-60 minutes in the future")
    io.validate_attestation(attestation, now=now)
    subject = attestation["subject"]
    io.require(subject["canary_host_id_sha256"] == host_id
               and subject["candidate_record_sha256"] == record_sha, "deadman attestation subject mismatch")
    production_host = record.get("host", {}).get("host_id_sha256")
    io.require(bool(io.HEX.fullmatch(str(production_host))) and production_host != host_id, "observer is not off-host")
    io.require(sdk_version == "4.2.0", "record the admitted released canary SDK")
    io.require(endpoint == "tcp://100.65.71.117:9201", "unexpected frozen endpoint")
    end = start + timedelta(seconds=DURATION)
    return {"schema_version": 1, "task": "TODO-0017", "window_id": "todo0017-" + start.strftime("%Y%m%dT%H%MZ"),
            "declared_at": io.stamp(now), "start": io.stamp(start), "end": io.stamp(end),
            "duration_seconds": DURATION, "expected_samples": SAMPLES, "maintenance_windows": [],
            "candidate": record_identity(record), "candidate_record_sha256": record_sha,
            "host_boundary": {"production_host_id_sha256": production_host, "canary_host_id_sha256": host_id},
            "endpoint": endpoint, "sdk_version": sdk_version, "admission": admission_reports,
            "deadman_attestation_sha256": attestation_sha, "create_only": True,
            "secret_material_recorded": False}


def validate_declaration(value: dict) -> None:
    io.require(set(value) == {"schema_version", "task", "window_id", "declared_at", "start", "end",
        "duration_seconds", "expected_samples", "maintenance_windows", "candidate", "candidate_record_sha256",
        "host_boundary", "endpoint", "sdk_version", "admission", "deadman_attestation_sha256",
        "create_only", "secret_material_recorded"}, "window declaration schema mismatch")
    start, end = io.instant(value["start"]), io.instant(value["end"])
    io.require(value["task"] == "TODO-0017" and value["schema_version"] == 1
               and value["create_only"] is True and value["secret_material_recorded"] is False,
               "invalid declaration provenance")
    io.require((end - start).total_seconds() == DURATION and not start.second and not start.microsecond
               and io.instant(value["declared_at"]) < start and value["duration_seconds"] == DURATION
               and value["expected_samples"] == SAMPLES and value["maintenance_windows"] == [], "invalid fixed window")
    io.require(start != io.instant("2026-09-05T10:30:00Z"), "superseded historical window is forbidden")
    io.require(set(value["admission"]) == ADMISSION_ROLES, "incomplete admission binding")
    io.require(value["window_id"] == "todo0017-" + start.strftime("%Y%m%dT%H%MZ"), "invalid window ID")


def sample_manifest(root: Path, declaration: dict) -> dict:
    """Stream individual bounded samples; hash the exact ordered input inventory."""
    start, end = io.instant(declaration["start"]), io.instant(declaration["end"])
    hasher = hashlib.sha256()
    count = 0
    for path in sorted((root / "samples").glob("**/*.json")):
        sample = io.read_json(path, owner_uid=root.stat().st_uid)
        # Legacy sample timestamps can include fractions; schedule is always a whole minute.
        minute = io.instant(sample.get("scheduled_minute"))
        if not start <= minute < end:
            continue
        io.require(sample.get("candidate") == declaration["candidate"]
                   and sample.get("host_boundary") == declaration["host_boundary"]
                   and sample.get("endpoint") == declaration["endpoint"]
                   and sample.get("sdk_version") == declaration["sdk_version"], "sample identity drift")
        relative = path.relative_to(root).as_posix()
        hasher.update(json.dumps([relative, path.stat().st_size, io.digest(path)], separators=(",", ":")).encode() + b"\n")
        count += 1
    return {"sample_count": count, "sample_manifest_sha256": hasher.hexdigest()}


def validate_aggregate(report: dict, declaration: dict) -> None:
    io.require(report.get("overall_pass") is True and report.get("window") == "30d", "30-day aggregate did not pass")
    period = report.get("period", {})
    io.require(set(period) == {"start", "end", "duration_seconds"}
               and io.instant(period["start"]) == io.instant(declaration["start"])
               and io.instant(period["end"]) == io.instant(declaration["end"])
               and period["duration_seconds"] == DURATION, "aggregate fixed interval mismatch")
    io.require(report.get("candidate") == declaration["candidate"]
               and report.get("endpoint") == declaration["endpoint"]
               and report.get("candidate_consistent") is True, "aggregate candidate mismatch")
    io.require(report.get("expected_samples") == SAMPLES and report.get("maintenance_windows") == []
               and report.get("invalid_samples") == [], "aggregate has invalid or duplicate samples")
    for key in ("coverage_rate", "recorded_success_rate", "availability_including_approved_maintenance",
                "availability_excluding_approved_maintenance"):
        value = report.get(key)
        io.require(type(value) in {float, int} and math.isfinite(value) and 0.999 <= value <= 1,
                   "aggregate SLO failed")
    io.require(type(report.get("max_nonmaintenance_gap_minutes")) is int
               and 0 <= report["max_nonmaintenance_gap_minutes"] <= 2, "aggregate gap exceeded")


def validate_host_window(report: dict, declaration: dict) -> None:
    """Machine-readable final host audit, supplied by the protected host review."""
    io.require(report.get("start") == declaration["start"] and report.get("end") == declaration["end"]
               and report.get("candidate_record_sha256") == declaration["candidate_record_sha256"], "host audit binding mismatch")
    for key in ("unknown_restarts", "oom_events", "sustained_thermal_throttles", "unexplained_growth"):
        io.require(type(report.get(key)) is int and report[key] == 0, "host resource gate failed")
    for key, limit in (("host_memory_max_ratio", 0.8), ("filesystem_max_used_ratio", 0.75)):
        value = report.get(key)
        io.require(type(value) in {int, float} and math.isfinite(value) and 0 <= value < limit, "host resource limit exceeded")
    io.require(type(report.get("coverage_rate")) in {float, int} and 0.999 <= report["coverage_rate"] <= 1,
               "host monitoring coverage failed")
    io.require(type(report.get("max_gap_seconds")) is int and 0 <= report["max_gap_seconds"] <= 120,
               "host monitoring gap exceeded")
