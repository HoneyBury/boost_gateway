#!/usr/bin/env python3

from __future__ import annotations

import copy
import unittest

from scripts.gates.release.verify_stability_soak import sustained_failure_violations


def failure(*, failed_runs: int, observed: float, direction: str = "max") -> dict[str, object]:
    return {
        "name": "latency" if direction == "max" else "throughput",
        "metric": "p99_us" if direction == "max" else "throughput_ops_per_sec",
        "threshold": 1000.0,
        "direction": direction,
        "failed_runs": failed_runs,
        "last_observed": observed,
        "worst_observed": observed,
    }


class SustainedFailurePolicyTest(unittest.TestCase):
    def evaluate(self, entry: dict[str, object], completed_runs: int) -> tuple[list[dict[str, object]], dict[str, object]]:
        failures = {str(entry["name"]): copy.deepcopy(entry)}
        violations = sustained_failure_violations(failures, completed_runs)
        return violations, failures[str(entry["name"])]

    def test_accepts_standard_transient_failure(self) -> None:
        violations, result = self.evaluate(failure(failed_runs=4, observed=1100.0), 1600)
        self.assertEqual([], violations)
        self.assertEqual("standard", result["acceptance_tier"])

    def test_accepts_rare_moderate_tail_spike(self) -> None:
        violations, result = self.evaluate(failure(failed_runs=1, observed=1214.98), 1577)
        self.assertEqual([], violations)
        self.assertEqual("rare_tail", result["acceptance_tier"])

    def test_rejects_rare_spike_above_hard_deviation_limit(self) -> None:
        violations, result = self.evaluate(failure(failed_runs=1, observed=1250.01), 1577)
        self.assertEqual(1, len(violations))
        self.assertEqual("rejected", result["acceptance_tier"])

    def test_rejects_frequent_small_regression(self) -> None:
        violations, result = self.evaluate(failure(failed_runs=17, observed=1050.0), 1600)
        self.assertEqual(1, len(violations))
        self.assertEqual("rejected", result["acceptance_tier"])

    def test_applies_policy_to_minimum_throughput_gate(self) -> None:
        violations, result = self.evaluate(
            failure(failed_runs=1, observed=760.0, direction="min"),
            1600,
        )
        self.assertEqual([], violations)
        self.assertEqual("rare_tail", result["acceptance_tier"])


if __name__ == "__main__":
    unittest.main()
