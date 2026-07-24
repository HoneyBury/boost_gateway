#!/usr/bin/env python3
"""Validate the maintained script inventory and public entrypoint boundaries."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
VALID_CATEGORIES = {
    "public_entrypoint",
    "aggregate_gate",
    "producer",
    "tool",
    "platform_wrapper",
    "legacy",
}
SCRIPT_REFERENCE_PATTERN = re.compile(r"scripts/[A-Za-z0-9_./-]+\.(?:py|sh|ps1)")


def maintained_reference_sources() -> list[Path]:
    sources: list[Path] = [ROOT / "README.md", ROOT / "CMakeLists.txt"]
    for directory in (
        ROOT / ".github/workflows",
        ROOT / "cmake",
        ROOT / "tests",
        ROOT / "src",
        ROOT / "env/cicd",
        ROOT / "docs",
        ROOT / "sdk/docs",
    ):
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or "docs/archive" in path.as_posix():
                continue
            if path.suffix.lower() in {".md", ".txt", ".yml", ".yaml", ".cmake", ".cpp", ".h", ".py"}:
                sources.append(path)
    for path in (ROOT / "conan/README.md", ROOT / "sdk/CMakeLists.txt"):
        if path.is_file():
            sources.append(path)
    return sorted(set(sources))


def script_references(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return set()
    return set(SCRIPT_REFERENCE_PATTERN.findall(text))


def load_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=ROOT / "docs/script-inventory.json")
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=ROOT / "runtime/validation/script-inventory-summary.json",
    )
    args = parser.parse_args()

    inventory_path = args.inventory if args.inventory.is_absolute() else ROOT / args.inventory
    summary_path = args.summary_path if args.summary_path.is_absolute() else ROOT / args.summary_path
    inventory = load_json(inventory_path)
    checks: list[dict[str, Any]] = []

    scripts = inventory.get("scripts")
    declared = set(scripts) if isinstance(scripts, dict) else set()
    actual_top_level = {
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in (ROOT / "scripts").iterdir()
        if path.is_file() and not path.name.startswith(".")
    }
    actual_scripts = {
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in (ROOT / "scripts").rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix in {".py", ".ps1", ".sh"}
    }
    canonical_paths = {
        str(meta.get("canonical"))
        for meta in scripts.values()
        if isinstance(meta, dict) and meta.get("canonical")
    } if isinstance(scripts, dict) else set()
    internal_scripts = inventory.get("internal_scripts")
    internal_declared = set(internal_scripts) if isinstance(internal_scripts, dict) else set()
    represented = {
        path
        for path in declared | canonical_paths | internal_declared
        if Path(path).suffix in {".py", ".ps1", ".sh"}
    }

    add(checks, "inventory-json", bool(inventory), "inventory is valid JSON object")
    add(checks, "inventory-schema-version", inventory.get("schema_version") == 3, "schema_version is 3")
    add(checks, "all-top-level-scripts-declared", actual_top_level <= declared, "all top-level files are declared")
    add(checks, "all-recursive-scripts-represented", actual_scripts == represented, "every executable script is represented exactly once by role")
    add(checks, "no-missing-declared-scripts", all((ROOT / p).exists() for p in declared), "all declared scripts exist")
    add(checks, "no-missing-internal-scripts", all((ROOT / p).exists() for p in internal_declared), "all internal scripts exist")
    runtime_files = [
        path for path in (ROOT / "scripts").rglob("*")
        if path.is_file() and "runtime" in path.relative_to(ROOT / "scripts").parts
    ]
    add(checks, "no-runtime-artifacts-under-scripts", not runtime_files, "runtime artifacts live under the repository runtime/ directory")

    if isinstance(scripts, dict):
        for path_text, meta in sorted(scripts.items()):
            category = meta.get("category") if isinstance(meta, dict) else ""
            add(checks, f"category:{path_text}", category in VALID_CATEGORIES, f"category={category}")
            wraps = meta.get("wraps") if isinstance(meta, dict) else None
            if wraps:
                add(checks, f"wraps:{path_text}", str(wraps) in declared and (ROOT / str(wraps)).exists(), f"wraps={wraps}")
            canonical = meta.get("canonical") if isinstance(meta, dict) else None
            if canonical:
                add(
                    checks,
                    f"canonical:{path_text}",
                    str(canonical) in actual_scripts and (ROOT / str(canonical)).exists(),
                    f"canonical={canonical}",
                )

    if isinstance(internal_scripts, dict):
        for path_text, meta in sorted(internal_scripts.items()):
            category = meta.get("category") if isinstance(meta, dict) else ""
            add(checks, f"internal-category:{path_text}", category in VALID_CATEGORIES, f"category={category}")
    else:
        add(checks, "internal-scripts-object", False, "internal_scripts must be an object")

    public = inventory.get("public_entrypoints")
    if isinstance(public, list):
        for path_text in public:
            meta = scripts.get(path_text, {}) if isinstance(scripts, dict) else {}
            add(
                checks,
                f"public-entrypoint:{path_text}",
                isinstance(meta, dict) and meta.get("category") == "public_entrypoint",
                "public entrypoint is declared with public_entrypoint category",
            )
    else:
        add(checks, "public-entrypoints-list", False, "public_entrypoints must be a list")

    if isinstance(public, list):
        root_public = {path for path in public if Path(path).parent == Path("scripts")}
        add(
            checks,
            "public-entrypoints-root-only",
            len(root_public) == len(public),
            "stable public entrypoints are root command surfaces",
        )
        compatibility_roots = {
            path_text
            for path_text, meta in scripts.items()
            if Path(path_text).parent == Path("scripts")
            and path_text not in root_public
            and Path(path_text).suffix in {".py", ".sh", ".ps1"}
            and path_text != "scripts/__init__.py"
        } if isinstance(scripts, dict) else set()
        add(
            checks,
            "compatibility-roots-have-canonical-target",
            all(bool(scripts[path].get("canonical")) for path in compatibility_roots),
            "every non-public root command delegates to a canonical implementation",
        )

    for source in maintained_reference_sources():
        source_name = source.relative_to(ROOT).as_posix()
        for reference in sorted(script_references(source)):
            add(
                checks,
                f"reference:{source_name}:{reference}",
                (ROOT / reference).is_file(),
                f"{source_name} references existing script {reference}",
            )

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "script_inventory" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {
            "summary_path": str(summary_path),
            "inventory_path": str(inventory_path),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"script inventory: {'PASS' if summary['passed'] else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)} checks)")
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
