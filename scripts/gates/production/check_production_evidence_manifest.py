#!/usr/bin/env python3
"""Validate the R2 production-candidate evidence manifest against local summaries."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]


def load_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_generated_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def summary_passed(summary: dict[str, Any]) -> bool:
    if "overall_pass" in summary:
        return summary.get("overall_pass") is True
    return summary.get("passed") is True


def freshness_status(summary: dict[str, Any], max_age_hours: float, now: datetime) -> tuple[bool, float | None, str]:
    generated_at = parse_generated_at(summary.get("generated_at"))
    if generated_at is None:
        return True, None, "summary does not expose generated_at; freshness not enforced"
    age_hours = (now - generated_at).total_seconds() / 3600.0
    return age_hours <= max_age_hours, round(age_hours, 3), ""


def collect_artifact_paths(summary: dict[str, Any]) -> set[str]:
    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, dict):
        return set()
    return {str(value) for value in artifacts.values() if value}


def check_evidence(
    item: dict[str, Any],
    root: Path,
    now: datetime,
    require_fixed_runner: bool,
    parent_artifacts: dict[str, set[str]],
) -> dict[str, Any]:
    evidence_id = str(item.get("id", "unknown"))
    relative_path = str(item.get("path", ""))
    required = bool(item.get("required"))
    fixed_runner_required = bool(item.get("fixed_runner_required"))
    effective_required = required or (require_fixed_runner and fixed_runner_required)
    path = root / relative_path
    check: dict[str, Any] = {
        "name": evidence_id,
        "category": str(item.get("category", "")),
        "required": effective_required,
        "declared_required": required,
        "fixed_runner_required": fixed_runner_required,
        "path": relative_path,
        "passed": True,
        "status": "passed",
        "details": [],
    }

    if not path.exists():
        if effective_required:
            check["passed"] = False
            check["status"] = "missing"
            check["details"].append("required evidence summary is missing")
        else:
            check["status"] = "optional-missing"
            check["details"].append("optional fixed-runner/pre-production evidence is not present")
        return check

    summary = load_json(path)
    if not summary:
        check["passed"] = False
        check["status"] = "invalid-json"
        check["details"].append("summary is missing or is not a JSON object")
        return check

    if not effective_required and fixed_runner_required:
        check["status"] = "optional-present"
        check["details"].append("optional fixed-runner/pre-production evidence is present but not required in bounded mode")
        return check

    if not summary_passed(summary):
        check["passed"] = False
        check["status"] = "failed-summary"
        check["details"].append("summary did not pass")

    max_age = float(item.get("freshness_hours", 0) or 0)
    if max_age > 0:
        fresh, age_hours, message = freshness_status(summary, max_age, now)
        check["age_hours"] = age_hours
        if message:
            check["details"].append(message)
        if not fresh:
            check["passed"] = False
            check["status"] = "stale"
            check["details"].append(f"summary is older than {max_age:g} hours")

    artifact_of = item.get("artifact_of")
    if isinstance(artifact_of, str) and artifact_of:
        parent_paths = parent_artifacts.get(artifact_of, set())
        absolute = str(path)
        if relative_path not in parent_paths and absolute not in parent_paths:
            check["passed"] = False
            check["status"] = "artifact-mismatch"
            check["details"].append(f"summary is not listed as an artifact of {artifact_of}")

    if check["passed"] and check["status"] == "passed":
        check["details"].append("summary exists and passed")
    return check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "docs/production/production-candidate-evidence-manifest.json",
    )
    parser.add_argument("--require-fixed-runner", action="store_true")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=None,
    )
    args = parser.parse_args()

    manifest_path = args.manifest if args.manifest.is_absolute() else REPO_ROOT / args.manifest
    if args.summary_path is None:
        summary_path = REPO_ROOT / (
            "runtime/validation/r2-production-evidence-manifest-fixed-runner-summary.json"
            if args.require_fixed_runner
            else "runtime/validation/r2-production-evidence-manifest-summary.json"
        )
    else:
        summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path
    manifest = load_json(manifest_path)
    now = datetime.now(UTC)
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    entries = manifest.get("evidence")
    if not isinstance(entries, list):
        errors.append("manifest evidence must be a list")
        entries = []

    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("manifest evidence entry is not an object")
            continue
        evidence_id = str(entry.get("id", ""))
        if evidence_id in seen_ids:
            duplicate_ids.append(evidence_id)
        seen_ids.add(evidence_id)
    if duplicate_ids:
        errors.append("duplicate evidence ids: " + ", ".join(sorted(set(duplicate_ids))))

    required_categories = manifest.get("required_categories", [])
    categories = {str(entry.get("category", "")) for entry in entries if isinstance(entry, dict)}
    if isinstance(required_categories, list):
        missing_categories = sorted(str(category) for category in required_categories if str(category) not in categories)
        if missing_categories:
            errors.append("missing required categories: " + ", ".join(missing_categories))

    parent_artifacts: dict[str, set[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        evidence_id = str(entry.get("id", ""))
        path = REPO_ROOT / str(entry.get("path", ""))
        parent_artifacts[evidence_id] = collect_artifact_paths(load_json(path)) if path.exists() else set()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        check = check_evidence(entry, REPO_ROOT, now, args.require_fixed_runner, parent_artifacts)
        checks.append(check)
        if check["status"] == "optional-missing":
            warnings.append(f"{check['name']}: optional evidence missing")

    failed = [check for check in checks if not check.get("passed")]
    summary = {
        "summary_version": 2,
        "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "manifest_path": str(manifest_path),
        "require_fixed_runner": args.require_fixed_runner,
        "overall_pass": not errors and not failed,
        "passed": not errors and not failed,
        "failed_category": "manifest" if errors else ("evidence" if failed else ""),
        "failed_step": errors[0] if errors else (str(failed[0].get("name", "")) if failed else ""),
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "artifacts": {
            "summary_path": str(summary_path),
            "manifest_path": str(manifest_path),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(f"production evidence manifest: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    if errors:
        print("manifest errors:")
        for error in errors:
            print(f"  - {error}")
    if failed:
        print("failed evidence:")
        for check in failed:
            print(f"  - {check['name']}: {', '.join(str(item) for item in check.get('details', []))}")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
