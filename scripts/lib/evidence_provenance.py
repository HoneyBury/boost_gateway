#!/usr/bin/env python3
"""Build and validate source provenance for production evidence summaries."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
from pathlib import Path
from typing import Any


REQUIRED_PROVENANCE_KEYS = {
    "candidate_revision",
    "git_commit",
    "git_ref",
    "workflow",
    "run_id",
    "runner",
    "build_configuration",
    "conan_lockfile",
    "conan_lockfile_sha256",
    "revision_matches_checkout",
}


def _git_value(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _resolve_revision(repo_root: Path, revision: str) -> str:
    if not revision:
        return ""
    resolved = _git_value(repo_root, "rev-parse", f"{revision}^{{commit}}")
    return resolved or revision


def _default_lockfile(configuration: str) -> str:
    build_type = "debug" if configuration.lower() == "debug" else "release"
    return f"conan/locks/linux-gcc-x64-{build_type}-nogrpc-nosqlite.lock"


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_evidence_provenance(
    repo_root: Path,
    *,
    build_configuration: str,
    conan_lockfile: str | Path | None = None,
    candidate_revision: str | None = None,
) -> dict[str, Any]:
    """Return stable provenance metadata for a validation summary."""

    root = repo_root.resolve()
    git_commit = _git_value(root, "rev-parse", "HEAD")
    requested_revision = (
        candidate_revision
        or os.environ.get("BOOST_GATEWAY_CANDIDATE_REVISION")
        or os.environ.get("GITHUB_SHA")
        or git_commit
    )
    resolved_candidate = _resolve_revision(root, requested_revision)

    lockfile_value = str(
        conan_lockfile
        or os.environ.get("BOOST_GATEWAY_CONAN_LOCKFILE")
        or _default_lockfile(build_configuration)
    )
    lockfile_path = Path(lockfile_value)
    if not lockfile_path.is_absolute():
        lockfile_path = root / lockfile_path
    try:
        normalized_lockfile = str(lockfile_path.relative_to(root))
    except ValueError:
        normalized_lockfile = str(lockfile_path)

    git_ref = os.environ.get("GITHUB_REF_NAME") or os.environ.get("GITHUB_REF")
    if not git_ref:
        git_ref = _git_value(root, "symbolic-ref", "--short", "HEAD") or "detached"

    return {
        "candidate_revision": resolved_candidate,
        "git_commit": git_commit,
        "git_ref": git_ref,
        "workflow": os.environ.get("GITHUB_WORKFLOW", "local"),
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        "runner": os.environ.get("RUNNER_NAME") or platform.node() or "local",
        "runner_os": os.environ.get("RUNNER_OS") or platform.system(),
        "runner_arch": os.environ.get("RUNNER_ARCH") or platform.machine(),
        "build_configuration": build_configuration,
        "conan_lockfile": normalized_lockfile,
        "conan_lockfile_sha256": _sha256(lockfile_path),
        "revision_matches_checkout": bool(git_commit and resolved_candidate == git_commit),
    }


def validate_evidence_provenance(
    provenance: Any,
    *,
    expected_candidate_revision: str = "",
    require_lockfile: bool = True,
) -> list[str]:
    """Return validation errors for a provenance payload."""

    if not isinstance(provenance, dict):
        return ["summary.provenance must be an object"]

    missing = sorted(REQUIRED_PROVENANCE_KEYS - set(provenance))
    errors = ["missing provenance keys: " + ", ".join(missing)] if missing else []
    for key in (
        "candidate_revision",
        "git_commit",
        "git_ref",
        "workflow",
        "run_id",
        "runner",
        "build_configuration",
    ):
        if not isinstance(provenance.get(key), str) or not str(provenance.get(key)).strip():
            errors.append(f"provenance.{key} must be a non-empty string")

    if require_lockfile:
        for key in ("conan_lockfile", "conan_lockfile_sha256"):
            if not isinstance(provenance.get(key), str) or not str(provenance.get(key)).strip():
                errors.append(f"provenance.{key} must be a non-empty string")

    if provenance.get("revision_matches_checkout") is not True:
        errors.append("provenance candidate_revision does not match git_commit")
    if provenance.get("candidate_revision") != provenance.get("git_commit"):
        errors.append("provenance candidate_revision and git_commit differ")
    if expected_candidate_revision and provenance.get("candidate_revision") != expected_candidate_revision:
        errors.append(
            "provenance candidate_revision does not match expected revision "
            f"{expected_candidate_revision}"
        )
    return errors
