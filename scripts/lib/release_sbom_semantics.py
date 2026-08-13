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



from scripts.lib.release_sbom_io import *  # noqa: F401,F403
from scripts.lib.release_sbom_io import _safe_relative_path, _spdx_id

def enrich_sbom_document(
    document: dict[str, Any],
    package_files: dict[str, str],
    dependencies: list[dict[str, str]],
) -> dict[str, Any]:
    if document.get("spdxVersion") != "SPDX-2.3":
        raise SbomSemanticError("SBOM spdxVersion must be SPDX-2.3")
    packages = document.get("packages")
    if not isinstance(packages, list) or not packages:
        raise SbomSemanticError("SBOM packages must contain a document root package")
    if not all(isinstance(package, dict) for package in packages):
        raise SbomSemanticError("SBOM packages entries must be JSON objects")

    root_package_id = str(packages[0].get("SPDXID", ""))
    if not root_package_id.startswith("SPDXRef-"):
        raise SbomSemanticError("SBOM document root package has no valid SPDXID")

    old_files = document.get("files", [])
    old_file_ids = {
        str(item.get("SPDXID"))
        for item in old_files
        if isinstance(item, dict) and item.get("SPDXID")
    }
    retained_packages: list[dict[str, Any]] = []
    old_conan_ids: set[str] = set()
    for package in packages:
        external_references = package.get("externalRefs", [])
        if not isinstance(external_references, list):
            raise SbomSemanticError(
                f"SBOM package {package.get('SPDXID')!r} externalRefs must be a list"
            )
        is_conan = any(
            isinstance(reference, dict)
            and str(reference.get("referenceLocator", "")).startswith("pkg:conan/")
            for reference in external_references
        )
        if is_conan:
            old_conan_ids.add(str(package.get("SPDXID", "")))
        else:
            retained_packages.append(package)

    files: list[dict[str, Any]] = []
    file_ids: list[str] = []
    for path, digest in sorted(package_files.items()):
        file_id = _spdx_id("File", f"{path}\0{digest}")
        file_ids.append(file_id)
        files.append(
            {
                "fileName": path,
                "SPDXID": file_id,
                "checksums": [{"algorithm": "SHA256", "checksumValue": digest}],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
                "copyrightText": "NOASSERTION",
            }
        )

    conan_packages: list[dict[str, Any]] = []
    conan_ids: list[str] = []
    for dependency in dependencies:
        package_id = _spdx_id("Package-Conan", dependency["reference"])
        conan_ids.append(package_id)
        conan_packages.append(
            {
                "name": dependency["name"],
                "SPDXID": package_id,
                "versionInfo": dependency["version"],
                "supplier": "NOASSERTION",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "primaryPackagePurpose": "LIBRARY",
                "comment": f"Conan lock reference: {dependency['reference']}",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": conan_purl(dependency),
                    }
                ],
            }
        )

    relationships = document.get("relationships", [])
    if not isinstance(relationships, list):
        raise SbomSemanticError("SBOM relationships must be a list")
    removed_ids = old_file_ids | old_conan_ids
    retained_relationships = [
        relationship
        for relationship in relationships
        if isinstance(relationship, dict)
        and str(relationship.get("spdxElementId", "")) not in removed_ids
        and str(relationship.get("relatedSpdxElement", "")) not in removed_ids
    ]
    retained_relationships.extend(
        {
            "spdxElementId": root_package_id,
            "relatedSpdxElement": file_id,
            "relationshipType": "CONTAINS",
        }
        for file_id in file_ids
    )
    retained_relationships.extend(
        {
            "spdxElementId": root_package_id,
            "relatedSpdxElement": package_id,
            "relationshipType": "DEPENDS_ON",
        }
        for package_id in conan_ids
    )

    document["files"] = files
    document["packages"] = retained_packages + conan_packages
    document["relationships"] = retained_relationships
    return document


def _conan_packages_from_sbom(
    document: dict[str, Any], failures: list[str]
) -> dict[str, tuple[str, str, str]]:
    discovered: dict[str, tuple[str, str, str]] = {}
    packages = document.get("packages")
    if not isinstance(packages, list):
        failures.append("SBOM packages must be a list")
        return discovered
    for package in packages:
        if not isinstance(package, dict):
            failures.append("SBOM package entry is not a JSON object")
            continue
        references = package.get("externalRefs", [])
        if not isinstance(references, list):
            failures.append(
                f"SBOM package {package.get('SPDXID')!r} externalRefs must be a list"
            )
            continue
        for reference in references:
            if (
                not isinstance(reference, dict)
                or reference.get("referenceType") != "purl"
            ):
                continue
            locator = str(reference.get("referenceLocator", ""))
            try:
                parsed = parse_conan_purl(locator)
            except (SbomSemanticError, ValueError) as exc:
                failures.append(str(exc))
                continue
            if parsed is None:
                continue
            name, version, revision = parsed
            if name in discovered:
                failures.append(f"duplicate Conan SBOM package: {name}")
            else:
                discovered[name] = (version, revision, str(package.get("SPDXID", "")))
    return discovered


