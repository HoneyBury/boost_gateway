import json
import tempfile
import unittest
from pathlib import Path

from scripts.gates.release.aggregate_cpu_capacity_evidence import (
    SourceSpec,
    aggregate_sources,
    parse_source,
)


SHA = "a" * 40
CAPACITY_CASES = [
    "echo-1000-30s",
    "echo-5000-30s",
    "echo-10000-30s",
    "battle-100-30s",
    "battle-500-30s",
]
BUSINESS_CASES = ["echo-1000-30s", "battle-100-30s", "battle-500-30s"]


def cpu_set(cpu_count: int) -> str:
    return "0" if cpu_count == 1 else f"0-{cpu_count - 1}"


def perf_summary(cpu_count: int, preset: str, cases: list[str], gates_passed: bool) -> dict:
    aggregates = []
    for index, case in enumerate(cases, start=1):
        aggregates.append({
            "case_name": case,
            "runs": 3,
            "throughput_msg_per_sec": {
                "min": 90.0 * cpu_count,
                "median": 100.0 * cpu_count * index,
                "max": 110.0 * cpu_count,
            },
            "latency_p99_ms": {"min": 1.0, "median": 10.0 / cpu_count, "max": 12.0},
            "failed_clients": {"min": 0, "median": 0, "max": 0},
            "rejected_clients": {"min": 0, "median": 0, "max": 0},
        })
    summary = {
        "summary_version": 2,
        "git_commit": SHA,
        "preset": preset,
        "repetitions": 3,
        "resource_constraint": {
            "type": "linux_cpu_affinity",
            "requested": cpu_set(cpu_count),
            "applied": True,
            "effective_cpu_set": cpu_set(cpu_count),
            "cpu_count": cpu_count,
        },
        "case_aggregates": aggregates,
        "release_gates": {"overall_pass": gates_passed, "checks": []},
    }
    if preset == "business-capacity":
        summary["business_operation_perf"] = {
            "scenario_aggregates": [
                {
                    "scenario": "matchmaking",
                    "runs": 3,
                    "passed_runs": 3,
                    "passed": True,
                    "time_to_match_p99_ms": 40.0 / cpu_count,
                    "operations": [],
                },
                {
                    "scenario": "leaderboard",
                    "runs": 3,
                    "passed_runs": 3,
                    "passed": True,
                    "operations": [{
                        "operation": "leaderboard_submit",
                        "failed": 0,
                        "throughput_ops_per_sec": {"median": 100.0 * cpu_count},
                        "latency_p99_ms": {"median": 20.0 / cpu_count},
                    }],
                },
            ],
        }
    return summary


def write_source(root: Path, cpu_count: int, run_id: str, gates_passed: bool = True) -> SourceSpec:
    validation = root / "validation"
    capacity_dir = root / "perf" / "fixed-runner-capacity"
    business_dir = root / "perf" / "fixed-runner-business-capacity"
    validation.mkdir(parents=True)
    capacity_dir.mkdir(parents=True)
    business_dir.mkdir(parents=True)
    long_soak = {
        "summary_version": 2,
        "provenance": {"candidate_revision": SHA, "run_id": run_id},
        "cpu_set": cpu_set(cpu_count),
        "perf_repetitions": 3,
        "backend_pool_size": 8,
        "battle_route_workers": 8,
        "business_flow_clients": 3,
        "business_operation_clients": 16,
        "business_operation_iterations": 10,
        "run_capacity": True,
        "run_business_capacity": True,
        "run_business_operation_perf": True,
    }
    r4 = {
        "summary_version": 2,
        "provenance": {"candidate_revision": SHA},
        "overall_pass": gates_passed,
        "checks": [],
    }
    (validation / "long-soak-capacity-summary.json").write_text(json.dumps(long_soak), encoding="utf-8")
    (validation / "fixed-runner-release-capacity-summary.json").write_text(json.dumps(r4), encoding="utf-8")
    (capacity_dir / "summary.json").write_text(
        json.dumps(perf_summary(cpu_count, "capacity", CAPACITY_CASES, gates_passed)),
        encoding="utf-8",
    )
    (business_dir / "summary.json").write_text(
        json.dumps(perf_summary(cpu_count, "business-capacity", BUSINESS_CASES, gates_passed)),
        encoding="utf-8",
    )
    return SourceSpec(cpu_count, run_id, root)


class AggregateCpuCapacityEvidenceTest(unittest.TestCase):
    def test_complete_matrix_keeps_workload_failure_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            specs = [
                write_source(root / "one", 1, "101", gates_passed=False),
                write_source(root / "two", 2, "102"),
                write_source(root / "four", 4, "104"),
            ]

            summary = aggregate_sources(specs)

        self.assertTrue(summary["evidence_complete"])
        self.assertTrue(summary["passed"])
        self.assertFalse(summary["all_workload_gates_passed"])
        self.assertEqual(summary["candidate_revision"], SHA)
        echo_10k = next(
            item
            for item in summary["case_comparisons"]
            if item["profile"] == "capacity" and item["case"] == "echo-10000-30s"
        )
        self.assertEqual(echo_10k["throughput_speedup_vs_1_cpu"], {"2": 2.0, "4": 4.0})
        self.assertEqual(echo_10k["throughput_scaling_efficiency"], {"2": 1.0, "4": 1.0})
        time_to_match = summary["business_operation_comparisons"][0]
        self.assertEqual(time_to_match["by_cpu"], {"1": 40.0, "2": 20.0, "4": 10.0})
        leaderboard = summary["business_operation_comparisons"][1]
        self.assertEqual(leaderboard["persistence_mode"], "in_memory_only")
        self.assertEqual(leaderboard["by_cpu"]["4"]["throughput_median"], 400.0)

    def test_rejects_revision_affinity_repetition_and_case_drift(self) -> None:
        mutations = ("revision", "affinity", "repetitions", "cases")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                specs = [
                    write_source(root / "one", 1, "201"),
                    write_source(root / "two", 2, "202"),
                    write_source(root / "four", 4, "204"),
                ]
                target = root / "four" / "perf" / "fixed-runner-capacity" / "summary.json"
                payload = json.loads(target.read_text(encoding="utf-8"))
                if mutation == "revision":
                    payload["git_commit"] = "b" * 40
                elif mutation == "affinity":
                    payload["resource_constraint"]["effective_cpu_set"] = "0-2"
                elif mutation == "repetitions":
                    payload["repetitions"] = 2
                else:
                    payload["case_aggregates"].pop()
                target.write_text(json.dumps(payload), encoding="utf-8")

                summary = aggregate_sources(specs)

                self.assertFalse(summary["evidence_complete"])
                self.assertFalse(summary["passed"])
                self.assertFalse(summary["all_workload_gates_passed"])
                self.assertTrue(summary["failed_step"])
                self.assertEqual(summary["case_comparisons"], [])
                self.assertEqual(summary["business_operation_comparisons"], [])

    def test_requires_exactly_one_source_for_each_cpu_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary = aggregate_sources([
                write_source(root / "one", 1, "301"),
                write_source(root / "two", 2, "302"),
            ])
        self.assertFalse(summary["evidence_complete"])
        self.assertEqual(summary["failed_step"], "source-cpu-counts")

    def test_parse_source_splits_only_first_two_colons(self) -> None:
        source = parse_source("2:123:/tmp/evidence:with-colon")
        self.assertEqual(source.cpu_count, 2)
        self.assertEqual(source.run_id, "123")
        self.assertTrue(str(source.extracted_dir).endswith("evidence:with-colon"))


if __name__ == "__main__":
    unittest.main()
