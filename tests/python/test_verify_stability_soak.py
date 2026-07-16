#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.gates.release import verify_stability_soak
from scripts.gates.release.verify_stability_soak import (
    archive_failed_arch_run,
    record_failure_episode,
    sustained_failure_violations,
)


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


def gate_failure(observed: float) -> dict[str, object]:
    return {
        "name": "latency",
        "metric": "p99_us",
        "value": observed,
        "threshold": 1000.0,
        "direction": "max",
        "passed": False,
    }


def run_result(returncode: int) -> dict[str, object]:
    return {
        "name": "baseline",
        "category": "baseline",
        "status": "passed" if returncode == 0 else "failed",
        "returncode": returncode,
        "duration_seconds": 1.0,
        "stdout_tail": "",
        "stderr_tail": "",
    }


class SustainedFailurePolicyTest(unittest.TestCase):
    def evaluate(
        self, entry: dict[str, object], completed_runs: int
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
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

    def test_accepts_rare_spike_when_two_confirmation_runs_recover(self) -> None:
        failures: dict[str, dict[str, object]] = {}
        record_failure_episode(failures, [[gate_failure(2141.51)], [], []])

        violations = sustained_failure_violations(failures, 1629)

        self.assertEqual([], violations)
        result = failures["latency"]
        self.assertEqual(0, result["confirmed_failed_runs"])
        self.assertEqual(1, result["unconfirmed_failed_runs"])
        self.assertEqual("confirmation_recovered", result["acceptance_tier"])

    def test_rejects_spike_when_confirmation_reproduces_it(self) -> None:
        failures: dict[str, dict[str, object]] = {}
        record_failure_episode(failures, [
            [gate_failure(2141.51)],
            [gate_failure(1900.0)],
            [],
        ])

        violations = sustained_failure_violations(failures, 1629)

        self.assertEqual(1, len(violations))
        result = failures["latency"]
        self.assertEqual(2, result["confirmed_failed_runs"])
        self.assertEqual("rejected", result["acceptance_tier"])

    def test_rejects_frequent_unconfirmed_spikes(self) -> None:
        failures: dict[str, dict[str, object]] = {}
        record_failure_episode(failures, [[gate_failure(2141.51)], [], []])
        record_failure_episode(failures, [[gate_failure(1800.0)], [], []])

        violations = sustained_failure_violations(failures, 1000)

        self.assertEqual(1, len(violations))
        self.assertEqual("rejected", failures["latency"]["acceptance_tier"])


class FailureArchiveTest(unittest.TestCase):
    def test_archives_failed_outputs_and_host_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory)
            for name in ("summary.json", "v2_arch_benchmark.json", "stdout.log", "stderr.log"):
                (output_root / name).write_text(name, encoding="utf-8")

            event = archive_failed_arch_run(
                output_root,
                17,
                "initial",
                {"load_average": [0.1, 0.2, 0.3]},
                {"load_average": [1.1, 1.2, 1.3]},
            )

            archive_dir = Path(str(event["archive_dir"]))
            self.assertEqual("summary.json", (archive_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("failed", event["status"])
            diagnostics = json.loads((archive_dir / "host-resources.json").read_text(encoding="utf-8"))
            self.assertEqual([0.1, 0.2, 0.3], diagnostics["before"]["load_average"])
            self.assertEqual([1.1, 1.2, 1.3], diagnostics["after"]["load_average"])


class SustainedBaselineConfirmationTest(unittest.TestCase):
    def run_baseline(self, results: list[dict[str, object]], failed_checks: list[list[dict[str, object]]]):
        with tempfile.TemporaryDirectory() as temporary_directory, \
                mock.patch.object(verify_stability_soak, "run_step", side_effect=results), \
                mock.patch.object(
                    verify_stability_soak, "failed_arch_checks", side_effect=failed_checks
                ), \
                mock.patch.object(
                    verify_stability_soak, "host_resource_snapshot", return_value={}
                ), \
                mock.patch.object(
                    verify_stability_soak,
                    "archive_failed_arch_run",
                    return_value={"archive_dir": "failure"},
                ) as archive, \
                mock.patch.object(
                    verify_stability_soak, "SUSTAINED_GATE_RARE_MAX_FAILURE_RATE", 1.0
                ):
            result = verify_stability_soak.run_sustained_arch_baseline(
                Path(temporary_directory),
                Path(temporary_directory),
                Path(temporary_directory) / "output",
                verify_stability_soak.SOAK_PROFILES["long"],
                30,
                "release",
                0.000001,
            )
        return result, archive

    def test_two_confirmation_passes_recover_initial_failure(self) -> None:
        result, archive = self.run_baseline(
            [run_result(2), run_result(0), run_result(0)],
            [[gate_failure(2141.51)]],
        )

        self.assertEqual("passed", result["status"])
        self.assertEqual(3, result["completed_runs"])
        self.assertEqual("confirmation_recovered", result["failed_checks"][0]["acceptance_tier"])
        self.assertEqual(3, archive.call_count)

    def test_repeated_confirmation_failure_is_rejected(self) -> None:
        result, archive = self.run_baseline(
            [run_result(2), run_result(2), run_result(0)],
            [[gate_failure(2141.51)], [gate_failure(1900.0)]],
        )

        self.assertEqual("failed", result["status"])
        self.assertEqual(3, result["completed_runs"])
        self.assertEqual("rejected", result["failed_checks"][0]["acceptance_tier"])
        self.assertEqual(3, archive.call_count)


if __name__ == "__main__":
    unittest.main()
