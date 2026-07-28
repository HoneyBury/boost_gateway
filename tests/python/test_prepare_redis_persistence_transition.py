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
    @mock.patch.object(transition, "freeze_writes")
    @mock.patch.object(transition, "active_volume")
    @mock.patch.object(transition, "actual_mode")
    def test_executes_frozen_checkpoint_before_mode_change(
        self,
        actual_mode: mock.Mock,
        active_volume: mock.Mock,
        freeze_writes: mock.Mock,
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

        result = transition.execute(self.arguments())

        self.assertTrue(result["overall_pass"])
        self.assertTrue(result["writes_frozen"])
        self.assertTrue(result["checkpoint_verified"])
        self.assertFalse(result["aof_to_rdb_downgrade"])
        self.assertEqual(result["active_volume"], volume)
        freeze_writes.assert_called_once()
        checkpoint.assert_called_once()
        self.assertGreater(checkpoint.call_args.args[0], 0)
        self.assertLessEqual(checkpoint.call_args.args[0], 180.0)

    @mock.patch.object(transition, "checkpoint")
    @mock.patch.object(transition, "freeze_writes")
    @mock.patch.object(transition, "active_volume")
    @mock.patch.object(transition, "actual_mode")
    def test_runtime_already_at_target_does_not_stop_services(
        self,
        actual_mode: mock.Mock,
        active_volume: mock.Mock,
        freeze_writes: mock.Mock,
        checkpoint: mock.Mock,
    ) -> None:
        actual_mode.return_value = (
            "aof_everysec_rdb",
            {"appendonly": "yes", "appendfsync": "everysec"},
        )
        active_volume.return_value = {"identity_sha256": "a" * 64}

        result = transition.execute(self.arguments())

        self.assertTrue(result["overall_pass"])
        self.assertTrue(result["runtime_already_target"])
        self.assertFalse(result["writes_frozen"])
        self.assertFalse(result["checkpoint_verified"])
        freeze_writes.assert_not_called()
        checkpoint.assert_not_called()

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
