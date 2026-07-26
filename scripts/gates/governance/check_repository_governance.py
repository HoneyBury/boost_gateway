#!/usr/bin/env python3
"""Validate repository ownership, contribution, disclosure, and support contracts."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
PRIMARY_OWNER = "@HoneyBury"
REQUIRED_SECTIONS = {
    "CONTRIBUTING.md": (
        "# Contributing to BoostGateway",
        "## Pull requests and review",
        "## Required validation",
        "## Sensitive changes",
        "## Documentation and commits",
    ),
    "SECURITY.md": (
        "# Security Policy",
        "## Supported versions",
        "## Reporting a vulnerability",
        "## Response expectations",
        "## Coordinated disclosure",
    ),
    "SUPPORT.md": (
        "# Support Policy",
        "## Supported requests",
        "## Unsupported requests",
        "## Where to ask",
        "## Maintenance expectations",
    ),
    "GOVERNANCE.md": (
        "# Repository Governance",
        "## Ownership",
        "## Normal change path",
        "## Emergency change path",
        "## Release governance",
        "## External GitHub settings",
    ),
}
REQUIRED_CODEOWNER_PATTERNS = (
    "*",
    "/.github/",
    "/scripts/gates/governance/",
    "/docs/",
    "/config/",
    "/env/",
)
REQUIRED_DOCUMENT_LINKS = {
    "README.md": (
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SUPPORT.md",
        "GOVERNANCE.md",
    ),
    "docs/README.md": (
        "../CONTRIBUTING.md",
        "../SECURITY.md",
        "../SUPPORT.md",
        "../GOVERNANCE.md",
    ),
    "CONTRIBUTING.md": (
        "SECURITY.md",
        "SUPPORT.md",
        "GOVERNANCE.md",
        "docs/ONBOARDING.md",
        ".github/COMMIT_CONVENTION.md",
    ),
    "GOVERNANCE.md": (
        "CODEOWNERS",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SUPPORT.md",
    ),
}


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def read_text(root: Path, relative: str) -> str:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ""


def parse_codeowners(text: str) -> dict[str, tuple[str, ...]]:
    rules: dict[str, tuple[str, ...]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            rules[parts[0]] = tuple(parts[1:])
    return rules


def evaluate_repository(root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    codeowners_text = read_text(root, "CODEOWNERS")
    add(checks, "file:CODEOWNERS", bool(codeowners_text.strip()), "CODEOWNERS exists and is non-empty")
    codeowners = parse_codeowners(codeowners_text)
    for pattern in REQUIRED_CODEOWNER_PATTERNS:
        owners = codeowners.get(pattern, ())
        add(
            checks,
            f"codeowners:{pattern}",
            PRIMARY_OWNER in owners,
            f"{pattern} is assigned to the primary maintainer",
        )

    for relative, sections in REQUIRED_SECTIONS.items():
        text = read_text(root, relative)
        add(checks, f"file:{relative}", bool(text.strip()), f"{relative} exists and is non-empty")
        for section in sections:
            add(
                checks,
                f"section:{relative}:{section}",
                section in text,
                f"{relative} contains {section}",
            )

    for relative, links in REQUIRED_DOCUMENT_LINKS.items():
        text = read_text(root, relative)
        for link in links:
            add(
                checks,
                f"link:{relative}:{link}",
                link in text,
                f"{relative} references {link}",
            )

    governance = read_text(root, "GOVERNANCE.md")
    normalized_governance = " ".join(governance.split())
    add(
        checks,
        "governance:no-silent-bypass",
        "No silent administrator bypass is permitted." in normalized_governance,
        "the emergency path explicitly rejects silent administrator bypass",
    )
    add(
        checks,
        "governance:external-state-boundary",
        "Repository files do not prove that these settings are active." in normalized_governance,
        "GitHub settings remain an explicitly external verification boundary",
    )

    security = read_text(root, "SECURITY.md")
    normalized_security = " ".join(security.split())
    add(
        checks,
        "security:external-private-reporting-boundary",
        "GitHub private vulnerability reporting is external repository state and must be verified before use."
        in normalized_security,
        "the security policy treats GitHub private reporting as externally verified state",
    )
    add(
        checks,
        "security:non-public-contact",
        "zoujiahe389+boost-gateway-security@gmail.com" in security,
        "the security policy provides a non-issue disclosure contact",
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("runtime/validation/repository-governance-summary.json"),
    )
    args = parser.parse_args()

    root = args.root.resolve()
    summary_path = args.summary_path
    if not summary_path.is_absolute():
        summary_path = root / summary_path

    checks = evaluate_repository(root)
    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "gate": "repository_governance",
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "repository_governance" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        f"repository governance: {'PASS' if not failed else 'FAIL'} "
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
