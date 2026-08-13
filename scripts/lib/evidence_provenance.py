#!/usr/bin/env python3
"""Build and validate source provenance for production evidence summaries."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import socket
import subprocess
import pwd
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
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


class OperationsIdentityError(ValueError):
    """Raised when required host or operator identity cannot be established."""


@dataclass
class EvidenceReport:
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str, **facts: Any) -> None:
        check: dict[str, Any] = {"name": name, "passed": passed, "detail": detail}
        check.update(facts)
        self.checks.append(check)

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [check for check in self.checks if not check["passed"]]


def operations_admission_summary(
    path: Path,
    phase: str,
    policy_path: Path,
    report: EvidenceReport,
    host_id: str,
    current_boot_id: str,
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed = report.failed
    try:
        policy_sha256 = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    except OSError:
        policy_sha256 = ""
    return {
        "summary_version": 2,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "phase": phase,
        "overall_pass": not failed,
        "passed": not failed,
        "failed_category": "operations_host_admission" if failed else "",
        "failed_step": failed[0]["name"] if failed else "",
        "host": {
            "hostname": socket.gethostname(),
            "host_id_sha256": host_id,
            "boot_id": current_boot_id,
        },
        "policy": {"path": str(policy_path), "sha256": policy_sha256},
        "checks": report.checks,
        "artifacts": {"summary_path": str(path), **(artifacts or {})},
    }


def _required_text(path: Path, label: str) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise OperationsIdentityError(f"{label} is empty")
    return value


def _host_os_release(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key in {"ID", "VERSION_ID"}:
                values[key] = value.strip().strip('"')
    return values


def _command_output(command: list[str], label: str) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OperationsIdentityError(f"cannot collect macOS {label}: {exc}") from exc
    value = completed.stdout.strip()
    if not value:
        raise OperationsIdentityError(f"macOS {label} is empty")
    return value


def _darwin_host_identity() -> dict[str, Any]:
    ioreg = _command_output(
        ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], "platform identity"
    )
    match = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]+)"', ioreg)
    if match is None:
        raise OperationsIdentityError("macOS platform identity lacks IOPlatformUUID")
    boot = _command_output(["sysctl", "-n", "kern.boottime"], "boot identity")
    version = platform.mac_ver()[0]
    if not version:
        raise OperationsIdentityError("macOS version is empty")
    return {
        "hostname": socket.gethostname(),
        "host_id_sha256": hashlib.sha256(
            match.group(1).lower().encode("ascii")
        ).hexdigest(),
        "boot_id": hashlib.sha256(boot.encode("utf-8")).hexdigest(),
        "os": {
            "id": "macos",
            "version_id": version,
            "kernel_release": platform.release(),
        },
        "architecture": platform.machine(),
    }


def _operator(environment: Mapping[str, str]) -> dict[str, Any]:
    sudo_user = environment.get("SUDO_USER", "").strip()
    sudo_uid = environment.get("SUDO_UID", "").strip()
    if sudo_user or sudo_uid:
        if (
            not sudo_user or not sudo_uid.isdecimal()
            or any(character.isspace() or ord(character) < 32 for character in sudo_user)
            or len(sudo_user) > 128
        ):
            raise OperationsIdentityError("SUDO_USER/SUDO_UID identity is invalid")
        return {"name": sudo_user, "uid": int(sudo_uid), "source": "sudo"}
    uid = os.getuid()
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError as exc:
        raise OperationsIdentityError(f"cannot resolve process uid {uid}") from exc
    return {"name": name, "uid": uid, "source": "process"}


def collect_operations_identity(
    *,
    environment: Mapping[str, str] | None = None,
    machine_id_path: Path = Path("/etc/machine-id"),
    boot_id_path: Path = Path("/proc/sys/kernel/random/boot_id"),
    os_release_path: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    """Return secret-free host and operator provenance for operations evidence."""
    default_linux_paths = (
        machine_id_path == Path("/etc/machine-id")
        and boot_id_path == Path("/proc/sys/kernel/random/boot_id")
        and os_release_path == Path("/etc/os-release")
    )
    if default_linux_paths and platform.system() == "Darwin":
        return {
            "host": _darwin_host_identity(),
            "operator": _operator(environment if environment is not None else os.environ),
        }
    machine_id = machine_id_path.read_bytes()
    if not machine_id.strip():
        raise OperationsIdentityError("machine-id is empty")
    release = _host_os_release(os_release_path)
    if not release.get("ID") or not release.get("VERSION_ID"):
        raise OperationsIdentityError("os-release lacks ID or VERSION_ID")
    return {
        "host": {
            "hostname": socket.gethostname(),
            "host_id_sha256": hashlib.sha256(machine_id).hexdigest(),
            "boot_id": _required_text(boot_id_path, "boot_id"),
            "os": {
                "id": release["ID"], "version_id": release["VERSION_ID"],
                "kernel_release": platform.release(),
            },
            "architecture": platform.machine(),
        },
        "operator": _operator(environment if environment is not None else os.environ),
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
