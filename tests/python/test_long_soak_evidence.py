import json
import io
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from scripts.gates.production.check_production_evidence_manifest import check_evidence
from scripts.gates.production import run_long_soak_capacity
from scripts.gates.production.run_long_soak_capacity import attach_provenance, parse_args
from scripts.lib.cancellable_process import (
    CancellationState,
    installed_signal_handlers,
    run_cancellable_process,
)


class LongSoakEvidenceTest(unittest.TestCase):
    def test_no_action_replaces_stale_outer_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            summary_path = Path(temp) / "summary.json"
            summary_path.write_text(
                json.dumps({"overall_pass": True, "passed": True, "stale": True}),
                encoding="utf-8",
            )
            with patch.object(
                sys,
                "argv",
                ["run_long_soak_capacity.py", "--summary-path", str(summary_path)],
            ):
                returncode = run_long_soak_capacity.main()

            summary = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(returncode, 2)
        self.assertFalse(summary["overall_pass"])
        self.assertFalse(summary["passed"])
        self.assertNotIn("stale", summary)

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork/process groups")
    def test_cancellation_kills_descendant_after_group_leader_exits(self):
        with tempfile.TemporaryDirectory() as temp:
            child_pid_path = Path(temp) / "child.pid"
            cancellation = CancellationState()
            child_pid: int | None = None

            def cancel_after_child_starts() -> None:
                deadline = time.monotonic() + 2
                while not child_pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                cancellation.request(signal.SIGTERM)

            program = (
                "import os, pathlib, signal, time\n"
                "child = os.fork()\n"
                "if child:\n"
                "    os._exit(0)\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                f"pathlib.Path({str(child_pid_path)!r}).write_text(str(os.getpid()))\n"
                "while True:\n"
                "    time.sleep(1)\n"
            )
            sender = threading.Thread(target=cancel_after_child_starts, daemon=True)
            started = time.monotonic()
            try:
                sender.start()
                result = run_cancellable_process(
                    [sys.executable, "-c", program],
                    Path.cwd(),
                    10,
                    cancellation,
                    cancellation_grace_seconds=0.05,
                    timeout_grace_seconds=0.05,
                    poll_interval_seconds=0.01,
                )
                elapsed = time.monotonic() - started
                sender.join(timeout=1)
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))

                self.assertEqual(result["status"], "cancelled")
                self.assertLess(elapsed, 3)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        os.kill(child_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)
                else:
                    self.fail("descendant process survived process-group cancellation")
            finally:
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_sigterm_cancels_process_group_step(self):
        cancellation = CancellationState()

        def cancel() -> None:
            time.sleep(0.1)
            os.kill(os.getpid(), signal.SIGTERM)

        sender = threading.Thread(target=cancel, daemon=True)
        with installed_signal_handlers(cancellation):
            sender.start()
            result = run_cancellable_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                Path.cwd(),
                10,
                cancellation,
                termination_grace_seconds=0.1,
                poll_interval_seconds=0.02,
            )
        sender.join(timeout=1)

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["signal"], "SIGTERM")
        self.assertIsNotNone(result["returncode"])

    def test_cancellable_process_preserves_timeout_status(self):
        result = run_cancellable_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            Path.cwd(),
            0.05,
            CancellationState(),
            termination_grace_seconds=0.1,
            poll_interval_seconds=0.01,
        )

        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["signal"], "")

    def test_main_atomically_writes_interrupted_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            summary_path = Path(temp) / "summary.json"
            cancelled = {
                "name": "capacity baseline evidence",
                "category": "capacity",
                "command": ["child"],
                "status": "cancelled",
                "returncode": -signal.SIGTERM,
                "signal": "SIGTERM",
                "duration_seconds": 1.0,
                "stdout_tail": "",
                "stderr_tail": "",
            }
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_long_soak_capacity.py",
                        "--run-capacity",
                        "--summary-path",
                        str(summary_path),
                    ],
                ),
                patch.object(run_long_soak_capacity, "run_step", return_value=cancelled),
                patch.object(
                    run_long_soak_capacity,
                    "build_evidence_provenance",
                    return_value={"candidate_revision": "a" * 40},
                ),
            ):
                returncode = run_long_soak_capacity.main()

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(returncode, 143)
            self.assertTrue(summary["interrupted"])
            self.assertFalse(summary["overall_pass"])
            self.assertEqual(summary["interruption_signal"], "SIGTERM")
            self.assertEqual(summary["current_step"], "capacity baseline evidence")
            self.assertEqual(summary["completed_steps"], [])
            self.assertEqual(summary["steps"][0]["status"], "cancelled")
            self.assertEqual(list(summary_path.parent.glob(".*.tmp")), [])

    def test_main_replaces_old_outer_pass_before_starting_first_step(self):
        with tempfile.TemporaryDirectory() as temp:
            summary_path = Path(temp) / "summary.json"
            summary_path.write_text(
                json.dumps({"overall_pass": True, "passed": True, "stale": True}),
                encoding="utf-8",
            )

            def inspect_initial_summary(name, category, command, _timeout, _cancellation):
                initial = json.loads(summary_path.read_text(encoding="utf-8"))
                self.assertFalse(initial["overall_pass"])
                self.assertFalse(initial["passed"])
                self.assertNotIn("stale", initial)
                return {
                    "name": name,
                    "category": category,
                    "command": command,
                    "status": "cancelled",
                    "returncode": -signal.SIGTERM,
                    "signal": "SIGTERM",
                    "duration_seconds": 0.1,
                    "stdout_tail": "",
                    "stderr_tail": "",
                }

            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_long_soak_capacity.py",
                        "--run-capacity",
                        "--summary-path",
                        str(summary_path),
                    ],
                ),
                patch.object(
                    run_long_soak_capacity,
                    "run_step",
                    side_effect=inspect_initial_summary,
                ),
                patch.object(
                    run_long_soak_capacity,
                    "build_evidence_provenance",
                    return_value={"candidate_revision": "a" * 40},
                ),
            ):
                self.assertEqual(run_long_soak_capacity.main(), 143)

    def test_passed_long_soak_process_requires_passing_child_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary_path = root / "outer-summary.json"
            passed = {
                "name": "2h long-soak evidence",
                "category": "long_soak",
                "command": ["child"],
                "status": "passed",
                "returncode": 0,
                "signal": "",
                "duration_seconds": 0.1,
                "stdout_tail": "",
                "stderr_tail": "",
            }
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_long_soak_capacity.py",
                        "--run-2h-soak",
                        "--summary-path",
                        str(summary_path),
                    ],
                ),
                patch.object(run_long_soak_capacity, "ROOT", root),
                patch.object(run_long_soak_capacity, "run_step", return_value=passed),
                patch.object(
                    run_long_soak_capacity,
                    "build_evidence_provenance",
                    return_value={"candidate_revision": "a" * 40},
                ),
            ):
                returncode = run_long_soak_capacity.main()

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(returncode, 1)
            self.assertFalse(summary["overall_pass"])
            self.assertEqual(summary["steps"][0]["status"], "failed")
            self.assertIn(
                "unavailable or invalid",
                summary["steps"][0]["artifact_validation_error"],
            )

    def test_passed_capacity_process_requires_passing_child_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary_path = root / "outer-summary.json"
            passed = {
                "name": "capacity baseline evidence",
                "category": "capacity",
                "command": ["child"],
                "status": "passed",
                "returncode": 0,
                "signal": "",
                "duration_seconds": 0.1,
                "stdout_tail": "",
                "stderr_tail": "",
            }
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_long_soak_capacity.py",
                        "--run-capacity",
                        "--summary-path",
                        str(summary_path),
                    ],
                ),
                patch.object(run_long_soak_capacity, "ROOT", root),
                patch.object(run_long_soak_capacity, "run_step", return_value=passed),
                patch.object(
                    run_long_soak_capacity,
                    "build_evidence_provenance",
                    return_value={"candidate_revision": "a" * 40},
                ),
            ):
                returncode = run_long_soak_capacity.main()

            summary = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(returncode, 1)
        self.assertFalse(summary["overall_pass"])
        self.assertEqual(summary["steps"][0]["status"], "failed")
        self.assertIn(
            "capacity-baseline-summary.json",
            summary["steps"][0]["artifact_validation_error"],
        )

    def test_long_soak_cancellation_does_not_rebind_stale_target_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target_summary = root / "runtime/validation/long-soak-2h-summary.json"
            target_summary.parent.mkdir(parents=True)
            target_summary.write_text(
                json.dumps({"overall_pass": True, "stale": True}),
                encoding="utf-8",
            )
            summary_path = root / "outer-summary.json"
            cancelled = {
                "name": "2h long-soak evidence",
                "category": "long_soak",
                "command": ["child"],
                "status": "cancelled",
                "returncode": -signal.SIGTERM,
                "signal": "SIGTERM",
                "duration_seconds": 1.0,
                "stdout_tail": "",
                "stderr_tail": "",
            }

            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_long_soak_capacity.py",
                        "--run-2h-soak",
                        "--summary-path",
                        str(summary_path),
                    ],
                ),
                patch.object(run_long_soak_capacity, "ROOT", root),
                patch.object(run_long_soak_capacity, "run_step", return_value=cancelled),
                patch.object(
                    run_long_soak_capacity,
                    "build_evidence_provenance",
                    return_value={"candidate_revision": "a" * 40},
                ),
            ):
                returncode = run_long_soak_capacity.main()

            self.assertEqual(returncode, 143)
            self.assertFalse(target_summary.exists())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["interrupted"])
            self.assertFalse(summary["overall_pass"])

    def test_main_finally_writes_summary_on_unexpected_step_error(self):
        with tempfile.TemporaryDirectory() as temp:
            summary_path = Path(temp) / "summary.json"
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_long_soak_capacity.py",
                        "--run-capacity",
                        "--summary-path",
                        str(summary_path),
                    ],
                ),
                patch.object(
                    run_long_soak_capacity,
                    "run_step",
                    side_effect=RuntimeError("step exploded"),
                ),
                patch.object(
                    run_long_soak_capacity,
                    "build_evidence_provenance",
                    return_value={"candidate_revision": "a" * 40},
                ),
                self.assertRaisesRegex(RuntimeError, "step exploded"),
            ):
                run_long_soak_capacity.main()

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertFalse(summary["interrupted"])
            self.assertFalse(summary["overall_pass"])
            self.assertEqual(summary["current_step"], "capacity baseline evidence")
            self.assertIn("RuntimeError: step exploded", summary["failed_step"])

    def test_signal_between_steps_cannot_produce_a_passing_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            summary_path = Path(temp) / "summary.json"

            def pass_then_cancel(name, category, command, _timeout, cancellation):
                cancellation.request(signal.SIGINT)
                return {
                    "name": name,
                    "category": category,
                    "command": command,
                    "status": "passed",
                    "returncode": 0,
                    "signal": "",
                    "duration_seconds": 0.1,
                    "stdout_tail": "",
                    "stderr_tail": "",
                }

            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_long_soak_capacity.py",
                        "--run-capacity",
                        "--run-business-capacity",
                        "--summary-path",
                        str(summary_path),
                    ],
                ),
                patch.object(run_long_soak_capacity, "run_step", side_effect=pass_then_cancel),
                patch.object(
                    run_long_soak_capacity,
                    "build_evidence_provenance",
                    return_value={"candidate_revision": "a" * 40},
                ),
            ):
                returncode = run_long_soak_capacity.main()

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(returncode, 130)
            self.assertTrue(summary["interrupted"])
            self.assertFalse(summary["overall_pass"])
            self.assertEqual(summary["current_step"], "between_steps")
            self.assertEqual(summary["completed_steps"], ["capacity baseline evidence"])
            self.assertEqual(len(summary["steps"]), 1)

    def test_signal_during_final_atomic_write_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            summary_path = Path(temp) / "summary.json"
            passed = {
                "name": "capacity baseline evidence",
                "category": "capacity",
                "command": ["child"],
                "status": "passed",
                "returncode": 0,
                "signal": "",
                "duration_seconds": 0.1,
                "stdout_tail": "",
                "stderr_tail": "",
            }
            real_atomic_write = run_long_soak_capacity.atomic_write_json
            write_count = 0

            def interrupt_final_write(path, payload):
                nonlocal write_count
                real_atomic_write(path, payload)
                write_count += 1
                if write_count == 3:
                    os.kill(os.getpid(), signal.SIGINT)

            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "run_long_soak_capacity.py",
                        "--run-capacity",
                        "--summary-path",
                        str(summary_path),
                    ],
                ),
                patch.object(run_long_soak_capacity, "run_step", return_value=passed),
                patch.object(
                    run_long_soak_capacity,
                    "atomic_write_json",
                    side_effect=interrupt_final_write,
                ),
                patch.object(
                    run_long_soak_capacity,
                    "build_evidence_provenance",
                    return_value={"candidate_revision": "a" * 40},
                ),
            ):
                returncode = run_long_soak_capacity.main()

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(returncode, 130)
            self.assertEqual(write_count, 4)
            self.assertTrue(summary["interrupted"])
            self.assertFalse(summary["overall_pass"])
            self.assertEqual(summary["current_step"], "between_steps")

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
