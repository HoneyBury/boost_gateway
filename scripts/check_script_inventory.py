#!/usr/bin/env python3
"""Validate the maintained script inventory and public entrypoint boundaries."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VALID_CATEGORIES = {
    "public_entrypoint",
    "aggregate_gate",
    "producer",
    "tool",
    "platform_wrapper",
    "legacy",
}


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
    actual = {
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in (ROOT / "scripts").iterdir()
        if path.is_file() and not path.name.startswith(".")
    }

    add(checks, "inventory-json", bool(inventory), "inventory is valid JSON object")
    add(checks, "inventory-schema-version", inventory.get("schema_version") == 1, "schema_version is 1")
    add(checks, "all-scripts-declared", actual <= declared, "all top-level scripts are declared")
    add(checks, "no-missing-declared-scripts", declared <= actual, "all declared scripts exist")

    if isinstance(scripts, dict):
        for path_text, meta in sorted(scripts.items()):
            category = meta.get("category") if isinstance(meta, dict) else ""
            add(checks, f"category:{path_text}", category in VALID_CATEGORIES, f"category={category}")
            wraps = meta.get("wraps") if isinstance(meta, dict) else None
            if wraps:
                add(checks, f"wraps:{path_text}", str(wraps) in declared and (ROOT / str(wraps)).exists(), f"wraps={wraps}")

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
