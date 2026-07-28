from __future__ import annotations

import unittest
from unittest import mock

from scripts.tools import collect_redis_persistence_metrics as collector


class RedisPersistenceMetricsTest(unittest.TestCase):
    @mock.patch.object(collector, "run_redis")
    def test_collects_effective_aof_and_rdb_state(self, run_redis: mock.Mock) -> None:
        run_redis.side_effect = [
            """# Persistence
aof_enabled:1
aof_delayed_fsync:0
aof_last_write_status:ok
aof_last_bgrewrite_status:ok
rdb_last_bgsave_status:ok
rdb_changes_since_last_save:0
""",
            "\n".join(
                item for pair in collector.EXPECTED_CONFIG.items() for item in pair
            )
            + "\n",
        ]

        values = collector.collect()

        self.assertEqual(values["aof_enabled"], 1)
        self.assertEqual(values["aof_delayed_fsync"], 0)
        self.assertEqual(values["aof_delayed_fsync_counter_present"], 1)
        self.assertEqual(values["aof_last_write_status"], 1)
        self.assertEqual(values["aof_last_bgrewrite_status"], 1)
        self.assertEqual(values["rdb_last_bgsave_status"], 1)
        self.assertEqual(values["rdb_changes_since_last_save"], 0)
        self.assertEqual(values["effective_config_valid"], 1)

    @mock.patch.object(collector, "run_redis")
    def test_disabled_aof_makes_counter_absence_explicit(
        self, run_redis: mock.Mock
    ) -> None:
        run_redis.side_effect = [
            """aof_enabled:0
aof_last_write_status:ok
aof_last_bgrewrite_status:ok
rdb_last_bgsave_status:ok
rdb_changes_since_last_save:3
""",
            "appendonly\nno\n",
        ]

        values = collector.collect()

        self.assertEqual(values["aof_enabled"], 0)
        self.assertEqual(values["aof_delayed_fsync"], 0)
        self.assertEqual(values["aof_delayed_fsync_counter_present"], 0)
        self.assertEqual(values["effective_config_valid"], 0)

    def test_invalid_status_fails_closed(self) -> None:
        with self.assertRaisesRegex(collector.CollectionError, "aof_last_write_status"):
            collector.status({}, "aof_last_write_status")

    def test_rendered_failure_removes_stale_persistence_values(self) -> None:
        rendered = collector.render_metrics(None, 1234567890)

        self.assertIn("boost_gateway_redis_persistence_collection_success 0", rendered)
        self.assertIn(
            "boost_gateway_redis_persistence_collection_timestamp_seconds 1234567890",
            rendered,
        )
        self.assertNotIn("boost_gateway_redis_aof_enabled ", rendered)


if __name__ == "__main__":
    unittest.main()