def verify_sbom_document(
    document: dict[str, Any],
    package_files: dict[str, str],
    dependencies: list[dict[str, str]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    if document.get("spdxVersion") != "SPDX-2.3":
        failures.append("SBOM spdxVersion must be SPDX-2.3")

    expected_paths = set(package_files)
    sbom_files = document.get("files")
    actual_digests: dict[str, str] = {}
    if not isinstance(sbom_files, list):
        failures.append("SBOM files must be a list")
        sbom_files = []
    for entry in sbom_files:
        if not isinstance(entry, dict):
            failures.append("SBOM file entry is not a JSON object")
            continue
        name = str(entry.get("fileName", ""))
        try:
            normalized = _safe_relative_path(name, "SBOM file path").as_posix()
        except SbomSemanticError as exc:
            failures.append(str(exc))
            continue
        if normalized in actual_digests:
            failures.append(f"duplicate SBOM file: {normalized}")
            continue
        checksums = entry.get("checksums")
        if not isinstance(checksums, list):
            failures.append(f"SBOM file has no checksum list: {normalized}")
            continue
        sha256_values = [
            str(checksum.get("checksumValue", ""))
            for checksum in checksums
            if isinstance(checksum, dict) and checksum.get("algorithm") == "SHA256"
        ]
        if len(sha256_values) != 1 or SHA256_RE.fullmatch(sha256_values[0]) is None:
            failures.append(
                f"SBOM file must have exactly one lowercase SHA256 checksum: {normalized}"
            )
            continue
        digest = sha256_values[0]
        if digest == "0" * 64:
            failures.append(f"SBOM file has an all-zero SHA256 checksum: {normalized}")
            continue
        actual_digests[normalized] = digest

    actual_paths = set(actual_digests)
    for path in sorted(expected_paths - actual_paths):
        failures.append(f"SBOM is missing package file: {path}")
    for path in sorted(actual_paths - expected_paths):
        failures.append(f"SBOM contains a file absent from the package: {path}")
    for path in sorted(expected_paths & actual_paths):
        if actual_digests[path] != package_files[path]:
            failures.append(
                f"SBOM SHA256 mismatch for {path}: expected {package_files[path]}, got {actual_digests[path]}"
            )

    expected_dependencies = {
        dependency["name"]: (dependency["version"], dependency["recipe_revision"])
        for dependency in dependencies
    }
    discovered_packages = _conan_packages_from_sbom(document, failures)
    discovered_dependencies = {
        name: (version, revision)
        for name, (version, revision, _package_id) in discovered_packages.items()
    }
    for name in sorted(expected_dependencies.keys() - discovered_dependencies.keys()):
        failures.append(f"SBOM is missing Conan runtime dependency: {name}")
    for name in sorted(discovered_dependencies.keys() - expected_dependencies.keys()):
        failures.append(f"SBOM contains a non-runtime Conan dependency: {name}")
    for name in sorted(expected_dependencies.keys() & discovered_dependencies.keys()):
        if discovered_dependencies[name] != expected_dependencies[name]:
            failures.append(
                f"SBOM Conan dependency mismatch for {name}: expected {expected_dependencies[name]}, "
                f"got {discovered_dependencies[name]}"
            )

    relationships = document.get("relationships")
    if not isinstance(relationships, list):
        failures.append("SBOM relationships must be a list")
        relationships = []
    root_ids = {
        str(relationship.get("relatedSpdxElement", ""))
        for relationship in relationships
        if isinstance(relationship, dict)
        and relationship.get("spdxElementId") == "SPDXRef-DOCUMENT"
        and relationship.get("relationshipType") == "DESCRIBES"
    }
    if len(root_ids) != 1:
        failures.append(
            "SBOM must have exactly one document root DESCRIBES relationship"
        )
    root_id = next(iter(root_ids), "")
    dependency_ids = {
        package_id: name
        for name, (_version, _revision, package_id) in discovered_packages.items()
        if name in expected_dependencies and package_id
    }
    depends_on_ids = {
        str(relationship.get("relatedSpdxElement", ""))
        for relationship in relationships
        if isinstance(relationship, dict)
        and relationship.get("spdxElementId") == root_id
        and relationship.get("relationshipType") == "DEPENDS_ON"
    }
    for package_id, name in sorted(dependency_ids.items()):
        if package_id not in depends_on_ids:
            failures.append(
                f"SBOM has no DEPENDS_ON relationship for Conan runtime dependency: {name}"
            )

    excluded = set(policy["excluded_conan_requires"])
    prohibited = sorted(excluded & discovered_dependencies.keys())
    checks = {
        "spdx_2_3": document.get("spdxVersion") == "SPDX-2.3",
        "safe_paths": not any("unsafe" in failure for failure in failures),
        "complete_file_coverage": expected_paths == actual_paths,
        "nonzero_sha256": len(actual_digests) == len(sbom_files),
        "file_digest_match": all(
            actual_digests.get(path) == digest for path, digest in package_files.items()
        ),
        "conan_runtime_complete": discovered_dependencies == expected_dependencies,
        "conan_runtime_relationships": set(dependency_ids) <= depends_on_ids,
        "excluded_dependencies_absent": not prohibited,
    }
    runtime_dependencies = [
        {
            "name": dependency["name"],
            "version": dependency["version"],
            "recipe_revision": dependency["recipe_revision"],
            "purl": conan_purl(dependency),
        }
        for dependency in dependencies
    ]
    return {
        "summary_version": 2,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "overall_pass": not failures and all(checks.values()),
        "passed": not failures and all(checks.values()),
        "sbom": {
            "spdx_version": document.get("spdxVersion", ""),
            "regular_file_count": len(package_files),
            "sha256_covered_file_count": len(actual_digests),
        },
        "conan": {
            "runtime_dependencies": runtime_dependencies,
            "missing_dependencies": sorted(
                expected_dependencies.keys() - discovered_dependencies.keys()
            ),
            "unexpected_dependencies": sorted(
                discovered_dependencies.keys() - expected_dependencies.keys()
            ),
        },
        "checks": checks,
        "failures": failures,
    }


def verify_attested_sbom_predicate(
    standalone_sbom: dict[str, Any], attestation_results: list[Any]
) -> dict[str, Any]:
    failures: list[str] = []
    predicate_count = 0
    matching_predicate_count = 0
    if not attestation_results:
        failures.append("SBOM attestation verification returned no results")

    for index, result in enumerate(attestation_results):
        if not isinstance(result, dict):
            failures.append(f"SBOM attestation result {index} is not a JSON object")
            continue
        verification_result = result.get("verificationResult")
        if not isinstance(verification_result, dict):
            failures.append(
                f"SBOM attestation result {index} has no verificationResult object"
            )
            continue
        statement = verification_result.get("statement")
        if not isinstance(statement, dict):
            failures.append(
                f"SBOM attestation result {index} has no verified statement object"
            )
            continue
        predicate_type = statement.get("predicateType")
        if predicate_type != SPDX_PREDICATE_TYPE:
            failures.append(
                f"SBOM attestation result {index} has unexpected predicateType: {predicate_type!r}"
            )
            continue
        predicate_count += 1
        predicate = statement.get("predicate")
        if not isinstance(predicate, dict):
            failures.append(
                f"SBOM attestation result {index} has no SPDX predicate object"
            )
            continue
        canonical_predicate = json.dumps(
            predicate, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        canonical_standalone = json.dumps(
            standalone_sbom, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        if canonical_predicate == canonical_standalone:
            matching_predicate_count += 1
        else:
            failures.append(
                f"SBOM attestation result {index} predicate does not match the published standalone SBOM"
            )

    predicate_matches = (
        predicate_count > 0 and matching_predicate_count == predicate_count
    )
    checks = {
        "verified_attestation_results_present": bool(attestation_results),
        "spdx_predicates_present": predicate_count > 0,
        "predicate_matches_published_sbom": predicate_matches,
    }
    passed = not failures and all(checks.values())
    return {
        "summary_version": 2,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "overall_pass": passed,
        "passed": passed,
        "predicate_matches_published_sbom": predicate_matches,
        "predicate_type": SPDX_PREDICATE_TYPE,
        "verified_result_count": len(attestation_results),
        "spdx_predicate_count": predicate_count,
        "matching_predicate_count": matching_predicate_count,
        "checks": checks,
        "failures": failures,
    }


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
