#!/usr/bin/env python3
"""Validate the accepted next-minor decision manifest and its ADRs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = Path("docs/decisions/v3.6-decision-manifest.json")
EXPECTED_SCHEMA_VERSION = 1
EXPECTED_TARGET_MINOR = "v3.6.0"
EXPECTED_STATUS = "accepted_for_implementation"
EXPECTED_DECISION_COUNT = 5
EXPECTED_DEFAULT_ACTIVATION_POLICY = "blocked_until_gates_pass"
EXPECTED_DECISION_IDS = [
    "v36-raft-wire-schema",
    "v36-identity-jwks",
    "v36-macos-arm64",
    "v36-sdk-distribution",
    "v36-debug-symbols",
]


def add_check(
    checks: list[dict[str, Any]],
    name: str,
    passed: bool,
    detail: str,
    *,
    decision_id: str = "",
) -> None:
    check: dict[str, Any] = {
        "name": name,
        "category": "next_minor_decisions",
        "passed": passed,
        "detail": detail,
    }
    if decision_id:
        check["decision_id"] = decision_id
    checks.append(check)


def load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {}, f"cannot read manifest: {exc}"
    except json.JSONDecodeError as exc:
        return {}, f"invalid manifest JSON: {exc}"
    if not isinstance(parsed, dict):
        return {}, "manifest root must be a JSON object"
    return parsed, ""


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def nonempty_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(nonempty_string(item) for item in value)
    )


def string_list(value: Any) -> bool:
    return isinstance(value, list) and all(nonempty_string(item) for item in value)


def normalized_markdown(text: str) -> str:
    return re.sub(r"[`*_]", "", text).casefold()


def value_markers(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [item.strip() for item in value if nonempty_string(item)]
    return []


def resolve_repo_document(root: Path, path_value: Any) -> tuple[Path | None, str]:
    if not nonempty_string(path_value):
        return None, "doc must be a non-empty repository-relative path"
    relative = Path(path_value)
    if relative.is_absolute():
        return None, "doc must be repository-relative"
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None, "doc must remain inside the repository root"
    if candidate.suffix.casefold() != ".md":
        return None, "doc must reference a Markdown ADR"
    return candidate, ""


def validate_adr(
    root: Path,
    decision: dict[str, Any],
    decision_id: str,
    checks: list[dict[str, Any]],
) -> str:
    document, path_error = resolve_repo_document(root, decision.get("document"))
    add_check(
        checks,
        f"decision:{decision_id}:doc-path",
        document is not None,
        path_error or str(decision.get("document")),
        decision_id=decision_id,
    )
    if document is None:
        return ""

    exists = document.is_file()
    add_check(
        checks,
        f"decision:{decision_id}:doc-exists",
        exists,
        str(document),
        decision_id=decision_id,
    )
    if not exists:
        return str(decision.get("document"))

    try:
        text = document.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        add_check(
            checks,
            f"decision:{decision_id}:doc-readable",
            False,
            str(exc),
            decision_id=decision_id,
        )
        return str(decision.get("document"))

    normalized = normalized_markdown(text)
    core_markers = [
        decision_id,
        str(decision.get("status", "")),
        str(decision.get("target_minor", "")),
        str(decision.get("default_activation", "")),
    ]
    for field in (
        "compatibility_window",
        "migration_window",
        "release_assets",
        "validation_gates",
        "dependencies",
    ):
        core_markers.extend(value_markers(decision.get(field)))
    missing_markers = [
        marker for marker in core_markers
        if marker and normalized_markdown(marker) not in normalized
    ]
    add_check(
        checks,
        f"decision:{decision_id}:doc-contract",
        not missing_markers,
        "all manifest contract values are present in ADR"
        if not missing_markers
        else "ADR missing manifest values: " + ", ".join(missing_markers),
        decision_id=decision_id,
    )
    return str(decision.get("document"))


def validate_manifest(
    root: Path,
    manifest: dict[str, Any],
    checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    add_check(
        checks,
        "manifest:schema-version",
        manifest.get("schema_version") == EXPECTED_SCHEMA_VERSION,
        f"schema_version={manifest.get('schema_version')!r}; expected={EXPECTED_SCHEMA_VERSION}",
    )
    target_minor = manifest.get("target_minor")
    add_check(
        checks,
        "manifest:target-minor",
        target_minor == EXPECTED_TARGET_MINOR,
        f"target_minor={target_minor!r}; expected={EXPECTED_TARGET_MINOR}",
    )
    add_check(
        checks,
        "manifest:default-activation-policy",
        manifest.get("default_activation_policy")
        == EXPECTED_DEFAULT_ACTIVATION_POLICY,
        "default_activation_policy="
        f"{manifest.get('default_activation_policy')!r}; "
        f"expected={EXPECTED_DEFAULT_ACTIVATION_POLICY}",
    )

    decisions = manifest.get("decisions")
    valid_list = isinstance(decisions, list)
    add_check(
        checks,
        "manifest:decisions-list",
        valid_list,
        "decisions must be a JSON array",
    )
    if not valid_list:
        return []

    add_check(
        checks,
        "manifest:decision-count",
        len(decisions) == EXPECTED_DECISION_COUNT,
        f"decision_count={len(decisions)}; expected={EXPECTED_DECISION_COUNT}",
    )
    ids = [item.get("id") for item in decisions if isinstance(item, dict)]
    unique_ids = (
        len(ids) == len(decisions)
        and all(nonempty_string(item) for item in ids)
        and len(set(ids)) == len(ids)
    )
    add_check(
        checks,
        "manifest:unique-decision-ids",
        unique_ids,
        "all five decisions must have unique, non-empty ids",
    )
    add_check(
        checks,
        "manifest:decision-scope-and-order",
        ids == EXPECTED_DECISION_IDS,
        f"decision ids must match the accepted P2 scope; actual={ids!r}",
    )
    implementation_orders = [
        item.get("implementation_order")
        for item in decisions
        if isinstance(item, dict)
    ]
    add_check(
        checks,
        "manifest:implementation-order",
        all(
            isinstance(order, int) and not isinstance(order, bool)
            for order in implementation_orders
        )
        and implementation_orders == list(range(1, EXPECTED_DECISION_COUNT + 1)),
        "implementation_order values must be unique and ordered exactly 1..5; "
        f"actual={implementation_orders!r}",
    )
    id_to_order = {
        str(item.get("id")): item.get("implementation_order")
        for item in decisions
        if isinstance(item, dict)
        and nonempty_string(item.get("id"))
        and isinstance(item.get("implementation_order"), int)
        and not isinstance(item.get("implementation_order"), bool)
    }

    decision_results: list[dict[str, Any]] = []
    seen_docs: set[str] = set()
    for index, raw_decision in enumerate(decisions):
        fallback_id = f"index-{index}"
        if not isinstance(raw_decision, dict):
            add_check(
                checks,
                f"decision:{fallback_id}:object",
                False,
                "decision must be a JSON object",
                decision_id=fallback_id,
            )
            decision_results.append(
                {"id": fallback_id, "document": "", "passed": False}
            )
            continue

        decision_id = raw_decision.get("id")
        decision_id = decision_id.strip() if nonempty_string(decision_id) else fallback_id
        check_start = len(checks)
        add_check(
            checks,
            f"decision:{decision_id}:status",
            raw_decision.get("status") == EXPECTED_STATUS,
            f"status={raw_decision.get('status')!r}; expected={EXPECTED_STATUS}",
            decision_id=decision_id,
        )
        add_check(
            checks,
            f"decision:{decision_id}:target-minor",
            raw_decision.get("target_minor") == target_minor == EXPECTED_TARGET_MINOR,
            f"target_minor={raw_decision.get('target_minor')!r}",
            decision_id=decision_id,
        )
        for field in ("compatibility_window", "migration_window"):
            add_check(
                checks,
                f"decision:{decision_id}:{field.replace('_', '-')}",
                nonempty_string(raw_decision.get(field)),
                f"{field} must be a non-empty string",
                decision_id=decision_id,
            )
        add_check(
            checks,
            f"decision:{decision_id}:default-activation",
            raw_decision.get("default_activation")
            == EXPECTED_DEFAULT_ACTIVATION_POLICY,
            "default_activation="
            f"{raw_decision.get('default_activation')!r}; "
            f"expected={EXPECTED_DEFAULT_ACTIVATION_POLICY}",
            decision_id=decision_id,
        )
        for field in ("release_assets", "validation_gates"):
            add_check(
                checks,
                f"decision:{decision_id}:{field.replace('_', '-')}",
                nonempty_string_list(raw_decision.get(field)),
                f"{field} must be a non-empty list of strings",
                decision_id=decision_id,
            )
        dependencies = raw_decision.get("dependencies")
        valid_dependencies = (
            string_list(dependencies)
            and len(set(dependencies)) == len(dependencies)
        )
        add_check(
            checks,
            f"decision:{decision_id}:dependencies",
            valid_dependencies,
            "dependencies must be a unique list of non-empty decision ids; an empty list is allowed",
            decision_id=decision_id,
        )
        dependency_errors: list[str] = []
        if valid_dependencies:
            current_order = raw_decision.get("implementation_order")
            for dependency in dependencies:
                dependency_order = id_to_order.get(dependency)
                if dependency_order is None:
                    dependency_errors.append(f"unknown dependency {dependency!r}")
                elif not isinstance(current_order, int) or dependency_order >= current_order:
                    dependency_errors.append(
                        f"dependency {dependency!r} order {dependency_order!r} "
                        f"must precede {current_order!r}"
                    )
        add_check(
            checks,
            f"decision:{decision_id}:dependency-order",
            valid_dependencies and not dependency_errors,
            "all dependencies reference earlier decisions"
            if valid_dependencies and not dependency_errors
            else "; ".join(dependency_errors) or "dependencies has invalid shape",
            decision_id=decision_id,
        )
        doc_text = raw_decision.get("document")
        normalized_doc = str(doc_text).replace("\\", "/") if nonempty_string(doc_text) else ""
        add_check(
            checks,
            f"decision:{decision_id}:unique-doc",
            bool(normalized_doc) and normalized_doc not in seen_docs,
            "each decision must reference a distinct ADR",
            decision_id=decision_id,
        )
        if normalized_doc:
            seen_docs.add(normalized_doc)
        resolved_doc = validate_adr(root, raw_decision, decision_id, checks)
        decision_checks = checks[check_start:]
        decision_results.append(
            {
                "id": decision_id,
                "status": raw_decision.get("status", ""),
                "target_minor": raw_decision.get("target_minor", ""),
                "document": resolved_doc,
                "passed": all(check["passed"] for check in decision_checks),
                "failed_checks": sum(not check["passed"] for check in decision_checks),
            }
        )

    return decision_results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("runtime/validation/next-minor-decisions-summary.json"),
    )
    args = parser.parse_args()

    root = args.root.resolve()
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    summary_path = (
        args.summary_path
        if args.summary_path.is_absolute()
        else root / args.summary_path
    )
    checks: list[dict[str, Any]] = []
    manifest, load_error = load_json(manifest_path)
    add_check(
        checks,
        "manifest:readable-json-object",
        not load_error,
        load_error or str(manifest_path),
    )
    decisions = validate_manifest(root, manifest, checks) if not load_error else []

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "gate": "next_minor_decisions",
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "next_minor_decisions" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "target_minor": manifest.get("target_minor", "") if manifest else "",
        "decision_count": len(decisions),
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "decisions": decisions,
        "checks": checks,
        "artifacts": {
            "manifest_path": str(manifest_path),
            "summary_path": str(summary_path),
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    passed_count = len(checks) - len(failed)
    print(
        "next minor decisions: "
        f"{'PASS' if summary['passed'] else 'FAIL'} "
        f"({passed_count}/{len(checks)} checks)"
    )
    print(f"summary: {summary_path}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
