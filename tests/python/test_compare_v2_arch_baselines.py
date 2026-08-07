import json
import tempfile
import unittest
from pathlib import Path

from scripts.gates.release.compare_v2_arch_baselines import (
    ComparisonError,
    evaluate_comparison,
)


class CompareV2ArchBaselinesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.config = {
            "architecture_regression_gates": {
                "minimum_repetitions": 5,
                "checks": [
                    {
                        "name": "latency",
                        "case": "runtime",
                        "metric": "p99_us",
                        "direction": "max",
                        "max_regression_pct": 10.0,
                        "min_absolute_delta": 1.0,
                    },
                    {
                        "name": "throughput",
                        "case": "runtime",
                        "metric": "throughput_ops_per_sec",
                        "direction": "min",
                        "max_regression_pct": 5.0,
                        "min_absolute_delta": 0.0,
                    },
                ],
            }
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def summaries(self, prefix: str, p99: float, throughput: float) -> list[Path]:
        paths = []
        for repetition in range(5):
            path = self.root / f"{prefix}-{repetition}.json"
            path.write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "name": "runtime",
                                "samples": 100,
                                "p99_us": p99 + repetition * 0.1,
                                "throughput_ops_per_sec": throughput + repetition,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            paths.append(path)
        return paths

    def evaluate(self, baseline: list[Path], candidate: list[Path]) -> dict:
        return evaluate_comparison(
            baseline,
            candidate,
            self.config,
            baseline_ref="main",
            candidate_ref="candidate",
        )

    def test_accepts_improved_candidate(self) -> None:
        result = self.evaluate(
            self.summaries("baseline", 10.0, 1000.0),
            self.summaries("candidate", 9.0, 1100.0),
        )

        self.assertTrue(result["overall_pass"])
        self.assertEqual(result["repetitions"], 5)

    def test_rejects_throughput_regression(self) -> None:
        result = self.evaluate(
            self.summaries("baseline", 10.0, 1000.0),
            self.summaries("candidate", 10.0, 900.0),
        )

        self.assertFalse(result["overall_pass"])
        failed = [check["name"] for check in result["checks"] if not check["passed"]]
        self.assertEqual(failed, ["throughput"])

    def test_ignores_sub_floor_latency_noise(self) -> None:
        result = self.evaluate(
            self.summaries("baseline", 2.0, 1000.0),
            self.summaries("candidate", 2.5, 1000.0),
        )

        self.assertTrue(result["overall_pass"])

    def test_requires_balanced_repetitions(self) -> None:
        baseline = self.summaries("baseline", 10.0, 1000.0)
        candidate = self.summaries("candidate", 10.0, 1000.0)[:-1]

        with self.assertRaisesRegex(ComparisonError, "counts differ"):
            self.evaluate(baseline, candidate)

    def test_fails_closed_on_missing_case(self) -> None:
        baseline = self.summaries("baseline", 10.0, 1000.0)
        candidate = self.summaries("candidate", 10.0, 1000.0)
        candidate[0].write_text('{"results": []}', encoding="utf-8")

        with self.assertRaisesRegex(ComparisonError, "missing case runtime"):
            self.evaluate(baseline, candidate)


if __name__ == "__main__":
    unittest.main()
