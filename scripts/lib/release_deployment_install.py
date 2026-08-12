"""Internal release deployment lifecycle implementation."""

from __future__ import annotations

from scripts.lib.release_deployment_core import *  # noqa: F403
from scripts.lib.release_deployment_executor import lifecycle_lock

class InstallMixin:
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
