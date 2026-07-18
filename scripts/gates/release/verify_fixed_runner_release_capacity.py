#!/usr/bin/env python3
"""Validate R4 fixed-runner release/capacity performance evidence."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lib.evidence_provenance import (
    build_evidence_provenance,
    validate_evidence_provenance,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def load_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def tail(value: str | bytes | None, max_chars: int = 5000) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return text if len(text) <= max_chars else text[-max_chars:]


def run_step(name: str, category: str, command: list[str], timeout_seconds: int) -> dict[str, Any]:
    print(f"==> {name}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "category": category,
            "command": command,
            "status": "timeout",
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(exc.stdout),
            "stderr_tail": tail(exc.stderr),
        }

    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return {
        "name": name,
        "category": category,
        "command": command,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(completed.stdout),
        "stderr_tail": tail(completed.stderr),
    }


def release_summary_passed(summary: dict[str, Any]) -> bool:
    if "overall_pass" in summary:
        return summary.get("overall_pass") is True
    return summary.get("passed") is True


def perf_summary_passed(summary: dict[str, Any]) -> bool:
    gates = summary.get("release_gates")
    return isinstance(gates, dict) and gates.get("overall_pass") is True


def observed_cases(summary: dict[str, Any]) -> set[str]:
    gates = summary.get("release_gates")
    if not isinstance(gates, dict):
        return set()
    checks = gates.get("checks")
    if not isinstance(checks, list):
        return set()
    return {str(check.get("case", "")) for check in checks if isinstance(check, dict)}


def business_flow_passed(summary: dict[str, Any]) -> bool:
    business_flow = summary.get("business_flow")
    if not isinstance(business_flow, dict):
        return False
    return business_flow.get("passed") is True


def integer_or(value: object, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def validate_leaderboard_redis_comparison(
    path: Path,
    required: bool,
    min_repetitions: int,
) -> dict[str, Any]:
    summary = load_json(path)
    comparison = summary.get("leaderboard_persistence_comparison") if summary else None
    if not isinstance(comparison, dict):
        return {
            "name": "leaderboard-redis-persistence-comparison",
            "category": "business_capacity",
            "path": str(path),
            "required": required,
            "status": "missing" if required else "optional-missing",
            "passed": not required,
            "details": ["leaderboard_persistence_comparison is missing"],
        }

    details: list[str] = []
    repetitions = integer_or(comparison.get("repetitions_per_mode"))
    modes = comparison.get("modes")
    mode_checks: list[bool] = []
    for mode in ("in_memory_only", "redis_primary_with_memory_shadow"):
        mode_entry = modes.get(mode) if isinstance(modes, dict) else None
        mode_summary = mode_entry.get("summary") if isinstance(mode_entry, dict) else None
        aggregate = leaderboard_aggregate_for_gate(mode_summary)
        operations = aggregate.get("operations", [])
        operation_names = {
            operation.get("operation")
            for operation in operations
            if isinstance(operation, dict)
        }
        mode_ok = (
            isinstance(mode_entry, dict)
            and mode_entry.get("log_verified") is True
            and aggregate.get("passed") is True
            and integer_or(aggregate.get("runs")) >= min_repetitions
            and integer_or(aggregate.get("passed_runs")) == integer_or(aggregate.get("runs"))
            and operation_names
            == {"leaderboard_submit", "leaderboard_top", "leaderboard_rank"}
            and all(integer_or(operation.get("failed"), -1) == 0 for operation in operations)
        )
        mode_checks.append(mode_ok)
        details.append(f"{mode}: runs={aggregate.get('runs', 0)} log_verified={bool(mode_entry and mode_entry.get('log_verified'))}")

    proof = comparison.get("redis_proof")
    proof_ok = (
        isinstance(proof, dict)
        and proof.get("verified") is True
        and proof.get("ping_before") is True
        and proof.get("ping_after") is True
        and bool(proof.get("key"))
        and integer_or(proof.get("zcard"), -1)
        >= integer_or(proof.get("expected_min_zcard"), -1)
        > 0
    )
    passed = (
        comparison.get("requested") is True
        and comparison.get("verified") is True
        and repetitions >= min_repetitions
        and all(mode_checks)
        and proof_ok
    )
    details.extend((
        f"repetitions_per_mode={repetitions}",
        f"redis_proof_verified={proof_ok}",
    ))
    return {
        "name": "leaderboard-redis-persistence-comparison",
        "category": "business_capacity",
        "path": str(path),
        "required": required,
        "status": "passed" if passed else "failed-summary",
        "passed": passed,
        "details": details,
    }


def leaderboard_aggregate_for_gate(summary: object) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    aggregates = summary.get("scenario_aggregates")
    if not isinstance(aggregates, list):
        return {}
    return next(
        (item for item in aggregates if isinstance(item, dict) and item.get("scenario") == "leaderboard"),
        {},
    )


def validate_release_summary(
    path: Path,
    required: bool,
    expected_candidate_revision: str,
) -> dict[str, Any]:
    summary = load_json(path)
    if not summary:
        return {
            "name": "release-baseline-summary",
            "category": "release_baseline",
            "path": str(path),
            "required": required,
            "status": "missing" if required else "optional-missing",
            "passed": not required,
            "details": ["summary is missing or invalid"],
        }
    provenance_errors = validate_evidence_provenance(
        summary.get("provenance"),
        expected_candidate_revision=expected_candidate_revision,
    )
    passed = release_summary_passed(summary) and not provenance_errors
    details = [
        "release baseline aggregate passed"
        if release_summary_passed(summary)
        else "release baseline aggregate did not pass"
    ]
    details.extend(provenance_errors)
    return {
        "name": "release-baseline-summary",
        "category": "release_baseline",
        "path": str(path),
        "required": required,
        "status": "passed" if passed else "failed-summary",
        "passed": passed,
        "details": details,
    }


def validate_perf_summary(
    name: str,
    category: str,
    path: Path,
    required_cases: set[str],
    require_business_flow: bool,
    required_preset: str,
    min_repetitions: int,
    expected_candidate_revision: str,
) -> dict[str, Any]:
    summary = load_json(path)
    if not summary:
        return {
            "name": name,
            "category": category,
            "path": str(path),
            "required": True,
            "status": "missing",
            "passed": False,
            "details": ["summary is missing or invalid"],
        }

    cases = observed_cases(summary)
    missing_cases = sorted(required_cases - cases)
    gates_passed = perf_summary_passed(summary)
    business_passed = business_flow_passed(summary) if require_business_flow else True
    preset = str(summary.get("preset", ""))
    repetitions_raw = summary.get("repetitions", 0)
    try:
        repetitions = int(repetitions_raw)
    except (TypeError, ValueError):
        repetitions = 0
    preset_ok = preset == required_preset
    repetitions_ok = repetitions >= min_repetitions
    observed_revision = str(summary.get("git_commit", ""))
    revision_ok = observed_revision == expected_candidate_revision
    passed = (
        gates_passed
        and not missing_cases
        and business_passed
        and preset_ok
        and repetitions_ok
        and revision_ok
    )
    details = [
        f"release_gates.overall_pass={gates_passed}",
        f"preset={preset}",
        f"repetitions={repetitions}",
        f"git_commit={observed_revision}",
        "cases=" + ",".join(sorted(cases)),
    ]
    if not preset_ok:
        details.append(f"expected preset={required_preset}")
    if not repetitions_ok:
        details.append(f"expected repetitions>={min_repetitions}")
    if missing_cases:
        details.append("missing cases: " + ",".join(missing_cases))
    if require_business_flow:
        details.append(f"business_flow.passed={business_passed}")
    if not revision_ok:
        details.append(f"expected git_commit={expected_candidate_revision}")
    return {
        "name": name,
        "category": category,
        "path": str(path),
        "required": True,
        "status": "passed" if passed else "failed-summary",
        "passed": passed,
        "preset": preset,
        "repetitions": repetitions,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/release")
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--collect-smoke", action="store_true", help="Collect fresh smoke evidence before validating existing capacity artifacts.")
    parser.add_argument("--step-timeout-seconds", type=int, default=900)
    parser.add_argument("--min-capacity-repetitions", type=int, default=3)
    parser.add_argument("--require-leaderboard-redis-comparison", action="store_true")
    parser.add_argument(
        "--release-summary",
        type=Path,
        default=REPO_ROOT / "runtime/validation/p6-release-baseline-summary.json",
    )
    parser.add_argument(
        "--capacity-summary",
        type=Path,
        default=REPO_ROOT / "runtime/perf/p1-capacity-battle-lock/summary.json",
    )
    parser.add_argument(
        "--business-capacity-summary",
        type=Path,
        default=REPO_ROOT / "runtime/perf/p0-business-capacity-local-r2/summary.json",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/fixed-runner-release-capacity-summary.json",
    )
    args = parser.parse_args()

    summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path
    release_summary_path = args.release_summary if args.release_summary.is_absolute() else REPO_ROOT / args.release_summary
    capacity_summary_path = args.capacity_summary if args.capacity_summary.is_absolute() else REPO_ROOT / args.capacity_summary
    business_capacity_summary_path = args.business_capacity_summary if args.business_capacity_summary.is_absolute() else REPO_ROOT / args.business_capacity_summary
    build_dir = args.build_dir if args.build_dir.is_absolute() else REPO_ROOT / args.build_dir
    steps: list[dict[str, Any]] = []
    provenance = build_evidence_provenance(
        REPO_ROOT,
        build_configuration=args.configuration,
    )
    candidate_revision = str(provenance["candidate_revision"])

    if (args.collect_smoke or not release_summary_path.exists()) and not args.skip_collect:
        smoke_summary = REPO_ROOT / "runtime/validation/r4-release-smoke-summary.json"
        steps.append(
            run_step(
                "R4 fresh smoke release baseline",
                "release_smoke",
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/collect_release_baseline.py"),
                    "--build-dir",
                    str(build_dir),
                    "--configuration",
                    args.configuration,
                    "--skip-build",
                    "--perf-preset",
                    "smoke",
                    "--perf-repetitions",
                    "1",
                    "--skip-perf",
                    "--summary-path",
                    str(smoke_summary),
                ],
                args.step_timeout_seconds,
            )
        )
        if steps[-1].get("status") == "passed":
            release_summary_path = smoke_summary

    provenance_errors = validate_evidence_provenance(provenance)
    checks = [
        {
            "name": "r4-evidence-provenance",
            "category": "evidence_provenance",
            "required": True,
            "status": "passed" if not provenance_errors else "provenance-invalid",
            "passed": not provenance_errors,
            "details": provenance_errors or ["R4 producer provenance matches the checked-out candidate"],
        },
        validate_release_summary(
            release_summary_path,
            required=True,
            expected_candidate_revision=candidate_revision,
        ),
        validate_perf_summary(
            "capacity-profile-summary",
            "capacity",
            capacity_summary_path,
            {"echo-1000-30s", "echo-5000-30s", "echo-10000-30s", "battle-100-30s", "battle-500-30s"},
            require_business_flow=False,
            required_preset="capacity",
            min_repetitions=args.min_capacity_repetitions,
            expected_candidate_revision=candidate_revision,
        ),
        validate_perf_summary(
            "business-capacity-summary",
            "business_capacity",
            business_capacity_summary_path,
            {"echo-1000-30s", "battle-100-30s", "battle-500-30s"},
            require_business_flow=True,
            required_preset="business-capacity",
            min_repetitions=args.min_capacity_repetitions,
            expected_candidate_revision=candidate_revision,
        ),
    ]
    if args.require_leaderboard_redis_comparison:
        checks.append(validate_leaderboard_redis_comparison(
            business_capacity_summary_path,
            required=True,
            min_repetitions=args.min_capacity_repetitions,
        ))

    failed_step = next((step for step in steps if step.get("status") != "passed"), None)
    failed_check = next((check for check in checks if check.get("passed") is not True), None)
    passed = failed_step is None and failed_check is None
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "provenance": provenance,
        "overall_pass": passed,
        "passed": passed,
        "failed_category": str(failed_step.get("category", "")) if failed_step else ("capacity_evidence" if failed_check else ""),
        "failed_step": str(failed_step.get("name", "")) if failed_step else (str(failed_check.get("name", "")) if failed_check else ""),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "host": platform.node(),
        },
        "scope": {
            "release_baseline_required": True,
            "capacity_required_cases": ["echo-1000-30s", "echo-5000-30s", "echo-10000-30s", "battle-100-30s", "battle-500-30s"],
            "business_capacity_required_cases": ["echo-1000-30s", "battle-100-30s", "battle-500-30s"],
            "business_flow_required": True,
            "required_capacity_preset": "capacity",
            "required_business_capacity_preset": "business-capacity",
            "min_capacity_repetitions": args.min_capacity_repetitions,
            "leaderboard_redis_comparison_required": args.require_leaderboard_redis_comparison,
        },
        "steps": steps,
        "checks": checks,
        "artifacts": {
            "summary_path": str(summary_path),
            "release_summary_path": str(release_summary_path),
            "capacity_summary_path": str(capacity_summary_path),
            "business_capacity_summary_path": str(business_capacity_summary_path),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"fixed-runner release/capacity evidence: {'PASS' if passed else 'FAIL'} ({sum(1 for c in checks if c.get('passed') is True)}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    if failed_step:
        print(f"failed step: {failed_step.get('name')}")
    if failed_check:
        print(f"failed check: {failed_check.get('name')} - {'; '.join(str(item) for item in failed_check.get('details', []))}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
