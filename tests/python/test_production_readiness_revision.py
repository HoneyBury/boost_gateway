#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVISION_A = "a" * 40
REVISION_B = "b" * 40


class ProductionReadinessRevisionTest(unittest.TestCase):
    def run_report(self, expected_revision: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            common = {
                "summary_version": 2,
                "overall_pass": True,
                "passed": True,
                "candidate_revision": REVISION_A,
                "checks": [],
                "artifacts": {},
            }
            bounded = root / "bounded.json"
            fixed = root / "fixed.json"
            r0 = root / "r0.json"
            bounded.write_text(json.dumps(common), encoding="utf-8")
            fixed.write_text(
                json.dumps({**common, "require_fixed_runner": True}),
                encoding="utf-8",
            )
            r0.write_text(json.dumps(common), encoding="utf-8")
            summary = root / "summary.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/render_production_readiness_report.py"),
                    "--manifest-summary",
                    str(bounded),
                    "--fixed-runner-summary",
                    str(fixed),
                    "--r0-summary",
                    str(r0),
                    "--require-fixed-runner",
                    "--expected-candidate-revision",
                    expected_revision,
                    "--output",
                    str(root / "report.md"),
                    "--summary-path",
                    str(summary),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            return completed, json.loads(summary.read_text(encoding="utf-8"))

    def test_matching_checkout_revision_passes(self) -> None:
        completed, summary = self.run_report(REVISION_A)

        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertTrue(summary["final_production_ready"])
        self.assertTrue(summary["candidate_revision_matches_expected"])
        self.assertEqual(REVISION_A, summary["expected_candidate_revision"])

    def test_old_evidence_revision_is_rejected(self) -> None:
        completed, summary = self.run_report(REVISION_B)

        self.assertNotEqual(0, completed.returncode)
        self.assertFalse(summary["overall_pass"])
        self.assertFalse(summary["final_production_ready"])
        self.assertFalse(summary["candidate_revision_matches_expected"])
        self.assertEqual(REVISION_A, summary["candidate_revision"])
        self.assertEqual(REVISION_B, summary["expected_candidate_revision"])
        self.assertEqual("provenance", summary["failed_category"])
        self.assertEqual("candidate_revision", summary["failed_step"])


if __name__ == "__main__":
    unittest.main()
