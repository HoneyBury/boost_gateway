#!/usr/bin/env python3
"""Validate GitHub workflow Python script invocations against declared CLI options."""

from __future__ import annotations

import argparse
import ast
import json
import re
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS_ROOT = ROOT / ".github" / "workflows"
INVENTORY_PATH = ROOT / "docs" / "script-inventory.json"
RUN_PATTERN = re.compile(r"^(?P<indent>\s*)run:\s*(?P<body>.*)$")
PYTHON_INVOCATION_PATTERN = re.compile(r"\bpython(?:3)?\b\s+['\"]?(scripts/[A-Za-z0-9_./-]+\.py)['\"]?")
LONG_OPTION_PATTERN = re.compile(r"(?<![\w-])(--[A-Za-z0-9][A-Za-z0-9-]*)(?:(?:=|\s)|$)")


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def extract_run_blocks(path: Path) -> list[tuple[int, str]]:
    blocks: list[tuple[int, str]] = []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    index = 0
    while index < len(lines):
        match = RUN_PATTERN.match(lines[index])
        if match is None:
            index += 1
            continue

        indent = len(match.group("indent"))
        body = match.group("body")
        line_number = index + 1

        if body.startswith("|") or body.startswith(">"):
            block_lines: list[str] = []
            index += 1
            while index < len(lines):
                raw = lines[index]
                stripped = raw.strip()
                current_indent = len(raw) - len(raw.lstrip(" "))
                if stripped and current_indent <= indent:
                    break
                block_lines.append(raw)
                index += 1
            blocks.append((line_number, textwrap.dedent("\n".join(block_lines))))
            continue

        blocks.append((line_number, body))
        index += 1
    return blocks


def normalize_run_block(block: str) -> list[str]:
    merged = re.sub(r"\\\s*\n\s*", " ", block)
    return [line.strip() for line in merged.splitlines() if line.strip()]


def segment_end(text: str, start: int, limit: int) -> int:
    quote: str | None = None
    for index in range(start, limit):
        char = text[index]
        if quote is not None:
            if char == quote and text[index - 1] != "\\":
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#":
            return index
        if char in {";", ">", "|"}:
            return index
        if char == "&" and index + 1 < limit and text[index + 1] == "&":
            return index
    return limit


def extract_invocations(workflow_path: Path) -> list[dict[str, Any]]:
    invocations: list[dict[str, Any]] = []
    for line_number, block in extract_run_blocks(workflow_path):
        for line in normalize_run_block(block):
            matches = list(PYTHON_INVOCATION_PATTERN.finditer(line))
            for idx, match in enumerate(matches):
                next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
                command_tail = line[match.end():segment_end(line, match.end(), next_start)]
                options = sorted(set(LONG_OPTION_PATTERN.findall(command_tail)))
                invocations.append(
                    {
                        "workflow": str(workflow_path.relative_to(ROOT)).replace("\\", "/"),
                        "line": line_number,
                        "script": match.group(1),
                        "options": options,
                        "command": line.strip(),
                    }
                )
    return invocations


def collect_declared_options(script_path: Path) -> tuple[set[str], str]:
    try:
        tree = ast.parse(script_path.read_text(encoding="utf-8-sig"), filename=str(script_path))
    except (OSError, SyntaxError) as exc:
        return set(), f"unable to parse {script_path}: {exc}"

    options = {"--help"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("--"):
                options.add(arg.value.split("=", 1)[0])
    return options, ""


def resolve_canonical_script(script: str, inventory: dict[str, Any]) -> tuple[Path | None, str]:
    scripts = inventory.get("scripts", {})
    canonical = script
    if isinstance(scripts, dict):
        meta = scripts.get(script)
        if isinstance(meta, dict) and isinstance(meta.get("canonical"), str):
            canonical = str(meta["canonical"])

    script_path = ROOT / script
    if not script_path.exists():
        return None, f"{script} does not exist"

    canonical_path = ROOT / canonical
    if not canonical_path.exists():
        return None, f"{script} canonical target {canonical} does not exist"
    return canonical_path, canonical


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=INVENTORY_PATH)
    parser.add_argument("--workflows-root", type=Path, default=WORKFLOWS_ROOT)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/workflow-python-cli-contracts-summary.json",
    )
    args = parser.parse_args()

    inventory_path = args.inventory if args.inventory.is_absolute() else ROOT / args.inventory
    workflows_root = args.workflows_root if args.workflows_root.is_absolute() else ROOT / args.workflows_root
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path

    inventory = load_json(inventory_path)
    checks: list[dict[str, Any]] = []
    add(checks, "inventory-json", bool(inventory), f"{inventory_path} is valid JSON")
    add(checks, "workflows-root-exists", workflows_root.exists(), f"{workflows_root} exists")

    declared_option_cache: dict[str, tuple[set[str], str]] = {}
    invocations: list[dict[str, Any]] = []
    if workflows_root.exists():
        for workflow_path in sorted(workflows_root.glob("*.yml")):
            invocations.extend(extract_invocations(workflow_path))

    add(checks, "python-invocations-discovered", bool(invocations), "workflow Python script invocations were discovered")

    for invocation in invocations:
        script = str(invocation["script"])
        canonical_path, canonical = resolve_canonical_script(script, inventory)
        location = f"{invocation['workflow']}:{invocation['line']}"
        if canonical_path is None:
            add(checks, f"{location}:{script}:exists", False, canonical)
            continue

        cache_key = str(canonical_path.relative_to(ROOT)).replace("\\", "/")
        if cache_key not in declared_option_cache:
            declared_option_cache[cache_key] = collect_declared_options(canonical_path)
        declared_options, parse_error = declared_option_cache[cache_key]
        add(checks, f"{location}:{script}:parse", not parse_error, parse_error or f"{canonical} parsed successfully")
        if parse_error:
            continue

        for option in invocation["options"]:
            passed = option in declared_options
            detail = f"{location} passes {option} to {script} (canonical: {canonical})"
            add(checks, f"{location}:{script}:{option}", passed, detail)

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "workflow_python_cli_contracts" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {
            "summary_path": str(summary_path),
            "inventory_path": str(inventory_path),
            "workflows_root": str(workflows_root),
        },
        "invocations": invocations,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(
        "workflow python cli contracts: "
        f"{'PASS' if summary['passed'] else 'FAIL'} "
        f"({len(checks) - len(failed)}/{len(checks)} checks)"
    )
    print(f"summary: {summary_path}")
    if failed:
        for check in failed:
            print(f"  - {check['name']}: {check['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
