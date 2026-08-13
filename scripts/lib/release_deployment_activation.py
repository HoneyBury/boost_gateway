"""Internal release deployment lifecycle implementation."""

from __future__ import annotations

from scripts.lib.release_deployment_core import *  # noqa: F403
from scripts.lib.release_deployment_executor import lifecycle_lock

class ActivationMixin:
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
