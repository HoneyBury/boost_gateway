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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from scripts.lib.tooling_metrics import (
    collect_tooling_metrics,
    explicitly_tested_cli,
    load_json_object,
)


ROOT = Path(__file__).resolve().parents[3]
BASELINE_PATH = ROOT / "docs/tooling-metrics-baseline.json"
GROWTH_EXCEPTION_FIELDS = {
    "kind",
    "domain",
    "consumers",
    "test",
    "why_new_script",
    "replaces",
    "retirement_condition",
    "temporary",
    "expires_on",
}
VALID_GROWTH_KINDS = {"cli", "tool-module", "library-module", "script-module"}
VALID_GROWTH_DOMAINS = {
    "contributor",
    "dependencies",
    "governance",
    "infrastructure",
    "performance",
    "platform",
    "production",
    "recovery",
    "release",
    "security",
    "sdk",
}


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def valid_growth_exception(path_text: str, metadata: Any) -> tuple[bool, str]:
    if not isinstance(metadata, dict):
        return False, "metadata must be an object"
    problems: list[str] = []
    if set(metadata) != GROWTH_EXCEPTION_FIELDS:
        problems.append("fields do not match the governed schema")
    if metadata.get("kind") not in VALID_GROWTH_KINDS:
        problems.append("kind is invalid")
    if metadata.get("domain") not in VALID_GROWTH_DOMAINS:
        problems.append("domain is invalid")
    consumers = metadata.get("consumers")
    if not (
        isinstance(consumers, list)
        and consumers
        and all(isinstance(item, str) and item.strip() for item in consumers)
        and len(consumers) == len(set(consumers))
    ):
        problems.append("consumers must be a non-empty unique string list")
    test_text = metadata.get("test")
    test_path = ROOT / str(test_text)
    test_is_valid = (
        isinstance(test_text, str)
        and test_text.startswith("tests/")
        and test_path.is_file()
        and test_path.suffix == ".py"
        and test_path.resolve().is_relative_to((ROOT / "tests").resolve())
    )
    if not test_is_valid:
        problems.append("test must reference an existing Python test under tests/")
    elif not explicitly_tested_cli(
        ROOT, ROOT / path_text, [], declared_test=str(test_text)
    ):
        problems.append("test must directly identify the governed script in Python syntax")
    if not (
        isinstance(metadata.get("why_new_script"), str)
        and len(metadata["why_new_script"].strip()) >= 30
    ):
        problems.append("why_new_script must contain at least 30 characters")
    if not isinstance(metadata.get("replaces"), str):
        problems.append("replaces must be a string, empty when there is no replacement")
    if not (
        isinstance(metadata.get("retirement_condition"), str)
        and len(metadata["retirement_condition"].strip()) >= 20
    ):
        problems.append("retirement_condition must contain at least 20 characters")
    temporary = metadata.get("temporary")
    expires_on = metadata.get("expires_on")
    if not isinstance(temporary, bool):
        problems.append("temporary must be a boolean")
    elif temporary:
        try:
            expiry = date.fromisoformat(str(expires_on))
        except ValueError:
            problems.append("temporary exceptions require an ISO expires_on date")
        else:
            if expiry < date.today():
                problems.append("temporary exception has expired")
    elif expires_on != "":
        problems.append("permanent exceptions must use an empty expires_on")
    if not (ROOT / path_text).is_file():
        problems.append("exception path does not exist")
    return not problems, "; ".join(problems) if problems else "metadata is complete"


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
    for metric in (
        "public_entrypoints",
        "workflow_duplicate_fragments",
        "untested_cli",
        "large_scripts_over_500",
        "large_scripts_over_800",
        "workflow_script_dependencies",
        "workflow_script_dependency_edges",
        "cross_cli_imports",
    ):
        limit = limits.get(metric, {}).get("maximum") if isinstance(limits, dict) else None
        add(
            checks,
            f"limit:{metric}",
            isinstance(limit, int) and int(current[metric]) <= limit,
            f"current={current[metric]}; maximum={limit}",
        )

    inventory = load_json_object(ROOT / "docs/script-inventory.json")
    exceptions = inventory.get("script_growth_exceptions", {})
    exception_map = exceptions if isinstance(exceptions, dict) else {}
    add(
        checks,
        "script-growth-exceptions-object",
        isinstance(exceptions, dict),
        "script_growth_exceptions is an object",
    )
    valid_exceptions: set[str] = set()
    for path_text, metadata in sorted(exception_map.items()):
        valid, detail = valid_growth_exception(path_text, metadata)
        add(checks, f"growth-exception:{path_text}", valid, detail)
        if valid:
            valid_exceptions.add(path_text)

    known = baseline.get("known_surfaces", {})
    known = known if isinstance(known, dict) else {}
    known_cli = set(known.get("cli_implementations", []))
    known_tools = set(known.get("tool_files", []))
    known_libraries = set(known.get("library_files", []))
    known_other_scripts = set(known.get("other_script_files", []))
    current_cli = set(current["cli_implementation_paths"])
    current_tools = set(current["tool_file_paths"])
    current_libraries = set(current["library_file_paths"])
    current_other_scripts = set(current["other_script_file_paths"])
    new_cli = current_cli - known_cli
    new_tools = current_tools - known_tools
    new_libraries = current_libraries - known_libraries
    new_other_scripts = current_other_scripts - known_other_scripts
    required_exceptions = new_cli | new_tools | new_libraries | new_other_scripts
    add(
        checks,
        "new-script-growth-exceptions",
        valid_exceptions == required_exceptions,
        f"missing={sorted(required_exceptions - valid_exceptions)}; "
        f"stale_or_invalid={sorted(set(exception_map) - required_exceptions)}",
    )
    for path_text in sorted(required_exceptions & valid_exceptions):
        expected_kind = (
            "cli"
            if path_text in new_cli
            else "tool-module"
            if path_text in new_tools
            else "library-module"
            if path_text in new_libraries
            else "script-module"
        )
        actual_kind = exception_map[path_text].get("kind")
        add(
            checks,
            f"growth-exception-kind:{path_text}",
            actual_kind == expected_kind,
            f"kind={actual_kind}; expected={expected_kind}",
        )
    for metric, additions in (
        ("cli_implementations", new_cli & valid_exceptions),
        ("tool_files", new_tools & valid_exceptions),
        ("library_files", new_libraries & valid_exceptions),
        ("other_script_files", new_other_scripts & valid_exceptions),
    ):
        limit = limits.get(metric, {}).get("maximum") if isinstance(limits, dict) else None
        permitted = limit + len(additions) if isinstance(limit, int) else None
        add(
            checks,
            f"limit:{metric}",
            isinstance(permitted, int) and int(current[metric]) <= permitted,
            f"current={current[metric]}; maximum={limit}; reviewed_additions={len(additions)}",
        )

    for name, current_key in (
        ("workflow_script_dependencies", "workflow_script_dependency_paths"),
        ("workflow_script_dependency_edges", "workflow_script_dependency_edge_paths"),
        ("cross_cli_import_edges", "cross_cli_import_edges"),
    ):
        allowed_items = known.get(name, [])
        allowed_set = set(allowed_items) if isinstance(allowed_items, list) else set()
        current_set = set(current[current_key])
        add(
            checks,
            f"known-surface:{name}",
            isinstance(allowed_items, list) and current_set <= allowed_set,
            f"new={sorted(current_set - allowed_set)}",
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
        f"untested-cli={current['untested_cli']}, "
        f"cli={current['cli_implementations']}, "
        f"tools={current['tool_files']}, "
        f"libraries={current['library_files']}, "
        f"other-scripts={current['other_script_files']}, "
        f"large-500={current['large_scripts_over_500']}, "
        f"workflow-deps={current['workflow_script_dependencies']}, "
        f"workflow-edges={current['workflow_script_dependency_edges']}, "
        f"cross-cli-imports={current['cross_cli_imports']}"
    )
    print(f"summary: {summary_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
