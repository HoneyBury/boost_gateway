"""Internal release deployment lifecycle implementation."""

from __future__ import annotations

from scripts.lib.release_deployment_core import *  # noqa: F403

class TransactionMixin:
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
