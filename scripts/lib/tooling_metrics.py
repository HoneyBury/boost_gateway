"""Deterministic metrics for script and workflow governance."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import textwrap
from pathlib import Path
from typing import Any


RUN_PATTERN = re.compile(r"^(?P<indent>\s*)run:\s*(?P<body>.*)$")


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_run_blocks(path: Path) -> list[str]:
    """Extract shell bodies from the small YAML subset used by Actions workflows."""
    blocks: list[str] = []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    index = 0
    while index < len(lines):
        match = RUN_PATTERN.match(lines[index])
        if match is None:
            index += 1
            continue
        indent = len(match.group("indent"))
        body = match.group("body")
        if body.startswith("|") or body.startswith(">"):
            block_lines: list[str] = []
            index += 1
            while index < len(lines):
                raw = lines[index]
                current_indent = len(raw) - len(raw.lstrip(" "))
                if raw.strip() and current_indent <= indent:
                    break
                block_lines.append(raw)
                index += 1
            blocks.append(textwrap.dedent("\n".join(block_lines)))
            continue
        blocks.append(body)
        index += 1
    return blocks


def normalized_shell_lines(block: str) -> list[str]:
    lines: list[str] = []
    for raw in re.sub(r"\\\s*\n\s*", " ", block).splitlines():
        line = " ".join(raw.strip().split())
        if not line or line.startswith("#") or line == "set -euo pipefail":
            continue
        lines.append(line)
    return lines


def duplicate_workflow_fragments(
    workflow_paths: list[Path], *, fragment_lines: int = 3, min_workflows: int = 3
) -> list[dict[str, Any]]:
    """Find repeated consecutive shell fragments spanning multiple workflows."""
    occurrences: dict[tuple[str, ...], set[str]] = {}
    for path in workflow_paths:
        seen_in_workflow: set[tuple[str, ...]] = set()
        for block in extract_run_blocks(path):
            lines = normalized_shell_lines(block)
            for index in range(len(lines) - fragment_lines + 1):
                seen_in_workflow.add(tuple(lines[index : index + fragment_lines]))
        for fragment in seen_in_workflow:
            occurrences.setdefault(fragment, set()).add(path.as_posix())

    repeated: list[dict[str, Any]] = []
    for fragment, workflows in occurrences.items():
        if len(workflows) < min_workflows:
            continue
        rendered = "\n".join(fragment)
        repeated.append(
            {
                "fingerprint": hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16],
                "workflow_count": len(workflows),
                "preview": list(fragment),
            }
        )
    return sorted(repeated, key=lambda item: str(item["fingerprint"]))


def python_declares_cli(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Attribute) and function.attr == "ArgumentParser":
                return True
        if not isinstance(node, ast.If):
            continue
        comparison = node.test
        if not isinstance(comparison, ast.Compare) or len(comparison.comparators) != 1:
            continue
        left = comparison.left
        right = comparison.comparators[0]
        if (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(right, ast.Constant)
            and right.value == "__main__"
        ):
            return True
    return False


def cli_implementations(root: Path, inventory: dict[str, Any]) -> list[Path]:
    scripts_root = root / "scripts"
    aliases = {
        path
        for path, metadata in inventory.get("scripts", {}).items()
        if isinstance(metadata, dict) and metadata.get("canonical")
    }
    implementations: list[Path] = []
    for path in sorted(scripts_root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        if relative in aliases or path.name == "__init__.py":
            continue
        if path.suffix == ".py" and python_declares_cli(path):
            implementations.append(path)
        elif path.suffix in {".sh", ".ps1"}:
            implementations.append(path)
    return implementations


def explicitly_tested_cli(root: Path, script_path: Path, test_paths: list[Path]) -> bool:
    stem = script_path.stem
    filename = script_path.name
    dotted = script_path.relative_to(root).with_suffix("").as_posix().replace("/", ".")
    for test_path in test_paths:
        if stem in test_path.stem:
            return True
        try:
            text = test_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        if filename in text or dotted in text:
            return True
    return False


def line_count(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += len(path.read_text(encoding="utf-8-sig").splitlines())
        except (OSError, UnicodeDecodeError):
            continue
    return total


def collect_tooling_metrics(
    root: Path, *, fragment_lines: int = 3, min_workflows: int = 3
) -> dict[str, Any]:
    inventory = load_json_object(root / "docs/script-inventory.json")
    workflow_paths = sorted(
        [
            *(root / ".github/workflows").glob("*.yml"),
            *(root / ".github/workflows").glob("*.yaml"),
        ]
    )
    script_paths = sorted(
        path
        for path in (root / "scripts").rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix in {".py", ".sh", ".ps1"}
    )
    test_paths = sorted(
        path for path in (root / "tests/python").glob("test_*.py") if path.is_file()
    )
    cli_paths = cli_implementations(root, inventory)
    untested = [
        path.relative_to(root).as_posix()
        for path in cli_paths
        if not explicitly_tested_cli(root, path, test_paths)
    ]
    repeated = duplicate_workflow_fragments(
        workflow_paths,
        fragment_lines=fragment_lines,
        min_workflows=min_workflows,
    )
    return {
        "public_entrypoints": len(inventory.get("public_entrypoints", [])),
        "workflow_files": len(workflow_paths),
        "workflow_duplicate_fragments": len(repeated),
        "workflow_duplicate_fragment_details": repeated,
        "cli_implementations": len(cli_paths),
        "untested_cli": len(untested),
        "untested_cli_paths": untested,
        "script_files": len(script_paths),
        "script_lines": line_count(script_paths),
        "workflow_lines": line_count(workflow_paths),
    }
