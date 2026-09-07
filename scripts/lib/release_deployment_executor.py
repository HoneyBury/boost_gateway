"""Internal release deployment lifecycle implementation."""

from __future__ import annotations

from scripts.lib.release_deployment_core import *  # noqa: F403
from scripts.lib.release_deployment_runtime import (
    legacy_production_network_evidence_is_complete,
)
from scripts.tools.check_release_compose import load_compose_document

TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"


def lifecycle_tool(name: str) -> Path:
    """Resolve a lifecycle helper from scripts/tools after the module split."""
    path = TOOLS_ROOT / name
    if not path.is_file():
        raise LifecycleError(f"lifecycle tool is missing: {path}")
    return path


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

    def verify_current_legacy_network_bridge(
        self, deployment_path: Path, summary_path: Path, timeout_seconds: float
    ) -> dict[str, Any]: ...

    def verify_read_only(
        self,
        deployment_path: Path,
        summary_path: Path,
        timeout_seconds: float,
        *,
        allow_legacy_redis_hardening_bridge: bool = False,
    ) -> dict[str, Any]: ...

    def runtime_status(self, deployment_path: Path) -> list[str]: ...

    def inactive_status(self) -> list[str]: ...


class SystemLifecycleExecutor(RuntimeStatusMixin):
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
        if environment.get("DOCKER_HOST") or environment.get("DOCKER_CONTEXT"):
            raise LifecycleError("remote Docker environment is forbidden")
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
        checker = lifecycle_tool("check_release_compose.py")
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
        tool = lifecycle_tool("prepare_redis_persistence_transition.py")
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
        allow_legacy_redis_hardening_bridge: bool = False,
        allow_legacy_production_network_bridge: bool = False,
    ) -> dict[str, Any]:
        verifier = lifecycle_tool("verify_release_deployment.py")
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
        if allow_legacy_redis_hardening_bridge:
            command.append("--allow-legacy-redis-hardening-bridge")
        if allow_legacy_production_network_bridge:
            if read_only:
                raise LifecycleError(
                    "legacy production network bridge requires full current verification"
                )
            try:
                current = self.layout.current.resolve(strict=True)
            except OSError as exc:
                raise LifecycleError(
                    f"cannot resolve current deployment for network bridge: {exc}"
                ) from exc
            if deployment_path.resolve() != current:
                raise LifecycleError(
                    "legacy production network bridge is restricted to current deployment"
                )
            command.append("--allow-legacy-production-network-bridge")
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
        if (
            allow_legacy_redis_hardening_bridge
            and summary.get("legacy_redis_hardening_bridge") is not True
        ):
            raise LifecycleError("legacy Redis hardening bridge was not validated")
        if (
            summary.get("legacy_production_network_bridge", False)
            is not allow_legacy_production_network_bridge
        ):
            raise LifecycleError(
                "legacy production network bridge result differs from request"
            )
        if (
            summary.get("legacy_production_network_bridge_requested", False)
            is not allow_legacy_production_network_bridge
        ):
            raise LifecycleError(
                "legacy production network bridge request evidence differs"
            )
        bridge_evidence = summary.get("legacy_production_network_evidence")
        if allow_legacy_production_network_bridge:
            if not legacy_production_network_evidence_is_complete(bridge_evidence):
                raise LifecycleError(
                    "legacy production network bridge evidence is incomplete"
                )
        elif bridge_evidence is not None:
            raise LifecycleError(
                "legacy production network bridge evidence was not requested"
            )
        return summary

    def verify(
        self, deployment_path: Path, summary_path: Path, timeout_seconds: float
    ) -> dict[str, Any]:
        return self._verify(
            deployment_path, summary_path, timeout_seconds, read_only=False
        )

    def verify_current_legacy_network_bridge(
        self, deployment_path: Path, summary_path: Path, timeout_seconds: float
    ) -> dict[str, Any]:
        return self._verify(
            deployment_path,
            summary_path,
            timeout_seconds,
            read_only=False,
            allow_legacy_production_network_bridge=True,
        )

    def verify_read_only(
        self,
        deployment_path: Path,
        summary_path: Path,
        timeout_seconds: float,
        *,
        allow_legacy_redis_hardening_bridge: bool = False,
    ) -> dict[str, Any]:
        return self._verify(
            deployment_path,
            summary_path,
            timeout_seconds,
            read_only=True,
            allow_legacy_redis_hardening_bridge=allow_legacy_redis_hardening_bridge,
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
