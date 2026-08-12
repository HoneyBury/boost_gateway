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


DEFAULT_POLICY = Path("config/release/sbom-policy.json")
DEFAULT_SUMMARY = Path("runtime/validation/release-sbom-semantics-summary.json")
SPDX_PREDICATE_TYPE = "https://spdx.dev/Document/v2.3"
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


def load_json_array(path: Path, label: str) -> list[Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SbomSemanticError(f"unable to read {label} {path}: {exc}") from exc
    if not isinstance(document, list):
        raise SbomSemanticError(f"{label} must be a JSON array: {path}")
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


