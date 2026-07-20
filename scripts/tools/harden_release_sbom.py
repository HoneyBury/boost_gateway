#!/usr/bin/env python3
"""Enrich and verify release SPDX SBOM file and Conan dependency semantics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit


DEFAULT_POLICY = Path("config/release/sbom-policy.json")
DEFAULT_SUMMARY = Path("runtime/validation/release-sbom-semantics-summary.json")
CONAN_REF_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.+-]+)/(?P<version>[^#%/]+)#(?P<revision>[0-9a-fA-F]+)(?:%(?P<timestamp>.+))?$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SbomSemanticError(ValueError):
    """Raised when an input cannot be safely interpreted."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SbomSemanticError(f"unable to read {label} {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise SbomSemanticError(f"{label} must be a JSON object: {path}")
    return document


def load_policy(path: Path) -> dict[str, Any]:
    policy = load_json_object(path, "SBOM policy")
    if policy.get("schema_version") != 1:
        raise SbomSemanticError("SBOM policy schema_version must be 1")
    excluded = policy.get("excluded_conan_requires", [])
    if not isinstance(excluded, list) or not all(
        isinstance(item, str) and item for item in excluded
    ):
        raise SbomSemanticError(
            "excluded_conan_requires must be a list of package names"
        )
    if len(excluded) != len(set(excluded)):
        raise SbomSemanticError("excluded_conan_requires contains duplicates")
    if policy.get("exclude_build_requires") is not True:
        raise SbomSemanticError(
            "exclude_build_requires must be true for a runtime SBOM"
        )
    return policy


def parse_conan_reference(reference: str) -> dict[str, str]:
    match = CONAN_REF_RE.fullmatch(reference)
    if match is None:
        raise SbomSemanticError(f"unsupported Conan lock reference: {reference!r}")
    return {
        "name": match.group("name"),
        "version": match.group("version"),
        "recipe_revision": match.group("revision").lower(),
        "reference": reference,
    }


def load_runtime_dependencies(
    lockfile: Path, policy: dict[str, Any]
) -> list[dict[str, str]]:
    lock = load_json_object(lockfile, "Conan lockfile")
    if lock.get("version") != "0.5":
        raise SbomSemanticError(
            f"unsupported Conan lockfile version: {lock.get('version')!r}"
        )
    requires = lock.get("requires")
    build_requires = lock.get("build_requires")
    if not isinstance(requires, list) or not all(
        isinstance(item, str) for item in requires
    ):
        raise SbomSemanticError("Conan lockfile requires must be a list of references")
    if not isinstance(build_requires, list) or not all(
        isinstance(item, str) for item in build_requires
    ):
        raise SbomSemanticError(
            "Conan lockfile build_requires must be a list of references"
        )

    excluded = set(policy["excluded_conan_requires"])
    dependencies: dict[str, dict[str, str]] = {}
    for reference in requires:
        dependency = parse_conan_reference(reference)
        if dependency["name"] in excluded:
            continue
        if dependency["name"] in dependencies:
            raise SbomSemanticError(
                f"duplicate Conan runtime package: {dependency['name']}"
            )
        dependencies[dependency["name"]] = dependency
    return [dependencies[name] for name in sorted(dependencies)]


def _safe_relative_path(value: str, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SbomSemanticError(f"unsafe {label}: {value!r}")
    return path


def collect_package_files(package_root: Path) -> dict[str, str]:
    if not package_root.is_dir():
        raise SbomSemanticError(f"package root is not a directory: {package_root}")
    files: dict[str, str] = {}
    for directory, directory_names, file_names in os.walk(
        package_root, followlinks=False
    ):
        directory_path = Path(directory)
        directory_names[:] = sorted(
            name for name in directory_names if not (directory_path / name).is_symlink()
        )
        for name in sorted(file_names):
            path = directory_path / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(package_root).as_posix()
            _safe_relative_path(relative, "package file path")
            files[relative] = sha256_file(path)
    if not files:
        raise SbomSemanticError(f"package root has no regular files: {package_root}")
    return files


def _validate_archive_link(member: tarfile.TarInfo, expected_root: str) -> None:
    if not (member.issym() or member.islnk()):
        return
    target = PurePosixPath(member.linkname)
    if not member.linkname or target.is_absolute():
        raise SbomSemanticError(
            f"unsafe archive link target: {member.name!r} -> {member.linkname!r}"
        )
    base = PurePosixPath(member.name).parent if member.issym() else PurePosixPath()
    resolved: list[str] = []
    for part in (*base.parts, *target.parts):
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved:
                raise SbomSemanticError(
                    f"archive link escapes package root: {member.name!r} -> {member.linkname!r}"
                )
            resolved.pop()
        else:
            resolved.append(part)
    if not resolved or resolved[0] != expected_root:
        raise SbomSemanticError(
            f"archive link escapes package root: {member.name!r} -> {member.linkname!r}"
        )


def collect_archive_files(archive: Path, expected_root: str) -> dict[str, str]:
    _safe_relative_path(expected_root, "expected archive root")
    if "/" in expected_root:
        raise SbomSemanticError(
            f"expected archive root must be one path component: {expected_root!r}"
        )
    files: dict[str, str] = {}
    member_names: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                path = _safe_relative_path(
                    member.name.rstrip("/"), "archive member path"
                )
                normalized = path.as_posix()
                if normalized in member_names:
                    raise SbomSemanticError(f"duplicate archive member: {normalized}")
                member_names.add(normalized)
                if path.parts[0] != expected_root:
                    raise SbomSemanticError(
                        f"archive member is outside expected root {expected_root!r}: {member.name!r}"
                    )
                _validate_archive_link(member, expected_root)
                if not member.isfile():
                    continue
                relative = PurePosixPath(*path.parts[1:])
                if not relative.parts:
                    raise SbomSemanticError(
                        f"archive root cannot be a regular file: {member.name!r}"
                    )
                stream = bundle.extractfile(member)
                if stream is None:
                    raise SbomSemanticError(
                        f"unable to read archive member: {member.name!r}"
                    )
                files[relative.as_posix()] = sha256_bytes(stream.read())
    except (OSError, tarfile.TarError) as exc:
        raise SbomSemanticError(
            f"archive is not a readable gzip-compressed tarball: {exc}"
        ) from exc
    if not files:
        raise SbomSemanticError(f"archive has no regular files: {archive}")
    return files


def conan_purl(dependency: dict[str, str]) -> str:
    name = quote(dependency["name"], safe="._+-")
    version = quote(dependency["version"], safe="._+-")
    revision = quote(dependency["recipe_revision"], safe="")
    return f"pkg:conan/{name}@{version}?rrev={revision}"


def parse_conan_purl(value: str) -> tuple[str, str, str] | None:
    if not value.startswith("pkg:conan/"):
        return None
    parsed = urlsplit(value)
    package = parsed.path.removeprefix("conan/")
    if "@" not in package:
        raise SbomSemanticError(f"Conan purl has no version: {value!r}")
    name, version = package.rsplit("@", 1)
    revisions = parse_qs(parsed.query, strict_parsing=True).get("rrev", [])
    if (
        not name
        or not version
        or len(revisions) != 1
        or not re.fullmatch(r"[0-9a-f]+", revisions[0])
    ):
        raise SbomSemanticError(f"invalid Conan purl: {value!r}")
    return unquote(name), unquote(version), revisions[0]


def _spdx_id(kind: str, identity: str) -> str:
    return f"SPDXRef-{kind}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


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


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("enrich", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--sbom", type=Path, required=True)
        child.add_argument("--lockfile", type=Path, required=True)
        child.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
        child.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY)
        sources = child.add_mutually_exclusive_group(required=True)
        sources.add_argument("--package-root", type=Path)
        sources.add_argument("--archive", type=Path)
        child.add_argument("--expected-root")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary: dict[str, Any]
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
