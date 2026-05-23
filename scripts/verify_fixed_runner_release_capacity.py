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


REPO_ROOT = Path(__file__).resolve().parents[1]


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


def validate_release_summary(path: Path, required: bool) -> dict[str, Any]:
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
    passed = release_summary_passed(summary)
    return {
        "name": "release-baseline-summary",
        "category": "release_baseline",
        "path": str(path),
        "required": required,
        "status": "passed" if passed else "failed-summary",
        "passed": passed,
        "details": ["release baseline aggregate passed" if passed else "release baseline aggregate did not pass"],
    }


def validate_perf_summary(
    name: str,
    category: str,
    path: Path,
    required_cases: set[str],
    require_business_flow: bool,
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
    passed = gates_passed and not missing_cases and business_passed
    details = [
        f"release_gates.overall_pass={gates_passed}",
        "cases=" + ",".join(sorted(cases)),
    ]
    if missing_cases:
        details.append("missing cases: " + ",".join(missing_cases))
    if require_business_flow:
        details.append(f"business_flow.passed={business_passed}")
    return {
        "name": name,
        "category": category,
        "path": str(path),
        "required": True,
        "status": "passed" if passed else "failed-summary",
        "passed": passed,
        "preset": summary.get("preset"),
        "repetitions": summary.get("repetitions"),
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build/release")
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--collect-smoke", action="store_true", help="Collect fresh smoke evidence before validating existing capacity artifacts.")
    parser.add_argument("--step-timeout-seconds", type=int, default=900)
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

    checks = [
        validate_release_summary(release_summary_path, required=True),
        validate_perf_summary(
            "capacity-profile-summary",
            "capacity",
            capacity_summary_path,
            {"echo-1000-30s", "echo-5000-30s", "echo-10000-30s", "battle-100-30s", "battle-500-30s"},
            require_business_flow=False,
        ),
        validate_perf_summary(
            "business-capacity-summary",
            "business_capacity",
            business_capacity_summary_path,
            {"echo-1000-30s", "battle-100-30s", "battle-500-30s"},
            require_business_flow=True,
        ),
    ]

    failed_step = next((step for step in steps if step.get("status") != "passed"), None)
    failed_check = next((check for check in checks if check.get("passed") is not True), None)
    passed = failed_step is None and failed_check is None
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
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
