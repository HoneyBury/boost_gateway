#!/usr/bin/env python3
"""Validate a production recovery drill record."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]

ALLOWED_ENVIRONMENT_TYPES = {
    "docker-compose",
    "native-process",
    "kubernetes",
    "systemd",
    "staging",
    "production",
}

ALLOWED_SCENARIOS = {
    "gateway_restart",
    "backend_restart",
    "redis_recovery",
    "compose_image_rollback",
    "k8s_rollout_rollback",
    "network_jitter",
    "config_reload",
}

SUMMARY_FIELDS = [
    "production_recovery_summary",
    "sdk_full_flow_summary",
    "redis_alert_runtime_summary",
    "docker_snapshot_summary",
    "k8s_full_flow_summary",
    "monitoring_summary",
]


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def get_path(record: dict[str, Any], dotted: str) -> Any:
    value: Any = record
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def parse_timestamp(value: Any) -> bool:
    if not non_empty_string(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def non_negative_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def non_empty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(non_empty_string(item) for item in value)


def resolve_record_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else REPO_ROOT / path


def validate_record(record: dict[str, Any], record_path: Path, allow_template: bool) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    is_template = record.get("template") is True

    add(checks, "record:template-allowed", allow_template or not is_template, "template records require --allow-template")
    add(checks, "record:summary-version", isinstance(record.get("summary_version"), int), "summary_version is numeric")
    add(checks, "record:drill-id", non_empty_string(record.get("drill_id")), "drill_id is present")
    add(checks, "record:executed-at", parse_timestamp(record.get("executed_at")), "executed_at is ISO-8601")
    add(checks, "record:operator", non_empty_string(record.get("operator")), "operator is present")

    environment = record.get("environment")
    add(checks, "environment:object", isinstance(environment, dict), "environment is an object")
    add(checks, "environment:type", get_path(record, "environment.type") in ALLOWED_ENVIRONMENT_TYPES, "environment.type is supported")
    add(checks, "environment:name", non_empty_string(get_path(record, "environment.name")), "environment.name is present")
    add(checks, "environment:git-commit", non_empty_string(get_path(record, "environment.git_commit")), "environment.git_commit is present")
    add(checks, "environment:image-before", non_empty_string(get_path(record, "environment.image_tag_before")), "image_tag_before is present")
    add(checks, "environment:image-after", non_empty_string(get_path(record, "environment.image_tag_after")), "image_tag_after is present")

    add(checks, "scenario:known", record.get("scenario") in ALLOWED_SCENARIOS, "scenario is one of the supported recovery drills")
    add(checks, "failure:method", non_empty_string(get_path(record, "failure_injection.method")), "failure injection method is present")
    add(checks, "failure:started-at", parse_timestamp(get_path(record, "failure_injection.started_at")), "failure start time is ISO-8601")
    add(checks, "failure:ended-at", parse_timestamp(get_path(record, "failure_injection.ended_at")), "failure end time is ISO-8601")

    add(checks, "recovery:actions", non_empty_list(get_path(record, "recovery.actions")), "recovery actions are recorded")
    add(checks, "recovery:rto", non_negative_number(get_path(record, "recovery.rto_seconds")), "RTO seconds is non-negative")
    add(checks, "recovery:rpo", non_negative_number(get_path(record, "recovery.rpo_seconds")), "RPO seconds is non-negative")
    add(checks, "recovery:data-risk", non_empty_string(get_path(record, "recovery.data_consistency_risk")), "data consistency risk is recorded")

    add(checks, "observability:alerts", non_empty_list(get_path(record, "observability.alerts_observed")), "observed alerts are recorded")
    add(checks, "observability:metrics", non_empty_list(get_path(record, "observability.metrics_checked")), "checked metrics are recorded")
    add(checks, "observability:logs", non_empty_list(get_path(record, "observability.log_sources")), "log sources are recorded")

    verification = record.get("verification")
    add(checks, "verification:object", isinstance(verification, dict), "verification is an object")
    add(checks, "verification:passed-bool", isinstance(get_path(record, "verification.passed"), bool), "verification.passed is boolean")
    add(
        checks,
        "verification:passed",
        is_template or get_path(record, "verification.passed") is True,
        "executed recovery drill passed",
    )

    summary_values = []
    if isinstance(verification, dict):
        summary_values = [verification.get(field, "") for field in SUMMARY_FIELDS]
    populated_summaries = [value for value in summary_values if non_empty_string(value)]
    add(checks, "verification:summary-present", bool(populated_summaries), "at least one validation summary path is recorded")

    if not is_template:
        for field, value in zip(SUMMARY_FIELDS, summary_values, strict=True):
            if not non_empty_string(value):
                continue
            path = resolve_record_path(value)
            add(checks, f"verification:{field}:exists", path.exists(), f"{field} exists: {path}")

    add(checks, "record:notes", non_empty_string(record.get("notes")), "notes are present")
    add(checks, "record:path", record_path.exists(), f"record file exists: {record_path}")
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True, help="Path to the recovery drill record JSON")
    parser.add_argument("--allow-template", action="store_true", help="Allow template=true records")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/recovery-drill-record-check-summary.json",
    )
    args = parser.parse_args()

    record_path = args.record if args.record.is_absolute() else REPO_ROOT / args.record
    summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path

    checks: list[dict[str, Any]] = []
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        add(checks, "record:json", isinstance(record, dict), "record JSON is an object")
        if isinstance(record, dict):
            checks.extend(validate_record(record, record_path, args.allow_template))
    except Exception as exc:  # noqa: BLE001 - validation tool should report parse failures in its summary.
        add(checks, "record:json", False, f"failed to load record: {exc}")

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "recovery_drill_record" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {
            "record_path": str(record_path),
            "summary_path": str(summary_path),
        },
    }

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"recovery drill record check: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks)-len(failed)}/{len(checks)} checks)")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
