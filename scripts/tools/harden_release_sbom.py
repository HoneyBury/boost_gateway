#!/usr/bin/env python3
"""Enrich and verify release SPDX SBOM file and Conan dependency semantics."""

from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    repo_import_root = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "scripts" / "__init__.py").is_file()
    )
    sys.path.insert(0, str(repo_import_root))

import argparse
import hashlib
import json
import os
import re
import tarfile
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.evidence_provenance import build_evidence_provenance



from scripts.lib.release_sbom_io import *  # noqa: E402,F401,F403
from scripts.lib.release_sbom_semantics import *  # noqa: E402,F401,F403

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("enrich", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--sbom", type=Path, required=True)
        child.add_argument("--lockfile", type=Path, required=True)
        child.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
        child.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY)
        child.add_argument("--configuration", default="Release")
        child.add_argument("--candidate-revision")
        sources = child.add_mutually_exclusive_group(required=True)
        sources.add_argument("--package-root", type=Path)
        sources.add_argument("--archive", type=Path)
        child.add_argument("--expected-root")
    attestation = subparsers.add_parser("verify-attestation")
    attestation.add_argument("--sbom", type=Path, required=True)
    attestation.add_argument("--attestation-verification", type=Path, required=True)
    attestation.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary: dict[str, Any]
    if args.command == "verify-attestation":
        try:
            standalone_sbom = load_json_object(
                args.sbom, "published standalone SPDX SBOM"
            )
            attestation_results = load_json_array(
                args.attestation_verification, "SBOM attestation verification"
            )
            summary = verify_attested_sbom_predicate(
                standalone_sbom, attestation_results
            )
            summary["standalone_sbom"] = {
                "path": str(args.sbom),
                "sha256": sha256_file(args.sbom),
            }
            summary["attestation_verification"] = {
                "path": str(args.attestation_verification),
                "sha256": sha256_file(args.attestation_verification),
            }
        except (OSError, SbomSemanticError, ValueError) as exc:
            summary = {
                "summary_version": 2,
                "generated_at": datetime.now(UTC)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "overall_pass": False,
                "passed": False,
                "predicate_matches_published_sbom": False,
                "predicate_type": SPDX_PREDICATE_TYPE,
                "checks": {},
                "failures": [str(exc)],
            }
        write_json(args.summary_path, summary)
        if summary["overall_pass"]:
            print(
                "published SBOM attestation binding: PASS "
                f"({summary['matching_predicate_count']} matching SPDX predicate)"
            )
            print(f"summary: {args.summary_path}")
            return 0
        print("published SBOM attestation binding: FAIL")
        for failure in summary.get("failures", []):
            print(f"  - {failure}")
        print(f"summary: {args.summary_path}")
        return 1

    try:
        if args.archive is not None and not args.expected_root:
            raise SbomSemanticError("--expected-root is required with --archive")
        if args.package_root is not None and args.expected_root:
            raise SbomSemanticError("--expected-root is only valid with --archive")
        policy = load_policy(args.policy)
        dependencies = load_runtime_dependencies(args.lockfile, policy)
        package_files = (
            collect_package_files(args.package_root)
            if args.package_root is not None
            else collect_archive_files(args.archive, args.expected_root)
        )
        document = load_json_object(args.sbom, "SPDX SBOM")
        if args.command == "enrich":
            document = enrich_sbom_document(document, package_files, dependencies)
            write_json(args.sbom, document)
        summary = verify_sbom_document(document, package_files, dependencies, policy)
        summary["sbom"]["path"] = str(args.sbom)
        summary["sbom"]["sha256"] = sha256_file(args.sbom)
        summary["conan"]["lockfile"] = str(args.lockfile)
        summary["conan"]["lockfile_sha256"] = sha256_file(args.lockfile)
        summary["policy"] = {
            "path": str(args.policy),
            "sha256": sha256_file(args.policy),
        }
    except (OSError, SbomSemanticError, ValueError) as exc:
        summary = {
            "summary_version": 2,
            "generated_at": datetime.now(UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "overall_pass": False,
            "passed": False,
            "checks": {},
            "failures": [str(exc)],
        }
    summary["provenance"] = build_evidence_provenance(
        ROOT,
        build_configuration=args.configuration,
        conan_lockfile=args.lockfile,
        candidate_revision=args.candidate_revision,
    )
    summary["artifacts"] = {
        "summary_path": str(args.summary_path),
        "sbom_path": str(args.sbom),
    }
    write_json(args.summary_path, summary)
    if summary["overall_pass"]:
        print(
            "release SBOM semantics: PASS "
            f"({summary['sbom']['sha256_covered_file_count']} files, "
            f"{len(summary['conan']['runtime_dependencies'])} Conan runtime dependencies)"
        )
        print(f"summary: {args.summary_path}")
        return 0
    print("release SBOM semantics: FAIL")
    for failure in summary.get("failures", []):
        print(f"  - {failure}")
    print(f"summary: {args.summary_path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
