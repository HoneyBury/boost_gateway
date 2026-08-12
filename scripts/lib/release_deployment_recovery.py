"""Internal release deployment lifecycle implementation."""

from __future__ import annotations

from scripts.lib.release_deployment_core import *  # noqa: F403

class RecoveryMixin:
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
        expected_manifest = expected_deployment / "manifest.json"
        expected_compose = (
            expected_deployment / "deploy/operations/docker-compose.production.yml"
        )

        def same_governed_file(recorded: Any, expected: Path) -> bool:
            if not isinstance(recorded, str) or not recorded:
                return False
            observed = Path(recorded)
            try:
                return observed.is_file() and os.path.samefile(observed, expected)
            except OSError:
                return False

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
            or not same_governed_file(
                verification.get("staging_manifest"), expected_manifest
            )
            or not same_governed_file(
                verification.get("compose_file"), expected_compose
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
            raise LifecycleError(
                "manual deployment verification summary did not pass: "
                + json.dumps(
                    {
                        "overall_pass": verification.get("overall_pass"),
                        "source_build_performed": verification.get(
                            "source_build_performed"
                        ),
                        "public_conan_access_performed": verification.get(
                            "public_conan_access_performed"
                        ),
                        "staging_manifest": verification.get("staging_manifest"),
                        "expected_manifest": str(expected_manifest),
                        "compose_file": verification.get("compose_file"),
                        "expected_compose": str(expected_compose),
                        "failed": verification.get("failed"),
                    },
                    sort_keys=True,
                )
            )

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
        allow_legacy_redis_hardening_bridge: bool,
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
            "legacy_redis_hardening_bridge": allow_legacy_redis_hardening_bridge,
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
            or verification.get("legacy_redis_hardening_bridge")
            is not allow_legacy_redis_hardening_bridge
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
        allow_legacy_redis_hardening_bridge: bool,
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
            or summary.get("legacy_redis_hardening_bridge")
            is not allow_legacy_redis_hardening_bridge
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
