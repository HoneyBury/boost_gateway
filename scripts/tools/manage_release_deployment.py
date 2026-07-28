#!/usr/bin/env python3
"""Manage immutable single-node release deployment lifecycle transactions."""

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
from scripts.tools.check_release_compose import load_compose_document  # noqa: E402

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


class LifecycleError(RuntimeError):
    """Raised when a lifecycle contract cannot be satisfied safely."""


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


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    if not path.is_dir():
        raise LifecycleError(f"directory is missing: {path}")
    digest = hashlib.sha256()
    files = sorted(
        item
        for item in path.rglob("*")
        if item.is_file()
        and "__pycache__" not in item.relative_to(path).parts
        and item.suffix not in {".pyc", ".pyo"}
    )
    if not files:
        raise LifecycleError(f"directory has no files: {path}")
    for item in files:
        if item.is_symlink():
            raise LifecycleError(
                f"symbolic links are forbidden in immutable input: {item}"
            )
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LifecycleError(f"{label} must be a JSON object: {path}")
    return value


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, content: bytes, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: dict[str, Any], mode: int = 0o640) -> None:
    atomic_write(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        mode,
    )


def atomic_write_new_json(path: Path, value: dict[str, Any], mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise LifecycleError(f"create-only JSON already exists: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise LifecycleError(f"create-only JSON already exists: {path}") from exc
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


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


class LifecycleExecutor(Protocol):
    def precheck(self, deployment_path: Path, timeout_seconds: float) -> None: ...

    def activate(self, deployment_path: Path, timeout_seconds: float) -> None: ...

    def commit(self, deployment_path: Path, timeout_seconds: float) -> None: ...

    def uncommit(self, timeout_seconds: float) -> None: ...

    def deactivate(self, deployment_path: Path, timeout_seconds: float) -> None: ...

    def prepare_transition(
        self,
        source_path: Path,
        target_path: Path,
        summary_path: Path,
        timeout_seconds: float,
    ) -> dict[str, Any] | None: ...

    def verify(
        self, deployment_path: Path, summary_path: Path, timeout_seconds: float
    ) -> dict[str, Any]: ...

    def verify_read_only(
        self, deployment_path: Path, summary_path: Path, timeout_seconds: float
    ) -> dict[str, Any]: ...

    def runtime_status(self, deployment_path: Path) -> list[str]: ...

    def inactive_status(self) -> list[str]: ...


class SystemLifecycleExecutor:
    def __init__(self, layout: Layout) -> None:
        self.layout = layout

    @staticmethod
    def _run(
        command: list[str],
        timeout_seconds: float,
        *,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if timeout_seconds <= 0:
            raise LifecycleError("lifecycle command deadline expired")
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=max(1.0, timeout_seconds),
            env=environment,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()[-4000:]
            raise LifecycleError(f"command failed ({command[0]}): {detail}")
        return completed

    def _environment(self, deployment_path: Path) -> dict[str, str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        images = parse_image_environment(deployment_path / "compose-images.env")
        secrets = parse_simple_environment(self.layout.secret_env)
        conflicts = set(secrets) & IMAGE_VARIABLES
        if conflicts:
            raise LifecycleError(
                f"Compose secret environment overrides image identities: {sorted(conflicts)}"
            )
        environment.update(images)
        environment.update(secrets)
        return environment

    def precheck(self, deployment_path: Path, timeout_seconds: float) -> None:
        checker = Path(__file__).resolve().parent / "check_release_compose.py"
        compose = deployment_path / "deploy/operations/docker-compose.production.yml"
        self._run(
            [
                sys.executable,
                str(checker),
                "--compose-file",
                str(compose),
            ],
            timeout_seconds,
            environment=self._environment(deployment_path),
        )

    @staticmethod
    def _config_directives(path: Path) -> dict[str, str]:
        if not path.is_file():
            raise LifecycleError(f"Redis configuration is missing: {path}")
        directives: dict[str, str] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise LifecycleError(f"cannot read Redis configuration: {exc}") from exc
        for raw in lines:
            content = raw.split("#", 1)[0].strip()
            if not content:
                continue
            parts = content.split(maxsplit=1)
            if len(parts) == 2:
                directives[parts[0].lower()] = parts[1].strip().strip('"')
        return directives

    def _redis_persistence_contract(self, deployment_path: Path) -> dict[str, Any]:
        compose = deployment_path / "deploy/operations/docker-compose.production.yml"
        document = load_compose_document(
            compose, environment=self._environment(deployment_path)
        )
        services = document.get("services")
        redis = services.get("redis") if isinstance(services, dict) else None
        if not isinstance(redis, dict):
            return {"mode": "unknown", "source": "missing-redis-service"}
        command = redis.get("command")
        if isinstance(command, str):
            arguments = command.split()
        elif isinstance(command, list):
            arguments = [str(item) for item in command]
        else:
            arguments = []

        directives: dict[str, str] = {}
        source = "compose-command"
        for index, argument in enumerate(arguments):
            if argument == "--appendonly" and index + 1 < len(arguments):
                directives["appendonly"] = arguments[index + 1].lower()
            if argument == "--appendfsync" and index + 1 < len(arguments):
                directives["appendfsync"] = arguments[index + 1].lower()
        config_arguments = [
            item
            for item in arguments[1:]
            if not item.startswith("-") and item.lower().endswith(".conf")
        ]
        if config_arguments:
            target = config_arguments[0]
            volumes = redis.get("volumes")
            matches = []
            if isinstance(volumes, list):
                matches = [
                    item
                    for item in volumes
                    if isinstance(item, dict)
                    and str(item.get("target", "")) == target
                    and str(item.get("type", "")) == "bind"
                    and item.get("read_only") is True
                ]
            if len(matches) != 1:
                raise LifecycleError(
                    "Redis configuration must have one read-only bind mount"
                )
            config_path = Path(str(matches[0].get("source", ""))).resolve()
            directives = self._config_directives(config_path)
            source = str(config_path)

        appendonly = directives.get("appendonly", "")
        appendfsync = directives.get("appendfsync", "")
        if appendonly == "no":
            mode = "rdb_only"
        elif appendonly == "yes" and appendfsync == "everysec":
            mode = "aof_everysec_rdb"
        elif appendonly == "yes":
            mode = "aof_other"
        else:
            mode = "unknown"
        return {
            "mode": mode,
            "source": source,
            "appendonly": appendonly,
            "appendfsync": appendfsync,
        }

    def prepare_transition(
        self,
        source_path: Path,
        target_path: Path,
        summary_path: Path,
        timeout_seconds: float,
    ) -> dict[str, Any] | None:
        if timeout_seconds <= 0:
            raise LifecycleError("lifecycle command deadline expired")
        source = self._redis_persistence_contract(source_path)
        target = self._redis_persistence_contract(target_path)
        if source["mode"] == target["mode"]:
            return None
        tool = (
            Path(__file__).resolve().parent / "prepare_redis_persistence_transition.py"
        )
        self._run(
            [
                sys.executable,
                str(tool),
                "--compose-file",
                str(source_path / "deploy/operations/docker-compose.production.yml"),
                "--source-mode",
                str(source["mode"]),
                "--target-mode",
                str(target["mode"]),
                "--timeout-seconds",
                str(min(timeout_seconds, 180.0)),
                "--summary-path",
                str(summary_path),
            ],
            timeout_seconds,
            environment=self._environment(source_path),
        )
        summary = load_json_object(summary_path, "persistence transition summary")
        if (
            summary.get("overall_pass") is not True
            or summary.get("source_mode") != source["mode"]
            or summary.get("target_mode") != target["mode"]
            or summary.get("secret_material_recorded") is not False
            or summary.get("checkpoint_verified") is not True
        ):
            raise LifecycleError("persistence transition summary is not passing")
        directory = summary.get("aof_directory_transition")
        if (
            not isinstance(directory, dict)
            or directory.get("files_deleted") is not False
        ):
            raise LifecycleError(
                "persistence transition AOF directory evidence is invalid"
            )
        if source["mode"] == "rdb_only":
            seed = summary.get("aof_seed")
            config = seed.get("effective_config") if isinstance(seed, dict) else None
            before = seed.get("key_count_before") if isinstance(seed, dict) else None
            after = seed.get("key_count_after") if isinstance(seed, dict) else None
            runtime_already_target = summary.get("runtime_already_target") is True
            expected_method = (
                "runtime-already-target-validated"
                if runtime_already_target
                else "runtime-config-set-and-rewrite"
            )
            expected_source = (
                "active-aof-keyspace"
                if runtime_already_target
                else "active-rdb-keyspace"
            )
            allowed_actions = (
                {"target-runtime-validated"} if runtime_already_target else {"absent"}
            )
            if (
                directory.get("action") not in allowed_actions
                or not isinstance(seed, dict)
                or seed.get("method") != expected_method
                or seed.get("source") != expected_source
                or not isinstance(before, int)
                or isinstance(before, bool)
                or before < 0
                or after != before
                or SHA256_RE.fullmatch(str(seed.get("manifest_sha256", ""))) is None
                or directory.get("manifest_sha256")
                not in {None, seed.get("manifest_sha256")}
                or seed.get("files_deleted") is not False
                or not isinstance(config, dict)
                or config.get("appendonly") != "yes"
                or config.get("appendfsync") != "everysec"
            ):
                raise LifecycleError("RDB-to-AOF seed evidence is invalid")
        elif (
            directory.get("action") != "entrypoint-readable"
            or directory.get("mode") != "0755"
            or SHA256_RE.fullmatch(str(directory.get("manifest_sha256", ""))) is None
            or summary.get("aof_seed") is not None
        ):
            raise LifecycleError("AOF-to-RDB directory evidence is invalid")
        return summary

    def _install_unit(self, deployment_path: Path) -> bool:
        source = deployment_path / "deploy/systemd/boost-gateway-compose.service"
        if not source.is_file() or source.is_symlink():
            raise LifecycleError(f"release Compose unit is missing or unsafe: {source}")
        content = source.read_bytes()
        if self.layout.unit_path.exists():
            if not self.layout.unit_path.is_file():
                raise LifecycleError("installed Compose unit is not a regular file")
            if self.layout.unit_path.read_bytes() != content:
                raise LifecycleError(
                    "release changes the host Compose unit; migrate the host controller separately"
                )
            return False
        atomic_write(self.layout.unit_path, content, 0o644)
        return True

    def activate(self, deployment_path: Path, timeout_seconds: float) -> None:
        compose = deployment_path / "deploy/operations/docker-compose.production.yml"
        self._run(
            [
                "docker",
                "compose",
                "-f",
                str(compose),
                "up",
                "-d",
                "--no-build",
                "--remove-orphans",
                "--wait",
                "--wait-timeout",
                "240",
            ],
            timeout_seconds,
            environment=self._environment(deployment_path),
        )

    def commit(self, deployment_path: Path, timeout_seconds: float) -> None:
        started = time.monotonic()
        unit_changed = self._install_unit(deployment_path)
        if unit_changed:
            self._run(["systemctl", "daemon-reload"], timeout_seconds)
        remaining = timeout_seconds - (time.monotonic() - started)
        self._run(["systemctl", "enable", "boost-gateway-compose.service"], remaining)

    def uncommit(self, timeout_seconds: float) -> None:
        if not self.layout.unit_path.exists():
            return
        self._run(
            ["systemctl", "disable", "boost-gateway-compose.service"], timeout_seconds
        )

    def deactivate(self, deployment_path: Path, timeout_seconds: float) -> None:
        compose = deployment_path / "deploy/operations/docker-compose.production.yml"
        self._run(
            ["docker", "compose", "-f", str(compose), "stop", "--timeout", "30"],
            timeout_seconds,
            environment=self._environment(deployment_path),
        )

    def _verify(
        self,
        deployment_path: Path,
        summary_path: Path,
        timeout_seconds: float,
        *,
        read_only: bool,
    ) -> dict[str, Any]:
        verifier = Path(__file__).resolve().parent / "verify_release_deployment.py"
        compose = deployment_path / "deploy/operations/docker-compose.production.yml"
        image_environment = deployment_path / "compose-images.env"
        command = [
            sys.executable,
            str(verifier),
            "--staging-dir",
            str(deployment_path),
            "--compose-file",
            str(compose),
            "--image-env-path",
            str(image_environment),
            "--summary-path",
            str(summary_path),
        ]
        if read_only:
            command.append("--read-only")
        self._run(
            command,
            timeout_seconds,
            environment=self._environment(deployment_path),
        )
        summary = load_json_object(summary_path, "deployment verification summary")
        if summary.get("overall_pass") is not True:
            raise LifecycleError("deployment verification summary is not passing")
        if read_only and (
            summary.get("read_only_verification") is not True
            or summary.get("protected_state_mutated") is not False
        ):
            raise LifecycleError("read-only deployment verification is invalid")
        return summary

    def verify(
        self, deployment_path: Path, summary_path: Path, timeout_seconds: float
    ) -> dict[str, Any]:
        return self._verify(
            deployment_path, summary_path, timeout_seconds, read_only=False
        )

    def verify_read_only(
        self, deployment_path: Path, summary_path: Path, timeout_seconds: float
    ) -> dict[str, Any]:
        return self._verify(
            deployment_path, summary_path, timeout_seconds, read_only=True
        )

    def runtime_status(self, deployment_path: Path) -> list[str]:
        failures: list[str] = []
        for state in ("is-enabled", "is-active"):
            completed = subprocess.run(
                ["systemctl", state, "--quiet", "boost-gateway-compose.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
            if completed.returncode:
                failures.append(f"systemd service is not {state.removeprefix('is-')}")
        compose = deployment_path / "deploy/operations/docker-compose.production.yml"
        environment = self._environment(deployment_path)
        expected = parse_image_environment(deployment_path / "compose-images.env")
        service_by_variable = {
            "GATEWAY_IMAGE_ID": "gateway",
            "LOGIN_IMAGE_ID": "login-backend",
            "ROOM_IMAGE_ID": "room-backend",
            "BATTLE_IMAGE_ID": "battle-backend",
            "MATCHMAKING_IMAGE_ID": "matchmaking-backend",
            "LEADERBOARD_IMAGE_ID": "leaderboard-backend",
        }
        for variable, service in service_by_variable.items():
            container = subprocess.run(
                ["docker", "compose", "-f", str(compose), "ps", "-q", service],
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
                env=environment,
            )
            container_id = container.stdout.strip()
            if container.returncode or not container_id:
                failures.append(f"running container is missing: {service}")
                continue
            inspected = subprocess.run(
                ["docker", "inspect", "--format", "{{.Image}}", container_id],
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
            if inspected.returncode or inspected.stdout.strip() != expected[variable]:
                failures.append(f"running image identity differs: {service}")
        return failures

    def inactive_status(self) -> list[str]:
        enabled = subprocess.run(
            ["systemctl", "is-enabled", "--quiet", "boost-gateway-compose.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        return (
            ["systemd service is enabled without a current deployment"]
            if not enabled.returncode
            else []
        )


@contextmanager
def lifecycle_lock(layout: Layout) -> Iterator[None]:
    layout.transaction_root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(layout.lock_path, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class ReleaseDeploymentManager:
    def __init__(
        self,
        layout: Layout,
        executor: LifecycleExecutor,
        *,
        monotonic: Any = time.monotonic,
        identity_provider: Callable[[], dict[str, Any]] = collect_operations_identity,
    ) -> None:
        self.layout = layout
        self.executor = executor
        self.monotonic = monotonic
        self.identity_provider = identity_provider

    def _identity(self) -> dict[str, Any]:
        try:
            identity = self.identity_provider()
            host = identity["host"]
            operator = identity["operator"]
            if not isinstance(host, dict) or not isinstance(operator, dict):
                raise ValueError("identity fields must be objects")
            return {"host": dict(host), "operator": dict(operator)}
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise LifecycleError(f"cannot collect operations identity: {exc}") from exc

    @staticmethod
    def _install_result(installed_at: str) -> dict[str, Any]:
        return {
            "operation": "install",
            "status": "installed",
            "completed": True,
            "overall_pass": True,
            "recorded_at": installed_at,
        }

    @staticmethod
    def _summary_references(transaction: Path) -> list[dict[str, Any]]:
        references: list[dict[str, Any]] = []
        for kind, name in TRANSACTION_SUMMARIES.items():
            path = transaction / name
            if not path.exists() and not path.is_symlink():
                continue
            if path.is_symlink() or not path.is_file():
                raise LifecycleError(
                    f"transaction summary is not a regular file: {path}"
                )
            status = path.stat()
            references.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "size_bytes": status.st_size,
                }
            )
        return references

    def _write_transaction_record(
        self, transaction: Path, record: dict[str, Any]
    ) -> None:
        if "host" not in record or "operator" not in record:
            identity = self._identity()
            record.setdefault("host", identity["host"])
            record.setdefault("operator", identity["operator"])
        status = str(record.get("status", ""))
        completed = status != "pending" and status not in INCOMPLETE_TRANSACTION_STATES
        result: dict[str, Any] = {
            "operation": str(record.get("operation", "")),
            "status": status,
            "completed": completed,
            "overall_pass": status in PASSING_TRANSACTION_STATES if completed else None,
            "recorded_at": record.get("completed_at")
            or record.get("failed_at")
            or record.get("started_at"),
        }
        if completed:
            result["summaries"] = self._summary_references(transaction)
        record["result"] = result
        atomic_write_json(transaction / "record.json", record)

    def _ensure_layout(self) -> None:
        self.layout.root.mkdir(parents=True, exist_ok=True)
        self.layout.releases.mkdir(parents=True, exist_ok=True)
        self.layout.deployments.mkdir(parents=True, exist_ok=True)
        self.layout.transaction_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _regular_evidence(path: Path, label: str) -> Path:
        if path.is_symlink() or not path.is_file():
            raise LifecycleError(f"{label} is not a regular file: {path}")
        return path

    @staticmethod
    def _evidence_reference(path: Path) -> dict[str, Any]:
        status = path.stat()
        return {
            "path": str(path),
            "sha256": sha256_file(path),
            "size_bytes": status.st_size,
        }

    def _blocking_recovery_transactions(self) -> list[tuple[Path, dict[str, Any]]]:
        blocking: list[tuple[Path, dict[str, Any]]] = []
        for record_path in sorted(self.layout.transaction_root.glob("*/record.json")):
            if record_path.is_symlink() or not record_path.is_file():
                raise LifecycleError(
                    f"transaction record is not a regular file: {record_path}"
                )
            record = load_json_object(record_path, "lifecycle transaction")
            if record.get("status") in BLOCKING_TRANSACTION_STATES:
                blocking.append((record_path.parent, record))
        return blocking

    def _validate_manual_recovery(
        self,
        transaction: Path,
        record: dict[str, Any],
        current: str,
        resolution_path: Path,
    ) -> dict[str, Any]:
        resolution_path = self._regular_evidence(
            resolution_path, "protected-state recovery summary"
        )
        resolution_parent = resolution_path.parent
        paths = {
            "manual": self._regular_evidence(
                transaction / MANUAL_RECOVERY_SUMMARY, "manual recovery summary"
            ),
            "status": self._regular_evidence(
                transaction / MANUAL_RECOVERY_STATUS, "manual runtime status summary"
            ),
            "verification": self._regular_evidence(
                transaction / MANUAL_RECOVERY_VERIFICATION,
                "manual deployment verification summary",
            ),
            "equivalence": self._regular_evidence(
                transaction / MANUAL_RECOVERY_EQUIVALENCE,
                "RDB/AOF equivalence summary",
            ),
            "transition": self._regular_evidence(
                transaction / MANUAL_RECOVERY_TRANSITION,
                "recovery persistence transition summary",
            ),
            "resolution": resolution_path,
            "merge_plan": self._regular_evidence(
                resolution_parent / "todo0012-pre-aof-merge-plan.json",
                "protected-state merge plan",
            ),
            "merge_application": self._regular_evidence(
                resolution_parent / "todo0012-pre-aof-merge-application.json",
                "protected-state merge application",
            ),
            "merge_verification": self._regular_evidence(
                resolution_parent
                / "todo0012-pre-aof-merge-deployment-verification.json",
                "protected-state merge deployment verification",
            ),
        }
        manual = load_json_object(paths["manual"], "manual recovery summary")
        status = load_json_object(paths["status"], "manual runtime status summary")
        verification = load_json_object(
            paths["verification"], "manual deployment verification summary"
        )
        equivalence = load_json_object(
            paths["equivalence"], "RDB/AOF equivalence summary"
        )
        transition = load_json_object(
            paths["transition"], "recovery persistence transition summary"
        )
        resolution = load_json_object(
            paths["resolution"], "protected-state recovery summary"
        )
        merge_plan = load_json_object(paths["merge_plan"], "protected-state merge plan")
        merge_application = load_json_object(
            paths["merge_application"], "protected-state merge application"
        )
        merge_verification = load_json_object(
            paths["merge_verification"],
            "protected-state merge deployment verification",
        )
        transaction_id = transaction.name
        active_volume = manual.get("active_volume")
        required_manual = {
            "schema_version": 1,
            "overall_pass": True,
            "operation": "manual-recovery-after-aof-activation-recovery-failure",
            "transaction_id": transaction_id,
            "current": current,
            "active_volume_preserved": True,
            "rdb_aof_canonical_equivalence_verified": True,
            "aof_files_deleted": False,
            "rdb_files_deleted": False,
            "production_volume_deleted": False,
            "lifecycle_blocker_preserved": True,
            "transaction_record_mutated": False,
            "secret_material_recorded": False,
            "formal_todo0012_claim": False,
        }
        if any(manual.get(key) != value for key, value in required_manual.items()):
            raise LifecycleError(
                "manual recovery summary does not satisfy closure policy"
            )
        if (
            not isinstance(active_volume, str)
            or DEPLOYMENT_ID_RE.fullmatch(active_volume) is None
            or IMAGE_ID_RE.fullmatch(str(manual.get("redis_image", ""))) is None
            or SHA256_RE.fullmatch(str(manual.get("rdb_sha256", ""))) is None
            or SHA256_RE.fullmatch(str(manual.get("aof_manifest_sha256", ""))) is None
            or manual.get("appendonly") != "no"
            or manual.get("aof_quarantine")
            != f"appendonlydir.recovery-failed-{transaction_id}"
        ):
            raise LifecycleError("manual recovery runtime binding is invalid")

        expected_hashes = {
            "status_sha256": paths["status"],
            "verification_sha256": paths["verification"],
            "rdb_aof_equivalence_sha256": paths["equivalence"],
        }
        if any(
            manual.get(field) != sha256_file(path)
            for field, path in expected_hashes.items()
        ):
            raise LifecycleError("manual recovery evidence digest binding differs")
        record_summaries = record.get("result", {}).get("summaries")
        transition_references = (
            [
                item
                for item in record_summaries
                if isinstance(item, dict)
                and item.get("kind") == "recovery_persistence_transition"
            ]
            if isinstance(record_summaries, list)
            else []
        )
        transition_evidence = self._evidence_reference(paths["transition"])
        if len(transition_references) != 1 or any(
            transition_references[0].get(key) != value
            for key, value in transition_evidence.items()
        ):
            raise LifecycleError(
                "recovery persistence transition is not bound to the blocking record"
            )

        if (
            status.get("schema_version") != 1
            or status.get("overall_pass") is not True
            or status.get("current") != current
            or status.get("failures") != []
            or status.get("lifecycle_blocker_preserved") is not True
            or status.get("secret_material_recorded") is not False
        ):
            raise LifecycleError("manual runtime status summary did not pass")
        verification_checks = verification.get("checks")
        expected_deployment = self._deployment_dir(current)
        required_verification_checks = {
            "compose-service-state",
            "container-image-identities",
            "redis-ping",
            "release-sdk-full-flow",
        }
        if (
            verification.get("overall_pass") is not True
            or verification.get("source_build_performed") is not False
            or verification.get("public_conan_access_performed") is not False
            or verification.get("staging_manifest")
            != str(expected_deployment / "manifest.json")
            or verification.get("compose_file")
            != str(
                expected_deployment / "deploy/operations/docker-compose.production.yml"
            )
            or not isinstance(verification_checks, list)
            or not verification_checks
            or any(
                not isinstance(check, dict) or check.get("passed") is not True
                for check in verification_checks
            )
            or not required_verification_checks
            <= {
                str(check.get("name", ""))
                for check in verification_checks
                if isinstance(check, dict)
            }
            or verification.get("failed") != []
        ):
            raise LifecycleError("manual deployment verification summary did not pass")

        rdb_sha = equivalence.get("rdb_canonical_sha256")
        aof_sha = equivalence.get("aof_canonical_sha256")
        rdb_count = equivalence.get("rdb_key_count")
        aof_count = equivalence.get("aof_key_count")
        if (
            equivalence.get("schema_version") != 1
            or equivalence.get("overall_pass") is not True
            or equivalence.get("transaction_id") != transaction_id
            or equivalence.get("source_volume") != active_volume
            or equivalence.get("source_volume_mounted_readonly") is not True
            or equivalence.get("production_volume_mutated") is not False
            or equivalence.get("production_switched") is not False
            or equivalence.get("key_sets_equal") is not True
            or equivalence.get("required_keys_present") is not True
            or equivalence.get("redis_image") != manual.get("redis_image")
            or equivalence.get("secret_material_recorded") is not False
            or equivalence.get("formal_todo0012_claim") is not False
            or SHA256_RE.fullmatch(str(rdb_sha)) is None
            or rdb_sha != aof_sha
            or not isinstance(rdb_count, int)
            or isinstance(rdb_count, bool)
            or rdb_count <= 0
            or rdb_count != aof_count
        ):
            raise LifecycleError("RDB/AOF equivalence summary did not pass")
        checkpoint = transition.get("checkpoint")
        transition_volume = transition.get("active_volume")
        if (
            transition.get("overall_pass") is not True
            or transition.get("source_mode") != "aof_everysec_rdb"
            or transition.get("target_mode") != "rdb_only"
            or transition.get("checkpoint_required") is not True
            or transition.get("checkpoint_verified") is not True
            or transition.get("writes_frozen") is not True
            or transition.get("secret_material_recorded") is not False
            or not isinstance(checkpoint, dict)
            or checkpoint.get("rdb_changes_since_last_save") != 0
            or checkpoint.get("rdb_last_bgsave_status") != "ok"
            or checkpoint.get("redis_check_rdb") is not True
            or SHA256_RE.fullmatch(str(checkpoint.get("rdb_sha256", ""))) is None
            or not isinstance(transition_volume, dict)
            or transition_volume.get("name") != active_volume
            or transition_volume.get("destination") != "/data"
            or transition_volume.get("read_write") is not True
        ):
            raise LifecycleError("recovery persistence transition did not pass")

        preservation = resolution.get("preservation")
        try:
            resolution_time = datetime.fromisoformat(
                str(resolution.get("recorded_at", "")).replace("Z", "+00:00")
            )
            failure_time = datetime.fromisoformat(
                str(
                    record.get("recovery_failed_completed_at")
                    or record.get("completed_at", "")
                ).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise LifecycleError(
                "protected-state recovery timestamps are invalid"
            ) from exc
        if (
            resolution.get("schema_version") != 1
            or resolution.get("overall_pass") is not True
            or resolution.get("operation")
            != "recover-pre-aof-state-with-post-activation-writes"
            or resolution.get("current") != current
            or resolution.get("lifecycle_blocker_preserved") is not True
            or resolution.get("production_volume_deleted") is not False
            or resolution.get("aof_quarantine_deleted") is not False
            or resolution.get("secret_material_recorded") is not False
            or resolution.get("formal_todo0012_claim") is not False
            or resolution.get("active_volume") != active_volume
            or resolution_time.tzinfo is None
            or failure_time.tzinfo is None
            or resolution_time <= failure_time
            or SHA256_RE.fullmatch(str(resolution.get("merged_canonical_sha256", "")))
            is None
            or SHA256_RE.fullmatch(str(resolution.get("payload_sha256", ""))) is None
            or SHA256_RE.fullmatch(str(resolution.get("plan_sha256", ""))) is None
            or SHA256_RE.fullmatch(str(resolution.get("verification_sha256", "")))
            is None
            or not isinstance(preservation, dict)
            or preservation.get("passed") is not True
            or preservation.get("missing_names") != []
            or preservation.get("missing_scores") != []
            or preservation.get("changed_names") != []
            or preservation.get("changed_scores") != []
            or preservation.get("missing_events")
            != {"events_by_type": 0, "events_global": 0}
            or not isinstance(preservation.get("next_seq"), int)
            or isinstance(preservation.get("next_seq"), bool)
            or preservation.get("next_seq") <= 0
        ):
            raise LifecycleError("protected-state recovery summary did not pass")

        payload = merge_plan.get("payload")
        payload_digest = (
            hashlib.sha256(
                (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
            ).hexdigest()
            if isinstance(payload, dict)
            else ""
        )
        if (
            resolution.get("plan_sha256") != sha256_file(paths["merge_plan"])
            or resolution.get("application_sha256")
            != sha256_file(paths["merge_application"])
            or resolution.get("verification_sha256")
            != sha256_file(paths["merge_verification"])
            or merge_plan.get("schema_version") != 1
            or merge_plan.get("overall_pass") is not True
            or merge_plan.get("operation") != "prepare-pre-aof-state-merge"
            or merge_plan.get("production_mutated") is not False
            or merge_plan.get("production_volume_deleted") is not False
            or merge_plan.get("secret_material_recorded") is not False
            or merge_plan.get("formal_todo0012_claim") is not False
            or merge_plan.get("payload_sha256") != payload_digest
            or merge_plan.get("payload_sha256") != resolution.get("payload_sha256")
            or merge_plan.get("current_canonical_sha256")
            != resolution.get("pre_merge_canonical_sha256")
            or merge_plan.get("merged_canonical_sha256")
            != resolution.get("merged_canonical_sha256")
        ):
            raise LifecycleError("protected-state merge plan binding differs")

        application_checkpoint = merge_application.get("checkpoint")
        if (
            merge_application.get("schema_version") != 1
            or merge_application.get("overall_pass") is not True
            or merge_application.get("operation") != "apply-pre-aof-state-merge"
            or merge_application.get("plan_sha256") != resolution.get("plan_sha256")
            or merge_application.get("payload_sha256")
            != resolution.get("payload_sha256")
            or merge_application.get("pre_merge_canonical_sha256")
            != resolution.get("pre_merge_canonical_sha256")
            or merge_application.get("merged_canonical_sha256")
            != resolution.get("merged_canonical_sha256")
            or merge_application.get("pre_merge_backup")
            != resolution.get("pre_merge_backup")
            or merge_application.get("production_volume_deleted") is not False
            or merge_application.get("secret_material_recorded") is not False
            or merge_application.get("formal_todo0012_claim") is not False
            or not isinstance(application_checkpoint, dict)
            or application_checkpoint.get("rdb_changes_since_last_save") != 0
            or application_checkpoint.get("redis_check_rdb") is not True
            or SHA256_RE.fullmatch(str(application_checkpoint.get("rdb_sha256", "")))
            is None
        ):
            raise LifecycleError("protected-state merge application did not pass")

        merge_checks = merge_verification.get("checks")
        if (
            merge_verification.get("overall_pass") is not True
            or merge_verification.get("source_build_performed") is not False
            or merge_verification.get("public_conan_access_performed") is not False
            or merge_verification.get("staging_manifest")
            != str(self._deployment_dir(current) / "manifest.json")
            or not isinstance(merge_checks, list)
            or not merge_checks
            or any(
                not isinstance(check, dict) or check.get("passed") is not True
                for check in merge_checks
            )
            or "release-sdk-full-flow"
            not in {
                str(check.get("name", ""))
                for check in merge_checks
                if isinstance(check, dict)
            }
            or merge_verification.get("failed") != []
        ):
            raise LifecycleError(
                "protected-state merge deployment verification did not pass"
            )
        resolution_backups: dict[str, dict[str, Any]] = {}
        for field in ("pre_merge_backup", "post_merge_backup"):
            backup = resolution.get(field)
            if not isinstance(backup, dict):
                raise LifecycleError("protected-state backup binding is invalid")
            summary_path = self._regular_evidence(
                Path(str(backup.get("summary_path", ""))),
                f"{field} summary",
            )
            backup_summary = load_json_object(summary_path, f"{field} summary")
            backup_manifest = backup_summary.get("manifest")
            remote_receipt = backup_summary.get("remote_receipt")
            if (
                not isinstance(backup.get("backup_id"), str)
                or DEPLOYMENT_ID_RE.fullmatch(str(backup.get("backup_id"))) is None
                or summary_path.parent.resolve() != resolution_parent.resolve()
                or backup.get("summary_sha256") != sha256_file(summary_path)
                or not isinstance(backup_manifest, dict)
                or backup_manifest.get("backup_id") != backup.get("backup_id")
                or backup_manifest.get("consistent_redis_snapshot") is not True
                or backup_manifest.get("encrypted_before_transfer") is not True
                or backup_manifest.get("secret_material_recorded") is not False
                or not isinstance(remote_receipt, dict)
                or remote_receipt.get("backup_id") != backup.get("backup_id")
                or remote_receipt.get("create_only") is not True
                or remote_receipt.get("remote_readback_sha256") is not True
                or remote_receipt.get("secret_material_recorded") is not False
                or not isinstance(remote_receipt.get("stored_at"), str)
            ):
                raise LifecycleError("protected-state backup digest binding differs")
            resolution_backups[field] = self._evidence_reference(summary_path)
        if record.get("from_current") != current:
            raise LifecycleError("blocking transaction source differs from current")
        evidence = {
            "manual": self._evidence_reference(paths["manual"]),
            "status": self._evidence_reference(paths["status"]),
            "verification": self._evidence_reference(paths["verification"]),
            "equivalence": self._evidence_reference(paths["equivalence"]),
            "transition": self._evidence_reference(paths["transition"]),
            "resolution": self._evidence_reference(paths["resolution"]),
            "merge_plan": self._evidence_reference(paths["merge_plan"]),
            "merge_application": self._evidence_reference(paths["merge_application"]),
            "merge_verification": self._evidence_reference(paths["merge_verification"]),
        }
        evidence.update(resolution_backups)
        return evidence

    def _validate_reconcile_reference(
        self,
        transaction: Path,
        value: Any,
        expected_name: str,
        label: str,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise LifecycleError(f"{label} reference is invalid")
        path = Path(str(value.get("path", "")))
        try:
            path.resolve().relative_to(transaction.resolve())
        except (OSError, ValueError) as exc:
            raise LifecycleError(f"{label} reference escapes transaction") from exc
        if path.name != expected_name:
            raise LifecycleError(f"{label} filename is invalid")
        path = self._regular_evidence(path, label)
        observed = self._evidence_reference(path)
        if observed != value:
            raise LifecycleError(f"{label} digest or size binding differs")
        return observed

    def _validate_existing_reconcile_summary(
        self,
        transaction: Path,
        current: str,
        record_sha256: str,
        manual_evidence: dict[str, Any],
        final_path: Path,
    ) -> dict[str, Any]:
        final_path = self._regular_evidence(
            final_path, "manual recovery reconcile summary"
        )
        summary = load_json_object(final_path, "manual recovery reconcile summary")
        required = {
            "schema_version": 1,
            "overall_pass": True,
            "operation": "reconcile-manual-recovery",
            "transaction_id": transaction.name,
            "current": current,
            "blocking_state_before": "recovery_failed",
            "terminal_state": "recovery_reconciled",
            "manual_recovery": manual_evidence,
            "transaction_record_sha256_before": record_sha256,
            "record_update_authorized": True,
            "protected_state_mutated": False,
            "secret_material_recorded": False,
        }
        if any(summary.get(key) != value for key, value in required.items()):
            raise LifecycleError("manual recovery reconcile summary is invalid")
        attempt_id = summary.get("attempt_id")
        if (
            not isinstance(attempt_id, str)
            or DEPLOYMENT_ID_RE.fullmatch(attempt_id) is None
        ):
            raise LifecycleError("manual recovery reconcile attempt ID is invalid")
        runtime_reference = self._validate_reconcile_reference(
            transaction,
            summary.get("runtime_status"),
            "runtime-status-summary.json",
            "reconcile runtime status summary",
        )
        verification_reference = self._validate_reconcile_reference(
            transaction,
            summary.get("deployment_verification"),
            "deployment-verification-summary.json",
            "reconcile deployment verification summary",
        )
        expected_attempt = transaction / "reconcile-attempts" / attempt_id
        for reference in (runtime_reference, verification_reference):
            if (
                Path(str(reference["path"])).parent.resolve()
                != expected_attempt.resolve()
            ):
                raise LifecycleError(
                    "reconcile evidence is not bound to the declared attempt"
                )
        runtime = load_json_object(
            Path(str(runtime_reference["path"])), "reconcile runtime status summary"
        )
        verification = load_json_object(
            Path(str(verification_reference["path"])),
            "reconcile deployment verification summary",
        )
        if (
            runtime.get("schema_version") != 1
            or runtime.get("overall_pass") is not True
            or runtime.get("transaction_id") != transaction.name
            or runtime.get("current") != current
            or runtime.get("failures") != []
            or runtime.get("secret_material_recorded") is not False
            or verification.get("overall_pass") is not True
            or verification.get("read_only_verification") is not True
            or verification.get("protected_state_mutated") is not False
        ):
            raise LifecycleError("reconcile attempt evidence did not pass")
        return summary

    def _complete_recovery_reconcile(
        self,
        transaction: Path,
        record: dict[str, Any],
        current: str,
        manual_evidence: dict[str, Any],
        reconcile_summary: dict[str, Any],
        final_path: Path,
    ) -> dict[str, Any]:
        runtime_reference = dict(reconcile_summary["runtime_status"])
        verification_reference = dict(reconcile_summary["deployment_verification"])
        reconcile_reference = self._evidence_reference(final_path)
        record.setdefault("recovery_failed_completed_at", record.get("completed_at"))
        record.update(
            {
                "status": "recovery_reconciled",
                "completed_at": now(),
                "reconciled": True,
                "reconciled_from_status": "recovery_failed",
                "restored_current": current,
                "current": current,
                "manual_recovery_transaction_record_mutated": False,
                "manual_recovery_summary_sha256": manual_evidence["manual"]["sha256"],
                "manual_recovery_reconcile": {
                    "summary": reconcile_reference,
                    "runtime_status": runtime_reference,
                    "deployment_verification": verification_reference,
                },
            }
        )
        self._write_transaction_record(transaction, record)
        return {
            **reconcile_summary,
            "reconcile_summary": reconcile_reference,
            "record_sha256": sha256_file(transaction / "record.json"),
        }

    def _resume_completed_recovery_reconcile(
        self,
        transaction: Path,
        record: dict[str, Any],
        resolution_summary: Path,
    ) -> dict[str, Any]:
        current = self._resolve_link(self.layout.current, required=True)
        assert current is not None
        if (
            record.get("status") != "recovery_reconciled"
            or record.get("reconciled_from_status") != "recovery_failed"
            or record.get("current") != current
            or record.get("from_current") != current
            or record.get("result", {}).get("overall_pass") is not False
        ):
            raise LifecycleError("completed recovery reconciliation record is invalid")
        manual_evidence = self._validate_manual_recovery(
            transaction, record, current, resolution_summary
        )
        final_path = self._regular_evidence(
            transaction / MANUAL_RECOVERY_RECONCILE_SUMMARY,
            "manual recovery reconcile summary",
        )
        summary = load_json_object(final_path, "manual recovery reconcile summary")
        if (
            summary.get("schema_version") != 1
            or summary.get("overall_pass") is not True
            or summary.get("operation") != "reconcile-manual-recovery"
            or summary.get("transaction_id") != transaction.name
            or summary.get("current") != current
            or summary.get("terminal_state") != "recovery_reconciled"
            or summary.get("manual_recovery") != manual_evidence
            or summary.get("protected_state_mutated") is not False
            or summary.get("secret_material_recorded") is not False
            or record.get("manual_recovery_reconcile", {}).get("summary")
            != self._evidence_reference(final_path)
        ):
            raise LifecycleError(
                "completed recovery reconciliation evidence is invalid"
            )
        return {
            **summary,
            "reconcile_summary": self._evidence_reference(final_path),
            "record_sha256": sha256_file(transaction / "record.json"),
            "idempotent": True,
        }

    def _release_manifest(self, release_source: Path) -> dict[str, Any]:
        manifest = load_json_object(
            release_source / "manifest.json", "release manifest"
        )
        if manifest.get("schema_version") != 1:
            raise LifecycleError("release manifest schema_version must be 1")
        tag = str(manifest.get("tag", ""))
        commit = str(manifest.get("commit", ""))
        if TAG_RE.fullmatch(tag) is None or COMMIT_RE.fullmatch(commit) is None:
            raise LifecycleError("release manifest lacks an exact tag or full commit")
        if manifest.get("platform") != "linux-x64":
            raise LifecycleError("release manifest platform must be linux-x64")
        if manifest.get("source_build_performed") is not False:
            raise LifecycleError("release is not source-build-free")
        assets = manifest.get("assets")
        configuration = manifest.get("configuration")
        runtime_name = f"boost-gateway-{tag}-linux-x64.tar.gz"
        if (
            not isinstance(assets, dict)
            or SHA256_RE.fullmatch(str(assets.get(runtime_name, ""))) is None
        ):
            raise LifecycleError("release manifest has no immutable runtime digest")
        if (
            not isinstance(configuration, dict)
            or SHA256_RE.fullmatch(str(configuration.get("sha256", ""))) is None
        ):
            raise LifecycleError("release manifest has no configuration digest")
        controller = manifest.get("deployment_controller")
        if not isinstance(controller, dict):
            raise LifecycleError(
                "release manifest has no deployment controller identity"
            )
        observed_controller = {
            "dockerfiles_sha256": sha256_tree(release_source / "deploy/runtime"),
            "systemd_sha256": sha256_tree(release_source / "deploy/systemd"),
            "compose_sha256": sha256_file(
                release_source / "deploy/operations/docker-compose.production.yml"
            ),
            "monitoring_sha256": sha256_tree(release_source / "env/monitoring"),
            "redis_sha256": sha256_tree(release_source / "env/redis"),
            "verification_tools_sha256": sha256_tree(release_source / "scripts/tools"),
            "verification_runtime_sha256": sha256_tree(release_source / "scripts"),
        }
        drifted = sorted(
            key
            for key, value in observed_controller.items()
            if controller.get(key) != value
        )
        if drifted:
            raise LifecycleError(
                f"release deployment controller digest drift: {drifted}"
            )
        binaries = manifest.get("binaries")
        if not isinstance(binaries, list) or not binaries:
            raise LifecycleError("release manifest has no binary inventory")
        for item in binaries:
            if not isinstance(item, dict):
                raise LifecycleError("release manifest contains a non-object binary")
            name = str(item.get("name", ""))
            digest = str(item.get("sha256", ""))
            if Path(name).name != name or SHA256_RE.fullmatch(digest) is None:
                raise LifecycleError(
                    "release manifest contains an invalid binary identity"
                )
            if sha256_file(release_source / "bin" / name) != digest:
                raise LifecycleError(f"release binary digest drift: {name}")
        return manifest

    @staticmethod
    def _deployment_id(manifest: dict[str, Any]) -> str:
        tag = str(manifest["tag"])
        runtime = str(manifest["assets"][f"boost-gateway-{tag}-linux-x64.tar.gz"])
        controller = manifest.get("deployment_controller", {})
        if not isinstance(controller, dict):
            raise LifecycleError(
                "release manifest has no deployment controller identity"
            )
        controller_digest = str(controller.get("verification_runtime_sha256", ""))
        if SHA256_RE.fullmatch(controller_digest) is None:
            raise LifecycleError("release manifest has invalid controller identity")
        return f"{tag}-{runtime[:12]}-{controller_digest[:12]}"

    def _deployment_dir(self, deployment_id: str) -> Path:
        if DEPLOYMENT_ID_RE.fullmatch(deployment_id) is None:
            raise LifecycleError(f"invalid deployment identity: {deployment_id!r}")
        return self.layout.deployments / deployment_id

    def _validate_unit_compatibility(self, candidate: str, current: str) -> None:
        relative = Path("deploy/systemd/boost-gateway-compose.service")
        candidate_unit = self._deployment_dir(candidate) / relative
        current_unit = self._deployment_dir(current) / relative
        if candidate_unit.read_bytes() != current_unit.read_bytes():
            raise LifecycleError(
                "release changes the host Compose unit; migrate the host controller separately"
            )
        if (
            not self.layout.unit_path.is_file()
            or self.layout.unit_path.read_bytes() != current_unit.read_bytes()
        ):
            raise LifecycleError(
                "installed Compose unit drifted from current deployment"
            )

    def _record(self, deployment_id: str) -> dict[str, Any]:
        deployment_path = self._deployment_dir(deployment_id)
        path = deployment_path / "record.json"
        record = load_json_object(path, "deployment record")
        if record.get("deployment_id") != deployment_id:
            raise LifecycleError(
                f"deployment record identity mismatch: {deployment_id}"
            )
        if record.get("deployment_path") != str(deployment_path):
            raise LifecycleError(f"deployment path mismatch: {deployment_id}")
        release_path = self.layout.releases / deployment_id
        if record.get("release_path") != str(release_path):
            raise LifecycleError(f"deployment release path mismatch: {deployment_id}")
        if not release_path.is_dir() or release_path.is_symlink():
            raise LifecycleError(
                f"installed release is missing or unsafe: {release_path}"
            )
        release_link = deployment_path / "release"
        if (
            not release_link.is_symlink()
            or Path(os.path.realpath(release_link)) != release_path.resolve()
        ):
            raise LifecycleError(f"deployment release link drift: {deployment_id}")
        for name in ("bin", "config", "deploy", "env", "scripts", "manifest.json"):
            surface = deployment_path / name
            expected = release_path / name
            if (
                not surface.is_symlink()
                or Path(os.path.realpath(surface)) != expected.resolve()
            ):
                raise LifecycleError(
                    f"deployment runtime link drift: {deployment_id}/{name}"
                )
        if sha256_file(release_path / "manifest.json") != record.get("manifest_sha256"):
            raise LifecycleError(f"installed release manifest drift: {deployment_id}")
        if sha256_tree(release_path) != record.get("release_tree_sha256"):
            raise LifecycleError(f"installed release tree drift: {deployment_id}")
        image_env = deployment_path / "compose-images.env"
        parse_image_environment(image_env)
        if sha256_file(image_env) != record.get("image_environment_sha256"):
            raise LifecycleError(f"deployment image environment drift: {deployment_id}")
        snapshot = deployment_path / "configuration-snapshot"
        if sha256_tree(snapshot) != record.get("configuration_sha256"):
            raise LifecycleError(f"deployment configuration drift: {deployment_id}")
        attestations = deployment_path / "attestations"
        for name, field in (
            ("release-runtime-staging-summary.json", "release_summary_sha256"),
            ("image-build-summary.json", "image_summary_sha256"),
        ):
            if sha256_file(attestations / name) != record.get(field):
                raise LifecycleError(
                    f"deployment attestation drift: {deployment_id}/{name}"
                )
        return record

    def install(
        self,
        release_source: Path,
        image_environment: Path,
        release_summary_path: Path,
        image_summary_path: Path,
        config_source: Path | None,
    ) -> dict[str, Any]:
        with lifecycle_lock(self.layout):
            self._ensure_layout()
            release_source = release_source.resolve()
            if not release_source.is_dir() or release_source.is_symlink():
                raise LifecycleError(
                    f"release source is missing or unsafe: {release_source}"
                )
            manifest = self._release_manifest(release_source)
            deployment_id = self._deployment_id(manifest)
            release_destination = self.layout.releases / deployment_id
            deployment_destination = self._deployment_dir(deployment_id)
            values = parse_image_environment(image_environment.resolve())
            config = (config_source or release_source / "config").resolve()
            config_digest = sha256_tree(config)
            expected_config = str(manifest["configuration"]["sha256"])
            if config_digest != expected_config:
                raise LifecycleError(
                    f"configuration snapshot differs from release manifest: {config_digest}"
                )

            manifest_sha = sha256_file(release_source / "manifest.json")
            release_summary, image_summary = validate_install_attestations(
                manifest,
                manifest_sha,
                values,
                release_summary_path.resolve(),
                image_summary_path.resolve(),
            )
            source_tree_digest = sha256_tree(release_source)
            expected_env = hashlib.sha256(render_image_environment(values)).hexdigest()

            def accept_existing() -> dict[str, Any]:
                existing = self._record(deployment_id)
                if (
                    existing.get("manifest_sha256") != manifest_sha
                    or existing.get("release_tree_sha256") != source_tree_digest
                    or existing.get("image_environment_sha256") != expected_env
                    or existing.get("configuration_sha256") != config_digest
                ):
                    raise LifecycleError(
                        f"existing deployment identity has different inputs: {deployment_id}"
                    )
                changed = False
                missing_identity = {
                    field for field in ("host", "operator") if field not in existing
                }
                if missing_identity:
                    identity = self._identity()
                    for field in missing_identity:
                        existing[field] = identity[field]
                    changed = True
                if "result" not in existing:
                    existing["result"] = self._install_result(
                        str(existing.get("installed_at", ""))
                    )
                    changed = True
                if changed:
                    atomic_write_json(deployment_destination / "record.json", existing)
                return existing

            if release_destination.exists() and deployment_destination.exists():
                return accept_existing()

            release_temp = Path(
                tempfile.mkdtemp(prefix=f".{deployment_id}.", dir=self.layout.releases)
            )
            try:
                if release_destination.exists():
                    if (
                        not release_destination.is_dir()
                        or release_destination.is_symlink()
                        or sha256_tree(release_destination) != source_tree_digest
                    ):
                        raise LifecycleError(
                            f"partial release install has different content: {deployment_id}"
                        )
                else:
                    shutil.copytree(
                        release_source,
                        release_temp,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                    )
                    if sha256_tree(release_temp) != source_tree_digest:
                        raise LifecycleError("release changed while installing")
                    os.replace(release_temp, release_destination)
                    fsync_directory(self.layout.releases)

                if deployment_destination.exists():
                    return accept_existing()

                deployment_temp = Path(
                    tempfile.mkdtemp(
                        prefix=f".{deployment_id}.", dir=self.layout.deployments
                    )
                )
                try:
                    shutil.copytree(config, deployment_temp / "configuration-snapshot")
                    atomic_write(
                        deployment_temp / "compose-images.env",
                        render_image_environment(values),
                        0o640,
                    )
                    attestations = deployment_temp / "attestations"
                    attestations.mkdir()
                    atomic_write_json(
                        attestations / "release-runtime-staging-summary.json",
                        release_summary,
                    )
                    atomic_write_json(
                        attestations / "image-build-summary.json", image_summary
                    )
                    (deployment_temp / "release").symlink_to(release_destination)
                    for name in (
                        "bin",
                        "config",
                        "deploy",
                        "env",
                        "scripts",
                        "manifest.json",
                    ):
                        (deployment_temp / name).symlink_to(Path("release") / name)
                    installed_at = now()
                    identity = self._identity()
                    record = {
                        "schema_version": 1,
                        "deployment_id": deployment_id,
                        "deployment_path": str(deployment_destination),
                        "installed_at": installed_at,
                        "status": "installed",
                        "release_path": str(release_destination),
                        "tag": manifest["tag"],
                        "commit": manifest["commit"],
                        "runtime_asset_sha256": manifest["assets"][
                            f"boost-gateway-{manifest['tag']}-linux-x64.tar.gz"
                        ],
                        "manifest_sha256": manifest_sha,
                        "release_summary_sha256": sha256_file(
                            attestations / "release-runtime-staging-summary.json"
                        ),
                        "image_summary_sha256": sha256_file(
                            attestations / "image-build-summary.json"
                        ),
                        "release_tree_sha256": source_tree_digest,
                        "image_environment_sha256": sha256_file(
                            deployment_temp / "compose-images.env"
                        ),
                        "image_ids": values,
                        "configuration_sha256": config_digest,
                        "host": identity["host"],
                        "operator": identity["operator"],
                        "result": self._install_result(installed_at),
                        "secret_material_recorded": False,
                        "protected_state_policy": {
                            "volumes_deleted": False,
                            "data_deleted": False,
                            "evidence_deleted": False,
                            "backups_deleted": False,
                        },
                    }
                    atomic_write_json(deployment_temp / "record.json", record)
                    os.replace(deployment_temp, deployment_destination)
                    fsync_directory(self.layout.deployments)
                    return record
                finally:
                    shutil.rmtree(deployment_temp, ignore_errors=True)
            finally:
                shutil.rmtree(release_temp, ignore_errors=True)

    def _resolve_link(self, link: Path, *, required: bool) -> str | None:
        if not link.exists() and not link.is_symlink():
            if required:
                raise LifecycleError(f"required lifecycle link is missing: {link}")
            return None
        if not link.is_symlink():
            raise LifecycleError(f"lifecycle path is not a symbolic link: {link}")
        target = Path(os.path.realpath(link))
        try:
            relative = target.relative_to(self.layout.deployments.resolve())
        except ValueError as exc:
            raise LifecycleError(
                f"lifecycle link escapes deployments root: {link} -> {target}"
            ) from exc
        if (
            len(relative.parts) != 1
            or DEPLOYMENT_ID_RE.fullmatch(relative.name) is None
        ):
            raise LifecycleError(f"lifecycle link target is not a deployment: {target}")
        self._record(relative.name)
        return relative.name

    def _legacy_current_matches(self, deployment_id: str) -> bool:
        """Recognize the single TODO-0009 release pointer during one-time adoption."""
        link = self.layout.current
        if not link.is_symlink():
            return False
        target = Path(os.path.realpath(link))
        expected = self.layout.releases / deployment_id
        record = self._record(deployment_id)
        if (
            not self.layout.active_image_env.is_file()
            or self.layout.active_image_env.is_symlink()
            or parse_image_environment(self.layout.active_image_env)
            != parse_image_environment(
                self._deployment_dir(deployment_id) / "compose-images.env"
            )
        ):
            return False
        if target == expected.resolve():
            return record["release_path"] == str(expected)
        try:
            target.relative_to(self.layout.releases.resolve())
        except ValueError:
            return False
        return (
            target.is_dir()
            and not target.is_symlink()
            and sha256_file(target / "manifest.json") == record["manifest_sha256"]
            and sha256_tree(target) == record["release_tree_sha256"]
        )

    def _atomic_link(self, deployment_id: str, link: Path) -> None:
        target = self._deployment_dir(deployment_id)
        self._record(deployment_id)
        temporary = link.with_name(f".{link.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.symlink_to(target)
            os.replace(temporary, link)
            fsync_directory(link.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _clear_link(self, link: Path) -> None:
        if link.exists() and not link.is_symlink():
            raise LifecycleError(
                f"refusing to remove non-symlink lifecycle path: {link}"
            )
        link.unlink(missing_ok=True)
        fsync_directory(link.parent)

    def _clear_active_image_link(self) -> None:
        if self.layout.active_image_env.is_symlink():
            self.layout.active_image_env.unlink()
            fsync_directory(self.layout.active_image_env.parent)

    def _ensure_active_image_link(self) -> None:
        expected = self.layout.current / "compose-images.env"
        if self.layout.active_image_env.is_symlink():
            raw_target = os.readlink(self.layout.active_image_env)
            resolved = (
                Path(raw_target)
                if Path(raw_target).is_absolute()
                else self.layout.active_image_env.parent / raw_target
            )
            if resolved == expected:
                return
        elif (
            self.layout.active_image_env.exists()
            and not self.layout.active_image_env.is_file()
        ):
            raise LifecycleError(
                f"active image environment is not a file or symlink: {self.layout.active_image_env}"
            )
        self.layout.active_image_env.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.layout.active_image_env.with_name(
            f".{self.layout.active_image_env.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.symlink_to(expected)
            os.replace(temporary, self.layout.active_image_env)
            fsync_directory(self.layout.active_image_env.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _activate_files(self, deployment_id: str) -> None:
        image_env = self._deployment_dir(deployment_id) / "compose-images.env"
        parse_image_environment(image_env)
        legacy_regular_env = (
            self.layout.active_image_env.exists()
            and not self.layout.active_image_env.is_symlink()
        )
        if legacy_regular_env:
            self._atomic_link(deployment_id, self.layout.current)
            self._ensure_active_image_link()
        else:
            self._ensure_active_image_link()
            self._atomic_link(deployment_id, self.layout.current)

    def _transaction(
        self, operation: str, **fields: Any
    ) -> tuple[Path, dict[str, Any]]:
        transaction_id = (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            + f"-{operation}-{uuid.uuid4().hex[:12]}"
        )
        path = self.layout.transaction_root / transaction_id
        path.mkdir(parents=True, exist_ok=False)
        fsync_directory(self.layout.transaction_root)
        record = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "operation": operation,
            "started_at": now(),
            "status": "pending",
            "secret_material_recorded": False,
            **fields,
        }
        self._write_transaction_record(path, record)
        return path, record

    def _reconcile_pending(self) -> None:
        pending: tuple[Path, dict[str, Any]] | None = None
        for record_path in sorted(
            self.layout.transaction_root.glob("*/record.json"), reverse=True
        ):
            record = load_json_object(record_path, "lifecycle transaction")
            if record.get("status") in BLOCKING_TRANSACTION_STATES:
                raise LifecycleError(
                    f"unresolved recovery failure blocks lifecycle: {record_path.parent.name}"
                )
            if record.get("status") in INCOMPLETE_TRANSACTION_STATES:
                pending = (record_path.parent, record)
                break
        if pending is None:
            return

        transaction, record = pending
        candidate = str(record.get("candidate", ""))
        if DEPLOYMENT_ID_RE.fullmatch(candidate) is None:
            raise LifecycleError("pending transaction has an invalid candidate")
        started = self.monotonic()
        if record.get("legacy_adoption") is True and self._legacy_current_matches(
            candidate
        ):
            self.executor.precheck(
                self._deployment_dir(candidate),
                self._remaining(started, ROLLBACK_DEADLINE_SECONDS, self.monotonic),
            )
            self.executor.activate(
                self._deployment_dir(candidate),
                self._remaining(started, ROLLBACK_DEADLINE_SECONDS, self.monotonic),
            )
            self._verify_target(
                candidate,
                transaction,
                self._remaining(started, ROLLBACK_DEADLINE_SECONDS, self.monotonic),
                "reconcile-verification-summary.json",
            )
            record.update(
                {
                    "status": "interrupted_legacy_preserved",
                    "completed_at": now(),
                    "reconciled": True,
                }
            )
            self._write_transaction_record(transaction, record)
            return

        current = self._resolve_link(self.layout.current, required=False)
        if current == candidate and self._record(candidate).get("status") == "verified":
            previous = record.get("from_current")
            try:
                self.executor.commit(
                    self._deployment_dir(candidate),
                    self._remaining(started, ROLLBACK_DEADLINE_SECONDS, self.monotonic),
                )
                self._activate_files(candidate)
                self._verify_target(
                    candidate,
                    transaction,
                    self._remaining(started, ROLLBACK_DEADLINE_SECONDS, self.monotonic),
                    "reconcile-verification-summary.json",
                )
                if isinstance(previous, str) and previous and previous != candidate:
                    self._atomic_link(previous, self.layout.previous)
            except Exception as exc:
                recovery_started = self.monotonic()
                try:
                    if isinstance(previous, str) and previous:
                        self._restore(
                            previous,
                            transaction,
                            recovery_started,
                            ROLLBACK_DEADLINE_SECONDS,
                            from_deployment=candidate,
                        )
                    else:
                        self.executor.deactivate(
                            self._deployment_dir(candidate),
                            self._remaining(
                                recovery_started,
                                ROLLBACK_DEADLINE_SECONDS,
                                self.monotonic,
                            ),
                        )
                        self._clear_link(self.layout.current)
                        self._clear_active_image_link()
                        self.executor.uncommit(
                            self._remaining(
                                recovery_started,
                                ROLLBACK_DEADLINE_SECONDS,
                                self.monotonic,
                            )
                        )
                except Exception as recovery_exc:
                    record.update(
                        {
                            "status": "recovery_failed",
                            "completed_at": now(),
                            "failure": str(exc),
                            "recovery_failure": str(recovery_exc),
                        }
                    )
                    self._write_transaction_record(transaction, record)
                    raise LifecycleError(
                        f"transaction reconciliation recovery failed: {recovery_exc}"
                    ) from recovery_exc
                record.update(
                    {
                        "status": "interrupted_rolled_back",
                        "completed_at": now(),
                        "reconciled": True,
                        "failure": str(exc),
                        "restored_current": previous,
                    }
                )
                self._write_transaction_record(transaction, record)
                return
            record.update(
                {
                    "status": "passed_reconciled",
                    "completed_at": now(),
                    "reconciled": True,
                    "current": candidate,
                    "previous": previous,
                }
            )
            self._write_transaction_record(transaction, record)
            return

        if current is not None:
            if self._record(current).get("status") != "verified":
                raise LifecycleError(
                    "pending transaction left current on an unverified deployment"
                )
            self._restore(
                current,
                transaction,
                started,
                ROLLBACK_DEADLINE_SECONDS,
                from_deployment=candidate,
            )
            record.update(
                {
                    "status": "interrupted_rolled_back",
                    "completed_at": now(),
                    "reconciled": True,
                    "restored_current": current,
                }
            )
            self._write_transaction_record(transaction, record)
            return

        self.executor.deactivate(
            self._deployment_dir(candidate),
            self._remaining(started, ROLLBACK_DEADLINE_SECONDS, self.monotonic),
        )
        self._clear_link(self.layout.current)
        self._clear_active_image_link()
        self.executor.uncommit(
            self._remaining(started, ROLLBACK_DEADLINE_SECONDS, self.monotonic)
        )
        record.update(
            {
                "status": "interrupted_failed_closed",
                "completed_at": now(),
                "reconciled": True,
            }
        )
        self._write_transaction_record(transaction, record)

    @staticmethod
    def _remaining(started: float, budget: float, monotonic: Any) -> float:
        remaining = budget - (monotonic() - started)
        if remaining <= 0:
            raise LifecycleError(f"lifecycle deadline exceeded ({budget:.0f}s)")
        return remaining

    def _update_deployment(self, deployment_id: str, **fields: Any) -> None:
        path = self._deployment_dir(deployment_id) / "record.json"
        record = self._record(deployment_id)
        record.update(fields)
        atomic_write_json(path, record)

    def _verify_target(
        self,
        deployment_id: str,
        transaction: Path,
        timeout_seconds: float,
        summary_name: str = "deployment-verification-summary.json",
    ) -> dict[str, Any]:
        return self.executor.verify(
            self._deployment_dir(deployment_id),
            transaction / summary_name,
            timeout_seconds,
        )

    def _prepare_transition(
        self,
        source: str | None,
        target: str,
        transaction: Path,
        started: float,
        budget: float,
        summary_name: str,
    ) -> dict[str, Any] | None:
        if source is None or source == target:
            return None
        return self.executor.prepare_transition(
            self._deployment_dir(source),
            self._deployment_dir(target),
            transaction / summary_name,
            self._remaining(started, budget, self.monotonic),
        )

    @staticmethod
    def _ensure_failure_summary(transaction: Path, failure: Exception) -> None:
        path = transaction / "deployment-verification-summary.json"
        if path.exists():
            return
        atomic_write_json(
            path,
            {
                "summary_version": 2,
                "generated_at": now(),
                "overall_pass": False,
                "passed": False,
                "failed_step": "release-lifecycle-activation",
                "failure": str(failure),
                "source_build_performed": False,
                "public_conan_access_performed": False,
            },
        )

    def _restore(
        self,
        old_current: str | None,
        transaction: Path,
        started: float,
        budget: float,
        *,
        from_deployment: str | None = None,
    ) -> dict[str, Any] | None:
        if old_current is None:
            candidate = self._resolve_link(self.layout.current, required=False)
            if candidate is not None:
                self.executor.deactivate(
                    self._deployment_dir(candidate),
                    self._remaining(started, budget, self.monotonic),
                )
            self._clear_link(self.layout.current)
            self._clear_active_image_link()
            self.executor.uncommit(self._remaining(started, budget, self.monotonic))
            return None
        self._prepare_transition(
            from_deployment,
            old_current,
            transaction,
            started,
            budget,
            "recovery-persistence-transition-summary.json",
        )
        self.executor.precheck(
            self._deployment_dir(old_current),
            self._remaining(started, budget, self.monotonic),
        )
        self.executor.activate(
            self._deployment_dir(old_current),
            self._remaining(started, budget, self.monotonic),
        )
        verification = self._verify_target(
            old_current,
            transaction,
            self._remaining(started, budget, self.monotonic),
            "recovery-verification-summary.json",
        )
        self.executor.commit(
            self._deployment_dir(old_current),
            self._remaining(started, budget, self.monotonic),
        )
        self._activate_files(old_current)
        return verification

    def _activate(
        self,
        operation: str,
        candidate: str,
        *,
        budget: float = ROLLBACK_DEADLINE_SECONDS,
    ) -> dict[str, Any]:
        with lifecycle_lock(self.layout):
            self._ensure_layout()
            self._reconcile_pending()
            self._record(candidate)
            legacy_adoption = operation == "deploy" and self._legacy_current_matches(
                candidate
            )
            old_current = (
                None
                if legacy_adoption
                else self._resolve_link(self.layout.current, required=False)
            )
            old_previous = self._resolve_link(self.layout.previous, required=False)
            if operation == "deploy" and old_current not in {None, candidate}:
                raise LifecycleError("deploy refuses to replace current; use upgrade")
            if operation == "upgrade" and old_current is None:
                raise LifecycleError("upgrade requires a current verified deployment")
            if (
                old_current is not None
                and self._record(old_current).get("status") != "verified"
            ):
                raise LifecycleError("current deployment is not verified")
            if old_current is not None:
                self._validate_unit_compatibility(candidate, old_current)
            if old_current == candidate:
                verification = self.verify_current(
                    timeout_seconds=budget, already_locked=True
                )
                return {
                    "operation": operation,
                    "idempotent": True,
                    "current": candidate,
                    "verification": verification,
                }

            transaction, record = self._transaction(
                operation,
                candidate=candidate,
                from_current=old_current,
                from_previous=old_previous,
                deadline_seconds=budget,
                legacy_adoption=legacy_adoption,
            )
            started = self.monotonic()
            try:
                self.executor.precheck(
                    self._deployment_dir(candidate),
                    self._remaining(started, budget, self.monotonic),
                )
                self._prepare_transition(
                    old_current,
                    candidate,
                    transaction,
                    started,
                    budget,
                    "candidate-persistence-transition-summary.json",
                )
                self.executor.activate(
                    self._deployment_dir(candidate),
                    self._remaining(started, budget, self.monotonic),
                )
                record["status"] = "candidate_activated"
                self._write_transaction_record(transaction, record)
                self._verify_target(
                    candidate,
                    transaction,
                    self._remaining(started, budget, self.monotonic),
                )
                self._update_deployment(
                    candidate,
                    status="verified",
                    verified_at=now(),
                    last_transaction=record["transaction_id"],
                )
                record["status"] = "candidate_verified"
                self._write_transaction_record(transaction, record)
                self.executor.commit(
                    self._deployment_dir(candidate),
                    self._remaining(started, budget, self.monotonic),
                )
                self._activate_files(candidate)
                if old_current is not None:
                    self._atomic_link(old_current, self.layout.previous)
                record.update(
                    {
                        "status": "passed",
                        "completed_at": now(),
                        "current": candidate,
                        "previous": old_current,
                        "elapsed_seconds": round(self.monotonic() - started, 3),
                    }
                )
                self._write_transaction_record(transaction, record)
                return record
            except Exception as exc:
                self._ensure_failure_summary(transaction, exc)
                record.update(
                    {
                        "status": "activation_failed",
                        "failed_at": now(),
                        "failure": str(exc),
                    }
                )
                self._write_transaction_record(transaction, record)
                if legacy_adoption:
                    recovery_started = self.monotonic()
                    try:
                        self.executor.precheck(
                            self._deployment_dir(candidate),
                            self._remaining(
                                recovery_started,
                                ROLLBACK_DEADLINE_SECONDS,
                                self.monotonic,
                            ),
                        )
                        self.executor.activate(
                            self._deployment_dir(candidate),
                            self._remaining(
                                recovery_started,
                                ROLLBACK_DEADLINE_SECONDS,
                                self.monotonic,
                            ),
                        )
                        self._verify_target(
                            candidate,
                            transaction,
                            self._remaining(
                                recovery_started,
                                ROLLBACK_DEADLINE_SECONDS,
                                self.monotonic,
                            ),
                            "recovery-verification-summary.json",
                        )
                    except Exception as recovery_exc:
                        record.update(
                            {
                                "status": "recovery_failed",
                                "completed_at": now(),
                                "recovery_failure": str(recovery_exc),
                            }
                        )
                        self._write_transaction_record(transaction, record)
                        raise LifecycleError(
                            "legacy adoption and topology recovery both failed: "
                            f"{exc}; {recovery_exc}"
                        ) from recovery_exc
                    record.update(
                        {
                            "status": "legacy_preserved",
                            "completed_at": now(),
                            "elapsed_seconds": round(self.monotonic() - started, 3),
                            "recovery_elapsed_seconds": round(
                                self.monotonic() - recovery_started, 3
                            ),
                        }
                    )
                    self._write_transaction_record(transaction, record)
                    raise LifecycleError(
                        f"legacy adoption failed; TODO-0009 pointer was preserved: {exc}"
                    ) from exc
                recovery_started = self.monotonic()
                try:
                    self._restore(
                        old_current,
                        transaction,
                        recovery_started,
                        ROLLBACK_DEADLINE_SECONDS,
                        from_deployment=candidate,
                    )
                except Exception as recovery_exc:
                    record.update(
                        {
                            "status": "recovery_failed",
                            "completed_at": now(),
                            "recovery_failure": str(recovery_exc),
                            "elapsed_seconds": round(self.monotonic() - started, 3),
                            "recovery_elapsed_seconds": round(
                                self.monotonic() - recovery_started, 3
                            ),
                        }
                    )
                    self._write_transaction_record(transaction, record)
                    raise LifecycleError(
                        f"{operation} failed and previous recovery failed: {exc}; {recovery_exc}"
                    ) from recovery_exc
                record.update(
                    {
                        "status": "rolled_back" if old_current else "failed_closed",
                        "completed_at": now(),
                        "restored_current": old_current,
                        "previous": old_previous,
                        "elapsed_seconds": round(self.monotonic() - started, 3),
                        "recovery_elapsed_seconds": round(
                            self.monotonic() - recovery_started, 3
                        ),
                    }
                )
                self._write_transaction_record(transaction, record)
                raise LifecycleError(
                    f"{operation} verification failed; previous deployment restored: {exc}"
                ) from exc

    def deploy(self, deployment_id: str) -> dict[str, Any]:
        return self._activate("deploy", deployment_id)

    def upgrade(self, deployment_id: str) -> dict[str, Any]:
        return self._activate("upgrade", deployment_id)

    def rollback(self) -> dict[str, Any]:
        with lifecycle_lock(self.layout):
            self._ensure_layout()
            self._reconcile_pending()
            old_current = self._resolve_link(self.layout.current, required=True)
            target = self._resolve_link(self.layout.previous, required=True)
            assert old_current is not None and target is not None
            if old_current == target:
                raise LifecycleError(
                    "current and previous cannot reference the same deployment"
                )
            if self._record(target).get("status") != "verified":
                raise LifecycleError("previous deployment is not verified")
            self._validate_unit_compatibility(target, old_current)
            transaction, record = self._transaction(
                "rollback",
                candidate=target,
                from_current=old_current,
                from_previous=target,
                deadline_seconds=ROLLBACK_DEADLINE_SECONDS,
            )
            started = self.monotonic()
            try:
                self.executor.precheck(
                    self._deployment_dir(target),
                    self._remaining(started, ROLLBACK_DEADLINE_SECONDS, self.monotonic),
                )
                self._prepare_transition(
                    old_current,
                    target,
                    transaction,
                    started,
                    ROLLBACK_DEADLINE_SECONDS,
                    "candidate-persistence-transition-summary.json",
                )
                self.executor.activate(
                    self._deployment_dir(target),
                    self._remaining(started, ROLLBACK_DEADLINE_SECONDS, self.monotonic),
                )
                record["status"] = "candidate_activated"
                self._write_transaction_record(transaction, record)
                self._verify_target(
                    target,
                    transaction,
                    self._remaining(started, ROLLBACK_DEADLINE_SECONDS, self.monotonic),
                )
                record["status"] = "candidate_verified"
                self._write_transaction_record(transaction, record)
                self.executor.commit(
                    self._deployment_dir(target),
                    self._remaining(started, ROLLBACK_DEADLINE_SECONDS, self.monotonic),
                )
                self._activate_files(target)
                self._atomic_link(old_current, self.layout.previous)
                record.update(
                    {
                        "status": "passed",
                        "completed_at": now(),
                        "current": target,
                        "previous": old_current,
                        "restored_runtime_asset_sha256": self._record(target)[
                            "runtime_asset_sha256"
                        ],
                        "restored_image_environment_sha256": self._record(target)[
                            "image_environment_sha256"
                        ],
                        "restored_configuration_sha256": self._record(target)[
                            "configuration_sha256"
                        ],
                        "elapsed_seconds": round(self.monotonic() - started, 3),
                    }
                )
                self._write_transaction_record(transaction, record)
                return record
            except Exception as exc:
                self._ensure_failure_summary(transaction, exc)
                record.update(
                    {
                        "status": "rollback_failed",
                        "failed_at": now(),
                        "failure": str(exc),
                    }
                )
                self._write_transaction_record(transaction, record)
                recovery_started = self.monotonic()
                try:
                    self._restore(
                        old_current,
                        transaction,
                        recovery_started,
                        ROLLBACK_DEADLINE_SECONDS,
                        from_deployment=target,
                    )
                except Exception as recovery_exc:
                    record.update(
                        {
                            "status": "recovery_failed",
                            "completed_at": now(),
                            "recovery_failure": str(recovery_exc),
                            "elapsed_seconds": round(self.monotonic() - started, 3),
                            "recovery_elapsed_seconds": round(
                                self.monotonic() - recovery_started, 3
                            ),
                        }
                    )
                    self._write_transaction_record(transaction, record)
                    raise LifecycleError(
                        f"rollback failed and current recovery failed: {exc}; {recovery_exc}"
                    ) from recovery_exc
                record.update(
                    {
                        "status": "rolled_forward",
                        "completed_at": now(),
                        "restored_current": old_current,
                        "previous": target,
                        "elapsed_seconds": round(self.monotonic() - started, 3),
                        "recovery_elapsed_seconds": round(
                            self.monotonic() - recovery_started, 3
                        ),
                    }
                )
                self._write_transaction_record(transaction, record)
                raise LifecycleError(
                    f"rollback failed; original current deployment restored: {exc}"
                ) from exc

    def reconcile_recovery(
        self, transaction_id: str, resolution_summary: Path
    ) -> dict[str, Any]:
        if DEPLOYMENT_ID_RE.fullmatch(transaction_id) is None:
            raise LifecycleError("recovery transaction ID is invalid")
        with lifecycle_lock(self.layout):
            self._ensure_layout()
            requested_transaction = self.layout.transaction_root / transaction_id
            requested_record_path = requested_transaction / "record.json"
            if (
                requested_record_path.is_file()
                and not requested_record_path.is_symlink()
            ):
                requested_record = load_json_object(
                    requested_record_path, "lifecycle transaction"
                )
                if requested_record.get("status") == "recovery_reconciled":
                    return self._resume_completed_recovery_reconcile(
                        requested_transaction, requested_record, resolution_summary
                    )
            blocking = self._blocking_recovery_transactions()
            if len(blocking) != 1:
                raise LifecycleError(
                    "manual recovery reconciliation requires exactly one blocking "
                    f"recovery_failed transaction; found {len(blocking)}"
                )
            transaction, record = blocking[0]
            if transaction.name != transaction_id:
                raise LifecycleError(
                    "specified transaction is not the unique blocking recovery failure"
                )
            record_path = self._regular_evidence(
                transaction / "record.json", "blocking transaction record"
            )
            if (
                record.get("schema_version") != 1
                or record.get("transaction_id") != transaction_id
                or record.get("status") != "recovery_failed"
                or record.get("secret_material_recorded") is not False
            ):
                raise LifecycleError("blocking recovery transaction is invalid")

            current = self._resolve_link(self.layout.current, required=True)
            assert current is not None
            if record.get("from_current") != current:
                raise LifecycleError(
                    "blocking recovery transaction and current deployment differ"
                )
            if self._record(current).get("status") != "verified":
                raise LifecycleError("recovered current deployment is not verified")
            expected_image_link = self.layout.current / "compose-images.env"
            if (
                not self.layout.active_image_env.is_symlink()
                or self.layout.active_image_env.readlink() != expected_image_link
            ):
                raise LifecycleError(
                    "active image environment is not bound to recovered current"
                )

            record_sha256_before = sha256_file(record_path)
            manual_evidence = self._validate_manual_recovery(
                transaction, record, current, resolution_summary
            )
            final_path = transaction / MANUAL_RECOVERY_RECONCILE_SUMMARY
            if final_path.exists() or final_path.is_symlink():
                reconcile_summary = self._validate_existing_reconcile_summary(
                    transaction,
                    current,
                    record_sha256_before,
                    manual_evidence,
                    final_path,
                )
                return self._complete_recovery_reconcile(
                    transaction,
                    record,
                    current,
                    manual_evidence,
                    reconcile_summary,
                    final_path,
                )

            attempts = transaction / "reconcile-attempts"
            if attempts.is_symlink() or (attempts.exists() and not attempts.is_dir()):
                raise LifecycleError("reconcile attempts path is unsafe")
            attempts.mkdir(mode=0o750, exist_ok=True)
            attempt_id = (
                datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
                + f"-{uuid.uuid4().hex[:12]}"
            )
            attempt = attempts / attempt_id
            attempt.mkdir(mode=0o750, exist_ok=False)
            runtime_path = attempt / "runtime-status-summary.json"
            verification_path = attempt / "deployment-verification-summary.json"

            runtime_failures = self.executor.runtime_status(
                self._deployment_dir(current)
            )
            runtime_summary = {
                "schema_version": 1,
                "generated_at": now(),
                "overall_pass": not runtime_failures,
                "transaction_id": transaction_id,
                "current": current,
                "failures": runtime_failures,
                "secret_material_recorded": False,
            }
            atomic_write_new_json(runtime_path, runtime_summary)
            if runtime_failures:
                raise LifecycleError(
                    "recovered current runtime status did not pass: "
                    + "; ".join(runtime_failures)
                )

            verification = self.executor.verify_read_only(
                self._deployment_dir(current),
                verification_path,
                ROLLBACK_DEADLINE_SECONDS,
            )
            if (
                verification.get("overall_pass") is not True
                or verification.get("read_only_verification") is not True
                or verification.get("protected_state_mutated") is not False
            ):
                raise LifecycleError(
                    "recovered current read-only verification did not pass"
                )

            if sha256_file(record_path) != record_sha256_before:
                raise LifecycleError(
                    "blocking transaction record changed during reconciliation"
                )
            for item in manual_evidence.values():
                path = Path(str(item["path"]))
                if sha256_file(path) != item["sha256"]:
                    raise LifecycleError(
                        "manual recovery evidence changed during reconciliation"
                    )

            reconcile_summary = {
                "schema_version": 1,
                "generated_at": now(),
                "overall_pass": True,
                "operation": "reconcile-manual-recovery",
                "transaction_id": transaction_id,
                "current": current,
                "blocking_state_before": "recovery_failed",
                "terminal_state": "recovery_reconciled",
                "manual_recovery": manual_evidence,
                "transaction_record_sha256_before": record_sha256_before,
                "attempt_id": attempt_id,
                "runtime_status": self._evidence_reference(runtime_path),
                "deployment_verification": self._evidence_reference(verification_path),
                "record_update_authorized": True,
                "protected_state_mutated": False,
                "secret_material_recorded": False,
            }
            atomic_write_new_json(final_path, reconcile_summary)
            return self._complete_recovery_reconcile(
                transaction,
                record,
                current,
                manual_evidence,
                reconcile_summary,
                final_path,
            )

    def verify_current(
        self,
        *,
        timeout_seconds: float = ROLLBACK_DEADLINE_SECONDS,
        already_locked: bool = False,
    ) -> dict[str, Any]:
        def execute() -> dict[str, Any]:
            current = self._resolve_link(self.layout.current, required=True)
            assert current is not None
            transaction, record = self._transaction(
                "verify", candidate=current, deadline_seconds=timeout_seconds
            )
            started = self.monotonic()
            try:
                summary = self._verify_target(current, transaction, timeout_seconds)
            except Exception as exc:
                record.update(
                    {
                        "status": "failed",
                        "completed_at": now(),
                        "failure": str(exc),
                        "elapsed_seconds": round(self.monotonic() - started, 3),
                    }
                )
                self._write_transaction_record(transaction, record)
                raise
            record.update(
                {
                    "status": "passed",
                    "completed_at": now(),
                    "elapsed_seconds": round(self.monotonic() - started, 3),
                }
            )
            self._write_transaction_record(transaction, record)
            return summary

        if already_locked:
            return execute()
        with lifecycle_lock(self.layout):
            self._ensure_layout()
            self._reconcile_pending()
            return execute()

    def status(self) -> dict[str, Any]:
        with lifecycle_lock(self.layout):
            self._ensure_layout()
            self._reconcile_pending()
            current = self._resolve_link(self.layout.current, required=False)
            previous = self._resolve_link(self.layout.previous, required=False)
            failures: list[str] = []
            if current is not None:
                if self._record(current).get("status") != "verified":
                    failures.append("current deployment is not verified")
                expected = self._deployment_dir(current) / "compose-images.env"
                expected_link = self.layout.current / "compose-images.env"
                if not self.layout.active_image_env.is_symlink():
                    failures.append(
                        "active image environment is not the fixed current symlink"
                    )
                else:
                    raw_target = os.readlink(self.layout.active_image_env)
                    target = (
                        Path(raw_target)
                        if Path(raw_target).is_absolute()
                        else self.layout.active_image_env.parent / raw_target
                    )
                    if target != expected_link:
                        failures.append(
                            "active image environment symlink target differs"
                        )
                if self.layout.active_image_env.is_file() and sha256_file(
                    self.layout.active_image_env
                ) != sha256_file(expected):
                    failures.append(
                        "active image environment differs from current deployment"
                    )
                failures.extend(
                    self.executor.runtime_status(self._deployment_dir(current))
                )
            else:
                if self.layout.active_image_env.is_symlink():
                    failures.append("active image environment exists without current")
                failures.extend(self.executor.inactive_status())
            return {
                "schema_version": 1,
                "generated_at": now(),
                "overall_pass": not failures,
                "current": current,
                "previous": previous,
                "failures": failures,
                "protected_state_mutated": False,
            }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install")
    install.add_argument("--release-dir", type=Path, required=True)
    install.add_argument("--image-env", type=Path, required=True)
    install.add_argument("--release-summary", type=Path, required=True)
    install.add_argument("--image-summary", type=Path, required=True)
    install.add_argument("--config-dir", type=Path)
    for command in ("deploy", "upgrade"):
        child = subparsers.add_parser(command)
        child.add_argument("--deployment-id", required=True)
    reconcile = subparsers.add_parser(
        "reconcile-recovery",
        help="resolve one blocking recovery_failed transaction from governed evidence",
    )
    reconcile.add_argument("--transaction-id", required=True)
    reconcile.add_argument("--resolution-summary", type=Path, required=True)
    subparsers.add_parser("rollback")
    subparsers.add_parser("status")
    subparsers.add_parser("verify")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        guard_target_host()
        layout = Layout()
        manager = ReleaseDeploymentManager(layout, SystemLifecycleExecutor(layout))
        if args.command == "install":
            result = manager.install(
                args.release_dir,
                args.image_env,
                args.release_summary,
                args.image_summary,
                args.config_dir,
            )
        elif args.command == "deploy":
            result = manager.deploy(args.deployment_id)
        elif args.command == "upgrade":
            result = manager.upgrade(args.deployment_id)
        elif args.command == "rollback":
            result = manager.rollback()
        elif args.command == "reconcile-recovery":
            result = manager.reconcile_recovery(
                args.transaction_id, args.resolution_summary
            )
        elif args.command == "verify":
            result = manager.verify_current()
        else:
            result = manager.status()
    except (OSError, LifecycleError, subprocess.SubprocessError) as exc:
        print(f"release lifecycle: FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
