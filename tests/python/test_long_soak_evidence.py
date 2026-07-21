import json
import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.gates.production.check_production_evidence_manifest import check_evidence
from scripts.gates.production import run_long_soak_capacity
from scripts.gates.production.run_long_soak_capacity import attach_provenance, parse_args
from scripts.lib.cancellable_process import (
    CancellationState,
    arm_parent_death_signal,
    installed_signal_handlers,
    run_cancellable_process,
)
from scripts.lib import cancellable_process


class LongSoakEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        parent_death_patcher = patch.object(
            run_long_soak_capacity,
            "arm_parent_death_signal",
            return_value=False,
        )
        parent_death_patcher.start()
        self.addCleanup(parent_death_patcher.stop)

    def test_parent_death_signal_is_noop_off_linux(self):
        with (
            patch.object(cancellable_process.sys, "platform", "darwin"),
            patch.object(cancellable_process.ctypes, "CDLL") as load_libc,
        ):
            self.assertFalse(arm_parent_death_signal(expected_parent_pid=123))
        load_libc.assert_not_called()

    def test_parent_death_signal_prctl_failure_is_fail_closed(self):
        libc = Mock()
        libc.prctl.return_value = -1
        with (
            patch.object(cancellable_process.sys, "platform", "linux"),
            patch.object(cancellable_process.ctypes, "CDLL", return_value=libc),
            patch.object(cancellable_process.ctypes, "get_errno", return_value=1),
            self.assertRaises(PermissionError),
        ):
            arm_parent_death_signal(expected_parent_pid=123)

    def test_parent_death_signal_detects_parent_exit_during_arm(self):
        libc = Mock()
        libc.prctl.return_value = 0
        with (
            patch.object(cancellable_process.sys, "platform", "linux"),
            patch.object(cancellable_process.ctypes, "CDLL", return_value=libc),
            patch.object(cancellable_process.os, "getppid", return_value=1),
            patch.object(cancellable_process.os, "getpid", return_value=456),
            patch.object(cancellable_process.os, "kill") as kill,
        ):
            self.assertTrue(arm_parent_death_signal(expected_parent_pid=123))

        libc.prctl.assert_called_once_with(1, signal.SIGTERM, 0, 0, 0)
        kill.assert_called_once_with(456, signal.SIGTERM)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux prctl")
    def test_parent_death_signal_fires_when_direct_parent_exits(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            ready_path = directory / "ready"
            worker_pid_path = directory / "worker.pid"
            result_path = directory / "result"
            worker_pid: int | None = None
            worker = (
                "import os, signal, time\n"
                "from pathlib import Path\n"
                "from scripts.lib.cancellable_process import (\n"
                "    CancellationState, arm_parent_death_signal, installed_signal_handlers\n"
                ")\n"
                "state = CancellationState()\n"
                "parent_pid = os.getppid()\n"
                "with installed_signal_handlers(state):\n"
                "    arm_parent_death_signal(expected_parent_pid=parent_pid)\n"
                f"    Path({str(worker_pid_path)!r}).write_text(str(os.getpid()))\n"
                f"    Path({str(ready_path)!r}).write_text('ready')\n"
                "    deadline = time.monotonic() + 5\n"
                "    while not state.cancelled and time.monotonic() < deadline:\n"
                "        time.sleep(0.01)\n"
                f"    Path({str(result_path)!r}).write_text(state.signal_name)\n"
            )
            mediator = (
                "import subprocess, sys, time\n"
                "from pathlib import Path\n"
                f"worker = {worker!r}\n"
                "subprocess.Popen(\n"
                "    [sys.executable, '-c', worker],\n"
                f"    cwd={str(Path.cwd())!r},\n"
                "    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
                ")\n"
                f"ready = Path({str(ready_path)!r})\n"
                "deadline = time.monotonic() + 3\n"
                "while not ready.exists() and time.monotonic() < deadline:\n"
                "    time.sleep(0.01)\n"
                "raise SystemExit(0 if ready.exists() else 1)\n"
            )
            try:
                subprocess.run(
                    [sys.executable, "-c", mediator],
                    cwd=Path.cwd(),
                    check=True,
                    timeout=5,
                )
                worker_pid = int(worker_pid_path.read_text(encoding="utf-8"))
                deadline = time.monotonic() + 3
                while not result_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(result_path.read_text(encoding="utf-8"), "SIGTERM")
            finally:
                if worker_pid is not None:
                    try:
                        os.kill(worker_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_outer_prctl_failure_writes_failure_summary_and_reraises(self):
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
                    "arm_parent_death_signal",
                    side_effect=PermissionError("prctl denied"),
                ),
                patch.object(
                    run_long_soak_capacity,
                    "build_evidence_provenance",
                    return_value={"candidate_revision": "a" * 40},
                ),
                self.assertRaisesRegex(PermissionError, "prctl denied"),
            ):
                run_long_soak_capacity.main()

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertFalse(summary["overall_pass"])
            self.assertFalse(summary["parent_death_signal_armed"])
            self.assertEqual(summary["failed_category"], "orchestrator")
            self.assertIn("PermissionError: prctl denied", summary["failed_step"])

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

    def test_saturation_requires_isolation_and_preserves_case_identity(self):
        with (
            patch.object(
                sys,
                "argv",
                ["run_long_soak_capacity.py", "--run-saturation"],
            ),
            patch("sys.stderr", new=io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            parse_args()
        self.assertEqual(raised.exception.code, 2)

        with patch.object(
            sys,
            "argv",
            [
                "run_long_soak_capacity.py",
                "--run-saturation",
                "--cpu-set",
                "0",
                "--loadgen-cpu-set",
                "4-7",
                "--saturation-case",
                "echo-sat-c1000-i20-60s",
                "--saturation-cpu-threshold-percent",
                "90",
                "--saturation-loadgen-headroom-percent",
                "80",
            ],
        ):
            args = parse_args()
        self.assertTrue(args.run_saturation)
        self.assertEqual(args.saturation_case, ["echo-sat-c1000-i20-60s"])
        self.assertEqual(args.saturation_cpu_threshold_percent, 90.0)
        self.assertEqual(args.saturation_loadgen_headroom_percent, 80.0)

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
