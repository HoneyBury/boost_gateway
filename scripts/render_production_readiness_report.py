#!/usr/bin/env python3
"""Render an R3 production-readiness report from R2 evidence summaries."""

from __future__ import annotations

import argparse
import json
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


def status_text(value: Any) -> str:
    if value is True or value == "passed":
        return "PASS"
    if value is False or value in {"failed", "missing", "stale", "invalid-json", "artifact-mismatch"}:
        return "FAIL"
    if value == "optional-missing":
        return "OPTIONAL-MISSING"
    return str(value or "UNKNOWN")


def bool_passed(summary: dict[str, Any]) -> bool:
    if "overall_pass" in summary:
        return summary.get("overall_pass") is True
    return summary.get("passed") is True


def render_evidence_table(checks: list[Any]) -> list[str]:
    lines = [
        "| Evidence | Category | Required | Status | Age(h) | Path |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for item in checks:
        if not isinstance(item, dict):
            continue
        age = item.get("age_hours")
        age_text = "" if age is None else str(age)
        lines.append(
            f"| `{item.get('name', 'unknown')}` | "
            f"`{item.get('category', '')}` | "
            f"`{item.get('required', False)}` | "
            f"{status_text(item.get('status', 'unknown'))} | "
            f"{age_text} | "
            f"`{item.get('path', '')}` |"
        )
    lines.append("")
    return lines


def render_blockers(title: str, summary: dict[str, Any]) -> list[str]:
    checks = summary.get("checks")
    blockers: list[dict[str, Any]] = []
    if isinstance(checks, list):
        blockers = [item for item in checks if isinstance(item, dict) and item.get("passed") is not True]
    errors = summary.get("errors")
    lines = [f"### {title}", ""]
    if not blockers and not errors:
        lines.extend(["No blockers recorded.", ""])
        return lines
    if isinstance(errors, list):
        for error in errors:
            lines.append(f"- manifest error: {error}")
    for item in blockers:
        details = item.get("details")
        detail_text = "; ".join(str(value) for value in details) if isinstance(details, list) else ""
        lines.append(f"- `{item.get('name', 'unknown')}`: {item.get('status', 'failed')} {detail_text}".rstrip())
    lines.append("")
    return lines


def failed_check_names(summary: dict[str, Any]) -> set[str]:
    checks = summary.get("checks")
    if not isinstance(checks, list):
        return set()
    return {
        str(item.get("name", ""))
        for item in checks
        if isinstance(item, dict) and item.get("passed") is not True
    }


def render_r0_summary(path: Path) -> list[str]:
    summary = load_json(path)
    if not summary:
        return ["### R0 Aggregate", "", f"- Missing or invalid summary: `{path}`", ""]
    lines = [
        "### R0 Aggregate",
        "",
        f"- Status: **{status_text(bool_passed(summary))}**",
        f"- Failed category: `{summary.get('failed_category', '')}`",
        f"- Failed step: `{summary.get('failed_step', '')}`",
    ]
    steps = summary.get("steps")
    if isinstance(steps, list):
        lines.extend(["", "| Step | Category | Status | Duration(s) |", "| --- | --- | --- | ---: |"])
        for step in steps:
            if not isinstance(step, dict):
                continue
            lines.append(
                f"| `{step.get('name', 'unknown')}` | `{step.get('category', '')}` | "
                f"{status_text(step.get('status'))} | {step.get('duration_seconds', '')} |"
            )
    lines.append("")
    return lines


def render_r1_summary(path: Path) -> list[str]:
    summary = load_json(path)
    if not summary:
        return ["### R1 TLS Readiness", "", f"- Missing or invalid summary: `{path}`", ""]
    perf = summary.get("performance_comparison")
    lines = [
        "### R1 TLS Readiness",
        "",
        f"- Status: **{status_text(bool_passed(summary))}**",
        f"- Failed category: `{summary.get('failed_category', '')}`",
        f"- Failed step: `{summary.get('failed_step', '')}`",
    ]
    if isinstance(perf, dict):
        lines.extend(
            [
                f"- Plain full-flow seconds: `{perf.get('plain_full_flow_seconds', '')}`",
                f"- TLS full-flow seconds: `{perf.get('tls_full_flow_seconds', '')}`",
                f"- TLS/plain overhead ratio: `{perf.get('overhead_ratio', '')}`",
            ]
        )
    lines.append("")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest-summary",
        type=Path,
        default=REPO_ROOT / "runtime/validation/r2-production-evidence-manifest-summary.json",
    )
    parser.add_argument(
        "--fixed-runner-summary",
        type=Path,
        default=REPO_ROOT / "runtime/validation/r2-production-evidence-manifest-fixed-runner-summary.json",
    )
    parser.add_argument(
        "--r0-summary",
        type=Path,
        default=REPO_ROOT / "runtime/validation/r0-production-candidate-evidence-summary.json",
    )
    parser.add_argument(
        "--r1-summary",
        type=Path,
        default=REPO_ROOT / "runtime/validation/r1-tls-production-readiness-summary.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "runtime/validation/r3-production-readiness-report.md",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=REPO_ROOT / "runtime/validation/r3-production-readiness-report-summary.json",
    )
    args = parser.parse_args()

    manifest_summary_path = args.manifest_summary if args.manifest_summary.is_absolute() else REPO_ROOT / args.manifest_summary
    fixed_runner_summary_path = args.fixed_runner_summary if args.fixed_runner_summary.is_absolute() else REPO_ROOT / args.fixed_runner_summary
    r0_summary_path = args.r0_summary if args.r0_summary.is_absolute() else REPO_ROOT / args.r0_summary
    r1_summary_path = args.r1_summary if args.r1_summary.is_absolute() else REPO_ROOT / args.r1_summary
    output_path = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    summary_path = args.summary_path if args.summary_path.is_absolute() else REPO_ROOT / args.summary_path

    manifest_summary = load_json(manifest_summary_path)
    fixed_runner_summary = load_json(fixed_runner_summary_path)
    bounded_ready = bool_passed(manifest_summary)
    final_ready = bool_passed(fixed_runner_summary) if fixed_runner_summary else False
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    lines = [
        "# Production Readiness Report",
        "",
        f"Generated at: `{now}`",
        "",
        "## Decision",
        "",
        f"- Bounded local candidate evidence: **{status_text(bounded_ready)}**",
        f"- Final production fixed-runner/pre-production readiness: **{status_text(final_ready)}**",
        "",
    ]
    if bounded_ready and not final_ready:
        lines.extend(
            [
                "Current decision: bounded local evidence is healthy, but final production approval is blocked until fixed-runner/pre-production evidence is populated and passes.",
                "",
            ]
        )
    elif bounded_ready and final_ready:
        lines.extend(["Current decision: evidence is ready for final production approval review.", ""])
    else:
        lines.extend(["Current decision: production candidate evidence has blocking local issues.", ""])

    lines.extend(["## R2 Evidence Manifest", ""])
    if manifest_summary:
        lines.extend(
            [
                f"- Summary: `{manifest_summary_path}`",
                f"- Status: **{status_text(bounded_ready)}**",
                f"- Warnings: `{len(manifest_summary.get('warnings', [])) if isinstance(manifest_summary.get('warnings'), list) else 0}`",
                "",
            ]
        )
        checks = manifest_summary.get("checks")
        lines.extend(render_evidence_table(checks if isinstance(checks, list) else []))
    else:
        lines.extend([f"- Missing R2 summary: `{manifest_summary_path}`", ""])

    lines.extend(render_blockers("Final Production Blockers", fixed_runner_summary if fixed_runner_summary else {}))
    lines.extend(render_r0_summary(r0_summary_path))
    lines.extend(render_r1_summary(r1_summary_path))
    required_next_evidence = {
        "fixed_runner_release_capacity": "fixed low-noise release/capacity baseline summary.",
        "preprod_recovery_drill": "real Docker/K8s recovery and rollback drill summary.",
        "tls_preprod_multi_run": "production-like TLS profile multi-run summary.",
    }
    remaining = failed_check_names(fixed_runner_summary) if fixed_runner_summary else set(required_next_evidence)
    lines.extend(["## Required Next Evidence", ""])
    if remaining:
        for name, description in required_next_evidence.items():
            if name in remaining:
                lines.append(f"- `{name}`: {description}")
    else:
        lines.append("No remaining fixed-runner/pre-production evidence blockers.")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")

    report_summary = {
        "summary_version": 2,
        "generated_at": now,
        "overall_pass": bounded_ready,
        "passed": bounded_ready,
        "final_production_ready": final_ready,
        "failed_category": "" if bounded_ready else "manifest",
        "failed_step": "" if bounded_ready else str(manifest_summary.get("failed_step", "manifest")),
        "artifacts": {
            "report_path": str(output_path),
            "summary_path": str(summary_path),
            "manifest_summary_path": str(manifest_summary_path),
            "fixed_runner_summary_path": str(fixed_runner_summary_path),
            "r0_summary_path": str(r0_summary_path),
            "r1_summary_path": str(r1_summary_path),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(report_summary, indent=2, sort_keys=True), encoding="utf-8")

    print(f"production readiness report: {'PASS' if bounded_ready else 'FAIL'}")
    print(f"final production ready: {'YES' if final_ready else 'NO'}")
    print(f"report: {output_path}")
    print(f"summary: {summary_path}")
    return 0 if bounded_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
