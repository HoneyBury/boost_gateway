import json
import io
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from scripts.gates.production.check_production_evidence_manifest import check_evidence
from scripts.gates.production.run_long_soak_capacity import attach_provenance, parse_args


class LongSoakEvidenceTest(unittest.TestCase):
    def test_capacity_accepts_disjoint_loadgen_contract(self):
        with patch.object(
            sys,
            "argv",
            [
                "run_long_soak_capacity.py",
                "--run-capacity",
                "--cpu-set",
                "0",
                "--loadgen-cpu-set",
                "4-7",
                "--loadgen-io-threads",
                "4",
                "--io-cores",
                "2",
                "--capacity-case",
                "echo-5000-30s",
                "--capacity-case",
                "echo-10000-30s",
            ],
        ):
            args = parse_args()
        self.assertEqual(args.cpu_set, "0")
        self.assertEqual(args.loadgen_cpu_set, "4-7")
        self.assertEqual(args.loadgen_io_threads, 4)
        self.assertEqual(args.io_cores, 2)
        self.assertEqual(args.capacity_case, ["echo-5000-30s", "echo-10000-30s"])

    def test_capacity_rejects_non_positive_loadgen_threads(self):
        with (
            patch.object(
                sys,
                "argv",
                ["run_long_soak_capacity.py", "--run-capacity", "--loadgen-io-threads", "0"],
            ),
            patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            parse_args()
        self.assertEqual(raised.exception.code, 2)

    def test_capacity_requires_explicit_loadgen_set_when_services_are_constrained(self):
        with (
            patch.object(
                sys,
                "argv",
                ["run_long_soak_capacity.py", "--run-capacity", "--cpu-set", "0"],
            ),
            patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            parse_args()
        self.assertEqual(raised.exception.code, 2)

    def test_business_operation_perf_requires_capacity_profile(self):
        with (
            patch.object(sys, "argv", ["run_long_soak_capacity.py", "--run-business-operation-perf"]),
            patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            parse_args()
        self.assertEqual(raised.exception.code, 2)

    def test_redis_comparison_requires_business_capacity_and_three_repetitions(self):
        invalid_argv = [
            ["run_long_soak_capacity.py", "--leaderboard-redis-comparison"],
            [
                "run_long_soak_capacity.py",
                "--leaderboard-redis-comparison",
                "--run-business-operation-perf",
                "--run-business-capacity",
                "--perf-repetitions",
                "2",
            ],
        ]
        for argv in invalid_argv:
            with (
                self.subTest(argv=argv),
                patch.object(sys, "argv", argv),
                patch("sys.stderr", new=io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                parse_args()
            self.assertEqual(raised.exception.code, 2)

    def test_otel_comparison_requires_business_capacity_and_three_repetitions(self):
        invalid_argv = [
            ["run_long_soak_capacity.py", "--run-otel-comparison"],
            [
                "run_long_soak_capacity.py",
                "--run-otel-comparison",
                "--run-business-capacity",
                "--perf-repetitions",
                "2",
            ],
        ]
        for argv in invalid_argv:
            with (
                self.subTest(argv=argv),
                patch.object(sys, "argv", argv),
                patch("sys.stderr", new=io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                parse_args()
            self.assertEqual(raised.exception.code, 2)

    def test_attach_provenance_updates_completed_long_soak_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            summary_path = Path(temp) / "long-soak-2h-summary.json"
            summary_path.write_text(
                json.dumps({"summary_version": 2, "overall_pass": True}),
                encoding="utf-8",
            )
            provenance = {"candidate_revision": "a" * 40, "workflow": "contract-test"}

            attach_provenance(summary_path, provenance)

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertIs(summary["overall_pass"], True)
            self.assertEqual(summary["provenance"], provenance)

    def test_manifest_accepts_two_hour_summary_independently_of_failed_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            validation = root / "runtime" / "validation"
            validation.mkdir(parents=True)
            generated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            (validation / "long-soak-2h-summary.json").write_text(
                json.dumps(
                    {
                        "summary_version": 2,
                        "generated_at": generated_at,
                        "overall_pass": True,
                        "passed": True,
                        "soak_profile": "long",
                        "artifacts": {},
                    }
                ),
                encoding="utf-8",
            )
            (validation / "long-soak-capacity-summary.json").write_text(
                json.dumps({"summary_version": 2, "overall_pass": False, "passed": False}),
                encoding="utf-8",
            )
            item = {
                "id": "long_soak_2h",
                "category": "long_soak_capacity",
                "path": "runtime/validation/long-soak-2h-summary.json",
                "fixed_runner_required": True,
                "freshness_hours": 336,
                "required_json_values": {"soak_profile": "long"},
            }

            result = check_evidence(
                item,
                root,
                datetime.now(UTC),
                True,
                {},
                "",
            )

            self.assertIs(result["passed"], True)
            self.assertEqual(result["status"], "passed")


if __name__ == "__main__":
    unittest.main()
