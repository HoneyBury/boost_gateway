"""Internal release deployment lifecycle implementation."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.operations_identity import collect_operations_identity  # noqa: E402
from scripts.lib.release_lifecycle_io import (  # noqa: E402
    LifecycleError,
    atomic_write,
    atomic_write_json,
    atomic_write_new_json,
    fsync_directory,
    load_json_object,
    now,
    sha256_file,
    sha256_tree,
)

IMAGE_VARIABLES = {
    "GATEWAY_IMAGE_ID",
    "LOGIN_IMAGE_ID",
    "ROOM_IMAGE_ID",
    "BATTLE_IMAGE_ID",
    "MATCHMAKING_IMAGE_ID",
    "LEADERBOARD_IMAGE_ID",
}
IMAGE_VARIABLE_BY_SERVICE = {
    "gateway": "GATEWAY_IMAGE_ID",
    "login": "LOGIN_IMAGE_ID",
    "room": "ROOM_IMAGE_ID",
    "battle": "BATTLE_IMAGE_ID",
    "matchmaking": "MATCHMAKING_IMAGE_ID",
    "leaderboard": "LEADERBOARD_IMAGE_ID",
}
PROVENANCE_LABELS = {
    "org.opencontainers.image.version": "tag",
    "org.opencontainers.image.revision": "commit",
    "io.boost-gateway.release.asset.sha256": "asset",
    "io.boost-gateway.release.config.sha256": "config",
}
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
DEPLOYMENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z")
ROLLBACK_DEADLINE_SECONDS = 600.0
INCOMPLETE_TRANSACTION_STATES = {
    "pending",
    "candidate_activated",
    "candidate_verified",
    "activation_failed",
    "rollback_failed",
}
BLOCKING_TRANSACTION_STATES = {"recovery_failed"}
PASSING_TRANSACTION_STATES = {"passed", "passed_reconciled"}
TRANSACTION_SUMMARIES = {
    "deployment": "deployment-verification-summary.json",
    "recovery": "recovery-verification-summary.json",
    "reconcile": "reconcile-verification-summary.json",
    "candidate_persistence_transition": "candidate-persistence-transition-summary.json",
    "recovery_persistence_transition": "recovery-persistence-transition-summary.json",
    "manual_recovery": "manual-recovery-summary.json",
    "manual_recovery_reconcile": "manual-recovery-reconcile-summary.json",
}
MANUAL_RECOVERY_STATUS = "manual-recovery-runtime-status.json"
MANUAL_RECOVERY_VERIFICATION = "manual-recovery-verification-summary.json"
MANUAL_RECOVERY_EQUIVALENCE = "rdb-aof-equivalence-summary.json"
MANUAL_RECOVERY_TRANSITION = "recovery-persistence-transition-summary.json"
MANUAL_RECOVERY_SUMMARY = "manual-recovery-summary.json"
MANUAL_RECOVERY_RECONCILE_SUMMARY = "manual-recovery-reconcile-summary.json"


def guard_target_host() -> None:
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise LifecycleError("lifecycle commands require Linux x86_64")
    try:
        os_release = parse_simple_environment(Path("/etc/os-release"))
    except LifecycleError as exc:
        raise LifecycleError(f"cannot verify target OS: {exc}") from exc
    if (
        os_release.get("ID", "").strip('"') != "ubuntu"
        or os_release.get("VERSION_ID", "").strip('"') != "24.04"
    ):
        raise LifecycleError("lifecycle commands require Ubuntu 24.04")
    if not Path("/run/systemd/system").is_dir():
        raise LifecycleError("systemd is not the active init system")
    if os.geteuid() != 0:
        raise LifecycleError("lifecycle commands require root; run with sudo")


def parse_image_environment(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LifecycleError(f"cannot read image environment {path}: {exc}") from exc
    values: dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise LifecycleError(f"invalid image environment line {line_number}")
        key, value = line.split("=", 1)
        if key not in IMAGE_VARIABLES or key in values:
            raise LifecycleError(f"unexpected or duplicate image variable: {key!r}")
        if IMAGE_ID_RE.fullmatch(value) is None:
            raise LifecycleError(f"image variable is not an immutable image ID: {key}")
        values[key] = value
    if set(values) != IMAGE_VARIABLES:
        raise LifecycleError(
            f"image environment is incomplete: missing {sorted(IMAGE_VARIABLES - set(values))}"
        )
    return values


def render_image_environment(values: dict[str, str]) -> bytes:
    return "".join(f"{key}={values[key]}\n" for key in sorted(values)).encode("utf-8")


def parse_simple_environment(path: Path) -> dict[str, str]:
    """Read root-managed Compose secrets without shell evaluation or logging."""
    if not path.exists():
        raise LifecycleError(f"required Compose secret environment is missing: {path}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise LifecycleError(f"invalid secret environment line {line_number}")
        key, value = line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None or key in values:
            raise LifecycleError(
                f"invalid or duplicate secret variable at line {line_number}"
            )
        if "\x00" in value or "\n" in value:
            raise LifecycleError(f"invalid secret value at line {line_number}")
        values[key] = value
    return values


def validate_install_attestations(
    manifest: dict[str, Any],
    manifest_sha256: str,
    images: dict[str, str],
    release_summary_path: Path,
    image_summary_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    release_summary = load_json_object(release_summary_path, "release staging summary")
    release = release_summary.get("release")
    if (
        release_summary.get("summary_version") != 2
        or release_summary.get("overall_pass") is not True
        or not isinstance(release, dict)
        or release.get("tag") != manifest["tag"]
        or release.get("commit") != manifest["commit"]
        or release.get("manifest_sha256") != manifest_sha256
    ):
        raise LifecycleError(
            "release staging summary does not attest the installed manifest"
        )

    image_summary = load_json_object(image_summary_path, "image build summary")
    inventory = image_summary.get("images")
    if (
        image_summary.get("summary_version") != 2
        or image_summary.get("overall_pass") is not True
        or image_summary.get("source_build_performed") is not False
        or image_summary.get("network_enabled_during_build") is not False
        or image_summary.get("target_platform") != "linux/amd64"
        or not isinstance(inventory, list)
        or len(inventory) != len(IMAGE_VARIABLE_BY_SERVICE)
    ):
        raise LifecycleError(
            "image build summary is not a passing offline linux/amd64 build"
        )
    tag = str(manifest["tag"])
    expected_labels = {
        label: {
            "tag": tag,
            "commit": str(manifest["commit"]),
            "asset": str(manifest["assets"][f"boost-gateway-{tag}-linux-x64.tar.gz"]),
            "config": str(manifest["configuration"]["sha256"]),
        }[field]
        for label, field in PROVENANCE_LABELS.items()
    }
    seen: set[str] = set()
    for item in inventory:
        if not isinstance(item, dict):
            raise LifecycleError("image build summary contains a non-object image")
        service = str(item.get("service", ""))
        variable = IMAGE_VARIABLE_BY_SERVICE.get(service)
        if variable is None or service in seen:
            raise LifecycleError(
                f"image build summary has unexpected service: {service}"
            )
        seen.add(service)
        if (
            item.get("image_id") != images[variable]
            or item.get("os") != "linux"
            or item.get("architecture") != "amd64"
            or item.get("labels") != expected_labels
        ):
            raise LifecycleError(f"image build attestation mismatch: {service}")
    if seen != set(IMAGE_VARIABLE_BY_SERVICE):
        raise LifecycleError(
            "image build summary does not contain all project services"
        )
    return release_summary, image_summary


@dataclass(frozen=True)
class Layout:
    root: Path = Path("/opt/boost-gateway")
    transaction_root: Path = Path("/var/lib/boost-gateway/deployment-transactions")
    active_image_env: Path = Path("/etc/boost-gateway/compose-images.env")
    secret_env: Path = Path("/etc/boost-gateway/compose.env")
    unit_path: Path = Path("/etc/systemd/system/boost-gateway-compose.service")

    @property
    def releases(self) -> Path:
        return self.root / "releases"

    @property
    def deployments(self) -> Path:
        return self.root / "deployments"

    @property
    def current(self) -> Path:
        return self.root / "current"

    @property
    def previous(self) -> Path:
        return self.root / "previous"

    @property
    def lock_path(self) -> Path:
        return self.transaction_root / ".lifecycle.lock"
