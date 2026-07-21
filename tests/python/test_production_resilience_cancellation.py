import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.gates.production import verify_production_resilience_gate
from scripts.lib.cancellable_process import CancellationState, installed_signal_handlers


def step_result(name: str, status: str, signal_name: str = "") -> dict[str, object]:
    return {
        "name": name,
        "category": "test",
        "command": ["child"],
        "cwd": str(Path.cwd()),
        "timeout_seconds": 1,
        "status": status,
        "returncode": 0 if status == "passed" else -signal.SIGTERM,
        "signal": signal_name,
        "duration_seconds": 0.1,
        "stdout_tail": "",
        "stderr_tail": "",
    }


class ProductionResilienceCancellationTest(unittest.TestCase):
    def test_run_step_captures_real_sigterm_and_stops_child_group(self) -> None:
        cancellation = CancellationState()

        def cancel() -> None:
            time.sleep(0.1)
            os.kill(os.getpid(), signal.SIGTERM)

        sender = threading.Thread(target=cancel, daemon=True)
        with installed_signal_handlers(cancellation):
            sender.start()
            result = verify_production_resilience_gate.run_step(
                "sleep",
                "test",
                [sys.executable, "-c", "import time; time.sleep(30)"],
                Path.cwd(),
                10,
                cancellation,
            )
        sender.join(timeout=1)

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["signal"], "SIGTERM")
        self.assertIsNotNone(result["returncode"])

    def test_current_invocation_replaces_old_pass_and_stops_after_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            summary_path = Path(temp) / "resilience-summary.json"
            summary_path.write_text(
                json.dumps({"overall_pass": True, "passed": True, "stale": True}),
                encoding="utf-8",
            )

            def cancel_first_step(name, _category, _command, _cwd, _timeout, cancellation):
                os.kill(os.getpid(), signal.SIGTERM)
                self.assertTrue(cancellation.cancelled)
                return step_result(name, "cancelled", cancellation.signal_name)

            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "verify_production_resilience_gate.py",
                        "--summary-path",
                        str(summary_path),
                    ],
                ),
                patch.object(
                    verify_production_resilience_gate,
                    "run_step",
                    side_effect=cancel_first_step,
                ) as run_step,
            ):
                returncode = verify_production_resilience_gate.main()

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(returncode, 143)
            self.assertEqual(run_step.call_count, 1)
            self.assertNotIn("stale", summary)
            self.assertTrue(summary["interrupted"])
            self.assertFalse(summary["overall_pass"])
            self.assertEqual(summary["current_step"], "P5 fixed-runner preflight")
            self.assertEqual(summary["completed_steps"], [])

    def test_normal_failure_continues_collecting_remaining_mandatory_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            summary_path = Path(temp) / "resilience-summary.json"
            results = [step_result("first", "failed")] + [
                step_result(f"step-{index}", "passed") for index in range(2, 7)
            ]
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "verify_production_resilience_gate.py",
                        "--summary-path",
                        str(summary_path),
                    ],
                ),
                patch.object(
                    verify_production_resilience_gate,
                    "run_step",
                    side_effect=results,
                ) as run_step,
            ):
                returncode = verify_production_resilience_gate.main()

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(returncode, 1)
            self.assertEqual(run_step.call_count, 6)
            self.assertFalse(summary["interrupted"])
            self.assertEqual(len(summary["completed_steps"]), 6)
            self.assertEqual(len(summary["steps"]), 6)
            self.assertFalse(summary["overall_pass"])

    def test_signal_during_final_atomic_write_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            summary_path = Path(temp) / "resilience-summary.json"
            real_atomic_write = verify_production_resilience_gate.atomic_write_json
            write_count = 0

            def interrupt_final_write(path, payload):
                nonlocal write_count
                real_atomic_write(path, payload)
                write_count += 1
                if write_count == 2:
                    os.kill(os.getpid(), signal.SIGTERM)

            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "verify_production_resilience_gate.py",
                        "--summary-path",
                        str(summary_path),
                    ],
                ),
                patch.object(
                    verify_production_resilience_gate,
                    "run_step",
                    side_effect=[
                        step_result(f"step-{index}", "passed")
                        for index in range(1, 7)
                    ],
                ),
                patch.object(
                    verify_production_resilience_gate,
                    "atomic_write_json",
                    side_effect=interrupt_final_write,
                ),
            ):
                returncode = verify_production_resilience_gate.main()

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(returncode, 143)
            self.assertEqual(write_count, 3)
            self.assertTrue(summary["interrupted"])
            self.assertFalse(summary["overall_pass"])
            self.assertEqual(summary["current_step"], "between_steps")

    def test_run_step_preserves_timeout_status(self) -> None:
        result = verify_production_resilience_gate.run_step(
            "sleep",
            "test",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            Path.cwd(),
            0.05,
            CancellationState(),
        )

        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["signal"], "")


if __name__ == "__main__":
    unittest.main()
