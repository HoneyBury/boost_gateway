import unittest
from unittest.mock import patch

from scripts.producers.collect_v2_perf_baseline import wait_for_local_connection_budget


class PerfConnectionBudgetTest(unittest.TestCase):
    @patch("scripts.producers.collect_v2_perf_baseline.platform.system", return_value="Linux")
    def test_non_darwin_does_not_inspect_socket_state(self, _system) -> None:
        with patch("scripts.producers.collect_v2_perf_baseline.subprocess.run") as run:
            result = wait_for_local_connection_budget(10_000)

        self.assertFalse(result["required"])
        self.assertEqual(result["target_connections"], 10_000)
        run.assert_not_called()

    @patch("scripts.producers.collect_v2_perf_baseline.platform.system", return_value="Darwin")
    @patch("scripts.producers.collect_v2_perf_baseline.time.sleep")
    @patch("scripts.producers.collect_v2_perf_baseline.time.monotonic")
    @patch("scripts.producers.collect_v2_perf_baseline.subprocess.run")
    def test_darwin_waits_for_time_wait_budget(
        self, run, monotonic, sleep, _system
    ) -> None:
        sysctl = unittest.mock.Mock(returncode=0, stdout="49152\n65535\n")
        busy = unittest.mock.Mock(
            returncode=0,
            stdout="tcp4 0 0 127.0.0.1.1 127.0.0.1.2 TIME_WAIT\n" * 6000,
        )
        ready = unittest.mock.Mock(
            returncode=0,
            stdout="tcp4 0 0 127.0.0.1.1 127.0.0.1.2 TIME_WAIT\n" * 5000,
        )
        run.side_effect = [sysctl, busy, ready]
        monotonic.side_effect = [10.0, 10.0, 11.0, 11.0]

        result = wait_for_local_connection_budget(
            10_000, headroom=1024, timeout_seconds=60, interval_seconds=1
        )

        self.assertTrue(result["required"])
        self.assertEqual(result["ephemeral_port_capacity"], 16_384)
        self.assertEqual(result["maximum_time_wait"], 5_360)
        self.assertEqual(result["initial_time_wait"], 6_000)
        self.assertEqual(result["final_time_wait"], 5_000)
        self.assertEqual(result["wait_seconds"], 1.0)
        sleep.assert_called_once_with(1)

    @patch("scripts.producers.collect_v2_perf_baseline.platform.system", return_value="Darwin")
    @patch("scripts.producers.collect_v2_perf_baseline.subprocess.run")
    def test_darwin_rejects_target_larger_than_port_pool(self, run, _system) -> None:
        run.return_value = unittest.mock.Mock(returncode=0, stdout="49152\n65535\n")

        with self.assertRaisesRegex(RuntimeError, "cannot support"):
            wait_for_local_connection_budget(16_000, headroom=1024)


if __name__ == "__main__":
    unittest.main()
