from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.tools import verify_restored_business_isolated as business


class CommandRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(
        self, command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[1:3] == ["inspect", "--format"]:
            return subprocess.CompletedProcess(command, 0, "healthy\n", "")
        if command[1:2] == ["port"]:
            return subprocess.CompletedProcess(command, 0, "127.0.0.1:49123\n", "")
        if command[1:3] == ["exec", "business-redis"]:
            return subprocess.CompletedProcess(command, 0, "PONG\n", "")
        if "sha256sum /data/dump.rdb" in command[-1]:
            return subprocess.CompletedProcess(
                command, 0, "a" * 64 + "  /data/dump.rdb\n", ""
            )
        return subprocess.CompletedProcess(command, 0, "container-id\n", "")


class UnhealthyRunner(CommandRunner):
    def __call__(
        self, command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[1:3] == ["inspect", "--format"]:
            if command[3] == "{{.State.Health.Status}}":
                return subprocess.CompletedProcess(command, 0, "unhealthy\n", "")
            return subprocess.CompletedProcess(
                command, 0, '{"Status":"exited","ExitCode":1}\n', ""
            )
        if command[1:2] == ["logs"]:
            return subprocess.CompletedProcess(
                command, 0, "gateway startup failure detail\n", ""
            )
        return subprocess.CompletedProcess(command, 0, "", "")


class RestoredBusinessValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.restore_summary = self.root / "restore.json"
        self.deployment_record = self.root / "record.json"
        self.release = self.root / "release"
        (self.release / "bin").mkdir(parents=True)
        self.client = self.release / "bin/sdk_full_flow_client"
        self.client.write_bytes(b"release-sdk-client")
        self.client.chmod(0o755)
        self.deployment_id = "v3.6.2-test-deployment"
        self.retained = "boost-gateway-recovery-pass-one"
        self.work = "boost-gateway-recovery-business-work"
        self.network = "boost-gateway-recovery-business-net"
        self.active = business.DEFAULT_ACTIVE_VOLUME
        self.images = {
            key: f"sha256:{index:064x}"
            for index, key in enumerate(business.IMAGE_KEYS.values(), start=1)
        }
        self.redis_image = "sha256:" + "f" * 64
        self.seed_sha = "d" * 64
        self.snapshot_sha = "c" * 64
        deployment = {
            "deployment_id": self.deployment_id,
            "tag": "v3.6.2",
            "commit": "a" * 40,
            "runtime_asset_sha256": "b" * 64,
        }
        self.restore_summary.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "restore_id": "restore-pass-one",
                    "backup_id": "backup-pass-one",
                    "deployment": deployment,
                    "overall_pass": True,
                    "status": "passed",
                    "target_volume": self.retained,
                    "target_volume_retained": True,
                    "leaderboard_seed_exact": True,
                    "redis_ping": True,
                    "production_switched": False,
                    "active_volume_mounted_by_drill": False,
                    "restore_known_good": False,
                    "formal_todo0012_claim": False,
                    "active_volume_identity_sha256": "1" * 64,
                    "target_volume_identity_sha256": "2" * 64,
                    "canonical_seed_restored_sha256": self.seed_sha,
                    "canonical_seed_key_count": 2,
                    "redis_snapshot_sha256": self.snapshot_sha,
                    "redis_image": self.redis_image,
                }
            ),
            encoding="utf-8",
        )
        self.deployment_record.write_text(
            json.dumps(
                {
                    **deployment,
                    "status": "verified",
                    "release_path": str(self.release.resolve()),
                    "image_ids": self.images,
                }
            ),
            encoding="utf-8",
        )
        (self.release / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "tag": "v3.6.2",
                    "commit": "a" * 40,
                    "platform": "linux-x64",
                    "source_build_performed": False,
                    "binaries": [
                        {
                            "name": "sdk_full_flow_client",
                            "sha256": business.sha256_file(self.client),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.summary = self.root / "business-summary.json"
        self.lock = self.root / "lifecycle.lock"

    def _bind_restore_runtime(
        self, retained_state: dict[str, object], active_state: dict[str, object]
    ) -> None:
        value = json.loads(self.restore_summary.read_text())
        value["active_volume_identity_sha256"] = business.restore.volume_identity(
            active_state
        )
        value["target_volume_identity_sha256"] = business.restore.volume_identity(
            retained_state
        )
        self.restore_summary.write_text(json.dumps(value), encoding="utf-8")

    def test_release_context_requires_bound_verified_release_sdk(self) -> None:
        summary, record, manifest, images, client = business.load_release_context(
            self.restore_summary,
            self.deployment_record,
            self.release,
            self.retained,
        )
        self.assertEqual("restore-pass-one", summary["restore_id"])
        self.assertEqual(self.deployment_id, record["deployment_id"])
        self.assertFalse(manifest["source_build_performed"])
        self.assertEqual(set(business.IMAGE_KEYS), set(images))
        self.assertEqual(self.client.resolve(), client)

        value = json.loads(self.restore_summary.read_text())
        value["leaderboard_seed_exact"] = False
        self.restore_summary.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(business.BusinessValidationError, "eligible"):
            business.load_release_context(
                self.restore_summary,
                self.deployment_record,
                self.release,
                self.retained,
            )

    def test_clone_mounts_retained_readonly_and_writes_only_work_volume(self) -> None:
        runner = CommandRunner()
        digest = business.clone_retained_volume(
            runner,
            "docker-test",
            self.redis_image,
            self.retained,
            self.work,
        )
        self.assertEqual("a" * 64, digest)
        command = runner.commands[0]
        self.assertIn(f"type=volume,src={self.retained},dst=/source,readonly", command)
        self.assertIn(f"type=volume,src={self.work},dst=/data", command)
        self.assertIn("redis", command)
        self.assertNotIn("0", command)
        self.assertNotIn("--cap-add", command)

    def test_topology_uses_internal_aliases_and_loopback_random_port(self) -> None:
        runner = CommandRunner()
        started: list[str] = []
        business.start_redis_networked(
            runner,
            "docker-test",
            self.redis_image,
            "business-redis",
            self.network,
            self.work,
            started.append,
        )
        business.start_backend(
            runner,
            "docker-test",
            self.images["LEADERBOARD_IMAGE_ID"],
            "business-leaderboard",
            self.network,
            "leaderboard-backend",
            "9305",
            {"REDIS_HOST": "redis", "REDIS_PORT": "6379"},
            started.append,
        )
        port = business.start_gateway(
            runner,
            "docker-test",
            self.images["GATEWAY_IMAGE_ID"],
            "business-gateway",
            self.network,
            started.append,
        )
        self.assertEqual(49123, port)
        joined = [" ".join(command) for command in runner.commands]
        self.assertTrue(any("--network-alias redis" in item for item in joined))
        self.assertTrue(any("REDIS_HOST=redis" in item for item in joined))
        gateway = next(
            item
            for item in joined
            if "business-gateway" in item and " run " in f" {item} "
        )
        self.assertIn("--publish 127.0.0.1::9201", gateway)
        self.assertIn("--tmpfs /app/v2_archive:rw,noexec,nosuid,size=32m", gateway)
        self.assertNotIn("0.0.0.0", gateway)
        redis = next(
            item
            for item in joined
            if "business-redis" in item and " run " in f" {item} "
        )
        self.assertIn("--protected-mode no", redis)

    def test_unhealthy_container_error_captures_bounded_state_and_logs(self) -> None:
        with self.assertRaisesRegex(
            business.BusinessValidationError, "gateway startup failure detail"
        ) as raised:
            business.wait_healthy(
                UnhealthyRunner(), "docker-test", "business-gateway", 1.0
            )
        self.assertIn('"ExitCode":1', str(raised.exception))

    def test_sdk_output_and_redis_effects_prove_submit_top_rank(self) -> None:
        stdout = "\n".join(
            (
                "Both connected.",
                "Alice logged in as: alice_123",
                "Bob logged in as: bob_123",
                "Manual leaderboard submit path OK.",
                "Leaderboard rank query path OK.",
                "Both left room.",
                "=== ALL TESTS PASSED ===",
            )
        )
        with mock.patch.object(
            business.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["client"], 0, stdout, ""),
        ):
            sdk = business.run_sdk(self.client, 49123, 180)
        self.assertEqual("alice_123", sdk["alice_user_id"])
        self.assertFalse(sdk["source_build_performed"])

        responses: dict[tuple[str, ...], object] = {
            ("ZREVRANGE", "lb:global", "0", "19"): ["bob_123", "alice_123"],
            ("ZSCORE", "lb:global", "alice_123"): "9000000000001",
            ("ZREVRANK", "lb:global", "alice_123"): 1,
            ("HGET", "lb:global:names", "alice_123"): "Alice",
            ("ZSCORE", "lb:global", "bob_123"): "9000000000101",
            ("ZREVRANK", "lb:global", "bob_123"): 0,
            ("HGET", "lb:global:names", "bob_123"): "Bob",
        }
        with mock.patch.object(
            business.restore,
            "redis_json",
            side_effect=lambda runner, docker, container, *args: responses[args],
        ):
            result = business.verify_leaderboard_effects(
                CommandRunner(),
                "docker-test",
                "business-redis",
                "alice_123",
                "bob_123",
            )
        self.assertTrue(result["leaderboard_submit"])
        self.assertTrue(result["leaderboard_top"])
        self.assertTrue(result["leaderboard_rank"])

    def test_orchestrator_preserves_retained_seed_and_cleans_disposable_topology(
        self,
    ) -> None:
        retained_state = {
            "Name": self.retained,
            "Driver": "local",
            "Mountpoint": "/retained",
            "Scope": "local",
            "Labels": {"boost-gateway.restore-id": "restore-pass-one"},
        }
        work_state = {
            "Name": self.work,
            "Driver": "local",
            "Mountpoint": "/work",
            "Scope": "local",
            "Labels": {
                "boost-gateway.todo": "TODO-0012",
                "boost-gateway.business-id": "business-pass-one",
                "boost-gateway.source-volume": self.retained,
            },
        }
        active_state = {
            "Name": self.active,
            "Driver": "local",
            "Mountpoint": "/active",
            "Scope": "local",
            "Labels": {},
        }
        sdk_result = {
            "exit_code": 0,
            "alice_user_id": "alice_123",
            "bob_user_id": "bob_123",
            "stdout_tail": "=== ALL TESTS PASSED ===",
            "stderr_tail": "",
            "source_build_performed": False,
        }
        leaderboard_result = {
            "leaderboard_submit": True,
            "leaderboard_top": True,
            "leaderboard_rank": True,
        }
        ticks = iter((0.0, 40.0, 40.0))
        self._bind_restore_runtime(retained_state, active_state)

        def started(*args: object, **kwargs: object) -> None:
            callback = args[-1]
            if callable(callback):
                callback(str(args[3]))

        with (
            mock.patch.object(business, "assert_image_ids"),
            mock.patch.object(
                business.restore,
                "inspect_volume",
                side_effect=lambda runner, docker, volume: (
                    active_state
                    if volume == self.active
                    else work_state if volume == self.work else retained_state
                ),
            ),
            mock.patch.object(
                business, "ensure_unused_volume", return_value=retained_state
            ),
            mock.patch.object(business, "ensure_absent"),
            mock.patch.object(
                business,
                "audit_retained_seed",
                side_effect=[
                    (self.seed_sha, 2, {"lb:global", "lb:global:names"}),
                    (self.seed_sha, 2, {"lb:global", "lb:global:names"}),
                ],
            ),
            mock.patch.object(
                business,
                "checked",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
            mock.patch.object(
                business, "clone_retained_volume", return_value=self.snapshot_sha
            ),
            mock.patch.object(business, "inspect_internal_network"),
            mock.patch.object(business, "start_redis_networked", side_effect=started),
            mock.patch.object(business, "start_backend", side_effect=started),
            mock.patch.object(
                business,
                "start_gateway",
                side_effect=lambda *args, **kwargs: (
                    args[-1](str(args[3])) if callable(args[-1]) else None
                )
                or 49123,
            ),
            mock.patch.object(
                business.restore,
                "canonical_keyspace",
                side_effect=[
                    (self.seed_sha, 2, {"lb:global", "lb:global:names"}),
                    ("changed-sha", 4, {"lb:global", "lb:global:names"}),
                ],
            ),
            mock.patch.object(business, "run_sdk", return_value=sdk_result),
            mock.patch.object(
                business, "verify_leaderboard_effects", return_value=leaderboard_result
            ),
            mock.patch.object(business, "remove_resource", return_value=True),
        ):
            result = business.run_business_validation(
                business_id="business-pass-one",
                restore_summary_path=self.restore_summary,
                deployment_record_path=self.deployment_record,
                release_dir=self.release,
                retained_volume=self.retained,
                work_volume=self.work,
                network=self.network,
                redis_image=self.redis_image,
                summary_path=self.summary,
                active_volume=self.active,
                lock_path=self.lock,
                docker="docker-test",
                monotonic=lambda: next(ticks),
            )

        self.assertTrue(result["overall_pass"])
        self.assertTrue(result["retained_seed_unchanged"])
        self.assertEqual(
            result["retained_volume_identity_sha256"],
            result["retained_volume_identity_after_sha256"],
        )
        self.assertTrue(result["restore_volume_identity_binding_verified"])
        self.assertTrue(result["restore_snapshot_binding_verified"])
        self.assertTrue(result["restore_redis_image_binding_verified"])
        self.assertTrue(result["work_seed_mutated_by_business_checks"])
        self.assertTrue(result["work_volume_removed"])
        self.assertTrue(result["isolated_network_removed"])
        self.assertFalse(result["production_switched"])
        self.assertFalse(result["restore_known_good"])
        self.assertFalse(result["formal_todo0012_claim"])

    def test_internal_network_failure_is_recorded_without_claim(self) -> None:
        retained_state = {
            "Name": self.retained,
            "Driver": "local",
            "Mountpoint": "/retained",
            "Scope": "local",
            "Labels": {"boost-gateway.restore-id": "restore-pass-one"},
        }
        active_state = {
            "Name": self.active,
            "Driver": "local",
            "Mountpoint": "/active",
            "Scope": "local",
            "Labels": {},
        }
        self._bind_restore_runtime(retained_state, active_state)
        with (
            mock.patch.object(business, "assert_image_ids"),
            mock.patch.object(
                business.restore,
                "inspect_volume",
                return_value=active_state,
            ),
            mock.patch.object(
                business, "ensure_unused_volume", return_value=retained_state
            ),
            mock.patch.object(business, "ensure_absent"),
            mock.patch.object(
                business,
                "audit_retained_seed",
                return_value=(
                    self.seed_sha,
                    2,
                    {"lb:global", "lb:global:names"},
                ),
            ),
            mock.patch.object(
                business,
                "checked",
                side_effect=business.BusinessValidationError(
                    "internal network publish unsupported"
                ),
            ),
            mock.patch.object(business, "remove_resource", return_value=True),
        ):
            with self.assertRaisesRegex(
                business.BusinessValidationError, "internal network publish unsupported"
            ):
                business.run_business_validation(
                    business_id="business-failure",
                    restore_summary_path=self.restore_summary,
                    deployment_record_path=self.deployment_record,
                    release_dir=self.release,
                    retained_volume=self.retained,
                    work_volume=self.work,
                    network=self.network,
                    redis_image=self.redis_image,
                    summary_path=self.summary,
                    active_volume=self.active,
                    lock_path=self.lock,
                    docker="docker-test",
                )
        summary = json.loads(self.summary.read_text())
        self.assertFalse(summary["overall_pass"])
        self.assertFalse(summary["restore_known_good"])
        self.assertFalse(summary["formal_todo0012_claim"])
        self.assertIn("internal network publish unsupported", summary["failure"])


if __name__ == "__main__":
    unittest.main()
