"""Unit tests for the immutable release deployment lifecycle state machine."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from scripts.tools import manage_release_deployment as module


class FakeExecutor:
    def __init__(self, layout: module.Layout) -> None:
        self.layout = layout
        self.calls: list[tuple[str, str]] = []
        self.fail_verification_for: set[str] = set()
        self.blocked_transitions: set[tuple[str, str]] = set()
        self.runtime_failures_for: dict[str, list[str]] = {}

    def precheck(self, deployment_path: Path, timeout_seconds: float) -> None:
        self.calls.append(("precheck", deployment_path.name))

    def activate(self, deployment_path: Path, timeout_seconds: float) -> None:
        self.calls.append(("activate", deployment_path.name))

    def commit(self, deployment_path: Path, timeout_seconds: float) -> None:
        self.calls.append(("commit", deployment_path.name))
        source = deployment_path / "deploy/systemd/boost-gateway-compose.service"
        self.layout.unit_path.parent.mkdir(parents=True, exist_ok=True)
        module.shutil.copyfile(source, self.layout.unit_path)

    def uncommit(self, timeout_seconds: float) -> None:
        self.calls.append(("uncommit", ""))

    def deactivate(self, deployment_path: Path, timeout_seconds: float) -> None:
        self.calls.append(("deactivate", deployment_path.name))

    def prepare_transition(
        self,
        source_path: Path,
        target_path: Path,
        summary_path: Path,
        timeout_seconds: float,
    ) -> dict[str, Any] | None:
        self.calls.append(
            (
                f"transition:{summary_path.name}",
                f"{source_path.name}->{target_path.name}",
            )
        )
        if (source_path.name, target_path.name) in self.blocked_transitions:
            summary_path.write_text(
                '{"overall_pass": false, "aof_to_rdb_downgrade": true}\n',
                encoding="utf-8",
            )
            raise module.LifecycleError(
                "AOF-to-RDB transition requires a verified fresh checkpoint"
            )
        return None

    def verify(
        self, deployment_path: Path, summary_path: Path, timeout_seconds: float
    ) -> dict[str, Any]:
        self.calls.append((f"verify:{summary_path.name}", deployment_path.name))
        if deployment_path.name in self.fail_verification_for:
            self.fail_verification_for.remove(deployment_path.name)
            summary_path.write_text('{"overall_pass": false}\n', encoding="utf-8")
            raise module.LifecycleError("injected verification failure")
        summary_path.write_text('{"overall_pass": true}\n', encoding="utf-8")
        return {"overall_pass": True}

    def verify_read_only(
        self,
        deployment_path: Path,
        summary_path: Path,
        timeout_seconds: float,
        *,
        allow_legacy_redis_hardening_bridge: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            (f"verify-read-only:{summary_path.name}", deployment_path.name)
        )
        if deployment_path.name in self.fail_verification_for:
            self.fail_verification_for.remove(deployment_path.name)
            summary_path.write_text('{"overall_pass": false}\n', encoding="utf-8")
            raise module.LifecycleError("injected verification failure")
        result = {
            "overall_pass": True,
            "read_only_verification": True,
            "protected_state_mutated": False,
            "legacy_redis_hardening_bridge": allow_legacy_redis_hardening_bridge,
        }
        summary_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
        return result

    def runtime_status(self, deployment_path: Path) -> list[str]:
        self.calls.append(("status", deployment_path.name))
        return list(self.runtime_failures_for.get(deployment_path.name, []))

    def inactive_status(self) -> list[str]:
        return []


class ReleaseDeploymentManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.layout = module.Layout(
            root=root / "opt",
            transaction_root=root / "transactions",
            active_image_env=root / "etc/compose-images.env",
            secret_env=root / "etc/compose.env",
            unit_path=root / "etc/boost-gateway-compose.service",
        )
        self.executor = FakeExecutor(self.layout)
        self.identity = {
            "host": {
                "hostname": "operations-host",
                "host_id_sha256": "a" * 64,
                "boot_id": "boot-1",
                "os": {
                    "id": "ubuntu",
                    "version_id": "24.04",
                    "kernel_release": "test-kernel",
                },
                "architecture": "x86_64",
            },
            "operator": {"name": "release-operator", "uid": 1000, "source": "sudo"},
        }
        self.manager = module.ReleaseDeploymentManager(
            self.layout, self.executor, identity_provider=lambda: self.identity
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_release(self, tag: str, marker: str) -> tuple[Path, Path, Path, Path]:
        source = Path(self.temporary.name) / f"source-{tag}"
        for name in (
            "bin",
            "config",
            "deploy/runtime",
            "deploy/systemd",
            "deploy/operations",
            "env/monitoring",
            "env/redis",
            "scripts/lib",
            "scripts/tools",
        ):
            (source / name).mkdir(parents=True, exist_ok=True)
        (source / "config/settings.json").write_text(marker, encoding="utf-8")
        (source / "deploy/systemd/boost-gateway-compose.service").write_text(
            "[Service]\n", encoding="utf-8"
        )
        (source / "deploy/runtime/Dockerfile.gateway").write_text(
            "FROM scratch\n", encoding="utf-8"
        )
        (source / "deploy/operations/docker-compose.production.yml").write_text(
            "name: test\n", encoding="utf-8"
        )
        (source / "env/monitoring/prometheus.yml").write_text(
            "global: {}\n", encoding="utf-8"
        )
        (source / "env/redis/redis.production-validation.conf").write_text(
            "appendonly yes\nappendfsync everysec\n", encoding="utf-8"
        )
        (source / "scripts/tools/check_release_compose.py").write_text(
            "pass\n", encoding="utf-8"
        )
        (source / "scripts/lib/operations_identity.py").write_text(
            marker, encoding="utf-8"
        )
        (source / "bin/sdk_full_flow_client").write_text(marker, encoding="utf-8")
        config_digest = module.sha256_tree(source / "config")
        manifest = {
            "schema_version": 1,
            "tag": tag,
            "commit": marker[0] * 40,
            "platform": "linux-x64",
            "source_build_performed": False,
            "assets": {f"boost-gateway-{tag}-linux-x64.tar.gz": marker[0] * 64},
            "configuration": {"sha256": config_digest},
            "deployment_controller": {
                "dockerfiles_sha256": module.sha256_tree(source / "deploy/runtime"),
                "systemd_sha256": module.sha256_tree(source / "deploy/systemd"),
                "compose_sha256": module.sha256_file(
                    source / "deploy/operations/docker-compose.production.yml"
                ),
                "monitoring_sha256": module.sha256_tree(source / "env/monitoring"),
                "redis_sha256": module.sha256_tree(source / "env/redis"),
                "verification_tools_sha256": module.sha256_tree(
                    source / "scripts/tools"
                ),
                "verification_runtime_sha256": module.sha256_tree(source / "scripts"),
            },
            "binaries": [
                {
                    "name": "sdk_full_flow_client",
                    "sha256": module.sha256_file(source / "bin/sdk_full_flow_client"),
                }
            ],
        }
        (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        image_env = Path(self.temporary.name) / f"images-{tag}.env"
        images = {
            variable: f"sha256:{index:064x}"
            for index, variable in enumerate(
                sorted(module.IMAGE_VARIABLES), start=1 if tag == "v1.0.0" else 11
            )
        }
        image_env.write_bytes(module.render_image_environment(images))
        release_summary = Path(self.temporary.name) / f"release-{tag}.json"
        release_summary.write_text(
            json.dumps(
                {
                    "summary_version": 2,
                    "overall_pass": True,
                    "release": {
                        "tag": tag,
                        "commit": manifest["commit"],
                        "manifest_sha256": module.sha256_file(source / "manifest.json"),
                    },
                }
            ),
            encoding="utf-8",
        )
        expected_labels = {
            "org.opencontainers.image.version": tag,
            "org.opencontainers.image.revision": manifest["commit"],
            "io.boost-gateway.release.asset.sha256": marker[0] * 64,
            "io.boost-gateway.release.config.sha256": config_digest,
        }
        image_summary = Path(self.temporary.name) / f"image-{tag}.json"
        image_summary.write_text(
            json.dumps(
                {
                    "summary_version": 2,
                    "overall_pass": True,
                    "source_build_performed": False,
                    "network_enabled_during_build": False,
                    "target_platform": "linux/amd64",
                    "images": [
                        {
                            "service": service,
                            "image_id": images[variable],
                            "os": "linux",
                            "architecture": "amd64",
                            "labels": expected_labels,
                        }
                        for service, variable in module.IMAGE_VARIABLE_BY_SERVICE.items()
                    ],
                }
            ),
            encoding="utf-8",
        )
        return source, image_env, release_summary, image_summary

    def install(self, tag: str, marker: str) -> str:
        source, image_env, release_summary, image_summary = self.make_release(
            tag, marker
        )
        return str(
            self.manager.install(
                source, image_env, release_summary, image_summary, None
            )["deployment_id"]
        )

    def make_blocking_manual_recovery(
        self, current: str
    ) -> tuple[Path, dict[str, Any]]:
        candidate = self.install("v1.0.1", "b")
        transaction, record = self.manager._transaction(
            "upgrade",
            candidate=candidate,
            from_current=current,
            from_previous=None,
        )
        record.update(
            {
                "status": "recovery_failed",
                "completed_at": "2026-07-28T06:45:00Z",
                "failure": "candidate activation failed",
                "recovery_failure": "previous recovery failed",
            }
        )
        self.manager._write_transaction_record(transaction, record)

        active_volume = "boost-gateway-production-redis-data"
        status_path = transaction / module.MANUAL_RECOVERY_STATUS
        verification_path = transaction / module.MANUAL_RECOVERY_VERIFICATION
        equivalence_path = transaction / module.MANUAL_RECOVERY_EQUIVALENCE
        transition_path = transaction / module.MANUAL_RECOVERY_TRANSITION
        module.atomic_write_json(
            status_path,
            {
                "schema_version": 1,
                "overall_pass": True,
                "current": current,
                "failures": [],
                "lifecycle_blocker_preserved": True,
                "secret_material_recorded": False,
            },
        )
        module.atomic_write_json(
            verification_path,
            {
                "summary_version": 2,
                "overall_pass": True,
                "source_build_performed": False,
                "public_conan_access_performed": False,
                "staging_manifest": str(
                    self.layout.deployments / current / "manifest.json"
                ),
                "compose_file": str(
                    (
                        self.layout.deployments
                        / current
                        / "deploy/operations/docker-compose.production.yml"
                    ).resolve()
                ),
                "checks": [
                    {"name": name, "passed": True}
                    for name in (
                        "compose-service-state",
                        "container-image-identities",
                        "redis-ping",
                        "release-sdk-full-flow",
                    )
                ],
                "failed": [],
            },
        )
        canonical = "d" * 64
        module.atomic_write_json(
            equivalence_path,
            {
                "schema_version": 1,
                "overall_pass": True,
                "transaction_id": transaction.name,
                "source_volume": active_volume,
                "source_volume_mounted_readonly": True,
                "redis_image": "sha256:" + "e" * 64,
                "rdb_canonical_sha256": canonical,
                "aof_canonical_sha256": canonical,
                "rdb_key_count": 5,
                "aof_key_count": 5,
                "key_sets_equal": True,
                "required_keys_present": True,
                "production_switched": False,
                "production_volume_mutated": False,
                "secret_material_recorded": False,
                "formal_todo0012_claim": False,
            },
        )
        module.atomic_write_json(
            transition_path,
            {
                "overall_pass": True,
                "source_mode": "aof_everysec_rdb",
                "target_mode": "rdb_only",
                "checkpoint_required": True,
                "checkpoint_verified": True,
                "writes_frozen": True,
                "checkpoint": {
                    "rdb_changes_since_last_save": 0,
                    "rdb_last_bgsave_status": "ok",
                    "redis_check_rdb": True,
                    "rdb_sha256": "c" * 64,
                },
                "active_volume": {
                    "name": active_volume,
                    "destination": "/data",
                    "read_write": True,
                },
                "secret_material_recorded": False,
            },
        )

        def backup_summary(path: Path, backup_id: str) -> None:
            module.atomic_write_json(
                path,
                {
                    "manifest": {
                        "backup_id": backup_id,
                        "consistent_redis_snapshot": True,
                        "encrypted_before_transfer": True,
                        "secret_material_recorded": False,
                    },
                    "remote_receipt": {
                        "backup_id": backup_id,
                        "create_only": True,
                        "remote_readback_sha256": True,
                        "stored_at": "2026-07-28T08:00:00Z",
                        "secret_material_recorded": False,
                    },
                },
            )

        pre_backup = Path(self.temporary.name) / "pre-merge-backup-summary.json"
        post_backup = Path(self.temporary.name) / "post-merge-backup-summary.json"
        backup_summary(pre_backup, "todo0012-linkfree-pre")
        backup_summary(post_backup, "todo0012-linkfree-post")
        payload = {"scores": {"alice": "100"}, "next_seq": 96}
        payload_sha256 = module.hashlib.sha256(
            (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        ).hexdigest()
        plan = Path(self.temporary.name) / "todo0012-pre-aof-merge-plan.json"
        application = (
            Path(self.temporary.name) / "todo0012-pre-aof-merge-application.json"
        )
        merge_verification = (
            Path(self.temporary.name)
            / "todo0012-pre-aof-merge-deployment-verification.json"
        )
        pre_canonical = "4" * 64
        merged_canonical = "8" * 64
        module.atomic_write_json(
            plan,
            {
                "schema_version": 1,
                "overall_pass": True,
                "operation": "prepare-pre-aof-state-merge",
                "payload": payload,
                "payload_sha256": payload_sha256,
                "current_canonical_sha256": pre_canonical,
                "merged_canonical_sha256": merged_canonical,
                "production_mutated": False,
                "production_volume_deleted": False,
                "secret_material_recorded": False,
                "formal_todo0012_claim": False,
            },
        )
        pre_backup_reference = {
            "backup_id": "todo0012-linkfree-pre",
            "summary_path": str(pre_backup),
            "summary_sha256": module.sha256_file(pre_backup),
        }
        post_backup_reference = {
            "backup_id": "todo0012-linkfree-post",
            "summary_path": str(post_backup),
            "summary_sha256": module.sha256_file(post_backup),
        }
        module.atomic_write_json(
            application,
            {
                "schema_version": 1,
                "overall_pass": True,
                "operation": "apply-pre-aof-state-merge",
                "plan_sha256": module.sha256_file(plan),
                "payload_sha256": payload_sha256,
                "pre_merge_canonical_sha256": pre_canonical,
                "merged_canonical_sha256": merged_canonical,
                "pre_merge_backup": pre_backup_reference,
                "checkpoint": {
                    "rdb_changes_since_last_save": 0,
                    "redis_check_rdb": True,
                    "rdb_sha256": "3" * 64,
                },
                "production_volume_deleted": False,
                "secret_material_recorded": False,
                "formal_todo0012_claim": False,
            },
        )
        module.atomic_write_json(
            merge_verification,
            {
                "overall_pass": True,
                "source_build_performed": False,
                "public_conan_access_performed": False,
                "staging_manifest": str(
                    self.layout.deployments / current / "manifest.json"
                ),
                "checks": [{"name": "release-sdk-full-flow", "passed": True}],
                "failed": [],
            },
        )
        resolution = Path(self.temporary.name) / "todo0012-pre-aof-merge-recovery.json"
        module.atomic_write_json(
            resolution,
            {
                "schema_version": 1,
                "overall_pass": True,
                "operation": "recover-pre-aof-state-with-post-activation-writes",
                "recorded_at": "2026-07-28T08:16:58Z",
                "current": current,
                "active_volume": active_volume,
                "lifecycle_blocker_preserved": True,
                "production_volume_deleted": False,
                "aof_quarantine_deleted": False,
                "pre_merge_canonical_sha256": pre_canonical,
                "merged_canonical_sha256": merged_canonical,
                "payload_sha256": payload_sha256,
                "plan_sha256": module.sha256_file(plan),
                "application_sha256": module.sha256_file(application),
                "verification_sha256": module.sha256_file(merge_verification),
                "preservation": {
                    "passed": True,
                    "missing_names": [],
                    "missing_scores": [],
                    "changed_names": [],
                    "changed_scores": [],
                    "missing_events": {
                        "events_by_type": 0,
                        "events_global": 0,
                    },
                    "next_seq": 102,
                },
                "pre_merge_backup": pre_backup_reference,
                "post_merge_backup": post_backup_reference,
                "secret_material_recorded": False,
                "formal_todo0012_claim": False,
            },
        )
        self.recovery_resolution = resolution
        manual = {
            "schema_version": 1,
            "overall_pass": True,
            "operation": "manual-recovery-after-aof-activation-recovery-failure",
            "transaction_id": transaction.name,
            "current": current,
            "active_volume": active_volume,
            "active_volume_preserved": True,
            "rdb_sha256": "f" * 64,
            "rdb_aof_canonical_equivalence_verified": True,
            "rdb_aof_equivalence_sha256": module.sha256_file(equivalence_path),
            "redis_image": "sha256:" + "e" * 64,
            "appendonly": "no",
            "aof_quarantine": f"appendonlydir.recovery-failed-{transaction.name}",
            "aof_manifest_sha256": "9" * 64,
            "aof_files_deleted": False,
            "rdb_files_deleted": False,
            "production_volume_deleted": False,
            "lifecycle_blocker_preserved": True,
            "transaction_record_mutated": False,
            "status_sha256": module.sha256_file(status_path),
            "verification_sha256": module.sha256_file(verification_path),
            "secret_material_recorded": False,
            "formal_todo0012_claim": False,
        }
        module.atomic_write_json(transaction / module.MANUAL_RECOVERY_SUMMARY, manual)
        self.manager._write_transaction_record(transaction, record)
        return transaction, record

    def test_install_is_idempotent_and_keeps_versioned_inputs(self) -> None:
        source, image_env, release_summary, image_summary = self.make_release(
            "v1.0.0", "a"
        )
        first = self.manager.install(
            source, image_env, release_summary, image_summary, None
        )
        sentinel = self.layout.transaction_root / "evidence-sentinel"
        sentinel.write_text("keep", encoding="utf-8")
        second = self.manager.install(
            source, image_env, release_summary, image_summary, None
        )

        self.assertEqual(first, second)
        deployment = self.layout.deployments / str(first["deployment_id"])
        self.assertTrue((deployment / "release").is_symlink())
        self.assertTrue((deployment / "compose-images.env").is_file())
        self.assertTrue((deployment / "configuration-snapshot").is_dir())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertEqual(first["host"], self.identity["host"])
        self.assertEqual(first["operator"], self.identity["operator"])
        self.assertEqual(first["result"]["status"], "installed")
        self.assertTrue(first["result"]["overall_pass"])

    def test_install_rejects_verification_runtime_digest_drift(self) -> None:
        source, image_env, release_summary, image_summary = self.make_release(
            "v1.0.0", "a"
        )
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        manifest["deployment_controller"]["verification_runtime_sha256"] = "0" * 64
        (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        summary = json.loads(release_summary.read_text(encoding="utf-8"))
        summary["release"]["manifest_sha256"] = module.sha256_file(
            source / "manifest.json"
        )
        release_summary.write_text(json.dumps(summary), encoding="utf-8")

        with self.assertRaisesRegex(
            module.LifecycleError, "verification_runtime_sha256"
        ):
            self.manager.install(
                source, image_env, release_summary, image_summary, None
            )

    def test_deployment_id_binds_complete_verification_runtime(self) -> None:
        source, image_env, release_summary, image_summary = self.make_release(
            "v1.0.0", "a"
        )
        first = self.manager.install(
            source, image_env, release_summary, image_summary, None
        )
        (source / "scripts/lib/operations_identity.py").write_text(
            "changed", encoding="utf-8"
        )
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        manifest["deployment_controller"]["verification_runtime_sha256"] = (
            module.sha256_tree(source / "scripts")
        )
        (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        summary = json.loads(release_summary.read_text(encoding="utf-8"))
        summary["release"]["manifest_sha256"] = module.sha256_file(
            source / "manifest.json"
        )
        release_summary.write_text(json.dumps(summary), encoding="utf-8")

        second = self.manager.install(
            source, image_env, release_summary, image_summary, None
        )

        self.assertNotEqual(first["deployment_id"], second["deployment_id"])

    def test_idempotent_install_backfills_only_missing_identity(self) -> None:
        source, image_env, release_summary, image_summary = self.make_release(
            "v1.0.0", "a"
        )
        first = self.manager.install(
            source, image_env, release_summary, image_summary, None
        )
        record_path = (
            self.layout.deployments / str(first["deployment_id"]) / "record.json"
        )
        legacy = json.loads(record_path.read_text(encoding="utf-8"))
        legacy.pop("host")
        legacy.pop("operator")
        legacy.pop("result")
        module.atomic_write_json(record_path, legacy)

        backfilled = self.manager.install(
            source, image_env, release_summary, image_summary, None
        )
        self.assertEqual(backfilled["host"], self.identity["host"])
        self.assertEqual(backfilled["operator"], self.identity["operator"])
        self.assertEqual(backfilled["result"]["operation"], "install")

        original = json.loads(json.dumps(backfilled))
        self.manager.identity_provider = lambda: (_ for _ in ()).throw(
            AssertionError("complete identity must not be collected again")
        )
        repeated = self.manager.install(
            source, image_env, release_summary, image_summary, None
        )
        self.assertEqual(repeated["host"], original["host"])
        self.assertEqual(repeated["operator"], original["operator"])

    def test_install_ignores_and_does_not_copy_python_bytecode_cache(self) -> None:
        source, image_env, release_summary, image_summary = self.make_release(
            "v1.0.0", "a"
        )
        cache = (
            source
            / "scripts"
            / "tools"
            / "__pycache__"
            / "check_release_compose.cpython-312.pyc"
        )
        cache.parent.mkdir()
        cache.write_bytes(b"runtime cache")

        record = self.manager.install(
            source, image_env, release_summary, image_summary, None
        )

        installed = self.layout.releases / str(record["deployment_id"])
        self.assertFalse((installed / "scripts" / "tools" / "__pycache__").exists())

    def test_install_recovers_release_only_interrupted_commit(self) -> None:
        source, image_env, release_summary, image_summary = self.make_release(
            "v1.0.0", "a"
        )
        first = self.manager.install(
            source, image_env, release_summary, image_summary, None
        )
        deployment = self.layout.deployments / str(first["deployment_id"])
        module.shutil.rmtree(deployment)

        recovered = self.manager.install(
            source, image_env, release_summary, image_summary, None
        )

        self.assertEqual(recovered["deployment_id"], first["deployment_id"])
        self.assertTrue(deployment.is_dir())

    def test_upgrade_and_explicit_rollback_swap_current_and_previous(self) -> None:
        first = self.install("v1.0.0", "a")
        second = self.install("v1.0.1", "b")
        self.manager.deploy(first)
        before_upgrade = len(self.executor.calls)
        self.manager.upgrade(second)
        self.assertEqual(self.layout.current.resolve().name, second)
        self.assertEqual(self.layout.previous.resolve().name, first)
        upgrade_calls = self.executor.calls[before_upgrade:]
        self.assertLess(
            upgrade_calls.index(("activate", second)),
            upgrade_calls.index(
                ("verify:deployment-verification-summary.json", second)
            ),
        )
        self.assertLess(
            upgrade_calls.index(
                ("verify:deployment-verification-summary.json", second)
            ),
            upgrade_calls.index(("commit", second)),
        )
        self.assertIn(
            (
                "transition:candidate-persistence-transition-summary.json",
                f"{first}->{second}",
            ),
            upgrade_calls,
        )

        result = self.manager.rollback()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["result"]["status"], "passed")
        self.assertTrue(result["result"]["overall_pass"])
        self.assertEqual(
            {item["kind"] for item in result["result"]["summaries"]},
            {"deployment"},
        )
        self.assertLessEqual(result["elapsed_seconds"], 600)
        self.assertEqual(self.layout.current.resolve().name, first)
        self.assertEqual(self.layout.previous.resolve().name, second)
        self.assertIn(
            (
                "transition:candidate-persistence-transition-summary.json",
                f"{second}->{first}",
            ),
            self.executor.calls,
        )

    def test_system_executor_requires_checkpoint_tool_for_both_mode_changes(
        self,
    ) -> None:
        profile = Path(self.temporary.name) / "redis.conf"
        profile.write_text("appendonly yes\nappendfsync everysec\n", encoding="ascii")
        aof_document = {
            "services": {
                "redis": {
                    "command": ["redis-server", "/etc/redis/redis.conf"],
                    "volumes": [
                        {
                            "type": "bind",
                            "source": str(profile),
                            "target": "/etc/redis/redis.conf",
                            "read_only": True,
                        }
                    ],
                }
            }
        }
        rdb_document = {
            "services": {"redis": {"command": ["redis-server", "--appendonly", "no"]}}
        }
        for source_mode, target_mode, source_document, target_document in (
            ("rdb_only", "aof_everysec_rdb", rdb_document, aof_document),
            ("aof_everysec_rdb", "rdb_only", aof_document, rdb_document),
        ):
            with self.subTest(source=source_mode, target=target_mode):
                source = Path(self.temporary.name) / f"source-{source_mode}"
                target = Path(self.temporary.name) / f"target-{target_mode}"
                summary = Path(self.temporary.name) / f"{source_mode}-transition.json"
                executor = module.SystemLifecycleExecutor(self.layout)

                def run_checkpoint(
                    command: list[str],
                    timeout_seconds: float,
                    *,
                    environment: dict[str, str] | None = None,
                ) -> mock.Mock:
                    self.assertIn("prepare_redis_persistence_transition.py", command[1])
                    self.assertIn(source_mode, command)
                    self.assertIn(target_mode, command)
                    payload: dict[str, Any] = {
                        "overall_pass": True,
                        "source_mode": source_mode,
                        "target_mode": target_mode,
                        "checkpoint_verified": True,
                        "runtime_already_target": False,
                        "aof_directory_transition": {
                            "action": (
                                "absent"
                                if source_mode == "rdb_only"
                                else "entrypoint-readable"
                            ),
                            "mode": "0755" if source_mode != "rdb_only" else None,
                            "files_deleted": False,
                        },
                        "aof_seed": None,
                        "secret_material_recorded": False,
                    }
                    if source_mode == "rdb_only":
                        payload["aof_seed"] = {
                            "method": "runtime-config-set-and-rewrite",
                            "source": "active-rdb-keyspace",
                            "key_count_before": 5,
                            "key_count_after": 5,
                            "manifest_sha256": "8" * 64,
                            "effective_config": {
                                "appendonly": "yes",
                                "appendfsync": "everysec",
                            },
                            "files_deleted": False,
                        }
                    else:
                        payload["aof_directory_transition"]["manifest_sha256"] = (
                            "9" * 64
                        )
                    summary.write_text(
                        json.dumps(payload),
                        encoding="utf-8",
                    )
                    return mock.Mock(returncode=0, stdout="", stderr="")

                with (
                    mock.patch.object(executor, "_environment", return_value={}),
                    mock.patch.object(executor, "_run", side_effect=run_checkpoint),
                    mock.patch.object(
                        module,
                        "load_compose_document",
                        side_effect=[source_document, target_document],
                    ),
                ):
                    result = executor.prepare_transition(source, target, summary, 60.0)

                self.assertTrue(result["overall_pass"])
                self.assertTrue(result["checkpoint_verified"])

    def test_system_executor_rejects_missing_rdb_to_aof_seed_evidence(self) -> None:
        source = Path(self.temporary.name) / "source-rdb"
        target = Path(self.temporary.name) / "target-aof"
        summary = Path(self.temporary.name) / "missing-seed.json"
        executor = module.SystemLifecycleExecutor(self.layout)
        documents = [
            {
                "services": {
                    "redis": {"command": ["redis-server", "--appendonly", "no"]}
                }
            },
            {
                "services": {
                    "redis": {
                        "command": [
                            "redis-server",
                            "--appendonly",
                            "yes",
                            "--appendfsync",
                            "everysec",
                        ]
                    }
                }
            },
        ]

        def run_checkpoint(*args: Any, **kwargs: Any) -> mock.Mock:
            summary.write_text(
                json.dumps(
                    {
                        "overall_pass": True,
                        "source_mode": "rdb_only",
                        "target_mode": "aof_everysec_rdb",
                        "checkpoint_verified": True,
                        "runtime_already_target": False,
                        "aof_directory_transition": {
                            "action": "absent",
                            "files_deleted": False,
                        },
                        "aof_seed": None,
                        "secret_material_recorded": False,
                    }
                ),
                encoding="utf-8",
            )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(executor, "_environment", return_value={}),
            mock.patch.object(executor, "_run", side_effect=run_checkpoint),
            mock.patch.object(module, "load_compose_document", side_effect=documents),
            self.assertRaisesRegex(module.LifecycleError, "seed evidence"),
        ):
            executor.prepare_transition(source, target, summary, 60.0)

    def test_system_executor_read_only_verification_is_explicit(self) -> None:
        deployment = Path(self.temporary.name) / "read-only-deployment"
        summary = Path(self.temporary.name) / "read-only-verification.json"
        executor = module.SystemLifecycleExecutor(self.layout)

        def run_verifier(
            command: list[str],
            timeout_seconds: float,
            *,
            environment: dict[str, str] | None = None,
        ) -> mock.Mock:
            self.assertIn("--read-only", command)
            self.assertIn("--allow-legacy-redis-hardening-bridge", command)
            summary.write_text(
                json.dumps(
                    {
                        "overall_pass": True,
                        "read_only_verification": True,
                        "protected_state_mutated": False,
                        "legacy_redis_hardening_bridge": True,
                    }
                ),
                encoding="utf-8",
            )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(executor, "_environment", return_value={}),
            mock.patch.object(executor, "_run", side_effect=run_verifier),
        ):
            result = executor.verify_read_only(
                deployment,
                summary,
                60.0,
                allow_legacy_redis_hardening_bridge=True,
            )

        self.assertTrue(result["read_only_verification"])
        self.assertFalse(result["protected_state_mutated"])
        self.assertTrue(result["legacy_redis_hardening_bridge"])

    def test_upgrade_rejects_release_that_changes_host_unit(self) -> None:
        first = self.install("v1.0.0", "a")
        source, image_env, release_summary, image_summary = self.make_release(
            "v1.0.1", "b"
        )
        (source / "deploy/systemd/boost-gateway-compose.service").write_text(
            "[Service]\nExecStart=/bin/false\n", encoding="utf-8"
        )
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        manifest["deployment_controller"]["systemd_sha256"] = module.sha256_tree(
            source / "deploy/systemd"
        )
        (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        summary = json.loads(release_summary.read_text(encoding="utf-8"))
        summary["release"]["manifest_sha256"] = module.sha256_file(
            source / "manifest.json"
        )
        release_summary.write_text(json.dumps(summary), encoding="utf-8")
        second = str(
            self.manager.install(
                source, image_env, release_summary, image_summary, None
            )["deployment_id"]
        )
        self.manager.deploy(first)

        with self.assertRaisesRegex(module.LifecycleError, "host Compose unit"):
            self.manager.upgrade(second)

    def test_failed_upgrade_restores_previous_without_overwriting_failure(self) -> None:
        first = self.install("v1.0.0", "a")
        second = self.install("v1.0.1", "b")
        self.manager.deploy(first)
        self.executor.fail_verification_for.add(second)

        with self.assertRaisesRegex(
            module.LifecycleError, "previous deployment restored"
        ):
            self.manager.upgrade(second)

        self.assertEqual(self.layout.current.resolve().name, first)
        records = sorted(self.layout.transaction_root.glob("*/record.json"))
        record = json.loads(records[-1].read_text(encoding="utf-8"))
        transaction = records[-1].parent
        self.assertEqual(record["status"], "rolled_back")
        self.assertTrue(
            (transaction / "deployment-verification-summary.json").is_file()
        )
        self.assertTrue((transaction / "recovery-verification-summary.json").is_file())
        self.assertEqual(record["host"], self.identity["host"])
        self.assertEqual(record["operator"], self.identity["operator"])
        self.assertEqual(record["result"]["status"], "rolled_back")
        self.assertTrue(record["result"]["completed"])
        self.assertFalse(record["result"]["overall_pass"])
        references = {item["kind"]: item for item in record["result"]["summaries"]}
        self.assertEqual(set(references), {"deployment", "recovery"})
        for item in references.values():
            path = Path(item["path"])
            self.assertEqual(item["sha256"], module.sha256_file(path))
            self.assertEqual(item["size_bytes"], path.stat().st_size)
        self.assertIn(
            (
                "transition:recovery-persistence-transition-summary.json",
                f"{second}->{first}",
            ),
            self.executor.calls,
        )

    def test_failed_upgrade_does_not_blindly_restore_rdb_after_aof_activation(
        self,
    ) -> None:
        first = self.install("v1.0.0", "a")
        second = self.install("v1.0.1", "b")
        self.manager.deploy(first)
        self.executor.fail_verification_for.add(second)
        self.executor.blocked_transitions.add((second, first))

        with self.assertRaisesRegex(
            module.LifecycleError, "previous recovery failed.*verified fresh checkpoint"
        ):
            self.manager.upgrade(second)

        records = sorted(self.layout.transaction_root.glob("*/record.json"))
        record = json.loads(records[-1].read_text(encoding="utf-8"))
        transition = records[-1].parent / "recovery-persistence-transition-summary.json"
        self.assertEqual(record["status"], "recovery_failed")
        self.assertTrue(transition.is_file())
        self.assertIn("verified fresh checkpoint", record["recovery_failure"])
        self.assertEqual(self.layout.current.resolve().name, first)
        self.assertNotIn(("activate", first), self.executor.calls[-3:])

    def test_next_command_reconciles_interrupted_candidate_activation(self) -> None:
        first = self.install("v1.0.0", "a")
        second = self.install("v1.0.1", "b")
        self.manager.deploy(first)
        transaction, record = self.manager._transaction(
            "upgrade", candidate=second, from_current=first, from_previous=None
        )
        record["status"] = "candidate_activated"
        record.pop("host")
        record.pop("operator")
        record.pop("result")
        module.atomic_write_json(transaction / "record.json", record)

        status = self.manager.status()

        reconciled = json.loads(
            (transaction / "record.json").read_text(encoding="utf-8")
        )
        self.assertTrue(status["overall_pass"])
        self.assertEqual(reconciled["status"], "interrupted_rolled_back")
        self.assertEqual(reconciled["host"], self.identity["host"])
        self.assertEqual(reconciled["operator"], self.identity["operator"])
        self.assertEqual(reconciled["result"]["status"], "interrupted_rolled_back")
        self.assertEqual(self.layout.current.resolve().name, first)

    def test_reconcile_failure_on_committed_candidate_restores_previous(self) -> None:
        first = self.install("v1.0.0", "a")
        second = self.install("v1.0.1", "b")
        original_previous = self.install("v1.0.2", "c")
        self.manager.deploy(first)
        self.manager._update_deployment(second, status="verified")
        self.manager._update_deployment(original_previous, status="verified")
        self.manager._atomic_link(original_previous, self.layout.previous)
        transaction, record = self.manager._transaction(
            "upgrade",
            candidate=second,
            from_current=first,
            from_previous=original_previous,
        )
        record["status"] = "candidate_verified"
        module.atomic_write_json(transaction / "record.json", record)
        self.manager._activate_files(second)
        self.executor.fail_verification_for.add(second)

        status = self.manager.status()

        reconciled = json.loads(
            (transaction / "record.json").read_text(encoding="utf-8")
        )
        self.assertTrue(status["overall_pass"])
        self.assertEqual(reconciled["status"], "interrupted_rolled_back")
        self.assertEqual(
            {item["kind"] for item in reconciled["result"]["summaries"]},
            {"recovery", "reconcile"},
        )
        self.assertEqual(self.layout.current.resolve().name, first)
        self.assertEqual(self.layout.previous.resolve().name, original_previous)

    def test_recovery_failed_transaction_blocks_new_lifecycle_work(self) -> None:
        deployment_id = self.install("v1.0.0", "a")
        transaction, record = self.manager._transaction(
            "deploy", candidate=deployment_id, from_current=None
        )
        record["status"] = "recovery_failed"
        module.atomic_write_json(transaction / "record.json", record)

        with self.assertRaisesRegex(module.LifecycleError, "blocks lifecycle"):
            self.manager.status()

    def test_reconcile_recovery_closes_unique_blocker_after_fresh_verification(
        self,
    ) -> None:
        current = self.install("v1.0.0", "a")
        self.manager.deploy(current)
        transaction, original = self.make_blocking_manual_recovery(current)

        result = self.manager.reconcile_recovery(
            transaction.name, self.recovery_resolution
        )

        record = json.loads((transaction / "record.json").read_text(encoding="utf-8"))
        self.assertTrue(result["overall_pass"])
        self.assertEqual("recovery_reconciled", record["status"])
        self.assertEqual("recovery_failed", record["reconciled_from_status"])
        self.assertEqual(current, record["current"])
        self.assertEqual(original["failure"], record["failure"])
        self.assertEqual(original["recovery_failure"], record["recovery_failure"])
        self.assertFalse(record["manual_recovery_transaction_record_mutated"])
        self.assertFalse(record["result"]["overall_pass"])
        self.assertEqual(
            {"manual_recovery", "manual_recovery_reconcile"},
            {
                item["kind"]
                for item in record["result"]["summaries"]
                if item["kind"].startswith("manual_recovery")
            },
        )
        self.assertTrue(
            (transaction / module.MANUAL_RECOVERY_RECONCILE_SUMMARY).is_file()
        )
        self.assertTrue(
            any(
                call[0] == "status" and call[1] == current
                for call in self.executor.calls
            )
        )
        self.assertTrue(
            any(
                call[0].startswith(
                    "verify-read-only:deployment-verification-summary.json"
                )
                and call[1] == current
                for call in self.executor.calls
            )
        )
        self.assertTrue(self.manager.status()["overall_pass"])

        repeated = self.manager.reconcile_recovery(
            transaction.name, self.recovery_resolution
        )
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(result["reconcile_summary"], repeated["reconcile_summary"])

    def test_reconcile_recovery_rejects_unbound_transition_digest(self) -> None:
        current = self.install("v1.0.0", "a")
        self.manager.deploy(current)
        transaction, _ = self.make_blocking_manual_recovery(current)
        transition = transaction / module.MANUAL_RECOVERY_TRANSITION
        document = json.loads(transition.read_text(encoding="utf-8"))
        document["checkpoint"]["rdb_sha256"] = "1" * 64
        module.atomic_write_json(transition, document)

        with self.assertRaisesRegex(module.LifecycleError, "blocking record"):
            self.manager.reconcile_recovery(transaction.name, self.recovery_resolution)

    def test_reconcile_recovery_rejects_tampered_merge_plan(self) -> None:
        current = self.install("v1.0.0", "a")
        self.manager.deploy(current)
        transaction, _ = self.make_blocking_manual_recovery(current)
        plan = self.recovery_resolution.parent / "todo0012-pre-aof-merge-plan.json"
        document = json.loads(plan.read_text(encoding="utf-8"))
        document["payload"]["scores"]["alice"] = "999"
        module.atomic_write_json(plan, document)

        with self.assertRaisesRegex(module.LifecycleError, "plan binding"):
            self.manager.reconcile_recovery(transaction.name, self.recovery_resolution)

    def test_reconcile_recovery_rejects_resolution_symlink(self) -> None:
        current = self.install("v1.0.0", "a")
        self.manager.deploy(current)
        transaction, _ = self.make_blocking_manual_recovery(current)
        link = Path(self.temporary.name) / "resolution-link.json"
        link.symlink_to(self.recovery_resolution)

        with self.assertRaisesRegex(module.LifecycleError, "regular file"):
            self.manager.reconcile_recovery(transaction.name, link)

    def test_reconcile_recovery_rejects_tampered_manual_evidence(self) -> None:
        current = self.install("v1.0.0", "a")
        self.manager.deploy(current)
        transaction, _ = self.make_blocking_manual_recovery(current)
        module.atomic_write_json(
            transaction / module.MANUAL_RECOVERY_STATUS,
            {
                "schema_version": 1,
                "overall_pass": True,
                "current": current,
                "failures": [],
                "lifecycle_blocker_preserved": False,
                "secret_material_recorded": False,
            },
        )

        with self.assertRaisesRegex(module.LifecycleError, "digest binding"):
            self.manager.reconcile_recovery(transaction.name, self.recovery_resolution)

        record = json.loads((transaction / "record.json").read_text(encoding="utf-8"))
        self.assertEqual("recovery_failed", record["status"])
        self.assertFalse(
            (transaction / module.MANUAL_RECOVERY_RECONCILE_SUMMARY).exists()
        )

    def test_reconcile_recovery_requires_the_unique_blocking_transaction(self) -> None:
        current = self.install("v1.0.0", "a")
        self.manager.deploy(current)
        transaction, _ = self.make_blocking_manual_recovery(current)
        other, record = self.manager._transaction(
            "upgrade", candidate="candidate-two", from_current=current
        )
        record["status"] = "recovery_failed"
        self.manager._write_transaction_record(other, record)

        with self.assertRaisesRegex(module.LifecycleError, "exactly one"):
            self.manager.reconcile_recovery(transaction.name, self.recovery_resolution)

        for path in (transaction, other):
            blocked = json.loads((path / "record.json").read_text(encoding="utf-8"))
            self.assertEqual("recovery_failed", blocked["status"])

    def test_reconcile_recovery_rejects_current_source_drift(self) -> None:
        current = self.install("v1.0.0", "a")
        self.manager.deploy(current)
        transaction, _ = self.make_blocking_manual_recovery(current)
        record = json.loads((transaction / "record.json").read_text(encoding="utf-8"))
        record["from_current"] = "different-deployment"
        self.manager._write_transaction_record(transaction, record)

        with self.assertRaisesRegex(module.LifecycleError, "current deployment differ"):
            self.manager.reconcile_recovery(transaction.name, self.recovery_resolution)

        blocked = json.loads((transaction / "record.json").read_text(encoding="utf-8"))
        self.assertEqual("recovery_failed", blocked["status"])

    def test_reconcile_recovery_runtime_failure_preserves_blocker(self) -> None:
        current = self.install("v1.0.0", "a")
        self.manager.deploy(current)
        transaction, _ = self.make_blocking_manual_recovery(current)
        self.executor.runtime_failures_for[current] = ["redis is not healthy"]

        with self.assertRaisesRegex(module.LifecycleError, "runtime status"):
            self.manager.reconcile_recovery(transaction.name, self.recovery_resolution)

        record = json.loads((transaction / "record.json").read_text(encoding="utf-8"))
        self.assertEqual("recovery_failed", record["status"])
        attempts = list((transaction / "reconcile-attempts").iterdir())
        self.assertEqual(1, len(attempts))
        runtime = json.loads(
            (attempts[0] / "runtime-status-summary.json").read_text(encoding="utf-8")
        )
        self.assertFalse(runtime["overall_pass"])
        self.assertFalse(
            (transaction / module.MANUAL_RECOVERY_RECONCILE_SUMMARY).exists()
        )

    def test_reconcile_recovery_verification_failure_preserves_blocker(self) -> None:
        current = self.install("v1.0.0", "a")
        self.manager.deploy(current)
        transaction, _ = self.make_blocking_manual_recovery(current)
        self.executor.fail_verification_for.add(current)

        with self.assertRaisesRegex(module.LifecycleError, "verification failure"):
            self.manager.reconcile_recovery(transaction.name, self.recovery_resolution)

        record = json.loads((transaction / "record.json").read_text(encoding="utf-8"))
        self.assertEqual("recovery_failed", record["status"])
        attempts = list((transaction / "reconcile-attempts").iterdir())
        self.assertEqual(1, len(attempts))
        verification = json.loads(
            (attempts[0] / "deployment-verification-summary.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(verification["overall_pass"])
        self.assertFalse(
            (transaction / module.MANUAL_RECOVERY_RECONCILE_SUMMARY).exists()
        )

    def test_reconcile_recovery_resumes_after_final_summary_before_record_update(
        self,
    ) -> None:
        current = self.install("v1.0.0", "a")
        self.manager.deploy(current)
        transaction, _ = self.make_blocking_manual_recovery(current)

        with mock.patch.object(
            self.manager,
            "_write_transaction_record",
            side_effect=OSError("injected record write interruption"),
        ):
            with self.assertRaisesRegex(OSError, "record write interruption"):
                self.manager.reconcile_recovery(
                    transaction.name, self.recovery_resolution
                )

        blocked = json.loads((transaction / "record.json").read_text(encoding="utf-8"))
        self.assertEqual("recovery_failed", blocked["status"])
        self.assertTrue(
            (transaction / module.MANUAL_RECOVERY_RECONCILE_SUMMARY).is_file()
        )
        attempts_before = list((transaction / "reconcile-attempts").iterdir())

        result = self.manager.reconcile_recovery(
            transaction.name, self.recovery_resolution
        )

        self.assertTrue(result["overall_pass"])
        self.assertEqual(
            attempts_before, list((transaction / "reconcile-attempts").iterdir())
        )
        record = json.loads((transaction / "record.json").read_text(encoding="utf-8"))
        self.assertEqual("recovery_reconciled", record["status"])

    def test_reconcile_recovery_records_explicit_legacy_redis_bridge(self) -> None:
        current = self.install("v1.0.0", "a")
        self.manager.deploy(current)
        transaction, _ = self.make_blocking_manual_recovery(current)

        result = self.manager.reconcile_recovery(
            transaction.name,
            self.recovery_resolution,
            allow_legacy_redis_hardening_bridge=True,
        )

        self.assertTrue(result["overall_pass"])
        self.assertTrue(result["legacy_redis_hardening_bridge"])
        verification = json.loads(
            Path(result["deployment_verification"]["path"]).read_text(encoding="utf-8")
        )
        self.assertTrue(verification["legacy_redis_hardening_bridge"])

    def test_reconcile_recovery_cli_requires_transaction_id(self) -> None:
        args = module.build_parser().parse_args(
            [
                "reconcile-recovery",
                "--transaction-id",
                "tx-123",
                "--resolution-summary",
                "/tmp/recovery.json",
                "--allow-legacy-redis-hardening-bridge",
            ]
        )
        self.assertEqual("reconcile-recovery", args.command)
        self.assertEqual("tx-123", args.transaction_id)
        self.assertEqual(Path("/tmp/recovery.json"), args.resolution_summary)
        self.assertTrue(args.allow_legacy_redis_hardening_bridge)

    def test_deploy_adopts_legacy_release_pointer_once(self) -> None:
        source, image_env, release_summary, image_summary = self.make_release(
            "v1.0.0", "a"
        )
        legacy = self.layout.releases / "v1.0.0-deploy-r3"
        self.layout.releases.mkdir(parents=True)
        module.shutil.copytree(source, legacy)
        deployment_id = str(
            self.manager.install(
                source, image_env, release_summary, image_summary, None
            )["deployment_id"]
        )
        self.layout.current.symlink_to(legacy)
        self.layout.active_image_env.parent.mkdir(parents=True, exist_ok=True)
        module.shutil.copyfile(image_env, self.layout.active_image_env)

        result = self.manager.deploy(deployment_id)

        self.assertTrue(result["legacy_adoption"])
        self.assertEqual(
            self.layout.current.resolve().parent, self.layout.deployments.resolve()
        )
        self.assertEqual(
            self.layout.active_image_env.readlink(),
            self.layout.current / "compose-images.env",
        )

    def test_failed_legacy_adoption_preserves_todo0009_pointer(self) -> None:
        source, image_env, release_summary, image_summary = self.make_release(
            "v1.0.0", "a"
        )
        legacy = self.layout.releases / "v1.0.0-deploy-r3"
        self.layout.releases.mkdir(parents=True)
        module.shutil.copytree(source, legacy)
        deployment_id = str(
            self.manager.install(
                source, image_env, release_summary, image_summary, None
            )["deployment_id"]
        )
        self.layout.current.symlink_to(legacy)
        self.layout.active_image_env.parent.mkdir(parents=True, exist_ok=True)
        module.shutil.copyfile(image_env, self.layout.active_image_env)
        self.executor.fail_verification_for.add(deployment_id)

        with self.assertRaisesRegex(module.LifecycleError, "pointer was preserved"):
            self.manager.deploy(deployment_id)

        self.assertEqual(self.layout.current.resolve(), legacy.resolve())
        self.assertFalse(self.layout.active_image_env.is_symlink())
        self.assertEqual(
            module.parse_image_environment(self.layout.active_image_env),
            module.parse_image_environment(image_env),
        )

    def test_image_environment_parser_rejects_extra_or_mutable_values(self) -> None:
        path = Path(self.temporary.name) / "bad.env"
        path.write_text("GATEWAY_IMAGE_ID=latest\nEXTRA=value\n", encoding="utf-8")
        with self.assertRaises(module.LifecycleError):
            module.parse_image_environment(path)

    def test_install_rejects_image_summary_that_does_not_match_environment(
        self,
    ) -> None:
        source, image_env, release_summary, image_summary = self.make_release(
            "v1.0.0", "a"
        )
        summary = json.loads(image_summary.read_text(encoding="utf-8"))
        summary["images"][0]["image_id"] = "sha256:" + "f" * 64
        image_summary.write_text(json.dumps(summary), encoding="utf-8")

        with self.assertRaisesRegex(module.LifecycleError, "attestation mismatch"):
            self.manager.install(
                source, image_env, release_summary, image_summary, None
            )

    def test_secret_environment_cannot_override_image_identity(self) -> None:
        deployment_id = self.install("v1.0.0", "a")
        self.layout.secret_env.parent.mkdir(parents=True, exist_ok=True)
        self.layout.secret_env.write_text(
            "GATEWAY_IMAGE_ID=sha256:" + "f" * 64 + "\n", encoding="utf-8"
        )
        executor = module.SystemLifecycleExecutor(self.layout)

        with self.assertRaisesRegex(module.LifecycleError, "overrides image"):
            executor._environment(self.layout.deployments / deployment_id)


if __name__ == "__main__":
    unittest.main()
