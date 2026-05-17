#!/usr/bin/env python3
"""Render validation JSON summaries as compact Markdown for CI step summaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def status_icon(value: Any) -> str:
    if value is True or value == "passed":
        return "PASS"
    if value is False or value in {"failed", "timeout"}:
        return "FAIL"
    return str(value or "unknown")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"summary is not a JSON object: {path}")
    return data


def render_step_table(steps: list[Any]) -> list[str]:
    if not steps:
        return ["_No steps recorded._", ""]

    lines = [
        "| Step | Category | Status | Duration |",
        "| --- | --- | --- | ---: |",
    ]
    for item in steps:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "unknown"))
        category = str(item.get("category", "-"))
        status = status_icon(item.get("status"))
        duration = item.get("duration_seconds", "")
        lines.append(f"| `{name}` | `{category}` | {status} | {duration} |")
    lines.append("")
    return lines


def render_release_gates(summary: dict[str, Any]) -> list[str]:
    gates = summary.get("release_gates")
    if not isinstance(gates, dict):
        return []

    lines = [
        "### Release Gates",
        "",
        f"- Overall: **{status_icon(gates.get('overall_pass', gates.get('passed')))}**",
    ]
    checks = gates.get("checks")
    if isinstance(checks, list) and checks:
        lines.extend([
            "",
            "| Case | Status | Criteria |",
            "| --- | --- | --- |",
        ])
        for check in checks:
            if not isinstance(check, dict):
                continue
            lines.append(
                f"| `{check.get('case', 'unknown')}` | "
                f"{status_icon(check.get('passed'))} | "
                f"{check.get('criteria', '')} |"
            )
    lines.append("")
    return lines


def render_summary(path: Path, title: str | None) -> str:
    summary = read_json(path)
    heading = title or path.name
    lines = [f"## {heading}", ""]
    lines.extend([
        f"- Path: `{path}`",
        f"- Passed: **{status_icon(summary.get('passed'))}**",
    ])
    for key in ("failed_category", "failed_step", "preset", "perf_preset", "soak_profile", "baseline_profile"):
        value = summary.get(key)
        if value not in (None, ""):
            lines.append(f"- {key}: `{value}`")
    lines.append("")

    steps = summary.get("steps")
    if isinstance(steps, list):
        lines.extend(["### Steps", ""])
        lines.extend(render_step_table(steps))

    lines.extend(render_release_gates(summary))
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, nargs="+", help="JSON summary file(s) to render")
    parser.add_argument("--title", default="", help="Markdown title for a single summary")
    parser.add_argument("--output", type=Path, default=Path("-"), help="Output Markdown path, or '-' for stdout")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    chunks: list[str] = []
    for path in args.summary:
        chunks.append(render_summary(path, args.title if len(args.summary) == 1 else None))
    markdown = "\n".join(chunks)
    if str(args.output) == "-":
        print(markdown, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
