#!/usr/bin/env python3
"""Authorize a release only from governed main with same-revision evidence."""

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
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lib.evidence_provenance import validate_evidence_provenance

ROOT = Path(__file__).resolve().parents[3]
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
RELEASE_TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")


def git_value(root: Path, *args: str) -> tuple[bool, str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    value = (
        completed.stdout.strip()
        if completed.returncode == 0
        else completed.stderr.strip()
    )
    return completed.returncode == 0, value


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_evidence(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"unable to read JSON evidence: {exc}"
    if not isinstance(value, dict):
        return None, "evidence must be a JSON object"
    return value, "JSON object loaded"


def evaluate_authorization(
    root: Path,
    *,
    event_name: str,
    github_ref: str,
    github_ref_name: str,
    candidate_revision: str,
    governed_ref: str,
    evidence_paths: list[Path],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    resolved: dict[str, Any] = {
        "event_name": event_name,
        "github_ref": github_ref,
        "github_ref_name": github_ref_name,
        "requested_candidate_revision": candidate_revision,
        "governed_ref": governed_ref,
        "evidence": [],
    }

    is_supported_event = event_name in {"push", "workflow_dispatch"}
    add(
        checks,
        "event:supported",
        is_supported_event,
        "only tag push and explicit workflow_dispatch events are authorized",
    )
    add(
        checks,
        "candidate:full-sha",
        bool(FULL_SHA_RE.fullmatch(candidate_revision)),
        "candidate revision must be a lowercase full commit SHA",
    )

    candidate_ok, candidate_commit = git_value(
        root, "rev-parse", f"{candidate_revision}^{{commit}}"
    )
    candidate_valid = (
        candidate_ok
        and bool(FULL_SHA_RE.fullmatch(candidate_commit))
        and candidate_commit == candidate_revision
    )
    add(
        checks,
        "candidate:commit",
        candidate_valid,
        f"candidate resolves to {candidate_commit!r}",
    )
    resolved["candidate_revision"] = candidate_commit if candidate_valid else ""

    head_ok, head_commit = git_value(root, "rev-parse", "HEAD^{commit}")
    add(
        checks,
        "candidate:checkout",
        head_ok and candidate_valid and head_commit == candidate_commit,
        f"checkout={head_commit!r} candidate={candidate_commit!r}",
    )
    resolved["checkout_revision"] = head_commit if head_ok else ""

    governed_ok, governed_commit = git_value(
        root, "rev-parse", f"{governed_ref}^{{commit}}"
    )
    add(
        checks,
        "main:ref-present",
        governed_ok and bool(FULL_SHA_RE.fullmatch(governed_commit)),
        f"{governed_ref} resolves to {governed_commit!r}",
    )
    resolved["governed_revision"] = governed_commit if governed_ok else ""

    ancestor_ok = False
    if candidate_valid and governed_ok:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", candidate_commit, governed_commit],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        ancestor_ok = ancestor.returncode == 0
    add(
        checks,
        "main:candidate-is-ancestor",
        ancestor_ok,
        "candidate commit must belong to governed main history",
    )

    tag_name = ""
    if github_ref.startswith("refs/tags/"):
        tag_name = github_ref.removeprefix("refs/tags/")
    is_tag_ref = bool(tag_name)
    is_main_dispatch = (
        event_name == "workflow_dispatch"
        and github_ref == "refs/heads/main"
        and github_ref_name == "main"
    )
    add(
        checks,
        "ref:event-contract",
        (event_name == "push" and is_tag_ref)
        or (event_name == "workflow_dispatch" and (is_tag_ref or is_main_dispatch)),
        "push requires a v* tag; workflow_dispatch requires main or a v* tag",
    )

    if is_tag_ref:
        add(
            checks,
            "tag:name",
            bool(RELEASE_TAG_RE.fullmatch(tag_name)) and github_ref_name == tag_name,
            f"ref tag={tag_name!r} ref_name={github_ref_name!r}",
        )
        tag_type_ok, tag_type = git_value(
            root, "cat-file", "-t", f"refs/tags/{tag_name}"
        )
        add(
            checks,
            "tag:annotated",
            tag_type_ok and tag_type == "tag",
            f"tag object type={tag_type!r}; annotated tag required",
        )
        tag_commit_ok, tag_commit = git_value(
            root, "rev-parse", f"refs/tags/{tag_name}^{{commit}}"
        )
        add(
            checks,
            "tag:candidate-binding",
            tag_commit_ok and candidate_valid and tag_commit == candidate_commit,
            f"tag commit={tag_commit!r} candidate={candidate_commit!r}",
        )
        resolved["tag"] = tag_name
        resolved["tag_revision"] = tag_commit if tag_commit_ok else ""
    else:
        add(
            checks,
            "dispatch:current-main",
            is_main_dispatch
            and candidate_valid
            and governed_ok
            and candidate_commit == governed_commit,
            "main dispatch must use the current governed main commit",
        )

    add(
        checks,
        "evidence:required",
        bool(evidence_paths),
        "at least one required candidate evidence summary must be supplied",
    )
    for index, raw_path in enumerate(evidence_paths, start=1):
        path = raw_path if raw_path.is_absolute() else root / raw_path
        evidence, detail = load_evidence(path)
        prefix = f"evidence:{index}:{raw_path.name}"
        add(checks, f"{prefix}:json", evidence is not None, detail)
        record: dict[str, Any] = {"path": str(raw_path), "sha256": ""}
        if evidence is not None:
            record["sha256"] = sha256_file(path)
            add(
                checks,
                f"{prefix}:passed",
                evidence.get("overall_pass") is True and evidence.get("passed") is True,
                "overall_pass and passed must both be true",
            )
            provenance_errors = validate_evidence_provenance(
                evidence.get("provenance"),
                expected_candidate_revision=(
                    candidate_commit if candidate_valid else candidate_revision
                ),
            )
            add(
                checks,
                f"{prefix}:provenance",
                not provenance_errors,
                (
                    "; ".join(provenance_errors)
                    if provenance_errors
                    else "same-revision provenance valid"
                ),
            )
        resolved["evidence"].append(record)

    return checks, resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--github-ref", required=True)
    parser.add_argument("--github-ref-name", required=True)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--governed-ref", default="refs/remotes/origin/main")
    parser.add_argument(
        "--evidence-summary",
        type=Path,
        action="append",
        default=[],
        help="Required passing same-revision JSON summary; repeat for every required input.",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=Path("runtime/validation/release-source-authorization-summary.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    summary_path = (
        args.summary_path
        if args.summary_path.is_absolute()
        else root / args.summary_path
    )
    checks, authorization = evaluate_authorization(
        root,
        event_name=args.event_name,
        github_ref=args.github_ref,
        github_ref_name=args.github_ref_name,
        candidate_revision=args.candidate_revision,
        governed_ref=args.governed_ref,
        evidence_paths=args.evidence_summary,
    )
    failed = [check for check in checks if not check["passed"]]
    summary = {
        "summary_version": 2,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "gate": "release_source_authorization",
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "release_source_authorization" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "authorization": authorization,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
        "artifacts": {"summary_path": str(summary_path)},
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        f"release source authorization: {'PASS' if not failed else 'FAIL'} "
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
