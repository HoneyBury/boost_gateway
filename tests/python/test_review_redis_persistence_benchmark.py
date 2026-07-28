"""Tests for the governed Redis persistence performance review."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.tools import review_redis_persistence_benchmark as review


class RedisPersistenceReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        repository = Path(__file__).resolve().parents[2]
        self.decision = json.loads(
            (
                repository / "docs/decisions/todo0012-redis-aof-activation.json"
            ).read_text(encoding="utf-8")
        )
        observed = self.decision["review"]["observed"]
        candidate_throughputs = [
            observed["candidate_worst_round_throughput_requests_per_second"],
            observed["candidate_throughput_requests_per_second_median"],
            35335.69,
        ]
        candidate_p99 = [
            observed["candidate_p99_latency_ms_median"],
            observed["candidate_worst_round_p99_latency_ms"],
            0.607,
        ]
        rounds = []
        for mode in ("rdb_only", "aof_everysec_rdb"):
            for index in range(3):
                candidate = mode == "aof_everysec_rdb"
                rounds.append(
                    {
                        "mode": mode,
                        "repetition": index + 1,
                        "passed": True,
                        "redis_aof_delayed_fsync": 0,
                        "redis_bgsave": {"last_status": "ok"},
                        "effective_configuration": {
                            "appendonly": "yes" if candidate else "no",
                            "appendfsync": "everysec",
                            "maxmemory-policy": "noeviction",
                        },
                        "workload": {
                            "throughput_requests_per_second": (
                                candidate_throughputs[index] if candidate else 35714.29
                            ),
                            "p99_latency_ms": (
                                candidate_p99[index] if candidate else 0.791
                            ),
                        },
                    }
                )
        self.benchmark = {
            "benchmark_id": self.decision["benchmark"]["benchmark_id"],
            "overall_pass": True,
            "measurement_complete": True,
            "activation_ready": False,
            "production_compose_changed": False,
            "secret_material_recorded": False,
            "controller": {
                "commit": self.decision["benchmark"]["controller_commit"],
                "runner_sha256": self.decision["benchmark"]["runner_sha256"],
                "worktree_clean": True,
            },
            "policy": {"sha256": self.decision["benchmark"]["policy_sha256"]},
            "candidate_profile": {
                "sha256": self.decision["benchmark"]["profile_sha256"]
            },
            "workload": {
                "repetitions_per_mode": 3,
                "requests_per_repetition": 10000,
            },
            "rounds": rounds,
            "aggregates": {
                "aof_everysec_rdb": {
                    "throughput_requests_per_second_median": observed[
                        "candidate_throughput_requests_per_second_median"
                    ],
                    "p50_latency_ms_median": observed[
                        "candidate_p50_latency_ms_median"
                    ],
                    "p99_latency_ms_median": observed[
                        "candidate_p99_latency_ms_median"
                    ],
                    "redis_cpu_percent_of_one_core_median": observed[
                        "candidate_redis_cpu_percent_of_one_core_median"
                    ],
                    "redis_rss_sampled_peak_bytes_median": observed[
                        "candidate_redis_rss_sampled_peak_bytes_median"
                    ],
                    "redis_workload_disk_write_bytes_median": observed[
                        "candidate_workload_disk_write_bytes_median"
                    ],
                    "redis_bgsave_disk_write_bytes_median": observed[
                        "candidate_bgsave_disk_write_bytes_median"
                    ],
                    "redis_aof_delayed_fsync_total": observed[
                        "candidate_aof_delayed_fsync_total"
                    ],
                }
            },
            "candidate_impact_percent": {
                key: observed[key]
                for key in (
                    "throughput_percent",
                    "p50_latency_percent",
                    "p99_latency_percent",
                    "redis_cpu_percent",
                    "redis_rss_percent",
                    "redis_disk_write_bytes_percent",
                )
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_inputs(
        self,
        *,
        benchmark: dict | None = None,
        decision: dict | None = None,
    ) -> tuple[Path, Path]:
        benchmark_path = self.root / "benchmark.json"
        decision_path = self.root / "decision.json"
        benchmark_path.write_text(
            json.dumps(benchmark or self.benchmark), encoding="utf-8"
        )
        value = copy.deepcopy(decision or self.decision)
        value["benchmark"]["sha256"] = review.sha256_file(benchmark_path)
        decision_path.write_text(json.dumps(value), encoding="utf-8")
        return benchmark_path, decision_path

    def test_accepts_bound_measurement_and_keeps_activation_false(self) -> None:
        benchmark, decision = self.write_inputs()
        result = review.validate_review(benchmark, decision)

        self.assertTrue(result["overall_pass"])
        self.assertTrue(result["performance_review_pass"])
        self.assertTrue(result["rollback_contract_valid"])
        self.assertTrue(result["governed_candidate_ready"])
        self.assertFalse(result["production_activated"])
        self.assertFalse(result["activation_ready"])
        self.assertFalse(result["formal_todo0012_claim"])

    def test_rejects_benchmark_digest_drift(self) -> None:
        benchmark, decision = self.write_inputs()
        value = json.loads(benchmark.read_text(encoding="utf-8"))
        value["host"] = {"unexpected": True}
        benchmark.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaisesRegex(review.ReviewError, "benchmark SHA-256"):
            review.validate_review(benchmark, decision)

    def test_rejects_regression_beyond_frozen_limit(self) -> None:
        value = copy.deepcopy(self.benchmark)
        value["candidate_impact_percent"]["redis_cpu_percent"] = 21.0
        decision = copy.deepcopy(self.decision)
        decision["review"]["observed"]["redis_cpu_percent"] = 21.0
        benchmark_path, decision_path = self.write_inputs(
            benchmark=value, decision=decision
        )

        with self.assertRaisesRegex(review.ReviewError, "Redis CPU impact exceeds"):
            review.validate_review(benchmark_path, decision_path)

    def test_rejects_blind_rdb_rollback_contract(self) -> None:
        decision = copy.deepcopy(self.decision)
        decision["rollback"]["blind_old_compose_restore_prohibited"] = False
        benchmark_path, decision_path = self.write_inputs(decision=decision)

        with self.assertRaisesRegex(
            review.ReviewError, "blind_old_compose_restore_prohibited"
        ):
            review.validate_review(benchmark_path, decision_path)

    def test_create_only_output_refuses_overwrite(self) -> None:
        path = self.root / "summary.json"
        review.write_new(path, b"{}\n")
        with self.assertRaisesRegex(review.ReviewError, "create-only"):
            review.write_new(path, b"{}\n")


if __name__ == "__main__":
    unittest.main()
