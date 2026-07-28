from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.tools import prepare_redis_persistence_transition as transition


class RedisPersistenceTransitionTest(unittest.TestCase):
    def arguments(self) -> argparse.Namespace:
        return argparse.Namespace(
            compose_file=Path("/release/docker-compose.production.yml"),
            source_mode="rdb_only",
            target_mode="aof_everysec_rdb",
            timeout_seconds=180.0,
            summary_path=Path("/evidence/transition.json"),
        )

    @mock.patch.object(transition, "checkpoint")
    @mock.patch.object(transition, "seed_aof_from_rdb")
    @mock.patch.object(transition, "prepare_aof_directory")
    @mock.patch.object(transition, "freeze_writes")
    @mock.patch.object(transition, "active_volume")
    @mock.patch.object(transition, "actual_mode")
    def test_executes_frozen_checkpoint_before_mode_change(
        self,
        actual_mode: mock.Mock,
        active_volume: mock.Mock,
        freeze_writes: mock.Mock,
        prepare_aof_directory: mock.Mock,
        seed_aof_from_rdb: mock.Mock,
        checkpoint: mock.Mock,
    ) -> None:
        volume = {
            "type": "volume",
            "name": "boost-gateway-production-redis-data",
            "identity_sha256": "a" * 64,
        }
        actual_mode.return_value = ("rdb_only", {"appendonly": "no"})
        active_volume.side_effect = [volume, volume]
        checkpoint.return_value = {
            "lastsave_before": 100,
            "lastsave_after": 101,
            "rdb_changes_since_last_save": 0,
            "rdb_last_bgsave_status": "ok",
            "rdb_sha256": "b" * 64,
            "redis_check_rdb": True,
        }
        prepare_aof_directory.return_value = {
            "action": "absent",
            "files_deleted": False,
        }
        seed_aof_from_rdb.return_value = {
            "method": "runtime-config-set-and-rewrite",
            "key_count_before": 5,
            "key_count_after": 5,
        }

        result = transition.execute(self.arguments())

        self.assertTrue(result["overall_pass"])
        self.assertTrue(result["writes_frozen"])
        self.assertTrue(result["checkpoint_verified"])
        self.assertFalse(result["aof_to_rdb_downgrade"])
        self.assertEqual(result["active_volume"], volume)
        freeze_writes.assert_called_once()
        checkpoint.assert_called_once()
        prepare_aof_directory.assert_called_once_with(
            "rdb_only", "aof_everysec_rdb", "evidence", mock.ANY
        )
        seed_aof_from_rdb.assert_called_once_with(mock.ANY)
        self.assertGreater(checkpoint.call_args.args[0], 0)
        self.assertLessEqual(checkpoint.call_args.args[0], 180.0)

    @mock.patch.object(transition, "checkpoint")
    @mock.patch.object(transition, "seed_aof_from_rdb")
    @mock.patch.object(transition, "prepare_aof_directory")
    @mock.patch.object(transition, "freeze_writes")
    @mock.patch.object(transition, "active_volume")
    @mock.patch.object(transition, "actual_mode")
    def test_runtime_already_at_target_is_frozen_and_revalidated(
        self,
        actual_mode: mock.Mock,
        active_volume: mock.Mock,
        freeze_writes: mock.Mock,
        prepare_aof_directory: mock.Mock,
        seed_aof_from_rdb: mock.Mock,
        checkpoint: mock.Mock,
    ) -> None:
        actual_mode.return_value = (
            "aof_everysec_rdb",
            {"appendonly": "yes", "appendfsync": "everysec"},
        )
        volume = {"identity_sha256": "a" * 64}
        active_volume.side_effect = [volume, volume]
        checkpoint.return_value = {"redis_check_rdb": True}

        with mock.patch.object(
            transition,
            "validate_existing_aof",
            return_value={
                "method": "runtime-already-target-validated",
                "manifest_sha256": "b" * 64,
                "files_deleted": False,
            },
        ) as validate_existing:
            result = transition.execute(self.arguments())

        self.assertTrue(result["overall_pass"])
        self.assertTrue(result["runtime_already_target"])
        self.assertTrue(result["writes_frozen"])
        self.assertTrue(result["checkpoint_verified"])
        freeze_writes.assert_called_once()
        prepare_aof_directory.assert_not_called()
        seed_aof_from_rdb.assert_not_called()
        checkpoint.assert_called_once()
        validate_existing.assert_called_once()

    @mock.patch.object(transition, "run")
    def test_aof_to_rdb_makes_directory_traversable_without_deleting_files(
        self, run: mock.Mock
    ) -> None:
        run.return_value = mock.Mock(
            returncode=0,
            stdout=f"{'d' * 64}  /data/appendonlydir/appendonly.aof.manifest\n",
            stderr="",
        )

        result = transition.prepare_aof_directory(
            "aof_everysec_rdb", "rdb_only", "transaction-1", 60.0
        )

        self.assertEqual(result["action"], "entrypoint-readable")
        self.assertEqual(result["mode"], "0755")
        self.assertFalse(result["files_deleted"])
        command = run.call_args.args[0]
        self.assertEqual(
            command[:5], ["docker", "exec", "--user", "redis", "boost-redis"]
        )
        self.assertIn("chmod 0755 /data/appendonlydir", command[-1])

    @mock.patch.object(transition, "run")
    def test_rdb_to_aof_rejects_unbound_existing_aof(self, run: mock.Mock) -> None:
        run.return_value = mock.Mock(
            returncode=0,
            stdout=f"{'e' * 64}  /data/appendonlydir/appendonly.aof.manifest\n",
            stderr="",
        )

        with self.assertRaisesRegex(transition.TransitionError, "existing AOF"):
            transition.prepare_aof_directory(
                "rdb_only", "aof_everysec_rdb", "transaction-2", 60.0
            )
        command = run.call_args.args[0]
        self.assertNotIn("mv /data/appendonlydir", " ".join(command))

    @mock.patch.object(transition, "run")
    def test_rdb_to_aof_accepts_absent_directory(self, run: mock.Mock) -> None:
        run.return_value = mock.Mock(returncode=0, stdout="absent\n", stderr="")

        result = transition.prepare_aof_directory(
            "rdb_only", "aof_everysec_rdb", "transaction-3", 60.0
        )

        self.assertEqual(result["action"], "absent")
        self.assertFalse(result["files_deleted"])

    @mock.patch.object(transition, "actual_mode")
    @mock.patch.object(transition, "run")
    @mock.patch.object(transition, "redis")
    def test_seeds_aof_from_complete_active_rdb_keyspace(
        self, redis: mock.Mock, run: mock.Mock, actual_mode: mock.Mock
    ) -> None:
        redis.side_effect = [
            "5\n",
            "OK\n",
            "OK\n",
            "OK\n",
            (
                "aof_enabled:1\n"
                "aof_rewrite_in_progress:0\n"
                "aof_last_bgrewrite_status:ok\n"
                "aof_rewrites:1\n"
                "aof_current_size:1024\n"
                "aof_base_size:900\n"
            ),
            "5\n",
        ]
        run.return_value = mock.Mock(
            returncode=0,
            stdout=f"{'f' * 64}  /data/appendonlydir/appendonly.aof.manifest\n",
            stderr="",
        )
        actual_mode.return_value = (
            "aof_everysec_rdb",
            {"appendonly": "yes", "appendfsync": "everysec"},
        )

        result = transition.seed_aof_from_rdb(60.0)

        self.assertEqual(result["key_count_before"], 5)
        self.assertEqual(result["key_count_after"], 5)
        self.assertEqual(result["method"], "runtime-config-set-and-rewrite")
        self.assertEqual(
            redis.call_args_list[3].args[0],
            ["CONFIG", "SET", "appendonly", "yes"],
        )
        self.assertEqual(
            run.call_args.args[0][:5],
            ["docker", "exec", "--user", "redis", "boost-redis"],
        )

    @mock.patch.object(transition, "active_volume")
    @mock.patch.object(transition, "actual_mode")
    def test_rejects_runtime_mode_outside_source_and_target(
        self, actual_mode: mock.Mock, active_volume: mock.Mock
    ) -> None:
        actual_mode.return_value = ("unknown", {"appendonly": "yes"})
        active_volume.return_value = {"identity_sha256": "a" * 64}

        with self.assertRaisesRegex(transition.TransitionError, "differs from source"):
            transition.execute(self.arguments())

    @mock.patch.object(transition, "run")
    @mock.patch.object(transition, "redis")
    def test_checkpoint_requires_advanced_lastsave_and_offline_validation(
        self, redis: mock.Mock, run: mock.Mock
    ) -> None:
        redis.side_effect = [
            "100\n",
            "Background saving started\n",
            (
                "rdb_bgsave_in_progress:0\n"
                "rdb_last_bgsave_status:ok\n"
                "rdb_changes_since_last_save:0\n"
            ),
            "101\n",
        ]
        run.side_effect = [
            mock.Mock(returncode=0, stdout="RDB looks OK!\n", stderr=""),
            mock.Mock(returncode=0, stdout=f"{'c' * 64}  /data/dump.rdb\n", stderr=""),
        ]

        result = transition.checkpoint(10.0)

        self.assertEqual(result["lastsave_before"], 100)
        self.assertEqual(result["lastsave_after"], 101)
        self.assertEqual(result["rdb_sha256"], "c" * 64)
        self.assertTrue(result["redis_check_rdb"])
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(
                command[:5], ["docker", "exec", "--user", "redis", "boost-redis"]
            )
        self.assertEqual(
            run.call_args_list[0].args[0][-2:], ["redis-check-rdb", "/data/dump.rdb"]
        )
        self.assertEqual(
            run.call_args_list[1].args[0][-2:], ["sha256sum", "/data/dump.rdb"]
        )

    @mock.patch.object(transition, "run")
    def test_freeze_writes_stops_and_verifies_exact_containers(
        self, run: mock.Mock
    ) -> None:
        run.side_effect = [
            mock.Mock(returncode=0, stdout="", stderr=""),
            *[
                mock.Mock(returncode=0, stdout="false\n", stderr="")
                for _ in transition.WRITE_CONTAINERS
            ],
        ]

        transition.freeze_writes(Path("/release/compose.yml"), 60.0)

        stop = run.call_args_list[0].args[0]
        self.assertEqual(
            stop[-len(transition.WRITE_SERVICES) :], list(transition.WRITE_SERVICES)
        )
        inspected = [call.args[0][-1] for call in run.call_args_list[1:]]
        self.assertEqual(inspected, list(transition.WRITE_CONTAINERS))

    def test_failure_summary_is_createable_without_secret_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            transition.atomic_write_json(
                path,
                {
                    "overall_pass": False,
                    "secret_material_recorded": False,
                },
            )
            self.assertIn('"secret_material_recorded": false', path.read_text())
            with self.assertRaisesRegex(transition.TransitionError, "create-only"):
                transition.atomic_write_json(path, {"overall_pass": True})


if __name__ == "__main__":
    unittest.main()
