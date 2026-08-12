#!/usr/bin/env python3
"""Exercise production evidence provenance acceptance and rejection paths."""

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
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from scripts.lib.evidence_provenance import build_evidence_provenance



from scripts.lib.evidence_provenance_cases import *  # noqa: E402,F403

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
            summaries["long_soak_2h"].pop("generated_at")

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
            expected_revision=REVISION_A,
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
            "r3-checkout-revision-mismatch",
            bounded_passed=True,
            fixed_passed=True,
            fixed_was_required=True,
            expected_revision=REVISION_B,
            evidence_revision=REVISION_A,
        )
        checks.append(
            {
                "name": "r3-rejects-evidence-from-different-checkout",
                "passed": returncode != 0
                and payload.get("overall_pass") is False
                and payload.get("final_production_ready") is False
                and payload.get("candidate_revision_matches_expected") is False,
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

        readiness_workflow = (ROOT / ".github/workflows/production-readiness.yml").read_text(encoding="utf-8")
        checks.extend(
            [
                {
                    "name": "readiness-workflow-binds-checkout-revision",
                    "passed": "BOOST_GATEWAY_CANDIDATE_REVISION: ${{ github.sha }}" in readiness_workflow,
                    "detail": "workflow exports the immutable checkout revision",
                },
                {
                    "name": "readiness-workflow-passes-expected-revision",
                    "passed": readiness_workflow.count(
                        '--expected-candidate-revision "$BOOST_GATEWAY_CANDIDATE_REVISION"'
                    ) >= 3,
                    "detail": "bounded R2, fixed R2, and R3 receive the expected revision",
                },
            ]
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
