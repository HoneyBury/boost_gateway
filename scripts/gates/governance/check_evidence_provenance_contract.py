#!/usr/bin/env python3
"""Exercise production evidence provenance acceptance and rejection paths."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from scripts.lib.evidence_provenance import build_evidence_provenance


ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_IDS = (
    "r0_production_candidate_evidence",
    "long_soak_capacity",
    "fixed_runner_release_capacity",
    "preprod_recovery_drill",
    "tls_preprod_multi_run",
)
REVISION_A = "a" * 40
REVISION_B = "b" * 40


def provenance(revision: str = REVISION_A) -> dict[str, Any]:
    return {
        "candidate_revision": revision,
        "git_commit": revision,
        "git_ref": "refs/heads/main",
        "workflow": "contract-test",
        "run_id": "1",
        "run_attempt": "1",
        "runner": "contract-runner",
        "runner_os": "Linux",
        "runner_arch": "X64",
        "build_configuration": "Release",
        "conan_lockfile": "conan/locks/test.lock",
        "conan_lockfile_sha256": "1" * 64,
        "revision_matches_checkout": True,
    }


def write_fixture(root: Path, mutate: Callable[[dict[str, dict[str, Any]]], None] | None = None) -> Path:
    generated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    summaries = {
        evidence_id: {
            "summary_version": 2,
            "generated_at": generated_at,
            "overall_pass": True,
            "passed": True,
            "provenance": provenance(),
            "artifacts": {},
        }
        for evidence_id in EVIDENCE_IDS
    }
    if mutate is not None:
        mutate(summaries)

    evidence = []
    for index, evidence_id in enumerate(EVIDENCE_IDS):
        relative = f"runtime/validation/{evidence_id}.json"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summaries[evidence_id], indent=2), encoding="utf-8")
        item: dict[str, Any] = {
            "id": evidence_id,
            "category": evidence_id,
            "path": relative,
            "required": index == 0,
            "provenance_required": True,
            "freshness_hours": 1,
        }
        if index > 0:
            item["fixed_runner_required"] = True
        evidence.append(item)

    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "required_categories": [],
                "evidence": evidence,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest_path


def run_case(
    root: Path,
    name: str,
    *,
    mutate: Callable[[dict[str, dict[str, Any]]], None] | None = None,
    expected_revision: str = "",
) -> tuple[int, dict[str, Any], str]:
    case_root = root / name
    manifest = write_fixture(case_root, mutate)
    output = case_root / "result.json"
    command = [
        sys.executable,
        str(ROOT / "scripts/check_production_evidence_manifest.py"),
        "--manifest",
        str(manifest),
        "--evidence-root",
        str(case_root),
        "--require-fixed-runner",
        "--summary-path",
        str(output),
    ]
    if expected_revision:
        command.extend(["--expected-candidate-revision", expected_revision])
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    return completed.returncode, payload, completed.stdout


def write_perf_summary(path: Path, *, preset: str, revision: str, business_flow: bool) -> None:
    required_cases = (
        {"echo-1000-30s", "battle-100-30s", "battle-500-30s"}
        if business_flow
        else {
            "echo-1000-30s",
            "echo-5000-30s",
            "echo-10000-30s",
            "battle-100-30s",
            "battle-500-30s",
        }
    )
    payload: dict[str, Any] = {
        "git_commit": revision,
        "preset": preset,
        "repetitions": 3,
        "release_gates": {
            "overall_pass": True,
            "checks": [{"case": case, "passed": True} for case in sorted(required_cases)],
        },
    }
    if business_flow:
        payload["business_flow"] = {"passed": True}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_r4_case(root: Path, name: str, *, mismatched_capacity: bool) -> tuple[int, dict[str, Any], str]:
    case_root = root / name
    case_root.mkdir(parents=True, exist_ok=True)
    current = build_evidence_provenance(ROOT, build_configuration="Release")
    revision = str(current["candidate_revision"])
    release_summary = case_root / "release.json"
    release_summary.write_text(
        json.dumps(
            {
                "summary_version": 2,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "overall_pass": True,
                "passed": True,
                "provenance": current,
                "artifacts": {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    capacity_summary = case_root / "capacity.json"
    business_summary = case_root / "business.json"
    write_perf_summary(
        capacity_summary,
        preset="capacity",
        revision=REVISION_B if mismatched_capacity else revision,
        business_flow=False,
    )
    write_perf_summary(
        business_summary,
        preset="business-capacity",
        revision=revision,
        business_flow=True,
    )
    output = case_root / "result.json"
    command = [
        sys.executable,
        str(ROOT / "scripts/verify_fixed_runner_release_capacity.py"),
        "--skip-collect",
        "--configuration",
        "Release",
        "--release-summary",
        str(release_summary),
        "--capacity-summary",
        str(capacity_summary),
        "--business-capacity-summary",
        str(business_summary),
        "--summary-path",
        str(output),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    return completed.returncode, payload, completed.stdout


def run_r3_case(
    root: Path,
    name: str,
    *,
    bounded_passed: bool,
    fixed_passed: bool,
    fixed_was_required: bool,
) -> tuple[int, dict[str, Any], str]:
    case_root = root / name
    case_root.mkdir(parents=True, exist_ok=True)
    bounded = case_root / "bounded.json"
    fixed = case_root / "fixed.json"
    r0 = case_root / "r0.json"
    generated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    common = {"summary_version": 2, "generated_at": generated_at, "checks": [], "artifacts": {}}
    bounded.write_text(
        json.dumps({**common, "overall_pass": bounded_passed, "passed": bounded_passed}),
        encoding="utf-8",
    )
    fixed.write_text(
        json.dumps(
            {
                **common,
                "overall_pass": fixed_passed,
                "passed": fixed_passed,
                "require_fixed_runner": fixed_was_required,
                "candidate_revision": REVISION_A,
            }
        ),
        encoding="utf-8",
    )
    r0.write_text(
        json.dumps({**common, "overall_pass": bounded_passed, "passed": bounded_passed}),
        encoding="utf-8",
    )
    output = case_root / "report.md"
    summary = case_root / "result.json"
    command = [
        sys.executable,
        str(ROOT / "scripts/render_production_readiness_report.py"),
        "--manifest-summary",
        str(bounded),
        "--fixed-runner-summary",
        str(fixed),
        "--r0-summary",
        str(r0),
        "--require-fixed-runner",
        "--output",
        str(output),
        "--summary-path",
        str(summary),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    payload = json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else {}
    return completed.returncode, payload, completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/evidence-provenance-contract-summary.json",
    )
    args = parser.parse_args()
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    checks: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="boost-gateway-provenance-") as temp:
        temp_root = Path(temp)

        returncode, payload, output = run_case(temp_root, "matching")
        checks.append(
            {
                "name": "matching-candidate-revision-passes",
                "passed": returncode == 0 and payload.get("overall_pass") is True,
                "detail": output[-2000:],
            }
        )

        def mismatch(summaries: dict[str, dict[str, Any]]) -> None:
            summaries["preprod_recovery_drill"]["provenance"] = provenance(REVISION_B)

        returncode, payload, output = run_case(temp_root, "mismatch", mutate=mismatch)
        checks.append(
            {
                "name": "cross-revision-evidence-fails",
                "passed": returncode != 0
                and any(check.get("status") == "provenance-mismatch" for check in payload.get("checks", [])),
                "detail": output[-2000:],
            }
        )

        def missing_provenance(summaries: dict[str, dict[str, Any]]) -> None:
            summaries["fixed_runner_release_capacity"].pop("provenance")

        returncode, payload, output = run_case(temp_root, "missing-provenance", mutate=missing_provenance)
        checks.append(
            {
                "name": "missing-provenance-fails",
                "passed": returncode != 0
                and any(check.get("status") == "provenance-invalid" for check in payload.get("checks", [])),
                "detail": output[-2000:],
            }
        )

        def missing_generated_at(summaries: dict[str, dict[str, Any]]) -> None:
            summaries["long_soak_capacity"].pop("generated_at")

        returncode, payload, output = run_case(temp_root, "missing-generated-at", mutate=missing_generated_at)
        checks.append(
            {
                "name": "missing-generated-at-fails",
                "passed": returncode != 0
                and any(check.get("status") == "stale" for check in payload.get("checks", [])),
                "detail": output[-2000:],
            }
        )

        returncode, payload, output = run_case(
            temp_root,
            "unexpected-revision",
            expected_revision=REVISION_B,
        )
        checks.append(
            {
                "name": "explicit-candidate-mismatch-fails",
                "passed": returncode != 0
                and all(
                    check.get("status") == "provenance-invalid"
                    for check in payload.get("checks", [])
                    if check.get("provenance_required") is True
                ),
                "detail": output[-2000:],
            }
        )

        def checkout_mismatch(summaries: dict[str, dict[str, Any]]) -> None:
            value = provenance()
            value["git_commit"] = REVISION_B
            value["revision_matches_checkout"] = False
            summaries["tls_preprod_multi_run"]["provenance"] = value

        returncode, payload, output = run_case(temp_root, "checkout-mismatch", mutate=checkout_mismatch)
        checks.append(
            {
                "name": "checkout-mismatch-fails",
                "passed": returncode != 0
                and any(check.get("status") == "provenance-invalid" for check in payload.get("checks", [])),
                "detail": output[-2000:],
            }
        )

        returncode, payload, output = run_r4_case(
            temp_root,
            "r4-matching-children",
            mismatched_capacity=False,
        )
        checks.append(
            {
                "name": "r4-matching-child-revisions-pass",
                "passed": returncode == 0 and payload.get("overall_pass") is True,
                "detail": output[-2000:],
            }
        )

        returncode, payload, output = run_r4_case(
            temp_root,
            "r4-mismatched-child",
            mismatched_capacity=True,
        )
        checks.append(
            {
                "name": "r4-cross-revision-capacity-fails",
                "passed": returncode != 0
                and any(
                    check.get("name") == "capacity-profile-summary"
                    and check.get("passed") is False
                    for check in payload.get("checks", [])
                ),
                "detail": output[-2000:],
            }
        )

        returncode, payload, output = run_r3_case(
            temp_root,
            "r3-all-pass",
            bounded_passed=True,
            fixed_passed=True,
            fixed_was_required=True,
        )
        checks.append(
            {
                "name": "r3-requires-bounded-and-fixed-pass",
                "passed": returncode == 0
                and payload.get("overall_pass") is True
                and payload.get("final_production_ready") is True,
                "detail": output[-2000:],
            }
        )

        returncode, payload, output = run_r3_case(
            temp_root,
            "r3-bounded-fails",
            bounded_passed=False,
            fixed_passed=True,
            fixed_was_required=True,
        )
        checks.append(
            {
                "name": "r3-rejects-failed-bounded-summary",
                "passed": returncode != 0
                and payload.get("overall_pass") is False
                and payload.get("final_production_ready") is False,
                "detail": output[-2000:],
            }
        )

        returncode, payload, output = run_r3_case(
            temp_root,
            "r3-fixed-not-required",
            bounded_passed=True,
            fixed_passed=True,
            fixed_was_required=False,
        )
        checks.append(
            {
                "name": "r3-rejects-bounded-summary-disguised-as-fixed",
                "passed": returncode != 0
                and payload.get("overall_pass") is False
                and payload.get("final_production_ready") is False,
                "detail": output[-2000:],
            }
        )

    failed = [check for check in checks if check["passed"] is not True]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "evidence_provenance_contract" if failed else "",
        "failed_step": str(failed[0]["name"]) if failed else "",
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        "evidence provenance contract: "
        f"{'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
