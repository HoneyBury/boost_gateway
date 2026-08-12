"""Internal release deployment lifecycle implementation."""

from __future__ import annotations

from scripts.lib.release_deployment_core import *  # noqa: F403
from scripts.lib.release_deployment_executor import lifecycle_lock

class CommandsMixin:
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
        self,
        transaction_id: str,
        resolution_summary: Path,
        *,
        allow_legacy_redis_hardening_bridge: bool = False,
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
                        requested_transaction,
                        requested_record,
                        resolution_summary,
                        allow_legacy_redis_hardening_bridge,
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
                    allow_legacy_redis_hardening_bridge,
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
                allow_legacy_redis_hardening_bridge=allow_legacy_redis_hardening_bridge,
            )
            if (
                verification.get("overall_pass") is not True
                or verification.get("read_only_verification") is not True
                or verification.get("protected_state_mutated") is not False
                or verification.get("legacy_redis_hardening_bridge")
                is not allow_legacy_redis_hardening_bridge
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
                "legacy_redis_hardening_bridge": allow_legacy_redis_hardening_bridge,
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

