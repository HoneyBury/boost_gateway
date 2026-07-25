"""Static contract tests for the governed real release failure drill."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/operations/run_release_failure_drill.sh"


class ReleaseFailureDrillScriptTest(unittest.TestCase):
    def test_script_has_valid_bash_syntax(self) -> None:
        completed = subprocess.run(
            ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_drill_uses_real_pause_and_always_unpauses(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('docker pause "${PAUSED_CONTAINER}"', text)
        self.assertIn('docker unpause "${PAUSED_CONTAINER}"', text)
        self.assertIn("trap cleanup EXIT INT TERM", text)
        self.assertIn("PAUSE_SECONDS:-120", text)
        self.assertIn('record.get("status") == "candidate_activated"', text)

    def test_drill_requires_failed_candidate_and_passing_recovery(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('record["status"] == "rolled_back"', text)
        self.assertIn('failed["overall_pass"] is False', text)
        self.assertIn('recovered["overall_pass"] is True', text)
        self.assertIn("TODO-0010 automatic recovery drill: PASS", text)

    def test_drill_has_no_destructive_volume_or_image_cleanup(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("down -v", "volume rm", "image prune", "docker rm"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
