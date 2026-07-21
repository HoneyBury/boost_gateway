import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.producers.collect_v2_perf_baseline import (
    analyze_resources,
    apply_cpu_affinity,
    evaluate_resource_isolation_evidence,
    format_cpu_set,
    parse_cpu_set,
    resolve_loadgen_cpu_set,
    service_resource_delta,
    wait_for_service_quiescence,
)


class PerfCpuAffinityTest(unittest.TestCase):
    def test_parse_and_format_cpu_set(self) -> None:
        self.assertEqual(parse_cpu_set("0-2,4,6-7"), {0, 1, 2, 4, 6, 7})
        self.assertEqual(format_cpu_set({7, 2, 1, 0, 6, 4}), "0-2,4,6-7")

    def test_parse_rejects_invalid_cpu_sets(self) -> None:
        for value in ("", "0,,2", "2-0", "a", "1-2-3", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_cpu_set(value)

    @patch("scripts.producers.collect_v2_perf_baseline.platform.system", return_value="Linux")
    @patch("scripts.producers.collect_v2_perf_baseline.os.sched_setaffinity", create=True)
    @patch("scripts.producers.collect_v2_perf_baseline.os.sched_getaffinity", create=True)
    def test_apply_cpu_affinity_verifies_effective_set(
        self,
        get_affinity,
        set_affinity,
        _system,
    ) -> None:
        get_affinity.side_effect = [{0, 1, 2, 3}, {0, 1}]

        result = apply_cpu_affinity("0-1")

        set_affinity.assert_called_once_with(0, {0, 1})
        self.assertTrue(result["applied"])
        self.assertEqual(result["allowed_cpu_set_before"], "0-3")
        self.assertEqual(result["effective_cpu_set"], "0-1")
        self.assertEqual(result["cpu_count"], 2)

    @patch("scripts.producers.collect_v2_perf_baseline.platform.system", return_value="Linux")
    @patch("scripts.producers.collect_v2_perf_baseline.os.sched_setaffinity", create=True)
    @patch(
        "scripts.producers.collect_v2_perf_baseline.os.sched_getaffinity",
        return_value={2, 3},
        create=True,
    )
    def test_apply_cpu_affinity_rejects_cpu_outside_allowed_set(
        self,
        _get_affinity,
        set_affinity,
        _system,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "outside the collector's allowed set"):
            apply_cpu_affinity("0")
        set_affinity.assert_not_called()

    @patch("scripts.producers.collect_v2_perf_baseline.platform.system", return_value="Linux")
    @patch(
        "scripts.producers.collect_v2_perf_baseline.os.sched_getaffinity",
        return_value={0, 1, 2, 3, 4, 5},
        create=True,
    )
    def test_loadgen_cpu_set_defaults_to_disjoint_allowed_complement(
        self,
        _get_affinity,
        _system,
    ) -> None:
        self.assertEqual(resolve_loadgen_cpu_set("0-1", ""), "2-5")
        with self.assertRaisesRegex(ValueError, "must be disjoint"):
            resolve_loadgen_cpu_set("0-1", "1-2")
        with self.assertRaisesRegex(ValueError, "requires --cpu-set"):
            resolve_loadgen_cpu_set("", "2-5")

    @patch(
        "scripts.producers.collect_v2_perf_baseline.process_snapshot",
        return_value={"cpu_seconds": 12.5},
    )
    @patch(
        "scripts.producers.collect_v2_perf_baseline.fetch_json",
        return_value={"backend_metrics": {"battle": {"total_requests": 10}}},
    )
    def test_service_quiescence_requires_stable_routing_and_cpu_samples(
        self,
        _fetch_json,
        _process_snapshot,
    ) -> None:
        result = wait_for_service_quiescence(
            [SimpleNamespace(name="gateway", pid=123)],
            "http://127.0.0.1/diagnostics",
            timeout_seconds=0.1,
            interval_seconds=0.0,
        )
        self.assertTrue(result["quiesced"])
        self.assertEqual(result["samples"], 2)

    def test_service_resource_delta_uses_adjacent_snapshots(self) -> None:
        first = {"cpu_seconds": 10.0, "working_set_mb": 100.0, "handles": 5, "threads": 2}
        second = {"cpu_seconds": 40.0, "working_set_mb": 110.0, "handles": 6, "threads": 3}
        third = {"cpu_seconds": 70.0, "working_set_mb": 115.0, "handles": 7, "threads": 3}

        first_run = service_resource_delta(first, second, 30.0)
        second_run = service_resource_delta(second, third, 30.0)

        self.assertEqual(first_run["cpu_percent_from_cpu_seconds"], 100.0)
        self.assertEqual(second_run["cpu_percent_from_cpu_seconds"], 100.0)
        self.assertEqual(first_run["working_set_mb_delta"], 10.0)
        self.assertEqual(second_run["working_set_mb_delta"], 5.0)

    def test_resource_analysis_reads_each_run_before_and_after_snapshots(self) -> None:
        def snapshot(cpu_seconds: float) -> dict:
            return {
                "service_name": "gateway",
                "cpu_seconds": cpu_seconds,
                "working_set_mb": 100.0,
                "private_memory_mb": 80.0,
                "virtual_memory_mb": 200.0,
                "handles": 5,
                "threads": 2,
            }

        summary = {
            "cases": [
                {"case_name": "echo.run1", "elapsed_seconds": 30.0, "connected_clients": 1},
                {"case_name": "echo.run2", "elapsed_seconds": 30.0, "connected_clients": 1},
            ],
            "process_snapshots": {
                "idle": [snapshot(10.0)],
                "echo.run1": {
                    "before": [snapshot(10.0)],
                    "after": [snapshot(40.0)],
                    "loadgen": {"cpu_percent_from_cpu_seconds": 50.0},
                    "elapsed_seconds": 30.0,
                    "quiescence": {"quiesced": True},
                },
                "echo.run2": {
                    "before": [snapshot(40.0)],
                    "after": [snapshot(70.0)],
                    "loadgen": {"cpu_percent_from_cpu_seconds": 50.0},
                    "elapsed_seconds": 30.0,
                    "quiescence": {"quiesced": True},
                },
            },
            "service_resource_constraint": {
                "type": "linux_cpu_affinity",
                "applied": True,
                "effective_cpu_set": "0",
            },
            "loadgen_resource_constraint": {
                "type": "linux_cpu_affinity",
                "applied": True,
                "effective_cpu_set": "2-3",
            },
        }

        analysis = analyze_resources(summary)
        summary["resource_analysis"] = analysis
        isolation = evaluate_resource_isolation_evidence(summary)

        self.assertEqual(
            [run["services"]["gateway"]["cpu_percent_from_cpu_seconds"] for run in analysis["per_run"]],
            [100.0, 100.0],
        )
        self.assertTrue(isolation["passed"])


if __name__ == "__main__":
    unittest.main()
