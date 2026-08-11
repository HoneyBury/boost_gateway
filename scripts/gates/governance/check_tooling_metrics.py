#!/usr/bin/env python3
"""Reject unreviewed growth in maintained script and workflow governance metrics."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lib.tooling_metrics import collect_tooling_metrics, load_json_object


ROOT = Path(__file__).resolve().parents[3]
BASELINE_PATH = ROOT / "docs/tooling-metrics-baseline.json"


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/tooling-metrics-summary.json",
    )
    args = parser.parse_args()

    baseline_path = args.baseline if args.baseline.is_absolute() else ROOT / args.baseline
    summary_path = (
        args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    )
    baseline = load_json_object(baseline_path)
    policy = baseline.get("policy", {}) if isinstance(baseline, dict) else {}
    fragment_lines = int(policy.get("fragment_lines", 3))
    min_workflows = int(policy.get("minimum_workflows", 3))
    current = collect_tooling_metrics(
        ROOT, fragment_lines=fragment_lines, min_workflows=min_workflows
    )
    limits = baseline.get("limits", {}) if isinstance(baseline, dict) else {}
    checks: list[dict[str, Any]] = []
    add(checks, "baseline-json", bool(baseline), "baseline is a JSON object")
    add(
        checks,
        "baseline-schema-version",
        baseline.get("schema_version") == 1,
        "schema_version is 1",
    )
    add(
        checks,
        "fragment-policy",
        fragment_lines >= 2 and min_workflows >= 2,
        f"fragment_lines={fragment_lines}; minimum_workflows={min_workflows}",
    )
    for metric in ("public_entrypoints", "workflow_duplicate_fragments", "untested_cli"):
        limit = limits.get(metric, {}).get("maximum") if isinstance(limits, dict) else None
        add(
            checks,
            f"limit:{metric}",
            isinstance(limit, int) and int(current[metric]) <= limit,
            f"current={current[metric]}; maximum={limit}",
        )

    allowed_untested = limits.get("untested_cli", {}).get("allowlist", [])
    allowed_set = set(allowed_untested) if isinstance(allowed_untested, list) else set()
    current_untested = set(current["untested_cli_paths"])
    add(
        checks,
        "untested-cli-allowlist",
        isinstance(allowed_untested, list) and current_untested <= allowed_set,
        f"new={sorted(current_untested - allowed_set)}",
    )

    external = baseline.get("external_metrics", {}).get(
        "automation_change_failure_rate", {}
    )
    add(
        checks,
        "external-change-failure-rate-definition",
        isinstance(external, dict)
        and external.get("source") == "github-actions"
        and isinstance(external.get("window_days"), int)
        and external.get("window_days") > 0
        and isinstance(external.get("target_percent"), (int, float))
        and bool(external.get("owner"))
        and bool(external.get("measurement")),
        "GitHub-hosted change-failure metric remains explicitly owned and defined",
    )

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 1,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "tooling_metrics" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "checks": checks,
        "metrics": current,
        "external_metrics": {
            "automation_change_failure_rate": {
                "status": "external-verification-required",
                **(external if isinstance(external, dict) else {}),
            }
        },
        "artifacts": {
            "baseline_path": str(baseline_path),
            "summary_path": str(summary_path),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"tooling metrics: {'PASS' if not failed else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    print(
        "metrics: "
        f"public={current['public_entrypoints']}, "
        f"workflow-duplicates={current['workflow_duplicate_fragments']}, "
        f"untested-cli={current['untested_cli']}"
    )
    print(f"summary: {summary_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
